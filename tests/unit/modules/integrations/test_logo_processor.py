# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the integration logo upload processor.

The processor is the trust boundary for user-supplied logo files: it must
detect the actual format (not trust the client mime/ext), reject anything
out of policy, and re-encode raster output to a known-good PNG with EXIF
stripped, while sanitising SVG to remove scripts, event handlers, and
external references.
"""
import io
import re

import pytest
from PIL import Image

from src.modules.integrations.services.logo_processor import (
    LogoUploadError,
    process_upload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _png_bytes(size=(200, 200), color=(255, 0, 0, 255), mode="RGBA") -> bytes:
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(200, 200), color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _webp_bytes(size=(200, 200), color=(0, 255, 0)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _png_with_exif() -> bytes:
    """A PNG with an EXIF block tagged in (Pillow can carry EXIF on PNG too)."""
    img = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    buf = io.BytesIO()
    # Save with explicit EXIF metadata that the processor must drop.
    exif = img.getexif()
    exif[0x010F] = "TestCamera"  # Make
    img.save(buf, format="PNG", exif=exif.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Raster: happy paths
# ---------------------------------------------------------------------------

class TestRasterAccept:
    def test_accepts_png(self):
        result = process_upload("logo.png", _png_bytes())
        assert result.mime_type == "image/png"
        assert result.filename.endswith(".png")
        # SHA-256 hex (64 chars)
        assert re.fullmatch(r"[0-9a-f]{64}\.png", result.filename)
        # Output is a valid 256x256 PNG
        out = Image.open(io.BytesIO(result.content))
        assert out.format == "PNG"
        assert out.size == (256, 256)

    def test_accepts_jpeg(self):
        result = process_upload("logo.jpg", _jpeg_bytes())
        # Re-encoded to PNG regardless of input
        assert result.mime_type == "image/png"
        out = Image.open(io.BytesIO(result.content))
        assert out.format == "PNG"
        assert out.size == (256, 256)

    def test_accepts_webp(self):
        result = process_upload("logo.webp", _webp_bytes())
        assert result.mime_type == "image/png"
        out = Image.open(io.BytesIO(result.content))
        assert out.format == "PNG"

    def test_pads_non_square_to_square_transparent(self):
        wide = _png_bytes(size=(300, 100))
        result = process_upload("wide.png", wide)
        out = Image.open(io.BytesIO(result.content))
        assert out.size == (256, 256)
        assert out.mode == "RGBA"
        # Top-left and bottom-right corners must be transparent (padding band)
        assert out.getpixel((0, 0))[3] == 0
        assert out.getpixel((255, 255))[3] == 0
        # Middle row should hit the actual image (red, opaque)
        cx, cy = 128, 128
        r, _g, _b, a = out.getpixel((cx, cy))
        assert a == 255 and r > 200

    def test_downscales_large_image(self):
        big = _png_bytes(size=(2048, 2048))
        result = process_upload("big.png", big)
        out = Image.open(io.BytesIO(result.content))
        assert out.size == (256, 256)

    def test_strips_exif_metadata(self):
        result = process_upload("exif.png", _png_with_exif())
        out = Image.open(io.BytesIO(result.content))
        # Pillow's getexif on the re-encoded image must be empty
        assert dict(out.getexif()) == {}

    def test_filename_is_content_addressed(self):
        png = _png_bytes()
        a = process_upload("a.png", png)
        b = process_upload("b.png", png)
        assert a.filename == b.filename, (
            "Same input bytes must yield the same content-hash filename"
        )

    def test_different_inputs_yield_different_filenames(self):
        a = process_upload("a.png", _png_bytes(color=(255, 0, 0, 255)))
        b = process_upload("b.png", _png_bytes(color=(0, 0, 255, 255)))
        assert a.filename != b.filename


# ---------------------------------------------------------------------------
# Raster: rejections
# ---------------------------------------------------------------------------

class TestRasterReject:
    def test_rejects_oversized(self):
        # 2 MB of zeros — definitely over 1 MB cap.
        with pytest.raises(LogoUploadError, match=r"too large|exceeds"):
            process_upload("big.png", b"\x00" * (2 * 1024 * 1024))

    def test_rejects_bytes_that_are_not_an_image(self):
        with pytest.raises(LogoUploadError, match=r"not a valid|could not be decoded"):
            process_upload("fake.png", b"not an image at all")

    def test_rejects_disallowed_extension(self):
        with pytest.raises(LogoUploadError, match=r"Unsupported|extension"):
            process_upload("evil.exe", _png_bytes())

    def test_rejects_html_disguised_as_svg(self):
        # GIF is not in the accepted list.
        img = Image.new("RGB", (100, 100), (0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        with pytest.raises(LogoUploadError):
            process_upload("logo.gif", buf.getvalue())


# ---------------------------------------------------------------------------
# SVG: happy + sanitisation
# ---------------------------------------------------------------------------

class TestSvg:
    SIMPLE_SVG = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        b'<circle cx="50" cy="50" r="40" fill="red"/>'
        b"</svg>"
    )

    def test_accepts_clean_svg(self):
        result = process_upload("logo.svg", self.SIMPLE_SVG)
        assert result.mime_type == "image/svg+xml"
        assert result.filename.endswith(".svg")
        assert b"<circle" in result.content
        assert b"<svg" in result.content

    def test_strips_script_tag(self):
        evil = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<script>alert(1)</script>'
            b'<circle cx="50" cy="50" r="40"/>'
            b"</svg>"
        )
        result = process_upload("evil.svg", evil)
        assert b"<script" not in result.content
        assert b"alert" not in result.content
        assert b"<circle" in result.content

    def test_strips_event_handlers(self):
        evil = (
            b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
            b'<circle cx="50" cy="50" r="40" onclick="steal()"/>'
            b"</svg>"
        )
        result = process_upload("evil.svg", evil)
        assert b"onload" not in result.content
        assert b"onclick" not in result.content
        assert b"alert" not in result.content
        assert b"steal" not in result.content

    def test_strips_external_href(self):
        # A javascript: URL or external http(s):// reference must not survive.
        evil = (
            b'<svg xmlns="http://www.w3.org/2000/svg" '
            b'xmlns:xlink="http://www.w3.org/1999/xlink">'
            b'<a xlink:href="javascript:alert(1)">'
            b'<rect x="0" y="0" width="100" height="100"/>'
            b"</a></svg>"
        )
        result = process_upload("evil.svg", evil)
        assert b"javascript:" not in result.content

    def test_strips_foreignobject(self):
        evil = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<foreignObject><iframe src="evil.com"/></foreignObject>'
            b'<circle cx="10" cy="10" r="5"/>'
            b"</svg>"
        )
        result = process_upload("evil.svg", evil)
        assert b"foreignObject" not in result.content
        assert b"iframe" not in result.content
        assert b"evil.com" not in result.content
        assert b"<circle" in result.content

    def test_rejects_xml_external_entity(self):
        # The classic XXE — defusedxml refuses to parse, so we reject up-front.
        xxe = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>'
        )
        with pytest.raises(LogoUploadError, match=r"invalid|unsafe|could not"):
            process_upload("xxe.svg", xxe)

    def test_rejects_non_svg_xml(self):
        # An XML that isn't SVG must be rejected.
        with pytest.raises(LogoUploadError, match=r"(?i)not (a |an )?svg"):
            process_upload(
                "logo.svg",
                b'<?xml version="1.0"?><foo><bar/></foo>',
            )

    def test_strips_style_element_with_css_import(self):
        """A `<style>` element with `@import` must not survive sanitisation.

        `<style>` is not in the tag allowlist, so the entire element
        (and its text content) gets dropped on the first walk. Without
        this guarantee, an attacker could ship a `<style>@import
        url(https://evil.example/x.css);</style>` payload that pulls in
        external CSS at render time.
        """
        evil = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<style>@import url("https://evil.example/x.css");</style>'
            b'<circle cx="10" cy="10" r="5"/>'
            b"</svg>"
        )
        result = process_upload("evil.svg", evil)
        assert b"<style" not in result.content
        assert b"@import" not in result.content
        assert b"evil.example" not in result.content
        assert b"<circle" in result.content

    def test_strips_style_element_with_expression(self):
        """Legacy IE `expression()` constructs in `<style>` text are also dropped."""
        evil = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<style>rect { width: expression(alert(1)); }</style>'
            b'<rect x="0" y="0" width="10" height="10"/>'
            b"</svg>"
        )
        result = process_upload("evil.svg", evil)
        assert b"<style" not in result.content
        assert b"expression" not in result.content
        assert b"alert" not in result.content

    def test_strips_style_attribute_url_javascript(self):
        """Style *attribute* (not element) — javascript: URLs in url() must be dropped."""
        evil = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<rect style="fill: url(javascript:alert(1))" '
            b'x="0" y="0" width="10" height="10"/>'
            b"</svg>"
        )
        result = process_upload("evil.svg", evil)
        assert b"javascript" not in result.content
        assert b"alert" not in result.content

    def test_keeps_style_attribute_with_internal_fragment_url(self):
        """``url(#fragment)`` references stay — they're how SVG legitimately wires
        gradient/clip refs from the ``style=""`` attribute."""
        ok = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<defs><linearGradient id="g"><stop offset="0%" stop-color="red"/></linearGradient></defs>'
            b'<rect style="fill: url(#g)" x="0" y="0" width="10" height="10"/>'
            b"</svg>"
        )
        result = process_upload("ok.svg", ok)
        assert b"url(#g)" in result.content
