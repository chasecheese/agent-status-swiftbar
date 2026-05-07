#!/usr/bin/env python3
"""Render the dropdown's per-session pill composites.

Produces two tables in ``lib/agentstatus.py``:

- ``STATE_BRAND_LOGOS`` — 2-icon ``(brand, state)`` template pill (alpha
  + black). NSMenu auto-tints with the menu bar. Used as a fallback when
  the session's host app isn't in our bundled set.

- ``APP_BRAND_STATE_PILLS`` — 3-icon ``(host_app, brand, state)`` pill
  with the host icon kept full-colour and the brand/state silhouettes
  rendered against a single dark pill background (white foreground).
  Single variant — SwiftBar has no built-in appearance picker for
  ``image=`` and per-tick ``defaults read`` is too slow for the
  dropdown, so we ship one design that reads in both menu modes:
  prominent on light dropdown bg, "blends in" on dark bg with the
  host icon and white silhouettes still clearly visible.

Run when:
- the brand assets, state list, or pill geometry/palette changes
- ``plugin/swiftbar-config.json``'s ``icons`` section changes
- a new entry is added to ``APP_LOGOS`` in ``lib/agentstatus.py``
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "assets"
LIB_PATH = REPO_ROOT / "lib" / "agentstatus.py"
SEED_CONFIG = REPO_ROOT / "plugin" / "swiftbar-config.json"

SOURCES = ("claude", "codex")
STATES = ("asking", "working", "waiting", "none")

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


# ── 2-icon pill (template, auto-tint) ────────────────────────────────────────
P2_SCALE = 3                    # 3× source for retina @2x and @3x sharpness
P2_W = 32 * P2_SCALE
P2_H = 16 * P2_SCALE
P2_RADIUS = 4 * P2_SCALE
P2_BRAND = 12 * P2_SCALE
P2_STATE = 14 * P2_SCALE        # state slightly bigger for visual balance
P2_PAD_X = 2 * P2_SCALE


# ── 3-icon pill (full-colour, single variant) ────────────────────────────────
# Single design — SwiftBar has no built-in light/dark image picker, and
# per-tick appearance detection is too slow for the dropdown. A
# translucent mid-grey pill sits between the two dropdown backgrounds:
# darkens against light bg, lightens against dark bg, so the host icon
# (full colour) and the white silhouettes stay legible in both modes.
# Tweak P3_BG/P3_FG here if the shade needs to shift.
P3_SCALE = 3                    # 3× source for retina @3x sharpness (matches P2)
P3_W = 48 * P3_SCALE
P3_H = 16 * P3_SCALE
P3_RADIUS = 4 * P3_SCALE
# Layout (logical): pad(2) + host(16) + gap(2) + brand(12) + gap(2) + state(12) + pad(2) = 48
P3_HOST_ICON = 16 * P3_SCALE    # bumped from 12 — host needs more visual weight
P3_ICON = 12 * P3_SCALE         # brand/state SF Symbols
P3_PAD_X = 2 * P3_SCALE
P3_GAP = 2 * P3_SCALE
P3_BG = (120, 120, 125, 220)    # translucent mid-grey
P3_FG = (255, 255, 255, 255)    # white silhouettes for brand + state


# ── Helpers ──────────────────────────────────────────────────────────────────
def _crop_to_content(alpha: Image.Image, size: int) -> Image.Image:
    bbox = alpha.getbbox()
    if bbox:
        alpha = alpha.crop(bbox)
    return alpha.resize((size, size), Image.LANCZOS)


def _render_svg_alpha(svg_path: Path, size: int) -> Image.Image:
    out = subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(size), str(svg_path)],
        capture_output=True, check=True,
    ).stdout
    img = Image.open(io.BytesIO(out)).convert("RGBA")
    return img.split()[-1]


def _render_sf_symbol_alpha(name: str, size: int) -> Image.Image:
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


def _png_bytes(img: Image.Image, palette: bool = False) -> bytes:
    buf = io.BytesIO()
    if palette:
        img = img.convert("P", palette=Image.ADAPTIVE, colors=64)
    img.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


# ── 2-icon pill ──────────────────────────────────────────────────────────────
def composite_2icon(brand_alpha: Image.Image, state_alpha: Image.Image) -> bytes:
    pill = Image.new("L", (P2_W, P2_H), 0)
    ImageDraw.Draw(pill).rounded_rectangle(
        (0, 0, P2_W - 1, P2_H - 1), P2_RADIUS, fill=255,
    )
    cutout = Image.new("L", (P2_W, P2_H), 0)
    cutout.paste(brand_alpha,
                 (P2_PAD_X, (P2_H - P2_BRAND) // 2))
    cutout.paste(state_alpha,
                 (P2_W - P2_STATE - P2_PAD_X, (P2_H - P2_STATE) // 2))

    final = ImageChops.subtract(pill, cutout)
    rgba = Image.new("RGBA", final.size, (0, 0, 0, 0))
    black = Image.new("RGBA", final.size, (0, 0, 0, 255))
    rgba.paste(black, (0, 0), final)
    return _png_bytes(rgba)


# ── 3-icon pill ──────────────────────────────────────────────────────────────
def _solid_silhouette(alpha: Image.Image,
                      color: tuple[int, int, int, int]) -> Image.Image:
    out = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", alpha.size, color), (0, 0), alpha)
    return out


def composite_3icon(host_rgba: Image.Image,
                    brand_alpha: Image.Image,
                    state_alpha: Image.Image) -> bytes:
    pill_mask = Image.new("L", (P3_W, P3_H), 0)
    ImageDraw.Draw(pill_mask).rounded_rectangle(
        (0, 0, P3_W - 1, P3_H - 1), P3_RADIUS, fill=255,
    )
    pill = Image.new("RGBA", (P3_W, P3_H), (0, 0, 0, 0))
    pill.paste(Image.new("RGBA", (P3_W, P3_H), P3_BG), (0, 0), pill_mask)

    cx = P3_PAD_X
    pill.alpha_composite(
        host_rgba.resize((P3_HOST_ICON, P3_HOST_ICON), Image.LANCZOS),
        (cx, (P3_H - P3_HOST_ICON) // 2),
    )
    cx += P3_HOST_ICON + P3_GAP
    pill.alpha_composite(_solid_silhouette(brand_alpha, P3_FG),
                         (cx, (P3_H - P3_ICON) // 2))
    cx += P3_ICON + P3_GAP
    pill.alpha_composite(_solid_silhouette(state_alpha, P3_FG),
                         (cx, (P3_H - P3_ICON) // 2))

    out = Image.new("RGBA", (P3_W, P3_H), (0, 0, 0, 0))
    out.paste(pill, (0, 0), pill_mask)
    # Translucent pill bg → can't use palette mode (it can only encode
    # 1-bit alpha via a single transparent index). Stay in full RGBA;
    # PNG compression still keeps each cell ~3-5 KB.
    return _png_bytes(out)


# ── Block writers ────────────────────────────────────────────────────────────
def _wrap(s: str, indent: int) -> list[str]:
    pad = " " * indent
    return [f'{pad}"{c}"' for c in textwrap.wrap(s, 72)]


def render_2icon_block(table: dict[str, dict[str, str]]) -> str:
    lines = ["STATE_BRAND_LOGOS = {"]
    for src in SOURCES:
        lines.append(f'    "{src}": {{')
        for state in STATES:
            lines.append(f'        "{state}": (')
            lines.extend(_wrap(table[src][state], 12))
            lines.append("        ),")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_3icon_block(table: dict, hosts: list[str]) -> str:
    lines = ["APP_BRAND_STATE_PILLS = {"]
    for host in hosts:
        lines.append(f'    "{host}": {{')
        for src in SOURCES:
            lines.append(f'        "{src}": {{')
            for state in STATES:
                lines.append(f'            "{state}": (')
                lines.extend(_wrap(table[host][src][state], 16))
                lines.append("            ),")
            lines.append("        },")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _replace_block(src_text: str, name: str, block: str) -> str:
    new, n = re.subn(
        rf"{name} = \{{.*?\n\}}\n",
        block,
        src_text,
        count=1,
        flags=re.DOTALL,
    )
    if n != 1:
        raise SystemExit(f"ERROR: {name} block not found in agentstatus.py")
    return new


def _import_app_logos() -> dict[str, Image.Image]:
    spec = importlib.util.spec_from_file_location("agentstatus", LIB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = {}
    for name, b64 in mod.APP_LOGOS.items():
        out[name] = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
    return out


def main() -> int:
    # 2-icon pill — same brand/state symbols both tables share.
    brand_alpha_p2 = {
        src: _render_svg_alpha(ASSETS / f"{src}.svg", P2_BRAND)
        for src in SOURCES
    }
    state_alpha_p2 = {
        st: _render_sf_symbol_alpha(STATE_SYMBOLS[st], P2_STATE)
        for st in STATES
    }
    table2: dict[str, dict[str, str]] = {}
    for src in SOURCES:
        table2[src] = {}
        for state in STATES:
            png = composite_2icon(brand_alpha_p2[src], state_alpha_p2[state])
            table2[src][state] = base64.b64encode(png).decode()
    print(f"  2-icon: {len(SOURCES) * len(STATES)} composites "
          f"avg {sum(len(table2[s][st]) for s in SOURCES for st in STATES) // (len(SOURCES) * len(STATES))}b base64")

    # 3-icon pill — needs APP_LOGOS already in agentstatus.py.
    app_icons = _import_app_logos()
    hosts = sorted(app_icons.keys())
    brand_alpha_p3 = {
        src: _render_svg_alpha(ASSETS / f"{src}.svg", P3_ICON)
        for src in SOURCES
    }
    state_alpha_p3 = {
        st: _render_sf_symbol_alpha(STATE_SYMBOLS[st], P3_ICON)
        for st in STATES
    }

    table3: dict = {}
    for host in hosts:
        table3[host] = {}
        host_rgba = app_icons[host]
        for src in SOURCES:
            table3[host][src] = {}
            for state in STATES:
                png = composite_3icon(
                    host_rgba, brand_alpha_p3[src], state_alpha_p3[state],
                )
                table3[host][src][state] = base64.b64encode(png).decode()
    n_hosts = len(hosts)
    n_3 = n_hosts * len(SOURCES) * len(STATES)
    avg = sum(len(table3[h][s][st]) for h in hosts for s in SOURCES for st in STATES) // n_3
    print(f"  3-icon: {n_3} composites "
          f"({n_hosts} hosts × {len(SOURCES)} brands × {len(STATES)} states) "
          f"avg {avg}b base64")

    src_text = LIB_PATH.read_text()
    src_text = _replace_block(src_text, "STATE_BRAND_LOGOS", render_2icon_block(table2))
    if "APP_BRAND_STATE_PILLS = {" in src_text:
        src_text = _replace_block(src_text, "APP_BRAND_STATE_PILLS",
                                  render_3icon_block(table3, hosts))
    else:
        anchor = render_2icon_block(table2)
        idx = src_text.index(anchor)
        end = idx + len(anchor)
        header = (
            "\n# 3-icon (host, brand, state) pill — single full-colour PNG.\n"
            "# Translucent grey bg + white silhouettes works in both menu\n"
            "# modes; tune P3_BG in scripts/build_composites.py to shift.\n"
        )
        src_text = src_text[:end] + header + render_3icon_block(table3, hosts) + src_text[end:]

    LIB_PATH.write_text(src_text)
    print(f"  wrote -> {LIB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
