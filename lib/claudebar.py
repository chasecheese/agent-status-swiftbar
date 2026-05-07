"""Shared utilities for claude-code-swiftbar.

Single source of truth for filesystem paths, default state taxonomy,
process-tree introspection, transcript parsing, and config loading.

Imported by:
- hook/claude-swiftbar-hook.py     (writes per-session state files)
- plugin/claude-status.py          (renders SwiftBar dropdown)
- scripts/install_settings.py      (patches ~/.claude/settings.json)

Designed to fail soft: every helper that touches the filesystem catches
its own exceptions and returns an empty/default value rather than raising.
A broken helper must never crash the Claude Code hook chain.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"

STATE_DIR = CLAUDE_DIR / "state" / "swiftbar"
CONFIG_PATH = CLAUDE_DIR / "swiftbar-config.json"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
SCRIPTS_DIR = CLAUDE_DIR / "scripts"
HOOK_PATH = SCRIPTS_DIR / "claude-swiftbar-hook.py"
PLUGIN_PY_PATH = SCRIPTS_DIR / "claude-swiftbar-plugin.py"
TOGGLE_PATH = SCRIPTS_DIR / "claude-swiftbar-toggle.py"

# Codex CLI lives next to Claude under the user home; its hooks file is the
# Codex-side equivalent of ~/.claude/settings.json (just hooks-only).
CODEX_DIR = HOME / ".codex"
CODEX_HOOKS_PATH = CODEX_DIR / "hooks.json"

PYTHON = "/usr/bin/python3"
OSASCRIPT = "/usr/bin/osascript"

# ── Default state taxonomy ───────────────────────────────────────────────────
DEFAULT_REFRESH_INTERVAL_MS = 1000
MIN_REFRESH_INTERVAL_MS = 100

# ── State taxonomy ───────────────────────────────────────────────────────────
# Three live states, plus `none` for the empty-aggregate menu-bar header.
# A fresh session opens as `waiting` (no separate `idle`).
STATE_LABELS = {
    "asking":  "ASKING",
    "working": "WORKING",
    "waiting": "WAITING",
}
DEFAULT_ICONS = {
    "asking":  "exclamationmark.bubble.circle.fill",
    "working": "hourglass.circle.fill",
    "waiting": "circle.badge.checkmark",
    "none":    "circle.dotted",
}
DEFAULT_PRIORITY = ["asking", "working", "waiting"]

# Per-source brand logos used as the row icon in the dropdown so each
# session row shows whether it came from Claude Code or Codex CLI. The
# actual state is rendered as an inline `:sf_symbol:` glyph at the start
# of the label text (NSMenuItem only allows one image per row).
#
# Source: Bootstrap Icons (https://icons.getbootstrap.com), MIT licensed.
# Rendered with `rsvg-convert -w 32 -h 32` to a 32×32 black-on-alpha PNG
# so SwiftBar's `templateImage=` auto-tints them for light / dark menu
# bars. Replace these strings to swap in your own logos.
BRAND_LOGOS = {
    "claude": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABmJLR0QA/wD/AP+gvaeTAAADeUlE"
        "QVRYha3XWWheRRQH8F8StyyGYhUbFSuiglX0Qa24olVaNxSr4lLcQBAFlyIUKmoR1IILLi8uIC7g"
        "m1JEKloLSoLWHXF/cIFYUzVtUmpta2NzfZi53MnkW9MeGL47Z79zz/mf+WhMffgSf2FlA71OXI/D"
        "m/hrm1aiiGsXjqmjtyLqfI+udgJ0NpFvz3TvrKN3Xfw9REh0j9FxmFCdwlYcUENve5R/UkO2D27E"
        "FTNN4vEkgQL3ZvL+RPZKJtsP70XZJC6cSQL92JAEGYmOSzoqkd2T8LuwKkv+sUaB5gtHvAGXZ7Kb"
        "M0d3J7LTE/6ChP9MZjOJ0xolsDxTfhR7RVknPk3kf6A3yq5MbMr6yBMu8Eij4HACtmVGH+DgKD8z"
        "ky2L/KVx/2vcz1cVZbnWaLE95wnAkxqP4LwofyPhj2J/PBH3q3AghjP733BQK8FL2hdPCkeagtBD"
        "OBY7E/5yvBqfH8TqLPgEzmgneErn4OfM4ZCqrQp8nQQdyXTLBHeLevG0cAK583L9XYf/kdpIewQu"
        "wa2Y3WoiZ5t+Go3WLpyCo7EkvsQQNmd6b8KAUHxzBNisR3141tTaaJTAWBOdH3BZB34XhkhJ/0Tj"
        "sZjxFmEcrxe+80Lt4/o4PscX8fdDAUt0YDHuwCwBdmcJUNvTZpCUhjEYAw0JY7qopdjRxFG/ACA9"
        "Qnt2C5W9pIndLwKkjwqtSACnMWyKaxDfNfEzhU7EWs2//068iG80rpd/cU0rgQeiw7IVt+HlJkm8"
        "LxTtbFwqTMF1poJYEXl1qQf3CxOyNBgUpt9Pyb5eEutM7/MenIsHhFnSVy/4IgHDS2dbcLswHd+N"
        "vI9xV3xOIXgrflS12txGb5lTr+m9/rbqtrsi8jbjSAFgClyMtxKbpSrIXo/jW02gdFhgo3DVLmmh"
        "qg6ujrx34v5kAfV2xP248ObPxf0mnNpKAjfgM7ygugcQ8HtjdPZ8wv8z8g6L+/uSF1gdecvwnzAz"
        "zm8liZy6BQQrhNbqjvy5kTepgvC98VWSxE2Rv0DAhR04q90EXlIV17yEv1j1qVI6SXWVH8ehkT9H"
        "qKdr2wl+iwo0LshkD0fZtzXs0k/xVDsBcxoWvt9VNWRrYoC1NWRdKox4fXcSWISLavA7VbP9tTq2"
        "A7hN6I49Tr3C2C6ES+mMqa1/sglNCJOuV6iF0Zkm8D+FrXE3z/WCrQAAAABJRU5ErkJggg=="
    ),
    "codex": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABmJLR0QA/wD/AP+gvaeTAAAD2ElE"
        "QVRYha3Xa4hVVRQH8N/Y1aEXOUbQl2roMRUivURSehi9xupDX/oYQQ9DRSqCMByzCCqiNyUEQeW3"
        "pDDEMoowAimKKHpHZUZUjPnAksZ7Z+z2Ye3D2ffcx9yJFhzuPWuvvfZaa//3f69D/3Ii7sIO/Iq/"
        "8R1ewbUYmIGvGcstOIBmj+dTjMzUcT9RP4a70//P8ZKown4MYxS34+ikG8ORqOG3ZLtzpoEVslxk"
        "9w/WJqed5DLUda/Oe7hwposfgz+Sgwe72ByHR3EoW+xrbMZGfIKppJ/CPf0ufhKeSxN3ac/8CFH2"
        "8Wzht7Ckg68z8Wpmd0evhUewRZS8mDBWsblCYKEY/wbXTJPQAB5O9nXM72R0HQ5mjg+n3+szmxey"
        "8b1YjdkVP+fh9C5BvJ/mbqwOLsZkGvwA5+L79J6XtZGepzGv4uM0vJbmTIotPL5ic2kan8CcQjko"
        "jkkTb6Z3XQKY1A7IHIj7sF1ZpX1iz4sqzcZfaWxh4eC2pBivZNUpgCmsT/9zIE7i2Szjq/Gl1pMx"
        "msZ+SrrLC6dvJ8X9lcx6BVAF4k5cWZlfw0rlUW7iDUFWLX53J8XFfQZQ2BdZXZVlu0U7Hc/FE1qJ"
        "alIwJ0rwnd1nAHuwSis31LAiBdfA42nhXEbwcRbARdUKLO0zgHW6y5o0pyFKv0JgpZBBAfSmwMLg"
        "LHGLkYEiyUT6zSszYPoLrIkF+BAb8FmWRB03CeAO40a4VXkKhjJHS5R7uxVnCXK6r8fiawSLFnJz"
        "mv9zxe4RJX238MBWrcyWI7mRbNbrLtUAFitZM5dLlEkjsi0W2CEukFzmCmDVk7OcXKqB9hPA/KQ/"
        "VCiOygIoQPSk1i2BM/B6svlW3B856Yz3GcDSpP89r0BTkMS2LJBOR44A7A+Z3ReCD/rdggeSflvu"
        "sCmaTFgmiKZY4KuUKUHXz4gq7RZHrQiwnwCG0ntTgBRckBQHMCvpauK63ZMFsj1NrotesUo266YJ"
        "YFAAvYkflRefOeLcd6LkeXhKiZHN2u/7IUG3DXFUOwVwjrIfqGNRxYeX0+A7OpPNsGg2cqkJjORV"
        "ejEbX6ZsbooG50/tFxeC8YrmslsTmsuowEYnnBRybzY+JT5iTu3ldHU2YZPIuioLlXzeFGd/pfaT"
        "UhP9YlP0Cif0kRTiI6S4IQ/jI1HWTVpPRkPsexWIhaxVArvavk0ri/Cu1u44f+rawVrIgOj/iz1f"
        "1Wuh6W62UwRJnSyqMoGHRNb78bwgk19wrED9cpyf5m+YLoD/IgvEF0+nyhTPQdz5fy+cyyzcIHCx"
        "S5yeveIiGxOf8n3JvxfEfc7oA3pUAAAAAElFTkSuQmCC"
    ),
}

# State-badged brand pill composites. Template PNGs (black + alpha) so
# SwiftBar's templateImage= picks up auto-tinting.
# Generated by scripts/build_composites.py.
STATE_BRAND_LOGOS = {
    "claude": {
        "asking": (
            "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"
        ),
        "working": (
            "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"
        ),
        "waiting": (
            "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"
            "AABJRU5ErkJggg=="
        ),
        "none": (
            "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"
            "juGNaNDf1fwHHsmlL6lb6ygAAAAASUVORK5CYII="
        ),
    },
    "codex": {
        "asking": (
            "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"
        ),
        "working": (
            "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"
        ),
        "waiting": (
            "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"
            "i74AAAAASUVORK5CYII="
        ),
        "none": (
            "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"
        ),
    },
}

# Real macOS app icons used as the row image on the dropdown's
# 'Return to Tab' submenu item, so each session shows the actual
# host app's logo. Captured at packaging time from the user's
# /Applications via `sips -s format png -z 32 32`. Add or refresh
# entries by re-running scripts/build_app_logos.py.
APP_LOGOS = {
    "Terminal": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABGdBTUEAALGPC/xhBQAAACBjSFJN"
        "AAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoA"
        "BQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAJAAAAAB"
        "AAAAkAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAB+C9pSAAAA"
        "CXBIWXMAABYlAAAWJQFJUiTwAAABnWlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPHg6eG1wbWV0"
        "YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNi4wLjAiPgogICA8"
        "cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRh"
        "eC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4"
        "bWxuczpleGlmPSJodHRwOi8vbnMuYWRvYmUuY29tL2V4aWYvMS4wLyI+CiAgICAgICAgIDxleGlm"
        "OlBpeGVsWERpbWVuc2lvbj4yNTY8L2V4aWY6UGl4ZWxYRGltZW5zaW9uPgogICAgICAgICA8ZXhp"
        "ZjpQaXhlbFlEaW1lbnNpb24+MjU2PC9leGlmOlBpeGVsWURpbWVuc2lvbj4KICAgICAgPC9yZGY6"
        "RGVzY3JpcHRpb24+CiAgIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+Cl6wHhsAAAOkSURBVFgJ7VbN"
        "S1tBEN+XRBMLgkiiIooa8dAUCjYGepDeciw9ePQm9ui1Qv+AHnoXFdSTxYIXIcciFQklUJIchFoR"
        "AiooihGD+JFE8zq/9U3YfYmJSQPtwYXN7O6bnfnN50aIp/GPPWA8pN80Tff8/Pyr4+PjQDab9ebz"
        "effd3Z2jUCiI29vbkpnL5eQZKPESWyFL9NTpdP5qb2+Pr66u5srpKguAFI8mk8nPe3t7r6+vrw0o"
        "hGIeBE4umWKDtX1v8ZtNTU2x5ubmDxsbG1HrrEhKAMzMzIxGo9HIzs5OGxQ7HI4iMxaGUXJF+15u"
        "Axkul+u8paXlrR2EU70QiUSeEcPXra0tP6ypR5kqj9eWdzy0f9nZ2fnl6Ogoz98083Z3d4MHBwdB"
        "il3RpVjD/ezieinlj6D8CHo8niArB9UAnJycvLi8vJQ+hlJMv98v+W9ubuSez+ukBhkUeBCAle1F"
        "a2kvxsbGxOzsrBgeHhZXV1fI8OL3Wr0B0HTHpwLQciAUCoX39/ffoJQwkAOxWAyuE5OTkyIQCAgK"
        "k7i4uJDfawUAeZSMPw4PD79LAfSjhYCsk3XOgsGESlhZWRETExNiaGhITE9PS0DMUwfVysjFSFgZ"
        "x5YrAPtwOCzGx8cFJahYXFyUnsF5rQPlaC/rsgBgFQbiPTU1JUZGRsTS0pJYX1+XAtxut8yDWgFA"
        "LoVAu6btuOMxAGqjYnNzUywsLIhMJiOokUjrmU+VBMvAX2lAbkUPINmsTC1amEgkpGCqX3mGeu7p"
        "6RHd3d0CawyE6+zsTKRSqRIFKqCqHoDLGQBfZJexV0DBh5JUAaBkcZdzh++rtCoA9kC1BKMyElSu"
        "qmxpOcBWugsAjw6BJt22gZX0wtlO71/EkkPlAADsHtKSUG23yr2GLeGdih4olwMN006CqoaAEolA"
        "3j9CjVTMsqwQ3DcZ61ALASUhEBRLkC82gkI5BoUgq8rTAFCDSYORmVXGv11b1iN506osDQDV9TYx"
        "muQF7cFQL9S7hmfRwltbW7dVGdprSK00TjMJtI2cUI7y8/l8iY6OjrgKQGve6XQ65/V6U8Twjrzh"
        "AYh6BxvAyvv6+jKDg4Pv5+bmfqsyy7p6YGAgTPnwicoySIIMFQg3EjuFUD7jNTojvSFmV1dXvLe3"
        "9+Py8vI3Vbnksx/wvr+/v40EhsgTzwmAl9ZumgaEYqITgtL/fflYocFgD0rTpHWWvp0SgG0y5Ofa"
        "2to5y36i/5UH/gCXelcRh5HlgAAAAABJRU5ErkJggg=="
    ),
    "iTerm": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABGdBTUEAALGPC/xhBQAAACBjSFJN"
        "AAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoA"
        "BQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAJAAAAAB"
        "AAAAkAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAB+C9pSAAAA"
        "CXBIWXMAABYlAAAWJQFJUiTwAAABnWlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPHg6eG1wbWV0"
        "YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNi4wLjAiPgogICA8"
        "cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRh"
        "eC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4"
        "bWxuczpleGlmPSJodHRwOi8vbnMuYWRvYmUuY29tL2V4aWYvMS4wLyI+CiAgICAgICAgIDxleGlm"
        "OlBpeGVsWERpbWVuc2lvbj4yNTY8L2V4aWY6UGl4ZWxYRGltZW5zaW9uPgogICAgICAgICA8ZXhp"
        "ZjpQaXhlbFlEaW1lbnNpb24+MjU2PC9leGlmOlBpeGVsWURpbWVuc2lvbj4KICAgICAgPC9yZGY6"
        "RGVzY3JpcHRpb24+CiAgIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+Cl6wHhsAAAPHSURBVFgJ7VfN"
        "T1NBEJ99H20phaIoGhK1khCN6MGoVASCcMEY/wGvJiRy0JuePHrSIzGY8Dd4McbEC6gg34nhgB6I"
        "iBqw4UMpFni8r3XmtdXdRwtUi16Y9PXt9s385rezM2+nAHvynyPA8vnv41w7sg71nMEp4FDJGOj5"
        "dHP9zjlYwGCJcXj3pQRGWxmzc+nlJDCZXIsvL31/+HZ4sOnzx2mWWlkB13U9+5wGPmSOc0VRIFJe"
        "DkdravjZ+KWBisp9d+qi4RGf6ubpeGIx/uT10LeGy22EU5SLsAhzArH9HqUFPZ+aCqo/zJf3bt+8"
        "ODbQD6qmQygcwdVoAPgBCoKbMVGQm2Tth0ZVxwYjlQLHtuBCYzPc73o87JQFLl+trd3IahPsL6nQ"
        "w+cGxvrjnnNdh+j+w8D2q+BWcVDfK3h3wD5kefraVx2UlIokKEj5hEEQF5BcSMDYm36YGBuJN7W3"
        "n0PtwayFkh3Q3Vk3T3/6MOWty1t5qQbGdQuMGxa41S44YVzRyVUwTqyBU2oDdzhwzLb8lwuKqkKo"
        "NOK5IWzyIfqUCGyYawd+YMKRKEwDdx86PcNBf6ECSyEv+jjMu7zsEJG2GCtqOtCETT5EVYmAZZgB"
        "285UC0ZXWWAQeqSB1eKC1egAmKJp4WPCJh+ipZQDtm1i4qAjEkw4t5KDeQXnOge2jMsnunRtte1k"
        "m0cIm3yIIhMwkYCbIYCOlASDwFNMwhoX9NcK2McZKEkMDb6dmJ3eEhFsuzFh2+hDFImASQSyESAt"
        "fKpOM7xU4KVYCfMahBfK0vYUBSrFAoSwyYcoEgFiJxEQNbPjwnxmrby7twU+AlISUpJsS0CCLGzi"
        "EcgmecZUJmAav3OgMOwdaTteDhiSro8ARSB96EhaRZpwqgJTPhR9BHaQA39BxsHF+atAJoA1SmHa"
        "LUnngFwFEoFNZVhkJvQe8JehRGBHZfgXpLYtQ0wSk0623RLCdtCHiC9FwLLNJV1Lv5uomSiWZLEI"
        "20EfIq70JjRXzcloNOo9N1ZTXjORPkr/NCoMyxp7CMQiIWzy4U0yXxKB2dmZ8aqDVeOxWOz8zMwM"
        "JOcTEIpgS5Y5z0XDnYzFluxYLAaETT5EWzzSZOnsvNW8bqSe9fX1lX9CEsUQct7a2rpSEopc6+7u"
        "6hcxNxGghx0dHS3YiT5IzM3VJ5eT2MVseG2XaLjdmOEfiWAgCNGKKFRXV49iW3+3p6fnld8uJwFS"
        "wm0ItbW1NQQCgTqcHsBDIug33nKuKNT5LmLdT/b29g7hlsqHwJbGew//YQR+Asrl7f/viCj+AAAA"
        "AElFTkSuQmCC"
    ),
    "Ghostty": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABGdBTUEAALGPC/xhBQAAACBjSFJN"
        "AAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoA"
        "BQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAJAAAAAB"
        "AAAAkAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAB+C9pSAAAA"
        "CXBIWXMAABYlAAAWJQFJUiTwAAABnWlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPHg6eG1wbWV0"
        "YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNi4wLjAiPgogICA8"
        "cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRh"
        "eC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4"
        "bWxuczpleGlmPSJodHRwOi8vbnMuYWRvYmUuY29tL2V4aWYvMS4wLyI+CiAgICAgICAgIDxleGlm"
        "OlBpeGVsWERpbWVuc2lvbj4yNTY8L2V4aWY6UGl4ZWxYRGltZW5zaW9uPgogICAgICAgICA8ZXhp"
        "ZjpQaXhlbFlEaW1lbnNpb24+MjU2PC9leGlmOlBpeGVsWURpbWVuc2lvbj4KICAgICAgPC9yZGY6"
        "RGVzY3JpcHRpb24+CiAgIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+Cl6wHhsAAAbJSURBVFgJ7VZN"
        "bFRVFP7e3/x2pgyVlD8h2trWEhVSsAoIiRA3BkyMGxMt6IaNYWcibmSlCxPXJsZEujRiogRIEAgi"
        "ERCRGhFr0Ma03dhaZpi+zrx5b957fue+edM+Gtnqwpu8d//P+c4537n3Av+Xf9kD2j/pP3XqVHp4"
        "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"
    ),
    "Alacritty": (
        "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"
        "AAAAAElFTkSuQmCC"
    ),
    "Visual Studio Code": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABGdBTUEAALGPC/xhBQAAACBjSFJN"
        "AAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoA"
        "BQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAJAAAAAB"
        "AAAAkAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAB+C9pSAAAA"
        "CXBIWXMAABYlAAAWJQFJUiTwAAABzWlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPHg6eG1wbWV0"
        "YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNi4wLjAiPgogICA8"
        "cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRh"
        "eC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4"
        "bWxuczpleGlmPSJodHRwOi8vbnMuYWRvYmUuY29tL2V4aWYvMS4wLyI+CiAgICAgICAgIDxleGlm"
        "OkNvbG9yU3BhY2U+MTwvZXhpZjpDb2xvclNwYWNlPgogICAgICAgICA8ZXhpZjpQaXhlbFhEaW1l"
        "bnNpb24+MTAyNDwvZXhpZjpQaXhlbFhEaW1lbnNpb24+CiAgICAgICAgIDxleGlmOlBpeGVsWURp"
        "bWVuc2lvbj4xMDI0PC9leGlmOlBpeGVsWURpbWVuc2lvbj4KICAgICAgPC9yZGY6RGVzY3JpcHRp"
        "b24+CiAgIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+CsHtO6kAAASCSURBVFgJ7VZtaFtVGH7uvbnt"
        "EvuRtE2nXXX9SKe0RUTqdM5iQcRFKg7nNj/o9IcT/zhGFcQ/wv4FxKr94SgD/xQKFcoEpwU/wC5z"
        "Y2k3h7Tomui067JuMUkT89Xb5F7fky7tPXfcNi0F/bFDwj3vfd/zPM95z3vOPcCd9h9nQDDj9/v9"
        "pS6X65FcLne/JEkOVVVLKVY0ize8V0VRXKCxURp7ORAIjLe0tCwYYszNdDr9hKZp5+m/We08wzRn"
        "1HlSqdRumu38ZjEXcBgmw9ZR3d6dmJiwUdouFgZt9pNhM47bmW+9iUajXRslvRnPaEeGJ7VPxhPa"
        "jZRqCsM49AIseoMKp1VvF9v/K5LCgeM/wTc1B3tHBQb9Gg62WPDqAyWoK+Pr9hbHjwVszkuyHQVH"
        "sc+pYBzuj8fg8/8NwSrDZhFwM6XhwwsZPHMygdOzWQ7KyMEJEAShhItewzj3RzhP/uvsPFAiLUdL"
        "tLmZkGBSxdmgsvyedYwcnADyc3ZO1TDgC+HS9RQHwozRyTk89+lpXA0nAZnINfrlVC6OCRGZg28c"
        "B2fo47JE/uaXM3hr+AqeOjGNwZ/Dy+4h3wz2f3YG4QSdLRaCoFhBU/HGrq1orN4CNrbYxhWhflBO"
        "VTF1LQpIIiLpLA6RkEtzGWyXk3hn+CKyGk2PfNRB9V0CPN0u9DzZiu6vs9BSlAnTM1bPQvp5c8Uq"
        "tUgY2leH10euwjtLM6I17Ru7DmQo5SpLXA5YFNBxrxX9B9qxq72JZi5QMuIrIEX0TJeAjW2qq8XI"
        "K43YS1sKbHnZopaVA84GSBYrDj9ag2+O7CbyZnIycr4GGMZabVUBbHCl3Q6bEgNiIVbCVGmUDYkE"
        "VdWjYlsT7FU1a3Gs6l9VQCSpYN/xsxjyBYF0BAjPUeZZgWnIiQI+8kbg/nwagUjxHzqjGlMBC1kV"
        "+wfO4dT4zNI2o9kfapPw/k4ZIssC0yEL+GE6hq6By/jqtxgdBWI+SUaS1WzTIhQFDQ6ZTjEiKrNo"
        "+ODZRhx1PwhZtqC27He8N5aAwopRFnEtpuCFwQDe7dyKbK58XSJMBciShBM9HWir+QWPNdfAvXMH"
        "TWTptDv6tAsO6xW8/W0M/2RJBBUn7UZ4vgtiW1MDZOsWKhWWorWbUQBXxg57JY69/DihLBEvwwky"
        "Xutsht36Jw6fiiCUYSKoRmkJ9LTsPMqXzPLAfIfjoJErjS4NiytWoWcgL7wmxuc7GvHFi7W4r5ww"
        "6VhYKgx2MArI5ARUWRQ8ZOchjRycALow0FdlPU1EV/t2nDx4D1qrVGjExYgrxAX01Ecx3LWIPc38"
        "/cPIwS0BXRamq6ur16MgH/uwqx4jL1lw7PsbaLg7QfeAUrTVOyGU8OQsmHGYEng8nspkMjlpep1Z"
        "w5HNJDVNSZtGMez+/v4KUwHM4fP59iiKkjBF2aCDYTLsVckLTq/X2x2PxzecCaNGhsUwC/j6p+lH"
        "0+12O3t7ezudTucOm81mZzcZ+nNFqwfS90kAu5UqdA2fD4VC0319fd7R0VH6mNxp/8MM/AtA5+OH"
        "fI1GcAAAAABJRU5ErkJggg=="
    ),
    "Cursor": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABGdBTUEAALGPC/xhBQAAACBjSFJN"
        "AAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoA"
        "BQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAJAAAAAB"
        "AAAAkAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAB+C9pSAAAA"
        "CXBIWXMAABYlAAAWJQFJUiTwAAABy2lUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPHg6eG1wbWV0"
        "YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNi4wLjAiPgogICA8"
        "cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRh"
        "eC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4"
        "bWxuczpleGlmPSJodHRwOi8vbnMuYWRvYmUuY29tL2V4aWYvMS4wLyI+CiAgICAgICAgIDxleGlm"
        "OkNvbG9yU3BhY2U+MTwvZXhpZjpDb2xvclNwYWNlPgogICAgICAgICA8ZXhpZjpQaXhlbFhEaW1l"
        "bnNpb24+NTEyPC9leGlmOlBpeGVsWERpbWVuc2lvbj4KICAgICAgICAgPGV4aWY6UGl4ZWxZRGlt"
        "ZW5zaW9uPjUxMjwvZXhpZjpQaXhlbFlEaW1lbnNpb24+CiAgICAgIDwvcmRmOkRlc2NyaXB0aW9u"
        "PgogICA8L3JkZjpSREY+CjwveDp4bXBtZXRhPgoAheCYAAAEcElEQVRYCe1WS28bVRT+ZvxK/Iqc"
        "xE3jpiRNKkcVTYDy2KBKLFpAZQGlqlQk1rBnwRLxJxAqC5AAwQqpIrBCCkiFpImcPkySxnVakQee"
        "OJ7EkeJH/MgM59wwyYzxjI1A6qZHur73nnvuOd953DMGntBjjoBkZ7+vL3isXt9/WYYrRjIyDd1O"
        "1obPujUN2rqnIk9tFAq5ZnJNAfR2+98DpI8kCSeaXfq3PF3HmgT949x26fPGu65GRm/E/74kS9eJ"
        "H248+w/7LkmS3gz4vSulcu2uWY8lAtFo4LiuIUnBjpqFmq11Ci4oRAaRAWPpNCvemjye2d1VDSHO"
        "7SHpdekVUuNoXKd47u/vIxgK4ezZMQwPj6Cjo4OSrYHPnIgw9le89fNmGbd5A0kfsOwbNmzY5+vA"
        "4OAgBk4+BZfLhZ18HsFgCLncphi1Wg2ybPHLooWK2lJXVgAW0aMNe8dKBwZOYujUMAKBgPCYARln"
        "/f0xRCLd2NhQkM9vUzQ0ypA9EEN7SwBsIBQOY/jUCCLd3SLMe3tlcZ/PqtXqYei5DmKxGEKUHkVR"
        "UKnsOUaDlTgCYANnzjyN1y+9AY/bg2qtaukG7GWxUCAAhj8Hs+ySoWs6Jid/wqNHDx1BOAJgj0Lh"
        "EDoo71evXsPg0JDVks2OU3N/cR6JuVkbiSO2IwAW4yhMTf2K27cTuHjxNbx1+YrI9ZGKo1WdClBV"
        "cygWd/HtN18jv52nOnB+ni0BsHqv1wuu7hs3vsP09BQuv30FFy68Co/HK6wzyB0qPDbOOXqQSmF2"
        "dobuecS5009bAFgBe8JPcGtLxfVPP8EvP0/i2jvvIh6PYzOroFQqHeZ6YuJ71Ot1vuVkW5y1ficN"
        "KvjteygiXFxfffkFltMp7O0dVDtHambmFpaW7sPtbs+39qT+BsGdjgGE6VkGAkHRD5jH0eGxs7OD"
        "H3+YOIxEA/am27YAGC2W33c43CW847wbfNbMwJLJJIV+X6zFt6KpSSuzJQDugJ2dnaLdcs9nMhvm"
        "PctwAaZSSxg5fRqFwi6yG1kUqS5akSMANlSpVBCNHhNGDgrrnyo5/HOJOZTLZRGdrq7I4feBdfC5"
        "HTkCYM+S9+5iS1Xx3Lnn0dd3XPQFDr9BbrcLq6sreEhFyc+S7yhKBnfuJKBkMi3rwREAG2H0a2ur"
        "Qmk8Porx8WfFt4GjwWe1Wh0J8p4NcwO6R4CX0w/EM+S6aEUtAbACVsReLyzMY2XlD4yNPYP46Cj1"
        "BR/S6TTW19doTmH+9ySBKAr5doyzbisAHX/a9Q72lt8253l6+jcsL6fxwosvYXFxgbreLZEmNtrq"
        "/VPyMmzYIEt19Pb6Y5ImJenj1mMI2M3mOmAZTkEblHV5pPFstrBpyFqSVCrVdjv9vpIs4ZIhYDdz"
        "RMzDTs7gkziR/kFOLd40eDxbADCjXK7OBvy+bbpwjkaQL/4PQ6H/Bx+q+dJnbMNMlhSYD3p6Ok9I"
        "mus8/U/k/3C2cuY7TdYaxWldl7WbqlpSmpw/YT3+CPwF9OO7hrNCDMUAAAAASUVORK5CYII="
    ),
    "Claude": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABGdBTUEAALGPC/xhBQAAACBjSFJN"
        "AAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoA"
        "BQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAJAAAAAB"
        "AAAAkAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAB+C9pSAAAA"
        "CXBIWXMAABYlAAAWJQFJUiTwAAABzWlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPHg6eG1wbWV0"
        "YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNi4wLjAiPgogICA8"
        "cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRh"
        "eC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4"
        "bWxuczpleGlmPSJodHRwOi8vbnMuYWRvYmUuY29tL2V4aWYvMS4wLyI+CiAgICAgICAgIDxleGlm"
        "OkNvbG9yU3BhY2U+MTwvZXhpZjpDb2xvclNwYWNlPgogICAgICAgICA8ZXhpZjpQaXhlbFhEaW1l"
        "bnNpb24+MTAyNDwvZXhpZjpQaXhlbFhEaW1lbnNpb24+CiAgICAgICAgIDxleGlmOlBpeGVsWURp"
        "bWVuc2lvbj4xMDI0PC9leGlmOlBpeGVsWURpbWVuc2lvbj4KICAgICAgPC9yZGY6RGVzY3JpcHRp"
        "b24+CiAgIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+CsHtO6kAAAcHSURBVFgJ7VZZjBRVFD219DZ7"
        "N7OJDFFBCLiAuKECxoiAKIkgRI0mmhgFTYyJRL/8MUZ/jH6IMSaKGmNc0Mgii0uCRiARMYACg4w4"
        "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"
    ),
    "Codex": (
        "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"
        "n3rgL+FQySvikfSXAAAAAElFTkSuQmCC"
    ),
}

# Per-action SF Symbols used by the dropdown rows that aren't tied to a
# session state — Open Folder, Return to Tab (jump-to-window), and the
# small info icon in front of Notification messages.
DEFAULT_ACTION_ICONS = {
    "open_folder":    "folder",
    "return_to_tab":  "arrow.up.right.square",
    "message":        "info.circle",
}


DEFAULT_NOTIFICATIONS = {
    # No notifications enabled out of the box — opt in via the dropdown's
    # per-session toggles (writes notify_states into that session's state).
    "enabled_states":   [],
    "sound":            False,
    "sound_name":       "Glass",
    "include_summary":  True,
}

# Full official Claude Code hook surface. install_settings.py iterates this so
# disabling an event in the user config (set null) actually removes our prior
# wiring instead of leaving a stale entry behind.
ALL_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "SessionEnd",
)
DEFAULT_EVENTS = {
    "SessionStart":     "waiting",     # fresh session → waiting
    "UserPromptSubmit": "working",
    "PreToolUse":       "working",
    "PostToolUse":      "working",
    "Notification": {
        "permission_prompt":  "asking",
        "elicitation_dialog": "asking",
        "idle_prompt":        None,
        "auth_success":       None,
    },
    "Stop":             "waiting",
    "SubagentStop":     None,
    "PreCompact":       None,
    "SessionEnd":       "end",
}

# Codex CLI's hook surface is similar but not identical to Claude's:
# permission requests are a top-level event rather than a Notification
# matcher subtype, and there's no SessionEnd / PreCompact equivalent.
ALL_CODEX_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Stop",
)
DEFAULT_CODEX_EVENTS = {
    "SessionStart":      "waiting",
    "UserPromptSubmit":  "working",
    "PreToolUse":        "working",
    "PostToolUse":       "working",
    "PermissionRequest": "asking",
    "Stop":              "waiting",
}

# Sessions older than this are treated as dead (Claude Code may have crashed
# without firing SessionEnd, leaving a stale state file behind).
STALE_AGE_S = 12 * 3600

# Caps on user-supplied strings that get embedded into state files / dropdown.
PROMPT_MAX_LEN = 200
SUMMARY_MAX_LEN = 80
MESSAGE_MAX_LEN = 120
TRANSCRIPT_TAIL_BYTES = 128 * 1024


# ── Host app detection ───────────────────────────────────────────────────────
# Pull `<NAME>.app/Contents/...` out of a process's comm path. macOS reports
# full paths like `/Applications/Ghostty.app/Contents/MacOS/ghostty`, so the
# first regex match yields the outer .app — exactly what AppleScript's
# `tell application "<NAME>"` expects.
APP_PATH_RE = re.compile(r"/([^/]+)\.app/Contents/")

# Override map for cases where the .app folder name doesn't match the
# AppleScript scripting name, or where we want to normalize legacy
# values written by earlier hook versions.
APP_NAME_OVERRIDES = {
    "iTerm2": "iTerm",
    # Old hook versions sometimes captured lowercase variants for
    # Electron-style nested bundles; pin them to the canonical name so
    # APP_LOGOS lookups and AppleScript activations both work.
    "claude": "Claude",
    "codex":  "Codex",
}

# IDEs that ship a `--reuse-window <path>` CLI; we use that instead of plain
# `tell ... to activate` so clicks land on the right project window.
IDE_BIN = {
    "Visual Studio Code": "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "Cursor":             "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
    "Windsurf":           "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf",
}


def get_tty_of(pid: int) -> str:
    """Return the controlling tty of `pid` (e.g. /dev/ttys015) or empty."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "tty="],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
    except Exception:
        return ""
    if not out or out in ("?", "??"):
        return ""
    return out if out.startswith("/dev/") else f"/dev/{out}"


