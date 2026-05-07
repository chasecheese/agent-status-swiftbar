#!/usr/bin/env python3
"""Refresh ``APP_LOGOS`` in ``lib/agentstatus.py`` from /Applications.

Walks a hardcoded list of host apps we know how to detect, pulls each
.app's `.icns` icon, downsamples to 32×32 PNG with ``sips``, base64-encodes,
and rewrites the ``APP_LOGOS = {...}`` block in ``lib/agentstatus.py``.

Run when:
- A new host app is added to the list below.
- An app is updated and its icon changed.
- You move to a new machine and want the bundled icons regenerated.
"""
from __future__ import annotations

import base64
import plistlib
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_PATH = REPO_ROOT / "lib" / "agentstatus.py"

APPS = [
    # Terminals
    ("Terminal",            "/System/Applications/Utilities/Terminal.app"),
    ("iTerm",               "/Applications/iTerm.app"),
    ("Ghostty",             "/Applications/Ghostty.app"),
    ("Warp",                "/Applications/Warp.app"),
    ("Alacritty",           "/Applications/Alacritty.app"),
    ("kitty",               "/Applications/kitty.app"),
    ("WezTerm",             "/Applications/WezTerm.app"),
    # IDEs with embedded terminals
    ("Visual Studio Code",  "/Applications/Visual Studio Code.app"),
    ("Cursor",              "/Applications/Cursor.app"),
    ("Windsurf",            "/Applications/Windsurf.app"),
    ("Zed",                 "/Applications/Zed.app"),
    # Native agent apps that themselves host a coding shell
    ("Claude",              "/Applications/Claude.app"),
    ("Codex",               "/Applications/Codex.app"),
]


def b64_icon(app_path: Path) -> str | None:
    info = app_path / "Contents" / "Info.plist"
    if not info.exists():
        return None
    try:
        d = plistlib.loads(info.read_bytes())
    except Exception:
        return None
    icon = d.get("CFBundleIconFile") or d.get("CFBundleIconName") or ""
    if not icon:
        return None
    name = icon if icon.endswith(".icns") else icon + ".icns"
    icns = app_path / "Contents" / "Resources" / name
    if not icns.exists():
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            ["sips", "-s", "format", "png", "-z", "32", "32",
             str(icns), "--out", str(tmp_path)],
            capture_output=True, check=False,
        )
        data = tmp_path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return base64.b64encode(data).decode()
    finally:
        tmp_path.unlink(missing_ok=True)


def render_block(items: list[tuple[str, str]]) -> str:
    lines = ["APP_LOGOS = {"]
    for name, b64 in items:
        lines.append(f'    "{name}": (')
        for chunk in textwrap.wrap(b64, 76):
            lines.append(f'        "{chunk}"')
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    items: list[tuple[str, str]] = []
    for name, path in APPS:
        b64 = b64_icon(Path(path))
        if b64:
            items.append((name, b64))
            print(f"  {name}")
        else:
            print(f"  {name}: not found, skipping")

    block = render_block(items)
    src = LIB_PATH.read_text()
    new_src, n = re.subn(
        r"APP_LOGOS = \{.*?\n\}\n",
        block,
        src,
        count=1,
        flags=re.DOTALL,
    )
    if n != 1:
        print("ERROR: APP_LOGOS = {...} block not found in agentstatus.py")
        return 1
    LIB_PATH.write_text(new_src)
    print(f"  wrote -> {LIB_PATH} ({len(items)} apps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
