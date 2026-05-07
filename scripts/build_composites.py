#!/usr/bin/env python3
"""Render brand+state pill composites used as the row icon in the dropdown.

For every (source, state) pair, produces a horizontal pill (rounded
rectangle) PNG containing the brand silhouette on the left and a real
SF Symbol on the right, both cut out of the pill so the result is a
template image (black + alpha) and macOS auto-tints it for light/dark
menu bars.

Source resolution is set to 3× the display size — covers Retina @2x and
@3x displays without visible re-sampling. State glyphs are rendered via
``NSImage.imageWithSystemSymbolName_`` (real SF Symbols, not hand-drawn)
at 4× headroom and bbox-cropped before downsampling, matching the
crispness NSMenu gets when it draws an SF Symbol natively.

Output is written to ``lib/claudebar.py`` as ``STATE_BRAND_LOGOS``.
The plugin reads it and feeds ``templateImage=`` plus ``width=`` /
``height=`` per session row.

Run when:
- the brand assets, state list, pill geometry, or state symbol mapping
  changes
- ``plugin/swiftbar-config.json``'s ``icons`` section changes
"""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "assets"
LIB_PATH = REPO_ROOT / "lib" / "claudebar.py"
SEED_CONFIG = REPO_ROOT / "plugin" / "swiftbar-config.json"

SOURCES = ("claude", "codex")
STATES = ("asking", "working", "waiting", "none")

# 3× source so the image looks sharp on both Retina @2x and @3x displays.
# The plugin downsamples via `width=` / `height=` for the logical size.
SCALE = 3
PILL_W = 32 * SCALE      # display 32 logical
PILL_H = 16 * SCALE      # display 16 logical
PILL_RADIUS = 4 * SCALE
BRAND_ICON_SIZE = 12 * SCALE
STATE_ICON_SIZE = 14 * SCALE   # state slightly larger to balance density
PAD_X = 2 * SCALE

DEFAULT_STATE_SYMBOLS = {
    "asking":  "exclamationmark.circle",
    "working": "hourglass.circle",
    "waiting": "checkmark.circle",
    "none":    "circle.dotted",
}


def _load_state_symbols() -> dict[str, str]:
    syms = dict(DEFAULT_STATE_SYMBOLS)
    try:
        cfg = json.loads(SEED_CONFIG.read_text())
    except Exception:
        return syms
    if not isinstance(cfg, dict):
        return syms
    icons = cfg.get("icons", {})
    if not isinstance(icons, dict):
        return syms
    for st in syms:
        v = icons.get(st)
        if isinstance(v, str) and v:
            syms[st] = v
    return syms


STATE_SYMBOLS = _load_state_symbols()


def _empty(mode: str = "L") -> Image.Image:
    return Image.new(mode, (PILL_W, PILL_H), 0)


def _crop_to_content(alpha: Image.Image, size: int) -> Image.Image:
    """Trim transparent border, then resample to ``size × size`` via LANCZOS.

    SF Symbols and SVG icons ship with margin around the glyph; cropping
    to the alpha bbox first means the resampled icon fills its slot at
    the same visual weight as the brand silhouette next to it.
    """
    bbox = alpha.getbbox()
    if bbox:
        alpha = alpha.crop(bbox)
    return alpha.resize((size, size), Image.LANCZOS)


def _render_svg_alpha(svg_path: Path, size: int) -> Image.Image:
    """Rasterise an SVG via rsvg-convert at exact target size and return alpha."""
    out = subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(size), str(svg_path)],
        capture_output=True, check=True,
    ).stdout
    img = Image.open(io.BytesIO(out)).convert("RGBA")
    return img.split()[-1]