def find_terminal_app(start_pid: int) -> str:
    """Walk up the parent process chain looking for a GUI app bundle.

    Returns the .app's display name (e.g. ``"Ghostty"``, ``"Visual Studio Code"``)
    or ``""`` if nothing in the chain is hosted by an .app bundle.
    """
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except Exception:
        return ""
    procs: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        procs[pid] = (ppid, parts[2])

    pid = start_pid
    for _ in range(20):  # safety cap on chain length
        if pid not in procs:
            return ""
        ppid, comm = procs[pid]
        m = APP_PATH_RE.search(comm)
        if m:
            return APP_NAME_OVERRIDES.get(m.group(1), m.group(1))
        if ppid <= 1:
            return ""
        pid = ppid
    return ""


def latest_ai_title(transcript_path: str) -> str:
    """Return the most recent ``ai-title`` Claude Code wrote to the transcript.

    Reads only the tail of the JSONL (~128 KB) to keep this cheap on long
    sessions. Empty string if the title hasn't been generated yet.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    try:
        size = p.stat().st_size
        window = min(TRANSCRIPT_TAIL_BYTES, size)
        with p.open("rb") as f:
            f.seek(size - window)
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    title = ""
    for line in tail.splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("type") == "ai-title":
            t = (r.get("aiTitle") or "").strip()
            if t:
                title = t
    return title


# ── Config ───────────────────────────────────────────────────────────────────
def _filter_events(user_events: dict, allowed_states: set) -> dict:
    """Strip user event entries that route to unknown states.

    Strings: dropped unless the named state is in the vocabulary
    (sentinels ``end`` / ``""`` / ``None`` are always kept).
    Dicts (per-matcher routes): each matcher entry is kept iff its target
    state is allowed. The whole event entry is preserved so legitimate
    matchers can still take effect even when one is dropped.
    """
    out: dict = {}
    for ev_name, value in user_events.items():
        if value is None or value == "":
            out[ev_name] = value
        elif isinstance(value, str):
            if value in allowed_states or value == "end":
                out[ev_name] = value
            # else: drop, fall back to base default
        elif isinstance(value, dict):
            cleaned = {}
            for matcher, target in value.items():
                if target is None or target == "":
                    cleaned[matcher] = target
                elif isinstance(target, str) and (target in allowed_states or target == "end"):
                    cleaned[matcher] = target
            out[ev_name] = cleaned
        else:
            out[ev_name] = value
    return out


def _coerce_interval(value) -> int:
    try:
        ms = int(value)
    except Exception:
        return DEFAULT_REFRESH_INTERVAL_MS
    return max(MIN_REFRESH_INTERVAL_MS, ms)


def load_config(path: Path | None = None) -> dict:
    """Read swiftbar-config.json and return a fully-populated config dict.

    Always returns the same shape regardless of what's on disk; missing or
    malformed sections fall back to defaults. User-supplied entries that
    name states outside the known vocabulary are silently dropped.

    Pass ``path`` to read from a non-default location (``install.sh`` uses
    this to read the repo's seed config before deployment).
    """
    src = path or CONFIG_PATH
    cfg: dict = {}
    try:
        loaded = json.loads(src.read_text())
        if isinstance(loaded, dict):
            cfg = loaded
    except Exception:
        cfg = {}

    allowed_states = set(DEFAULT_ICONS)

    icons = dict(DEFAULT_ICONS)
    if isinstance(cfg.get("icons"), dict):
        for k, v in cfg["icons"].items():
            if isinstance(v, str) and v and k in allowed_states:
                icons[k] = v

    # Menu-bar header icons: optional separate map. Falls back to the
    # row-icons set per state if `header_icons` is missing or doesn't
    # specify a particular state.
    header_icons = dict(icons)
    if isinstance(cfg.get("header_icons"), dict):
        for k, v in cfg["header_icons"].items():
            if isinstance(v, str) and v and k in allowed_states:
                header_icons[k] = v

    # Per-state icons used by the dropdown's "Notify on: …" toggles.
    # Falls back to the row icons same way header_icons does.
    notify_icons = dict(icons)
    if isinstance(cfg.get("notify_icons"), dict):
        for k, v in cfg["notify_icons"].items():
            if isinstance(v, str) and v and k in allowed_states:
                notify_icons[k] = v

    priority = list(DEFAULT_PRIORITY)
    pr = cfg.get("priority")
    if isinstance(pr, list):
        valid = [s for s in pr if isinstance(s, str) and s in allowed_states and s != "none"]
        if valid:
            priority = valid

    # Accept either `claude_events` (preferred) or the legacy `events` key.
    claude_events = dict(DEFAULT_EVENTS)
    user_claude = cfg.get("claude_events")
    if not isinstance(user_claude, dict):
        user_claude = cfg.get("events")  # backward-compat
    if isinstance(user_claude, dict):
        claude_events.update(_filter_events(user_claude, allowed_states))

    codex_events = dict(DEFAULT_CODEX_EVENTS)
    if isinstance(cfg.get("codex_events"), dict):
        codex_events.update(_filter_events(cfg["codex_events"], allowed_states))

    action_icons = dict(DEFAULT_ACTION_ICONS)
    if isinstance(cfg.get("action_icons"), dict):
        for k, v in cfg["action_icons"].items():
            if isinstance(v, str) and v and k in DEFAULT_ACTION_ICONS:
                action_icons[k] = v

    notifications = dict(DEFAULT_NOTIFICATIONS)
    if isinstance(cfg.get("notifications"), dict):
        user_notif = cfg["notifications"]
        states = user_notif.get("enabled_states")
        if isinstance(states, list):
            notifications["enabled_states"] = [
                s for s in states if isinstance(s, str) and s in icons
            ]
        for key in ("sound", "include_summary"):
            if isinstance(user_notif.get(key), bool):
                notifications[key] = user_notif[key]
        sound_name = user_notif.get("sound_name")
        if isinstance(sound_name, str) and sound_name:
            notifications["sound_name"] = sound_name

    return {
        "refresh_interval_ms": _coerce_interval(cfg.get("refresh_interval_ms",
                                                         DEFAULT_REFRESH_INTERVAL_MS)),
        "icons": icons,
        "header_icons": header_icons,
        "notify_icons": notify_icons,
        "action_icons": action_icons,
        "priority": priority,
        "claude_events": claude_events,
        "codex_events": codex_events,
        "notifications": notifications,
    }


def plugin_filename_for(refresh_interval_ms: int) -> str:
    """Encode a refresh interval as the SwiftBar plugin filename suffix.

    SwiftBar reliably parses both ``Ns`` and ``Nms`` filename suffixes.
    Fractional seconds (``0.5s``) are not honored uniformly across versions,
    so anything that isn't a whole number of seconds becomes ``Nms``.
    """
    ms = max(MIN_REFRESH_INTERVAL_MS, int(refresh_interval_ms))
    suffix = f"{ms // 1000}s" if ms % 1000 == 0 else f"{ms}ms"
    return f"claude-status.{suffix}.sh"


# ── State files ──────────────────────────────────────────────────────────────
def _pid_alive(pid) -> bool:
    """True if `pid` looks alive enough to claim its state row."""
    if not isinstance(pid, int) or pid <= 0:
        # Legacy records without `agent_pid` — be permissive so they stay
        # visible until the stale-age guard sweeps them.
        return True
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # process exists, just not signalable by us
    except OSError:
        return True
    return True


def read_state_files() -> list[dict]:
    """Return per-session state records, oldest first, stale ones filtered.

    Also opportunistically deletes stale state files (too old, or whose
    agent process has since exited) so the directory doesn't grow without
    bound when SessionEnd doesn't fire (Codex hard quit, Claude crash, …).
    """
    if not STATE_DIR.exists():
        return []
    now = int(time.time())
    records: list[dict] = []
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        too_old = now - int(rec.get("since", 0) or 0) >= STALE_AGE_S
        agent_dead = not _pid_alive(rec.get("agent_pid"))
        if too_old or agent_dead:
            try:
                f.unlink()
            except OSError:
                pass
            continue
        records.append(rec)
    return records


def _osa_quote(s: str) -> str:
    """Wrap a string as an AppleScript literal with proper escaping."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osa_shell_command(*lines: str) -> str:
    """Build a single shell command string that runs `osascript -e <line> ...`.

    Each line is shell-quoted so the result is safe to embed in another
    shell-string slot (notably terminal-notifier's ``-execute`` argument).
    """
    parts = [OSASCRIPT]
    for line in lines:
        parts.append("-e")
        parts.append(shlex.quote(line))
    return " ".join(parts)


def click_command_shell(terminal_app: str, tty: str, cwd: str) -> str:
    """Build a shell command that focuses the originating window/tab.

    Used by ``terminal-notifier -execute`` so a notification click jumps
    back to the right place. Returns ``""`` when no useful action is
    available (e.g. unrecognised host or missing tty for tab focus).
    """
    if terminal_app == "Terminal" and tty:
        return _osa_shell_command(
            'tell application "Terminal"',
            '  activate',
            '  repeat with w in windows',
            '    try',
            f'      set t to (first tab of w whose tty is "{tty}")',
            '      set frontmost of w to true',
            '      set selected of t to true',
            '      return',
            '    end try',
            '  end repeat',
            'end tell',
        )
    if terminal_app == "iTerm" and tty:
        return _osa_shell_command(
            'tell application "iTerm"',
            '  activate',
            '  repeat with w in windows',
            '    repeat with t in tabs of w',
            '      repeat with s in sessions of t',
            f'        if tty of s is "{tty}" then',
            '          select t',
            '          select w',
            '          return',
            '        end if',
            '      end repeat',
            '    end repeat',
            '  end repeat',
            'end tell',
        )
    if terminal_app in IDE_BIN and cwd:
        bin_path = IDE_BIN[terminal_app]
        if Path(bin_path).exists():
            return f"{shlex.quote(bin_path)} --reuse-window {shlex.quote(cwd)}"
    if terminal_app:
        return _osa_shell_command(f'tell application "{terminal_app}" to activate')
    return ""


def effective_enabled_states(record: dict, notifications: dict) -> list[str]:
    """Enabled states for one session. Per-session override > global default.

    The override lives in the session's state file as ``notify_states``;
    a list (even empty) is treated as authoritative. Missing / non-list
    falls back to the global ``enabled_states``.
    """
    v = record.get("notify_states")
    if isinstance(v, list):
        return [s for s in v if isinstance(s, str)]
    return list(notifications.get("enabled_states") or [])


def maybe_notify(new_state: str, prev_state: str, summary: str, cwd: str,
                 record: dict, notifications: dict) -> None:
    """Fire a macOS desktop notification if this state transition is enabled.

    Skips when state hasn't actually changed (avoids spam from PreToolUse /
    PostToolUse rewriting the same state every tool call). Per-session
    ``notify_states`` from ``record`` overrides the global default.
    """
    if not new_state or new_state == prev_state:
        return
    enabled = set(effective_enabled_states(record, notifications))
    if new_state not in enabled:
        return

    title = f"Claude Code · {STATE_LABELS.get(new_state, new_state.upper())}"
    body = ""
    if notifications.get("include_summary", True):
        body = (summary or (Path(cwd).name if cwd else "")).strip()
    body = body[:160]
    sound_name = notifications.get("sound_name", "Glass")

    # Prefer terminal-notifier when present — it ships its own bundle ID, so
    # macOS lets the user grant "Banners" / "Alerts" without having to dig
    # into Script Editor's notification settings. It also supports `-execute`
    # for click-to-focus, which osascript cannot do.
    tn = shutil.which("terminal-notifier")
    if tn:
        args = [tn, "-title", title, "-message", body]
        if notifications.get("sound"):
            args += ["-sound", sound_name]
        click_cmd = click_command_shell(
            (record.get("terminal_app") or "").strip(),
            (record.get("tty") or "").strip(),
            cwd,
        )
        if click_cmd:
            args += ["-execute", click_cmd]
        try:
            subprocess.run(args, capture_output=True, timeout=3)
            return
        except Exception:
            pass  # fall through to osascript

    script = f"display notification {_osa_quote(body)} with title {_osa_quote(title)}"
    if notifications.get("sound"):
        script += f" sound name {_osa_quote(sound_name)}"
    try:
        subprocess.run([OSASCRIPT, "-e", script], capture_output=True, timeout=3)
    except Exception:
        pass


def aggregate_state(records: list[dict], priority: list[str]) -> str:
    """Pick the highest-priority state across active sessions.

    Falls back to ``"none"`` when no record matches anything in ``priority``.
    """
    for s in priority:
        if any(r.get("state") == s for r in records):
            return s
    return "none"
