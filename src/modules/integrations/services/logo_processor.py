# SPDX-License-Identifier: AGPL-3.0-or-later
"""Logo upload validation, sanitisation, and re-encoding.

The processor is the trust boundary for user-supplied logo files. It must:

  - Reject anything that isn't actually a supported image (don't trust the
    client-supplied MIME type or filename extension).
  - Re-encode raster uploads to a normalised PNG so the served bytes look
    consistent with the bundled library (256x256, square, transparent
    padding, EXIF stripped).
  - Sanitise SVG uploads by parsing with defusedxml (no DTD / external
    entity expansion) and walking the tree against an allowlist that
    drops scripts, event handlers, external references, and any other
    XSS-friendly construct.

Output is content-addressed (SHA-256 of the normalised bytes) so the same
input always yields the same on-disk filename — uploads naturally dedupe
and the URL stays stable across re-uploads of the same image.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from typing import Set

from PIL import Image, UnidentifiedImageError
from defusedxml import ElementTree as DefusedET

logger = logging.getLogger(__name__)

# 1 MB default cap matches the issue spec; callers can pass a tighter limit.
DEFAULT_MAX_BYTES = 1024 * 1024
TARGET_SIZE = 256

ALLOWED_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_SVG_EXTS = {".svg"}
# Pillow's format string -> normalised input recognition. We don't trust the
# client MIME, only what Pillow actually decoded.
ALLOWED_PILLOW_FORMATS = {"PNG", "JPEG", "WEBP"}

# Strict SVG allowlist — only the drawing primitives a logo realistically
# needs. Filters, animations, and interactive elements are rejected
# wholesale: they're not necessary for a static logo and dramatically
# enlarge the attack surface.
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ALLOWED_SVG_TAGS: Set[str] = {
    "svg", "g", "defs", "title", "desc", "metadata", "symbol", "use",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath",
    "linearGradient", "radialGradient", "stop",
    "clipPath", "mask", "pattern",
    "marker",
}

# Attributes are allowed if either:
#   1. They appear in this set explicitly, OR
#   2. They start with "data-" / "aria-" / a vendor-neutral prefix.
# Anything starting with "on" (event handlers) is always rejected, even if
# it appears here by accident — defence in depth.
ALLOWED_SVG_ATTRS: Set[str] = {
    # Identity / structural
    "id", "class", "style",
    # SVG core
    "xmlns", "viewBox", "version", "preserveAspectRatio",
    "width", "height", "x", "y", "cx", "cy", "r", "rx", "ry",
    "x1", "y1", "x2", "y2", "points", "d", "transform",
    # Painting
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
    "stroke-dashoffset", "stroke-miterlimit", "stroke-opacity",
    "opacity", "color", "display", "visibility",
    # Text
    "font-family", "font-size", "font-weight", "font-style",
    "text-anchor", "letter-spacing", "word-spacing", "dx", "dy",
    "dominant-baseline", "alignment-baseline",
    # Gradients / patterns
    "offset", "stop-color", "stop-opacity",
    "gradientUnits", "gradientTransform", "spreadMethod",
    "patternUnits", "patternContentUnits", "patternTransform",
    "fx", "fy", "fr",
    # Clip / mask / marker
    "clip-path", "clip-rule", "mask",
    "marker-start", "marker-mid", "marker-end",
    "markerWidth", "markerHeight", "markerUnits", "orient",
    "refX", "refY",
    # Symbol/use linkage
    "href",
}

# Attribute values that mention any of these prefixes are rejected.
# We don't try to allowlist URL schemes: a logo must reference internal
# fragments only (#some-id), so anything else gets dropped.
DISALLOWED_URL_PATTERNS = re.compile(
    r"(?:javascript|data|vbscript|file):", re.IGNORECASE,
)

# style="" attribute scrubbing — drop any url(...) reference. SVG logos
# legitimately use url(#fragment) for fills/clips, so allow that one form.
_STYLE_URL_RE = re.compile(r"url\s*\(\s*([^)]*)\s*\)", re.IGNORECASE)


class LogoUploadError(ValueError):
    """Raised on any validation or sanitisation failure.

    Callers (the blueprint) translate this into a 400 Bad Request with the
    message returned to the client — keep the messages user-friendly.
    """


@dataclass(frozen=True)
class ProcessedLogo:
    """A normalised, ready-to-store logo file."""
    filename: str          # content-hash + extension, e.g. "<sha256>.png"
    content: bytes         # bytes to write to disk
    mime_type: str         # Content-Type to serve back


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_upload(
    filename: str,
    raw_bytes: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    target_size: int = TARGET_SIZE,
) -> ProcessedLogo:
    """Validate, sanitise, and re-encode an uploaded logo.

    The client-supplied MIME type is intentionally not consulted — actual
    format is detected from the bytes (Pillow for raster, defusedxml for
    SVG) so a renamed/spoofed file can't bypass validation.

    Args:
        filename: Original filename (used only for extension hinting).
        raw_bytes: The uploaded file contents.
        max_bytes: Hard size cap; defaults to 1 MB per issue spec.
        target_size: Square output dimension for raster uploads.

    Returns:
        ProcessedLogo with content-addressed filename and normalised bytes.

    Raises:
        LogoUploadError: For any validation, format, or sanitisation
            failure. Message is safe to surface to the user.
    """
    if not raw_bytes:
        raise LogoUploadError("Uploaded file is empty.")
    if len(raw_bytes) > max_bytes:
        raise LogoUploadError(
            f"File is too large ({len(raw_bytes)} bytes) — "
            f"limit is {max_bytes} bytes."
        )

    ext = _extract_extension(filename)
    if ext in ALLOWED_SVG_EXTS:
        return _process_svg(raw_bytes)
    if ext in ALLOWED_RASTER_EXTS:
        return _process_raster(raw_bytes, target_size=target_size)

    raise LogoUploadError(
        "Unsupported file extension. "
        "Allowed: PNG, JPG/JPEG, WEBP, SVG."
    )


# ---------------------------------------------------------------------------
# Raster pipeline
# ---------------------------------------------------------------------------

def _process_raster(raw_bytes: bytes, *, target_size: int) -> ProcessedLogo:
    try:
        with Image.open(io.BytesIO(raw_bytes)) as src:
            # verify() consumes the file pointer and only validates header
            # consistency; reopen for actual decoding.
            src.verify()
        # Second open is required because verify() invalidates the image.
        # Wrap in `with` so the underlying BytesIO buffer and any backing
        # resources are released deterministically once we've finished
        # producing the canvas — don't rely on GC ordering.
        with Image.open(io.BytesIO(raw_bytes)) as decoded:
            decoded.load()
            if decoded.format not in ALLOWED_PILLOW_FORMATS:
                raise LogoUploadError(
                    f"Unsupported image format: {decoded.format}. "
                    "Allowed: PNG, JPG/JPEG, WEBP."
                )
            canvas = _resize_and_pad(decoded, target_size)
    except LogoUploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise LogoUploadError(
            "File is not a valid PNG/JPEG/WEBP image."
        ) from exc

    out = io.BytesIO()
    # Saving without `exif=` and from a freshly-constructed RGBA image
    # naturally drops every metadata block (EXIF/XMP/ICC). optimize=True
    # shrinks the output without changing pixels.
    canvas.save(out, format="PNG", optimize=True)
    payload = out.getvalue()

    digest = hashlib.sha256(payload).hexdigest()
    return ProcessedLogo(
        filename=f"{digest}.png",
        content=payload,
        mime_type="image/png",
    )


def _resize_and_pad(image: Image.Image, size: int) -> Image.Image:
    """Resize preserving aspect ratio, then centre on a transparent square."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    w, h = image.size
    if w == 0 or h == 0:
        raise LogoUploadError("Image has zero width or height.")

    scale = min(size / w, size / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - new_w) // 2, (size - new_h) // 2), resized)
    return canvas


