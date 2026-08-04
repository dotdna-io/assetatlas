#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regenerate the installer banner art from the real logo PNG.

The installer runs on bare servers, so it cannot depend on chafa/figlet/PIL at
runtime — a banner is never worth a dependency. Instead the logo is rendered
here, at development time, and the resulting escape sequences are pasted into
`print_banner()` in deploy/lib/common.sh and deploy/bootstrap/install.sh.

Rendering technique (the same one chafa uses): every terminal cell is U+2580
UPPER HALF BLOCK, with the foreground colour painting the top pixel and the
background colour the bottom one. That gives two pixels of vertical resolution
per row. Escape codes are emitted only when a colour actually changes, which
collapses the large flat areas of a logo — the output is ~1.9 KB rather than the
~8 KB a naive per-cell encoding would produce.

Escapes are written as literal `\\033[...` text, not raw ESC bytes, so the shell
scripts stay diffable, greppable and safe to patch. They are printed with
`printf '%b'`.

Usage:
    python3 scripts/render-banner.py [--cols 12] [--icon PATH]

Requires Pillow (dev-only: `pip install pillow`).
"""
import argparse
import sys

try:
    from PIL import Image, ImageEnhance
except ImportError:
    sys.exit("Pillow is required: pip install pillow")

UPPER_HALF = "▀"
REPO_ICON = "ui/prod/public/assetatlas_logo.png"

# Sampled from the flat wordmark areas of assetatlas_logo_full.png, not from the
# icon: the icon carries a dark gradient whose dominant pixels are olive
# (~#477221) and are not what the eye reads as the brand colour.
BRAND_GREEN = (107, 204, 19)    # #6bcc13 - the 'A' in Atlas, and DNA in dotdna
BRAND_SILVER = (154, 161, 169)  # #9aa1a9 - the wordmark grey
# Nearest xterm-256 indices, for terminals that advertise 256 colours but not
# truecolor - which is most SSH sessions, since sshd forwards TERM but not
# COLORTERM.
X256_GREEN, X256_SILVER = 76, 247

# The wordmark is drawn rather than rendered: at banner size the real one is
# 4.8:1 and collapses to ~5 illegible rows, so it is set in a block font that
# keeps the A's as bare Λ (no crossbar) and the Asset/A/tlas silver-green-silver
# split, which is what makes the logo read.
GREEN = r"\033[38;2;%d;%d;%dm" % BRAND_GREEN
SILVER = r"\033[38;2;%d;%d;%dm" % BRAND_SILVER
WHITE = r"\033[1;37m"
DIM = r"\033[2m"
RESET = r"\033[0m"


def _rebrand(rgb):
    """Re-tint a source pixel to the brand hue, keeping its shading.

    The icon in the PNG carries a dark gradient whose greens sit around #477221
    - olive, not the brand green the eye reads from the logo. Flattening to two
    colours fixes the hue but throws away the shading that gives the mark its
    shape at 12x5 cells. So instead: classify the pixel as green or silver,
    then scale the brand colour by that pixel's relative luminance. Hue becomes
    correct, shading survives.

    The clamp keeps the darkest pixels from going muddy and the lightest from
    blowing out to white.
    """
    r, g, b = rgb
    base = BRAND_GREEN if (g - (r + b) / 2) > 12 else BRAND_SILVER
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    base_lum = 0.2126 * base[0] + 0.7152 * base[1] + 0.0722 * base[2]
    k = 1.0 if base_lum == 0 else lum / base_lum
    k = max(0.72, min(1.12, k))
    return tuple(max(0, min(255, round(c * k))) for c in base)


def render_icon(path, cols, mode="truecolor"):
    """PNG -> list of half-block rows with printf-safe escapes."""
    img = Image.open(path).convert("RGBA")
    box = img.getbbox()
    if box:
        img = img.crop(box)
    rows = max(1, round(cols * img.height / img.width / 2))
    img = img.resize((cols, rows * 2), Image.LANCZOS)

    pix, alpha = img.convert("RGB").load(), img.getchannel("A").load()

    if mode == "256":
        # Shading is dropped here on purpose: the 256-colour cube has no room
        # for it at these hues, and banding looks worse than flat.
        # Snap to the flat brand colour BEFORE the change-detection compares
        # values, or two shades that map to the same index still look different
        # and every cell re-emits its escape.
        def shade(rgb):
            return BRAND_GREEN if (rgb[1] - (rgb[0] + rgb[2]) / 2) > 12 else BRAND_SILVER

        idx = {BRAND_GREEN: X256_GREEN, BRAND_SILVER: X256_SILVER}
        fgc = lambda c: r"\033[38;5;%dm" % idx[c]
        bgc = lambda c: r"\033[48;5;%dm" % idx[c]
    else:
        shade = _rebrand
        fgc = lambda c: r"\033[38;2;%d;%d;%dm" % c
        bgc = lambda c: r"\033[48;2;%d;%d;%dm" % c

    out = []
    for ry in range(rows):
        line, cur_fg, cur_bg = [], None, None
        for x in range(cols):
            fg = shade(pix[x, ry * 2]) if alpha[x, ry * 2] > 110 else None
            bg = shade(pix[x, ry * 2 + 1]) if alpha[x, ry * 2 + 1] > 110 else None
            if fg is None and bg is None:
                if cur_fg is not None or cur_bg is not None:
                    line.append(RESET)
                    cur_fg = cur_bg = None
                line.append(" ")
                continue
            if fg != cur_fg:
                line.append(r"\033[39m" if fg is None else fgc(fg))
                cur_fg = fg
            if bg != cur_bg:
                line.append(r"\033[49m" if bg is None else bgc(bg))
                cur_bg = bg
            line.append(UPPER_HALF)
        line.append(RESET)
        out.append("".join(line))
    return out


def wordmark(mode="truecolor"):
    green = r"\033[38;5;%dm" % X256_GREEN if mode == "256" else GREEN
    silver = r"\033[38;5;%dm" % X256_SILVER if mode == "256" else SILVER

    def row(pre, mid, post):
        return silver + pre + RESET + green + mid + RESET + silver + post + RESET

    return [
        row("▄▀▄ █▀▀ █▀▀ █▀▀ ▀█▀ ", "▄▀▄", " ▀█▀ █   ▄▀▄ █▀▀"),
        row("█ █ ▀▀█ ▀▀█ █▀▀  █  ", "█ █", "  █  █   █▀█ ▀▀█"),
        row("▀ ▀ ▀▀▀ ▀▀▀ ▀▀▀  ▀  ", "▀ ▀", "  ▀  ▀▀▀ ▀ ▀ ▀▀▀"),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icon", default=REPO_ICON)
    ap.add_argument("--cols", type=int, default=12)
    ap.add_argument("--mode", choices=("truecolor", "256"), default="truecolor")
    args = ap.parse_args()

    icon = render_icon(args.icon, args.cols, args.mode)
    _green = r"\033[38;5;%dm" % X256_GREEN if args.mode == "256" else GREEN
    body = wordmark(args.mode) + [
        DIM + "by " + RESET + WHITE + "DOT" + RESET + _green + "DNA" + RESET
        + DIM + "   IT asset intelligence" + RESET
    ]

    pad = " " * args.cols
    height = max(len(icon), len(body))
    icon_off = (height - len(icon)) // 2
    body_off = (height - len(body)) // 2

    print("# --- generated by scripts/render-banner.py; do not hand-edit ---")
    for i in range(height):
        left = icon[i - icon_off] if icon_off <= i < icon_off + len(icon) else pad
        right = body[i - body_off] if body_off <= i < body_off + len(body) else ""
        print(("  " + left + "   " + right).rstrip())


if __name__ == "__main__":
    main()