def _render_sf_symbol_alpha(name: str, size: int) -> Image.Image:
    """Render a real SF Symbol via macOS APIs at 4× headroom; bbox-crop to
    ``size``. Matches what NSMenu produces when it draws the symbol natively.
    """
    from AppKit import (NSImage, NSImageSymbolConfiguration,  # type: ignore
                        NSGraphicsContext, NSBitmapImageRep,
                        NSCalibratedRGBColorSpace,
                        NSBitmapImageFileTypePNG)
    from Foundation import NSSize, NSMakeRect  # type: ignore
    point = size * 4
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        raise ValueError(f"unknown SF Symbol: {name}")
    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
        point, 9, 3,
    )
    img = img.imageWithSymbolConfiguration_(cfg)
    img.setSize_(NSSize(point, point))
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, point, point, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0,
    )
    NSGraphicsContext.saveGraphicsState()
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.setCurrentContext_(ctx)
    img.drawInRect_(NSMakeRect(0, 0, point, point))
    NSGraphicsContext.restoreGraphicsState()
    png_data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    raster = Image.open(io.BytesIO(bytes(png_data))).convert("RGBA")
    return _crop_to_content(raster.split()[-1], size)


def composite(brand_alpha: Image.Image, state_alpha: Image.Image) -> bytes:
    """Build the pill composite for one (source, state) pair (template PNG)."""
    pill = _empty()
    ImageDraw.Draw(pill).rounded_rectangle(
        (0, 0, PILL_W - 1, PILL_H - 1), PILL_RADIUS, fill=255,
    )

    cutout = _empty()
    cutout.paste(brand_alpha,
                 (PAD_X, (PILL_H - BRAND_ICON_SIZE) // 2))
    cutout.paste(state_alpha,
                 (PILL_W - STATE_ICON_SIZE - PAD_X,
                  (PILL_H - STATE_ICON_SIZE) // 2))

    final = ImageChops.subtract(pill, cutout)

    rgba = Image.new("RGBA", final.size, (0, 0, 0, 0))
    black = Image.new("RGBA", final.size, (0, 0, 0, 255))
    rgba.paste(black, (0, 0), final)

    buf = io.BytesIO()
    rgba.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def render_block(table: dict[str, dict[str, str]]) -> str:
    lines = ["STATE_BRAND_LOGOS = {"]
    for src in SOURCES:
        lines.append(f'    "{src}": {{')
        for state in STATES:
            b64 = table[src][state]
            lines.append(f'        "{state}": (')
            for chunk in textwrap.wrap(b64, 72):
                lines.append(f'            "{chunk}"')
            lines.append("        ),")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    brand_alpha = {
        src: _render_svg_alpha(ASSETS / f"{src}.svg", BRAND_ICON_SIZE)
        for src in SOURCES
    }
    state_alpha = {
        st: _render_sf_symbol_alpha(STATE_SYMBOLS[st], STATE_ICON_SIZE)
        for st in STATES
    }

    table: dict[str, dict[str, str]] = {}
    for src in SOURCES:
        table[src] = {}
        for state in STATES:
            png = composite(brand_alpha[src], state_alpha[state])
            table[src][state] = base64.b64encode(png).decode()
            print(f"  {src} · {state:7} {len(png)}b")

    block = render_block(table)
    src_text = LIB_PATH.read_text()

    if "STATE_BRAND_LOGOS = {" in src_text:
        new_text, n = re.subn(
            r"STATE_BRAND_LOGOS = \{.*?\n\}\n",
            block,
            src_text,
            count=1,
            flags=re.DOTALL,
        )
        if n != 1:
            print("ERROR: STATE_BRAND_LOGOS replacement failed")
            return 1
    else:
        # First-time insertion — anchor right after BRAND_LOGOS, since
        # they're conceptually the same family of constants.
        m = re.search(r"BRAND_LOGOS = \{.*?\n\}\n", src_text, flags=re.DOTALL)
        if not m:
            print("ERROR: nowhere to anchor STATE_BRAND_LOGOS in claudebar.py")
            return 1
        header = (
            "\n# State-badged brand pill composites. Template PNGs (black + "
            "alpha) so\n# SwiftBar's templateImage= picks up auto-tinting.\n"
            "# Generated by scripts/build_composites.py.\n"
        )
        new_text = src_text[:m.end()] + header + block + src_text[m.end():]

    LIB_PATH.write_text(new_text)
    print(f"  wrote -> {LIB_PATH} ({len(SOURCES) * len(STATES)} composites)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