# ---------------------------------------------------------------------------
# SVG pipeline
# ---------------------------------------------------------------------------

def _process_svg(raw_bytes: bytes) -> ProcessedLogo:
    # defusedxml refuses DTDs and external entities, which neutralises
    # XXE/billion-laughs before we even start the allowlist walk.
    try:
        root = DefusedET.fromstring(raw_bytes)
    except DefusedET.ParseError as exc:
        raise LogoUploadError(
            "Uploaded SVG could not be parsed (invalid XML or unsafe "
            "constructs were present)."
        ) from exc
    except Exception as exc:  # defusedxml raises a few internal types
        raise LogoUploadError(
            "Uploaded SVG could not be parsed (invalid or unsafe XML)."
        ) from exc

    if _localname(root.tag) != "svg":
        raise LogoUploadError("Uploaded file is not an SVG document.")

    _scrub_element(root)

    # Re-serialise. We use the standard library's ElementTree for output —
    # defusedxml's parser produces stdlib Element instances that the
    # stdlib serializer can write back out.
    import xml.etree.ElementTree as ET  # noqa: PLC0415 (deliberate)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)

    digest = hashlib.sha256(body).hexdigest()
    return ProcessedLogo(
        filename=f"{digest}.svg",
        content=body,
        mime_type="image/svg+xml",
    )


def _scrub_element(element) -> None:
    """Recursively allowlist-filter element tags, attributes, and children."""
    # Drop disallowed children first so we don't keep walking into hostile
    # subtrees.
    for child in list(element):
        if _localname(child.tag) not in ALLOWED_SVG_TAGS:
            element.remove(child)
            continue
        _scrub_element(child)

    # Filter attributes on the surviving element.
    for attr_name in list(element.attrib.keys()):
        local = _localname(attr_name)
        value = element.attrib[attr_name]

        # Event handlers (onclick, onload, onmouseover, …) are always out.
        if local.lower().startswith("on"):
            del element.attrib[attr_name]
            continue

        # href / xlink:href: only intra-document fragments allowed, so
        # `<use href="#icon-foo"/>` keeps working but external refs and
        # javascript: URLs are dropped.
        if local in ("href",) or attr_name.endswith("}href"):
            if not value.lstrip().startswith("#"):
                del element.attrib[attr_name]
                continue

        # Style attribute can hide url(javascript:...) tricks. Allow only
        # url(#fragment) references.
        if local == "style":
            element.attrib[attr_name] = _scrub_style(value)
            continue

        # Generic value scrubbing: any attribute referencing a hostile URL
        # scheme is dropped (catches things like fill="url(javascript:...)"
        # which renderers historically tolerated).
        if DISALLOWED_URL_PATTERNS.search(value):
            del element.attrib[attr_name]
            continue

        if local not in ALLOWED_SVG_ATTRS and not (
            local.startswith("data-") or local.startswith("aria-")
        ):
            # Unknown attribute — be conservative and drop it.
            del element.attrib[attr_name]


def _scrub_style(value: str) -> str:
    """Drop url(...) references that aren't internal fragments."""
    def _keep(match: re.Match) -> str:
        ref = match.group(1).strip().strip('"').strip("'")
        if ref.startswith("#"):
            return f"url({ref})"
        return ""  # unsafe — drop
    return _STYLE_URL_RE.sub(_keep, value)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_extension(filename: str) -> str:
    """Return the lowercase extension including the dot, or '' if none."""
    if not filename:
        return ""
    idx = filename.rfind(".")
    if idx == -1:
        return ""
    return filename[idx:].lower()


def _localname(tag: str) -> str:
    """Strip the {namespace} prefix that ElementTree puts on qualified names."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag
