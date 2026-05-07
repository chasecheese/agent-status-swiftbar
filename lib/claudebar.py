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

# 3-icon (host, brand, state) pills with light + dark variants.
# Plugin emits `image=<light_b64>,<dark_b64>` and SwiftBar picks
# the matching variant from the system Appearance.
APP_BRAND_STATE_PILLS = {
    "Alacritty": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqMQfcjntMQBpfqNUH"
                    "ZqK2awjKggINQGFrHw+qm3fmxHunfncbGx38/PwQHCMlJScAAAA1NjhKSkpXVld3d3eQlZnX"
                    "19dnZ2eGhoampqe2t7fo6OjHx8ePNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBI2PUFEHBiYZA0PJzQdg8IHK0AlHh+4"
                    "cgTghgCvLQeTnaTikACxTQaRADmLAAAAQHRSTlP/////////////////////AP//////////"
                    "////////////////////////////////////////////////////ahH+YQAAAwBJREFUeNq1"
                    "ltl2mzAQhsmetsiKNpAEhsSu63pv7ezr+79VNZIQwoeek4t4LowwzKeZYfRLyZW19AvNEZOv"
                    "xjbo5ABcS04OwQVy8jluqe0F496nhOssU5zEZAe+Tf4aGxq7NnZj7O7ubhp5IsTgWhQ9WJYh"
                    "byWLwNbvx9HRT2u/wX5Z+/6tjWCAUAYBo9LGHWNxwFp0eGbBQoseU6J1zxEiAJZmrBHr5GIs"
                    "E5yrwg5xDFYSpwNrT7e3T26UYqm8L0SeQbAEkApFYGzD9KnRLCI7MPfcweV6femHKfdgBHES"
                    "CJkaL+mq4g2i5O2thGniUjRg/Lpev+IGLMLbpnTaeOTIsNtsbYU81/cLkFkPePHH2GIPbGHE"
                    "JE1kAanTTsCuD3fNdFmTUBc8rJKkGu6DU6xNYBpplWW2Lmk8ox3xJtDwlwVLD66rarmsqtqD"
                    "IwQLDQWdjAmz8YlQbxYSKXxxHDh34OWkqutqsnTgXMbtqj1Y5Aou0uctA9i3hvLVicH4YzKk"
                    "dDj5wBFYaSV5zgjR7SrgFPt+aTqPBjD3WVgwd+DpeLJ4e1tMxlMHtinhnEuhlC49NVaEFkxC"
                    "r/SBb8bXu/l8dz2+icB7tbDLK9MC+1I07wR5EnEpcgbg0Xg2Xc3nq+lsPAIwyyMuNzQLxpgK"
                    "Q1e+nmXTIKTtQBmBIcTt7Ky+ODm5qM9mW7iPwAxKgEQQA0xCr+Buu5FOu1lw/fyyGdw/PNwP"
                    "Ni/PdQw2AmaWXqGMVKBSoI7oudDbj1e6hvRgBqXYvp+PBunpaToYnb9voRTthzFT6MIIr/Gj"
                    "ebT0ePh8GO/94cAUSrw5NrTVysxxvIEiUw/GOQYPkkpt1Kww0bGOCLG0M1GWxmCbGPw8PoYh"
                    "jTx2EHQmQXZEils5t3Ks/K1r9Vg2KSU9Rmkku6AbtrdKFG9tVhtgyTAmXaeTWOgNo89agpVm"
                    "bJPGsbqBbxFvTVm7NV250vfZ/qa5h2xagwd0ke/v0p8yrPB/HjAFMixod/s/2IHlcEeswx0K"
                    "D3KM/QdpQKbqRcWSDwAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwbZiAAaIimyMwVW"
                    "HxR/f4HxxHAILEH10sQdg8JfqNUNQGFMQBoHZqLxe2rMpJzQwJzYj3uToKPz8/YBAQEPGyMT"
                    "GBoAAABWVlbo6OuTlpk0NTYlJSZ3d3doaGjGxsmnp6lJSkuIiInX19m3uLrLRQErJBuUm6Ha"
                    "mXvUWADdtXrTaAC+vsA7Mxzyp2zep3vsiG/xlmxzNRFxURPbdwDKNgH0uWze3uFvTBITUXlN"
                    "KRWNOAhbNxDIKQGvxtMzPUFpHkuqAAAAQHRSTlP//////////////////////////////wD/"
                    "////////////////////////////////////////////////////0khfawAAAzZJREFUeNq1"
                    "lmmbmyAUhc0y+3SVUDZRcEnTJO00STvZ5///q15ARDPp88yHyfkQNcLr8QAXo29W8TvKEaP3"
                    "xnp0dAGuJUeX4Bpy9CZujkVujll29i4jAmNBGG2RHfj24xPoN+in0V/Q9fVtaKYRSuBAEXqN"
                    "fZYY1cIJDWDzSz89fflu9cvoh9X152AghU5wNUPCtM5azuKswRqJwv9vwbPiK6s1Hjdn2Sx0"
                    "lwhpgCAFDoV1X4s5q5wQ7tBZG5xpgUdW/eGw786w0HUbxsAlRoICJYmpQEgGv9Yme7ZRa2u+"
                    "aIOTmjsaHg7D+hQnDpwjpKhJmcUJ+FHgLw+DCiTSJJMr5DLzYObBYnA4DIQHM9ccWos0FtBD"
                    "ojwJnlxCiNizIoXXsm3roLrg7R/Q9gRs3CINL60VKpAN2zs0OdC6iX0/iKy23AXvysGg3J2C"
                    "4xRyVQIJjqGfCiOXNINFvFFjgjVgXYOrslwsyrKqwcEaTZoJBX5oylz+vElU+hfJfTgOLB14"
                    "MSmrqpwsHFjq1nQ1pq24tPPK+g7+k9qnfZg4BYtoslNqN4lEC8wFV0QmOhPBNNGFNYqamRfA"
                    "EApuwIkD76eT7Xi8nUz3DmxTyyRRinPhuYo1k60Fzpq5cg4cTaOX4/EFDi2wVxEcIyx4WkdB"
                    "/G2/TDtRSGLA8+lqvzkeN/vVdG7ARLYKGAGaW7JFYkLm3cGjWtcnPv8abCyuV1H1cH//UEWr"
                    "tbkOYFgZiDDEfTGgM1chz0w31pluFlxFveWof3fXHy17UdUGQwHDWY44lAokXhBrFz1nvRk8"
                    "U0ncirdgYqJY93rzEf7wAY/mvd7aREGagZEUeuQJhoWlJbpqyKQZvqI4+cOBlYl4+Qi0zQae"
                    "8bg0IasanMrC9GCxEuYhuQ7VzSwHeGoTmeHi5zYYYzN8eIRvbuDHnGLswW6lwgVW5gE8LnDW"
                    "KcfcVX46Fq2CbMFmor6WClVhhjgFe8RWGRbTsIVcuY1DSancVsLahX5MzmocsjQ1LLMDXyDd"
                    "2fJYe2dC4V3cZpqeVehtLSYOSU8205QErsxPduk3KeXpf24k3JRhfpV3tv+LfbBc7hPrch+F"
                    "F/mM/QePh/QO1d3q6AAAAABJRU5ErkJggg=="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWmKAncjntrHw9fqNUN"
                    "QGG2awjKggIlHh8HZqKqm3fmxHunfncbGx38/PwQHCMmJicAAAA1NjhKSktXVld3d3eQlZnW"
                    "1tZnZ2eGhobn5+i2t7fIyMimpqePNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBKYZA1EHBgzPUEHK0C4cgTghgBMQBqT"
                    "naTikACxTQYPJzQdg8K1OgQOKmnOAAAAQHRSTlP/////////////////////AP//////////"
                    "////////////////////////////////////////////////////ahH+YQAAAxZJREFUeNq1"
                    "ltl2mzAQhsnWpC1C0QISQobErut6b7Pvef+36owkg/BxzslF/F8EYaJP/wyjEcm5U/qF8sTk"
                    "q7EbdLIHriMn++AiOfkctzLuQunOp0yaorCSxWQPvkn+gQagC9Al6Pr6ehLNJETgtSx3YEVB"
                    "gioRgd28nwcHv5z+oH47/fjeOcgIKdAwqZzvGEtbrEO3zxw4N/kO2bybrghhCNYwNkT0YgGV"
                    "uZS2dEMag62maeZ0enNz6kcp1TbMRecFmmWItCQCU2czhMaLiOzBMnCzw9XqMAxTGcAEfTK0"
                    "zGGW9lkJQpeyu9W4TJyKDZgmq1VCN+C8/W9InYEZigC7i9ZlCLmqqAqQ8GSxAzz/C5pvgR2M"
                    "QdBMlxg67xk2wXfwWmwC6oMH9ft7PdgGp9SAMUOMLQqXlzReEed7sIx+8mAdwE1dLxZ13QRw"
                    "hBBtQWElUyZcOvJgjxKawVvIQhCyAysPXozrpqnHCw9WOi5XE8C5snhxz1r/AhISasP67PTA"
                    "9GU84HwwfqER2BqrpRKMmW4XSE5DvWwqTwul/EiGKBxYevBkNJ4/Pc3Ho4kHu5Cokjq31lSB"
                    "GneEDqyE5B+DL0cXt7PZ7cXoMgJv5cIVQGFyGlIhw/4TMlRhHqdCCQQPR9PJcjZbTqajIYKF"
                    "irgSaA5MKc+BbkM+q7D/KCPlpgJ1BEaLV9OT5vjo6Lg5mV7hfQQWmAKSt82AsrZW8BbqG12r"
                    "0DtYH9zcP6yz17e312z9cN/EYGhgsPVKCwGTKie9puesE0NBBea2IsG5AwtMxdXzt2GWnp2l"
                    "2fDb8xWmQrQNDJYwJTRemMdVtPWkf33av1fV/tCCOaZ4/Qi05RLWeFxjknkAU0VxBku1gWyW"
                    "sLlErwmJtLdQkcZgFxj+ubtrhzyacYt2Co1tJ09p185dO7bh1pd63DY5ZzvEedR2sW+42qpI"
                    "fLS53gC1ARtE+0pncaMHxi51BNeaqQuaxt0N55bx0VR0R9O5P313afvQ3EJuSkO26FJtn9Kf"
                    "ErX0gwfCYhvOef/439sHy/4+sfb3UbiXz9j/IQCoDtVKkyUAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwbZiAAiNkFWHxSy"
                    "MwUaIinxxHB/f4H10sRfqNUNQGEdg8IHZqJMQBrxe2rQwJxfX2CToKPMpJzYj3vz8/YBAQEP"
                    "GyMUGBoAAABWVlbo6OuTlpk0NjYlJSZoZ2jGxsl3d3enp6lJSkvW1tmIiIm2trjLRQErJBuU"
                    "m6E7MxzUWADamXvTaADdtXryp2zsiG/ep3u+vsCvxtP0uWxbNxBxURNvTBJNKRXe3uETUXnb"
                    "dwDKNgFzNRHxlmzIKQGNOAi5pWtxAAAAQHRSTlP///////////////////////////////8A"
                    "////////////////////////////////////////////////////Bu0rrAAAA1RJREFUeNq1"
                    "lll32jAQhQ3Zt64aVK2WZRsIpU1JszdN/v+/6owkW4Yk5/Qh3AewsfVxfTUaufgWxN5RkVi8"
                    "N7ZDF1vgBnKxDS6Ri//i1tzU9F1Vr15VwnBuhJIDcgQffrhA/UX9JP1G7e8f5tsaAIdfEuAl"
                    "9s5ySOJOZjB9yi8XX78H/SL9CNr/mA2UOAjPVmDo7mrgjFU9lmTa7vcAXrWfVdJ83h9Vqzzc"
                    "AjQIAY8OTXCfpCJQC6HjUTUEV43hk6C9nZ29eMRNk+5RCl1yMBIpjkkDYLPfYDOGWzfBfDsE"
                    "u8Sd7Dw97aRD7iK4BvCSUlbMoR+PqdR5UpEkJJNBrPYQM+vAqgOb3aenXdOBVRyNd5uSGRxh"
                    "oXbZU0wIBGOCB1kmiexeAd/+Qd1ugMktNPjQjYcWQtidYcpBUrHEogiRJcvr4Ofp7u70eRPM"
                    "SszVGzCa4zifZ86FyZIszZtgITJQPbhJ4Nl0+vg4nc4SOFuTri8o9CNLFfPXyV4LVVkrKNND"
                    "iAy2Efy4mM5m08VjBNtmUK5kOhaWDf6C796/M/KOx9vxqtkEm2Lx7P3zojADsDbaC+uaymTT"
                    "omlDjtBXnnciHYmQdQK7CL5aLm7n89vF8iqCw/RWVnivtem4XvXF1oMls1Zgib8FLpbF+PJy"
                    "jF8DcKc2OwZudJmiEGn9OZHW3FoUVhD4fPlwdX95eX/1sDwnsLCDBiaQFpds6yhkPZw8bCUV"
                    "ru069qmYewKTxZuHYnZ8dHQ8Kx5u6DyDcWWAUKC7ZiBXcRF35ebpRxXKTa2VWwDPitH1ZO/k"
                    "ZG9yPSpmQzA2MF7VoPGBwYxBDZseWZfY1Kqq5bRATLfiA1hQFDej0fmEn57yyflodENRiL7R"
                    "WIkjasdxYTUWDnqyiNPnY/Sf+h96sKeIr8+Qdn+P/3F2TSH7BC5tSyMU84b+pG5yd6PlQGd1"
                    "EJPE5XdDMLYQmq8JH4/xgw4578CxXeAJ9/QHmrW8WmvHOp7KuRk05ACmQn0pn7vCCjQ2RYJj"
                    "GAq7ZH/lINaft9bHrUQNG/1cvKp5zhJ7GMbgQl9o1rY8NdyZID9L3EzLV5VHB4suIuXGZlqK"
                    "zLX1xi79Xyp1+cYFp6kN64N6bfvf2gvL9l6xtvdSuJXX2H8j2/9jEu/4agAAAABJRU5ErkJg"
                    "gg=="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWmKAncjntrHw9fqNUN"
                    "QGG2awjKggIlHh8HZqKqm3fmxHunfncbGx38/PwQHCMlJScAAAA0NjdKSkqRlZl3d3dXV1fX"
                    "19dnZ2eGhoampqfo6Oi2t7fGxsePNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBKYZA1EHBgzPUEHK0C4cgTghgBMQBqT"
                    "naTikACxTQYPJzQdg8K1OgT4smyRAAAAQHRSTlP/////////////////////AP//////////"
                    "////////////////////////////////////////////////////ahH+YQAAAwhJREFUeNq1"
                    "ltl2mzAQhsmetghFGyBkg2PXdb23zr6//1tVI8kwcOg5uYjnIgiT+Zj5NTMiunIWf6F5YvTV"
                    "2D06OgDXkaNDcIEcfY47MO5Cae9TJk2aaskw2YNvo7/WhtZG1q6t3dzczJAnIQquRdGDVSkJ"
                    "NlAI7Px+HB39dPYb7Jez79+aCBJCUgiYDFzcGEtrrEPXzxxYGNFjWjTuGSEMwLldG6JauVhL"
                    "hZS6cEuKwTqnceLs4vb2wq9imuvgC5GnECwDpCYITF2YITWeIrIHy8BNjjeb47CMZQATiJNB"
                    "yNx65V6VYBClbG5zeA2WYg+m0WYT0T1Y1P9tpTPWIyOW3WTrFMJcT1Y94OUfa8sO2MGYTZrl"
                    "BaTOWwGb5o5TkCwk1AYPy4+PctgFx9TYwAwxOk2dLjF+I4p2gH5y4DyAq7JcrcqyCmCEUHVB"
                    "QSVTppwcAuttd0H4JGQDzjx4NS2rqpyuPDjLcbmaABaZhot71oo/JUUSu6IxXTB9nQ45H05f"
                    "KQJro3OZKcZM0wWS01Avbqdco2dBfBmycGDpwbPJdPn8vJxOZh7sUqKZzIXWZhCoeCIEMCDt"
                    "rvrq7ANfT0Z3i8XdaHKNwB0tXHulRtAghfR8ZuVO4qB7I0WmADyezGfrxWI9m0/GAFYZ4kpL"
                    "c2BKuShCeNq3g29rXldgjsAQ4m5+Vp2enJxWZ/Md3COwAgmIqIcBZXWt0FB2upkdrA2uHh63"
                    "ydv7+1uyfXyoMNgOMNt6hbajggwEaQ09D8xsJmFw+4IMYAVS7F7Ox0l8eRkn4/OXHUih6iDs"
                    "K0xhB6/14xlqPbnvYJp0fghgDhJvnyxtvbbveNqCyDyAaUbBg8W5sZtf2MRVawipuPWiNMZg"
                    "lxj8ub+vlxx53EHQaQ5Ji5g249ztmw63vtTx2OSc9RjnaOxCt7raGhB8tLl9g5ZRKveVzvCg"
                    "t4w+awhuNFPfZni6gW+Bj6a0OZqu/OnbZ91Ds4Pcl4as0UXWPaU/ZVTT/zxQGsaw4O3j/2Af"
                    "LIf7xDrcR+FBPmP/AdbqpxpYdVRcAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwbZiABWHxSyMwUa"
                    "Iin10sQNQGEdg8IHZqIILEFfqNVMQBrYj3vQwJyToKPxe2rMpJzz8/YBAQEPGyMTGBoAAABW"
                    "Vlbp6ew0NjaTlpnGxsklJSZnZ2h3d3hJSUunp6mHh4nW1tm3t7mVm6ErJBvLRQHamXvUWADd"
                    "tXrTaAA7MxzsiG++vsDyp2ze3uHep3vxlmx/f4ETUXlzNRFxURPbdwBbNxDKNgH0uWzIKQGv"
                    "xtNNKRWNOAhvTBLxxHAzPUHfUyWDAAAAQHRSTlP///////////////////////////8A////"
                    "////////////////////////////////////////////////////BE+NBQAAA0lJREFUeNq1"
                    "lul22jAQhU3Inm4eVC2WLC9AKQlNm7C0gSzv/1adkWxLEHJOfoR7Dl6w/fn6ajxy8t0p/UB5"
                    "YvLR2BadHIDryMkhuERO3sWtmK5oXRR7jyquGdNciYjswSdfb1B/UL9I/1DPzyfhtBIgx5UA"
                    "eI29NQwasVwEMC3Ft5tPP5x+k346PX8OBjK8CPdmoOnsInKWFh2WpOv2fwee1V9Uo+Gw2ypm"
                    "4XIDUCIELDrUzn0j5a1KzqVHFzG4KDUbOB0fHR37LabL5hyl0CUDLZCSp0IDmODX2VS3LurS"
                    "ma9jcN5wB0cPD0fNJss9uAKwglJWaY5+LPqrwqAiiXfJVBZ8Zi1YtWDdf3jo6xas/Ol4ts5S"
                    "jVcYqPLgyScEPApcEDnfA978RW12wOQWSnzo0kINLuzWIeXQ+RVllt6y1vI2+HHc748fd8Fp"
                    "hrlaDVoyvM4Gg3k0WPRk2kUGqgOXDXg0Hs/n4/GoAQdrIu8KCv2ITPn8ZZeorw/rH4IHsPHg"
                    "+WQ8Go0ncw82ZZQemXaSxtWV8x37x7qBzN9M74J1Mnm09nGS6AgstbTc5GWhg2le1s4oUOWJ"
                    "NCtwYZrwOR7vwLkHr6aTzXC4mUxXHuyGtzDcWil1y7WqKzYPpqe/S3FUZfoWOJkmL+v1C64i"
                    "cKs6OAamZdZEwX2sSvogdqIwnMDX0+Xqfr2+Xy2n1wTmJmpgHGn+la1zCllGgzd0B7wN0ebf"
                    "gMni0zIZnV5eno6S5RPtBzC+GcAVyLYZiJnvkL7chFs39ay2ys2BR0lvMTg+OzseLHrJKAZj"
                    "A2NFBRJbBegXUHHTI+tu5HwQ1En8G+/AnKJ46vWuB+z8nA2ue70nioJ3jcYIvKLKGdZUaXCo"
                    "WnE/fCItsq0/OrCliBdXSLu/x3tcLShk24AzU9MVKrWablKVobvRuOFdnVf6EZfdxmDGaPjY"
                    "gF1c4II2GbM8nkJwh1m6gUxrVmy1Y+k7vxjqqCE7MBXqa9nQFWYgBdrj7gVTqQhTyJ2fOKwx"
                    "1k8lKm70Q75Xw5AljXnhKqqGcmvKU/HMBOFZ/GSa7VXUZ4UrrrINM1bGA9dUO7P0u5TJ7I0D"
                    "uaQ2LO+qren/YB8sh/vEOtxH4UE+Y/8Dk4zgKOY2V1kAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWmKAncjntrHw9fqNUN"
                    "QGG2awjKggIlHh8HZqKqm3fmxHunfncbGx39/f0QHCMkJCYAAABJSUo1NjhXV1eQlZlmZmZ3"
                    "dne2trenp6iFhYbHx8fp6enX19ePNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBKYZA1EHBgzPUEHK0C4cgTghgBMQBqT"
                    "naTikACxTQYPJzQdg8K1OgQxAK8bAAAAQHRSTlP/////////////////////AP//////////"
                    "////////////////////////////////////////////////////ahH+YQAAAt1JREFUeNq1"
                    "lul22jAQhZ09bS1PJFmLF+wESil7S/b9/d8qGkmADG7KjzDnYBtkPl9fjWYUXdiIvzAcMfpq"
                    "7BId7YFrydE+uEiOduPmyp4AWkdpgjcAC8kOfBP9NdExcWniysT19fVgfRsjhOO5KNq4nChz"
                    "A8sJDcH2fz8ODn7a+I3xy8b3b2sFCSElCia51R1SQXFKdCxFnBMm1GrMglOVtoRO138XRhGC"
                    "M3OtnPr1yPJJYJ5PG2CdQZzYOLu5OXNXMWTa+4DKSxTLEKlJAAYOWWAAl+aHECw9NzmczQ79"
                    "ZSw9mKBONDGmBOLMueKjtO8QREpUaMUSDNFsFsES7K0wqByMAbl5bcMmJPBYFHRjIgveBh7/"
                    "MTHeAFsYA/PJCnMM557TrRRJ/G9NcKd6f686m2Az84RIRZQuSxK+O22o9/faafbgzIPrqppM"
                    "qqr24CxM1WVgJgNzUwRKbSvO9VpxJhx40q/quupPHFhkjXT14FRoPLkx1rZe2BYYXvodSjv9"
                    "FwjAWulMCs6YWonOJXUO5I2M9iGJXoGlAw96/fHT07jfGziwtFKFzFKtVe6pkoXJJnYCX/Uu"
                    "b0ej28veVQDe8KLAQ6lS2MkKwRHc7Q0H09FoOhj2ugjmoqGjtOQCgKaGblUlSm9PntIhGCUu"
                    "hif18dHRcX0yXOD3AMzRApJazWDTYrd0s+D6/mGevL69vSbzh/s6BEOJS6/QplSQPCXsPwuE"
                    "rxVztGLxfNpN4vPzOOmePi/QCr4qxuYRqoh5afKAinDptSxpGixpTtHi+aOhTafmGY9zNJl6"
                    "MAhAj1mcmS5hFggNkmyrCOlGEXJvlODh7m51SYMkvUXRZYblN40hh6Bsyk/KJqWsJSgNZgTr"
                    "BpF2UTRz7NNCbxhtsSbY0gzWAiBNVyEX2JpMxppHyqA1Xbju2xZbPZO2t3DfTMtmM919WwEa"
                    "/jFiSge2f9ps/3vbsOxvi7W/TeFetrEfvK+lrTN1120AAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwbZiAAiNkFWHxQa"
                    "IimyMwXxxHD10sRfqNUdg8IHZqJMQBoNQGHxe2rQwJxfX2DMpJyToKPYj3vz8/YBAQEPGyMA"
                    "AAATGBvp6exXV1fGxsgmJidoaGkzNTZ3dneTl5qIiImoqKrX19pKSky3uLorJBvLRQGSm6Hd"
                    "tXrUWAA7MxzamXvTaADd3eDyp2y+vsB+foDsiG/ep3vxlmxbNxDbdwATUXlzNRFxURP0uWzK"
                    "NgFNKRVvTBKvxtPIKQGNOAhxxOzYAAAAQHRSTlP/////////////////////////////AP//"
                    "////////////////////////////////////////////////////15ytDAAAAwdJREFUeNq1"
                    "lul2mzAQhfGWtTvjSkILCGFcu3adtN5ab+//Vh0JA/KSxD/ie04CHORPlytppOC7U/iOKojB"
                    "e2NLdHAFriMH1+BacnARNyU8tVetz72lSZRyTrOEeuQCfPvxGfUH9cvqL+rm5rZulgAoSwA4"
                    "gw0FcGwQE0hCWoPduy/P3346/bb64XTzqTYQARB8mgC3rTX1qBkXT8BTJlIEMx6Vbxx4kn2N"
                    "9+r3qzs9qW0JQDuhBhOGY+7ce2/SbOzyirD/pwOwTjjpOrVbrXZxR3iyDzSO0RkBTsMYkZQD"
                    "iAqbKW3qbqhgWkU+WO253dZ229rfElWAUwBDbcpxqECHBlNJKxKx3+BLurRKcFyCeWe77fAS"
                    "HBdNEIXRcUxZQKoAIPODUCE9HEh1Drz6h1odga1bSDT+GcjAhV1y1FN6PEkipegpeJd3Ovnu"
                    "GBxGmKvhwCUh4H879hgdg9GArsDJHtzL88Uiz3t7sG8NSuG0o1Fc5B9xfuqYy7QGiwK8GOS9"
                    "Xj5YFGCRHLTfg6WQ9uJ8j2N6ZpXG9BjMg8HOmN0g4B5YcmmYUInmtWmWZO7HxJt4lRjICqwK"
                    "8HQ0WPX7q8FoWoDd+GrBjJGSl1wT11//Apgfg4NR0NxsmnjxwNVSqB0D4TLyvvrlKASz4OFo"
                    "Np1vNvPpbDS0YObZSRnSHFRnyoYsLxo8wazF9Szofbi7+9ALZmv7XINxZQCLQRZkfJ4Urt6c"
                    "bg7cCxrLbvv+vt1dNoKeD9YEiE5BYqkA3oTYqw3q9QXCbBTrRmPYJQ8PpDtsNNY2ClZ5EBRr"
                    "T6oI1oZE1BXMLunktSXNjI14+Yi0+Rz7eFzakM0eHInMjnUcGm47SZN6LrxVhJghxA4f6ZJm"
                    "E//ZW0JKcJEmPhBTzNKMVDtUJrTx1pFiWvhl007UU5nazAQkxfrJnMc4pPSw0EcvFfo+O6t+"
                    "PemxymMMNr4MkoMND7emBOT4s0j54dbkNtPorLzfW4uqQNILN9PLjxWRjM6/sNs/Od3+r3Zg"
                    "ud4R63qHwqscY/8D6Sn1Knz6dUgAAAAASUVORK5CYII="
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWmKAncjntrHw9fqNUN"
                    "QGG2awjKggIlHh8HZqKqm3fmxHunfncbGx38/PwRHCMmJicAAAA0NjdKSkuSlZhYWFl3d3jn"
                    "5+dnZ2fX19eGhoempqfHx8e3t7iPNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBKYZA1EHBgzPUEHK0C4cgTghgBMQBqT"
                    "naTikACxTQYPJzQdg8K1OgSdTjbpAAAAQHRSTlP/////////////////////AP//////////"
                    "////////////////////////////////////////////////////ahH+YQAAAx5JREFUeNq1"
                    "ltl62yAQhZWtSVshwo4WS4ld1/XeOvv+/m9VGBBC/nSRi3guEiyJX4fDMKPkEiL9wnDE5Kux"
                    "LTo5ABfIySG4lpx8lssrqXM8fI+ISutCkJjswLfJPxMjE1cmrk3c3NzMouewRIUSGomhV2rk"
                    "o+QRGN748+joF8QfG78hfnzvFGBUgVhmp7OebhywgA73AMwqNhAFC7MLbf9mOZKKqjLWTYCn"
                    "mRCFhCGOwYUxL4M4v709d6MU50UrKkfU/FNIUrd2pLqlWJl+aVRHZAcWnpsdbzbHfpgKD87h"
                    "aSJBaGavCJR5sFUZ6bePlrEVLRgnm02CW7CzgiFODYgVGGaCi8jvkgpcjAOZD4CXf00se2Bi"
                    "HiQWbNbLpSKl3TydB8EVDO5aC4wbegA8qj8+6lEPzHTqwUSDt1QiJZ1MavQ5f0UrNFwCcO7B"
                    "TV2vVnXdeDDIsgwAywrjCrLOGEC9S16e2U9/yS5CdGDlwKtp3TT1dOXACsCoBXMHyOG4lO26"
                    "8wD2qVF4d2Iwfp2OKB1NX3EEZpUFY/DYjAouuc0y0Bd2yjrgwcKvAsDCgWeT6fL5eTmdzBxY"
                    "uCnY7hovHJgZqBmVrA8mIX+HwNeTq7vF4u5qch2B01K6PINDwVmaWbVCeivaJMbtiWOxFYpb"
                    "8Hgyn60Xi/VsPhlbMFftKu1su3G4ki1Yez/9cUgp6Y5MHoGtxN38rDk9OTltzuY7+9uDhfRn"
                    "mSNNiAdr5rfMOxDSjfTSDcDNw+M2e3t/f8u2jw9NB85lipmpB8rumgdTNzkzlKK/eSVCsjsg"
                    "3Fqxe/k2ztKLizQbf3vZWSu4zyQjCpcuzwoA8zbLRNg+jPcuODC1Fm+fDG29Nu942lqTqXsi"
                    "66qMQJJAQcvTqAh11R24Oo3BaVu47u/DkIZK40+rdEcaRR0KynHhf5Nqv2xSSgaC0qCj5FRp"
                    "xOB9uogbCHWNQ3CelzAkcaE3jKEIrQm7lgTDCvU7KpFxa9Jda7p01g9Fv2cKpHPFkNzv1JkI"
                    "aKn2u/SnAucaVXzoBi9sGWa03/4P9sFyuE+sw30UHuQz9j8ZC6hcLdDEUwAAAABJRU5ErkJg"
                    "gg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwafNgfZiAAaIinK"
                    "NgFWHxRfX2DxxHB+foD10sQILEFMQBpfqNUNQGEdg8IHZqLQwJzxe2rMpJyToKPYj3vz8/YC"
                    "AgIPGyMTFxoAAABWVVY1NzhoaGmTlpnIyMro6Ot4d3hJSUolJSWmpqiGhojW1tm1triXnKHL"
                    "RQErJBs5PkDamXvTaAA7MxzdtXrUWADsiG/ep3vyp2zbdwD0uWy+vsBNKRXe3uFxURMTUXnx"
                    "lmzIKQGvxtNbNxBzNRFvTBIzRDFIAAAAQHRSTlP/////////////////////////////////"
                    "AP//////////////////////////////////////////////////GKkOzwAAA0hJREFUeNq1"
                    "ltl22jAQhg2U7Es3hKrFluQNUyhpaVjK9v5v1RlJFjal5+QizEmIgqVPo380M46+WRu8ozli"
                    "9N7YGh1dgGvJ0SW4SI7eyM2VIVSn559JZig1TCYNsgPffnoB+wP2C+032PX1bWNtSggXmhKW"
                    "5MkJdiko8UZVcgTjZ/L55et3az/Rfli7/nhEFMTEOI/jct7yOw1YNFPU31vwa/FFeivLMEpf"
                    "w2pOc/iMNSHiRqDf4Yl0rnLGuEOnTXCaGTq01uv3e25ETVbPSTVR4K4gxB4VBlcNicBNubRS"
                    "Z9b5oglWnjvsbzZ9P6TKgRNtZwOCgdsxohmJfdgQdfQ/x6k0OYJlDTadzaZjarC0M8ZEZADS"
                    "/NWupLBdjidAE8h1UYCtgIhkdQa8+AC2aIElEfALYIkaCEkJjwdUOw9RB+tg5sVNaO1yG9wZ"
                    "deCnBeYwD8FjCUwBSxVE0LupQrBY7SjsQGQAZx5cjUb7/WhUeXCGz5BhwcSkhcHrlAcKD4qC"
                    "Jlk4BDuChQPvZ6OqGs32DizaYOEAOsdr7XhwbqcJ+i4H9WbmFGyiWUfrziwyDTA3CC4G3C4s"
                    "OeicwBVxmxIiTsFwHBrAyoF3k9miLBezyc6BlRMttVGrweAuQA1vg9Nwf8+Bo0k03m7H8KcB"
                    "hjgXNukEnl7wQYxgRrwUrE76Ok1bUgiG4Olkvltvt+vdfDJFMBP1KTEzIHAQPFKDaTt4SZb5"
                    "AfG6ezC6eJhH1ePDw2MVzQ/4vwdrcuVyWRCaSQdOKP/PdZOt62bBVdRdDXtPT73hqhtVTfAg"
                    "5oRKG7XSgZVbHGOtawUvMXCIPIAZSnHodqdD+vxMh9Nu94BS1FkA2Zoae89ijmCl6lvGQviK"
                    "4uQLB9Yo8eoeaOs17HG/QpG1A8c2QEnsMqOEWwEq+rqD6eCi6gRGLl02wZRi+OiQ3t3BBw4p"
                    "9WDQ1lVLzOUlnqDRoaSr/BadlKZRkC1Ya37GtA5+QLJAfdexrZSm2UBuXOPQAhsXISFPHLhk"
                    "Z60M5dy1JPQrNqRotTzZ7Ey2qHqwbabxWWsshx4KpVhfcXLChbXsyBX5SZd+k6VwWKOSfx/E"
                    "imMZ5jd5q/1f7IXlcq9Yl3spvMhr7F+NvQnXZ0kyogAAAABJRU5ErkJggg=="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWmKAncjntMQBpfqNUH"
                    "ZqK2awjKggIlHh9rHw+qm3fmxHunfncbGx38/PwRHCMmJicAAAA0NjeSlZhKSktYWFl3d3hn"
                    "Z2fn5+fW1taGhofIyMi3t7impqePNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBKYZA1EHBgHK0C4cgTghgAuPEKTnaTi"
                    "kACxTQYPJzQNQGEdg8K1OgSl4/boAAAAQHRSTlP/////////////////////AP//////////"
                    "////////////////////////////////////////////////////ahH+YQAAAzJJREFUeNq1"
                    "ltl2mzAQhp2tSVuEql0CbBK7ruu9zb7n/d+qmpHAIoeek4t4LhIZxMevWRn8QMs+0QJx8NnY"
                    "Bj3YAxfJg31wgTz4KFeWxjraf4+p0tpKsZQcwNeDv96G3s69XXi7urqaJfuoIZVWlqi+V1oS"
                    "rZAJGN/4/eDgJ9pvsF9o377uFFBSolgOj/OObtpiEd3eQzAveY9VvH26svA3d8RooYtUN0Oe"
                    "4UpVBpc0BVfeeTna6fX1aVhl1FWNKEeE/6eJEeHsRO+OAjLj0YRNyAGsIjc/22zO4jJTEexw"
                    "NzMoNIcriuQRDCoT/bC1SF3RgOlgsxnQBhxcwYkUHsQrik+iF0mMkg5cbQvrTQay7AEv/3hb"
                    "dsDMb2QA9ueVRrMCgmddK7iMuqNW7w3bAx6O3t5Gww6Y2yyCmUXfCkO0CccXnsaaZAk+aS4F"
                    "sIvgejRarUajOoJRFjAQbEpKS8w67wARvRTkUUJz3BTUqx1YB/BqOqrr0XQVwBrBpAHLkBAO"
                    "yyVEyJ/bxbLMspgbVfBOB0yfpkMhhtMnmoB5CWCKPvarShoJElFyG6nMSR1TUMVTIFgF8Gwy"
                    "XT48LKeTWQCr4EcKUZNVAHMP9auCd8FaKvF/8MXk/H6xuD+fXCTgrDAhz1CR5FkOapWJrlCx"
                    "/qSKlcFTV2gJ4PFkPlsvFuvZfDIGsNRN6OFpCBwtTQO20Z9FrD/KiGky0CVgkHg5P6lfjo5e"
                    "6pP5JfyOYGViLUtiGYtgy0MoQwU7OBTDM7FOuiG4vr3b5s+vr8/59u623oGdySj3/UBD1CJY"
                    "hIdzT4G6973Pm4VTFCQqR7AEV1w+fhnn2fFxlo+/PF6CK2RsOV4OLUKeVQiWMcsgUiQWsjfd"
                    "XmjBAly8PfS09dq/43ALThZhR77rMooYhg3NZUkT2nV35NosBWdN47q5aZei7TRhAbWMwUwm"
                    "FLbjKv5m5fu2KQTrMSFaHYUU2hKO77NVOkBEaD++QFyBS5Y2es/os3Y00TCScFmS7kRlJh1N"
                    "djeacJjSXuvOTEWs05yY95M6Vy3a6PdT+kNGnSWl7LshK2jDXHTH/94+WPb3ibW/j8K9fMb+"
                    "AyCJqXcAX5PVAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwbZiADvsG+yMwVW"
                    "HxQaIinxxHB+foC/v8D10sQNQGFfqNUdg8IHZqIILEFMQBrMpJzxe2qToKPQwJzz8/YCAgIP"
                    "GyMUGBpWVlYAAADo6Os2NzhoaGmTlpnIyMpJSUp3d3inp6nW1tklJSaGhoe0tLaXnKHLRQEr"
                    "JBs7MxzUWAA5PkDTaADdtXramXvep3vyp2zsiG/bdwBfX2BNKRXe3uFxURMTUXnxlmzIKQGv"
                    "xtNbNxBzNRFvTBLKNgGNOAiigGlnAAAAQHRSTlP/////////////////////////////////"
                    "AP//////////////////////////////////////////////////GKkOzwAAA2tJREFUeNq1"
                    "lml3ojAUhrG1+zYLMZksJBBAap2lanVa7fj//9XcmwQMHeecfqjvOdVUyMPdQ/LNKf1AeWLy"
                    "0dgWnRyA68jJIbhITt7JLYwlVOf7rylmKbVM8YjswRefH0G/Qb9QR6DT04tob06IkJoSxgv+"
                    "BvskKQmihu/A+Mk/PR79cPqJ+u50+nWHqIjN8D6B20XP7rzDomzV/u7A59UXFVTX3So/73YL"
                    "WsBnpgmRZxLt7q4oDxSMCb/KY3BeWjpyOhkOT/yK2rK9J9fEgLmSEOcqLOZRiMBMH9yidMZX"
                    "MdgE7mj4/DwMS2o8mGt3NyAYmJ0hg5EspA1RYD93Sgu8lfIdWLVge/z8fGxbsHJ3PBBZAkgL"
                    "iAzspPC4Aj1ASeTCg6iT9EaYPeDFH9CiB1ZEwh+AFcZAKkpEllLtDcY4gL1tUYB7tDW5D96O"
                    "j4/H2x5YwH0IflDAlLDVQAbRTJBxyeJpyBuDZQnfqgOXAdyMx5vNeNwEcInXkOHAxOaVxXIq"
                    "WOuuCOZVJM8K5QJf+OC0YOnBm8m4acaTjQfLPlj6oOoCQd5d8NvHxFj+RMv2YfYt2CaTrdbb"
                    "SWIjsLAIrlLhHKwFxJlDifiHEiJ9FrVhYcVcrAPYePB6OlnU9WIyXXuw87fEKAKtBYO5ALUi"
                    "BvNUSmZS/j9wMk0eXl4e4CsCQ54r13QS90mRZghmJISChf4zLPRcLxSSIfh+OluvXl5W69n0"
                    "HsHBOeM7AxIHySMtmMbJy6A4oLex7bH0dARGE19nSXN5dXXZJLNX/D+ANZn7XpaElsqDORVx"
                    "uWm0VrlyU71yc+AmGSxHJ9fXJ6PlIGlicJoJQpXLWu3Bxm/OcPyglbbK84pig1hwoujADEPx"
                    "Ohjcj+jNDR3dDwavGIq2C6A+c+vqLBMINiZUGWYK06d9g8y7HzqwxhAv74C2WsEz7pYYZO3B"
                    "mUsQz3xn1FAVEMUwN7EdEFQ4pRy59CkGwwjBfI3o7S184JLSAIbY+mmJvfyEHkQnlIomP69t"
                    "NJAdWGuxRzr4C3ZAs8B8165nqY0PkDM/frTEg4u0mWvBNduruhvn/khylWVJ1TvyVHwyuaEa"
                    "wO4wzfYq2g5nKIxiPRfkDRf2sh1XFm9O6XcpB2et4f9eyIzAMSzOit7xf7AXlsO9Yh3upfAg"
                    "r7F/AcNLAUhWd7GLAAAAAElFTkSuQmCC"
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWmKAncjntMQBpfqNUH"
                    "ZqK2awjKggIlHh9rHw+qm3fmxHunfncbGx38/PwRHCMmJicAAAA0NTdKSkuSlZhZWFl4d3jo"
                    "6OhnZ2fX19eGh4empqfHx8e3t7iPNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBKYZA1EHBgHK0C4cgTghgAuPEKTnaTi"
                    "kACxTQYPJzQNQGEdg8K1OgRjk9awAAAAQHRSTlP/////////////////////AP//////////"
                    "////////////////////////////////////////////////////ahH+YQAAAyJJREFUeNq1"
                    "ltl62yAQRpU9bYUoO0Kyldh1Xe9t9j3v/1aFASGUTxe5iOciwRIchn+GGWU/wfIvNE/Mvhrb"
                    "orM9cIGc7YPryNlnubyWusTD74iotTaCpGQPvsn+WRtZu7B2ae36+nqezMMSGSU0EkNbahSs"
                    "4gkYdvxxcPAL7I+z32Dfv3UeYFSDs8wtZz2/ccQCOr4DMKvZgBkWVxvt/hYlkoqqKvWbAE8z"
                    "IYyEIU7BxopXgJ3d3Jz5UY5L0zpVImr/KSSpPztS3VGcm+FoVCdkDxaBW5xvt+dhmIsALmE2"
                    "keBo4Z4IVASw8zLx302tUilaMM622wy3YC8FQ5xaEDMYVoKKKERJ9bmezAfAq7/WVj0wsROJ"
                    "A9vzcqlI5YKny+hw3VGp3dOqoQfAo/H7+3jUAzOdBzDRoC2VSEnvJrX+kcTbKnkE4DKAm/F4"
                    "vR6PmwAGtxwDwLLGuIasswLQoFJwL+QH84cQHVh58Ho2bprxbO3BCsCoBXOfECVcFx8he+4y"
                    "gjWSLqQmqJOC8fNsROlo9owTMKsdGIPGdmS45C7LwOUQKZweQ4RTAFh48Hw6Wz0+rmbTuQcL"
                    "ryN2UePGg5mF2lHFErBD2oT22TkEvpxePCyXDxfTywScV9LnGVwKzvLCeSZkkEJ4PrFyF63u"
                    "nRSKO/BkuphvlsvNfDGdODBXbejdahc4XMsWDF4Zfx38taYxA8sE7Fy8Wpw0r0dHr83J4sr9"
                    "DmAhw13mSBMSwJr5UPob7HLMdLWD9MHN3f2ueHl7eyl293dNBy5ljpmtB8pFLYCpX1y0QLt1"
                    "KBEVQrK7INxJcfV0Oiny4+O8mJw+XTkpeCg5dg2ufJ4ZAPM2y0R7g3Hx4UEAUyfx7tDSNhu7"
                    "x+HOiUz9jKKrBgJJAgUtZq+MoMjVeQrO28J1exuHNFYaP3B3GQRNOhTEzYTfpP5YNiklA0Zp"
                    "9KPiVGnEYD9t0nJGfeMQnJcVDEla6C1jyGJ9wb4lwbBG/Y5KZNqadNeaoJniQev3TIF0qVgM"
                    "f7RCRLRUH7v0pwyXGtV86AU3rgwz2m//e/tg2d8n1v4+CvfyGfsfMgiogTfkY5EAAAAASUVO"
                    "RK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwZlKhLZiACyMwUa"
                    "IinKNgG/v8DxxHBfX2D10sQdg8IHZqIILEFMQBoNQGFfqNXQwJzxe2rYj3uToKPMpJzz8/YC"
                    "AgIPGyMTFxkAAABWVVY1Nzjp6ezIyMpoZ2iTlplISUp4eHglJSanp6nW1tiGhoe1tbeYnKHL"
                    "RQErJBvUWAA7MxzTaADdtXramXs5PkDyp2zsiG/e3uHep3tvTBKNOAhbNxBxURPbdwBNKRX0"
                    "uWzxlmzIKQGvxtN+foATUXl7LGIrAAAAQHRSTlP/////////////////////////////////"
                    "AP//////////////////////////////////////////////////GKkOzwAAA19JREFUeNq1"
                    "ltlW2zAQhp1A2SndrKiStVi24ySkpaSsLZD3f6vOIjsyzQUXZM7BMbL0eeaf0cjZd7L8HY2J"
                    "2XtjO3S2Ay6Rs11wkZy9kVuXQUhfbX/mVJAyKFckZAYff74G+wv2C+0P2OHhcbK2EkJbL4Uq"
                    "6uIVdmWliCbLYgPGa/Ht+stPst9oP8gOP20QUxEMztO4XA/8rnosWph24wQ+mH510dq2v6sO"
                    "+tVa1nA1Xgj7YtHv/oljV7VSmtFVCq6aICdk+6PRPt/J0HRzKi9KcNcKQaHCzVEiEbjpViR1"
                    "Q85PU3AZuZPR09Mo3sqSwYWn2YBQ4LZBtBImpg1RG/9rnCqLDdh14LD39LQXOrCjGVfCNgDy"
                    "+oBWSnhdjRGgWeQmgpMT5Rbw4wewxwHYCQt/AHaogXVSaJNLzx6iDr2/RWPylexcHoJvZ3t7"
                    "s9sBWMM8BF85YFpYXkIGo5tlkqw89/iSBoZcD24ieD6bjUaz2TyCG3yGDAKLUE0DllOtunB1"
                    "ryjXh+cg1AZsGTxazObz2YLTJ+0QbFlUX2NZM08SKwoB/xh+WXgNDtni1vvbRRYSsA4Inuaa"
                    "Amw16FxAifBLQZW8yE0FF8gjjUE4sgeXDL5bLh7b9nGxvGMwxduAipi1DgzuAiDoHozRv8Co"
                    "oKGt4GyZXT0/X8FPAoYQp7TpLEZvdW4QrESUQrGsTotY2wMprELw5fLhbv38vL57WF4iWNku"
                    "9bgzIHGQPNGBZZK8lvYy57MQUfcIRhdvHrL5x4uLj/Ps4Qb/j2AvjngvWyEbx+BC6k25FfQb"
                    "69kNyo3A82x8P9k/Odmf3I+zeQrOjRbSUdZaBpe82AhSljLHQhQBgqh7sEIpbsbjy4k8PZWT"
                    "y/H4BqXodgGsqQLVmdEILsuuyhSnD8rEDAZ6sEeJ78+Btl7DO87vUWTPYEMFXxjeGS3mX/i4"
                    "LTBvnNWc+h5y5SoFS4npkxN5dgYXvJUygiFO7pa4l1cYQXJCOe78jG5D0pAJ7L3eYj7GC37A"
                    "ZoH+7g11ypAeIC98cHiLB5foMteBW7XV2r6d85GEfpkgpoMjz6UnEzXVCKbD1Gy1tNHW0Gql"
                    "P9LiFRfWqg3X1q9O6TdZBcGGsvj/gSk1tmH9Ug+O/519sOzuE2t3H4U7+Yz9B88XCj6QBzPw"
                    "AAAAAElFTkSuQmCC"
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWmKAncjntMQBpfqNUH"
                    "ZqK2awjKggIlHh9rHw+qm3fmxHunfncbGx0RHCP9/f0lJSZJSkoAAAA0NjeRlZhYWFlmZmZ3"
                    "d3jo6OiGhoa2t7enqKjY2NjHx8ePNwrLRQErJBuWSQmWVAw7MxzTaADamXvUWADdtXpYNxHe"
                    "p3vbdwCvxtNNKRVzNRETUXnIKQFxURPKNgGQJw1vTBKYZA1EHBgHK0C4cgTghgAuPEKTnaTi"
                    "kACxTQYPJzQNQGEdg8K1OgTRzi4tAAAAQHRSTlP//////////////////////wD/////////"
                    "////////////////////////////////////////////////////4ukumwAAAvJJREFUeNq1"
                    "lld7mzAUhslq0larmiBkSOy6rnfr7P3//1W1WDbN44v4u8ACw8vhTCU/vMAnKhCTz8ZW6OQA"
                    "XE9ODsF15GRfLs9UnuL+/wgEJgOYtskBfJP8tRpYXVpdWV1fX09a92GFtJA5kr3vRBlFiBpE"
                    "2mB3pN+Pjn56/Xb65fXta2MBRpk3liEr1rEbZ5wgDaQABlGR1f95MMtYjzSrn9a5O8IUKUGE"
                    "6dgtEAKRhiFqjPZgbZ0Hvc5vbs7DCuBUV0al/n6BFImfLmp7OU5bDuDSXmiDZeTCi9XqIi6B"
                    "jODUfj4GVHlDobsiEYykHKVdfzOUtV1RgXGyWiW4ArN4q3UiBExj/xLjfhCvHKHIViAV7wPP"
                    "/1jNO2BqIdSBqXtK2Njb4OXRTk52UgTGa13woHh/LwYdMMtBBNPc+5YoJFQIH3E+2hK2iVeD"
                    "0wgui2KxKIoygr1ZjuHBKsM48+kkquDb812LjW4sTkUAL8ZFWRbjRQALD0YVmIeESH25mECh"
                    "ffVCd8D4eTwgZDB+xi0wyxwYex/blfbRwcFkUwexJYl0DZYBPBmN54+P8/FoEsAy+BH7WOsA"
                    "ZhZqV4aFZBN7ga9Glw+z2cPl6KoFBkaFPPMMzgB01kq1lysEd+DhaDpZzmbLyXQ0dGAuqtDL"
                    "ECgbPFWBQ41nejd4mW6DnYmb6Vn5enLyWp5NN+48gqWKtcxRTmkE52yvdPPg8u5+DV/e3l7g"
                    "+v6ubMCpApghQ4WLWgST8HB/gfDGYu5csXn6MoTg9BTA4ZenjXMFjy3HWoVNyDPtwbzuED0l"
                    "TVolzYlz8frY0pZL+47jtXMyCWDYdEmJFHXf2nSenSakO00ofJHvWLe39TKCbaGFhatl79jW"
                    "hLJdUn7QNgmhPSKkNtRwInLE/PvybiZ82Ogto091luIwkkJ72EoEbIQbTTZj7WiSrdHkhynu"
                    "1XZF5algSOH/DdO8O0z331bgNEcZ7x3/2I9/0h3/B9uwHG6LdbhN4UG2sf8AN+GnD3ptBEwA"
                    "AAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEWqWwafNgfZiAAaIilW"
                    "HxTKNgHxxHC/v8D10sQNQGEdg8IHZqIILEFfqNVMQBrQwJyToKPxe2rYj3vMpJzz8/YPGyMC"
                    "AgMAAAATGBvHx8poaGnp6uxWVlY1Nzh3d3iTl5omJidJSUqHh4jX19mnp6m1triWnKHLRQEr"
                    "JBvTaADdtXramXs5PkDUWAA7Mxx+foDyp2zd3eDep3vsiG/IKQFbNxBzNRETUXn0uWxfX2BN"
                    "KRVxURPxlmzbdwCvxtNvTBK6AaH5AAAAQHRSTlP//////////////////////////////wD/"
                    "////////////////////////////////////////////////////0khfawAAAxtJREFUeNq1"
                    "ltl22jAQhg1JsyfdYKrdkjegDaE0LAVKeP+36oxkjE1IDhdhDos4tj9G/2yKfnjrfKAFYvTR"
                    "2C06OgHXk6NTcIkcHclNjQXmsoPXZMJTa2WeyBo5gK++PqP9Q/tN9hft4uKq9mwGoIVjoGQq"
                    "97EdATYBiBkkHbkD+2vfn7/98vaH7Ke3i887RA6W030a0HRWp+ZWjMCmSqQIVv42bx58mX+J"
                    "SyuKapVdVs9rluIndwBiJMjvHVoApPnYq8U5wKgBzhLLut7Ozs/PworZZOtZ5sCgc4gwRMTF"
                    "otqKyZzZ+S9UZngdbEpu93wyOS+XzASwRD8hJ5UVus0JraB8WjJwTcU12Bo43oJtazJp2S04"
                    "9nc8gkgQ5DQqkzpg+HcpmEoI05HNQJpD4OUntGUDHIPAN4Jj0kBg7DXvsOCnNKN0P/e48Xrt"
                    "gVu9Fr4aYM2kBz/GyBQEwwiC8hBMMr4PRsmyCpyU4H6vN5v1ev0SnNA1Yngw2Cy3NkchFJT7"
                    "5da+9tjqdAcWATwb9vr93nAWwKIJFkFTl1JaM7/dcSwP1Ggs98E2Gracaw0jWwNrS+C8o70w"
                    "hUadJe7XX/Pa7JsCXYFNAE8Hw2VRLIeDaQCboGPmo7YFo7sItfodsN0HR4Po8eXlEb9qYEzV"
                    "3BedoB0K3eEEVlDb9dtSCEXgp8F6On95mU/XgycCq+COCZWBgcPgwRbMjgqeUOTiZh31r+/v"
                    "r/vRekO/S7CDRahlASyJA1gyfVS6eXA/aq+6Zzc3Z91VO+rXwR2ugcU+akUAG4hDbzDvF4gi"
                    "KTbt9lOX3d6y7lO7vSEpVCkFepVZn2dcE9iYqkOIeg9+XdLKkcSrB6TN5/gfDysS2QUw92Um"
                    "eaiMArMCwMnjmpByjFH4WJfd3eEHLRkrweSE75ZUy2PaQW1C5SJzyQ5rVCbqbdM5fcBc6YxU"
                    "gMWC/d3RMymzDRep0fO3Gn2hDlpRRTqMJOmzidK6OZoS0OOFSG1zNPlhyg9aHZBiv2duoaHB"
                    "fW+YHn+syHBKWyMPj3/2evyf7MByuiPW6Q6FJznG/ge/VwDjjIb1DAAAAABJRU5ErkJggg=="
                ),
            },
        },
    },
    "Claude": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB79/PwAAADYdVTY"
                    "akcpJyc3Nznid1RlOy/u6edWVld3d3fX19e3t7foqZXHx8dHR0impqb0186FhYbibkjvxrnr"
                    "t6ZmZmeYmJhmQDVbOC3hlHvjnYXchmrUYTyRVEHxzMDgi2+RSzUeHiDUXTYmHx7ugl/3490/"
                    "P0DcfmDfkHbpsJ4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACJ9deOAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAiunTMQAAAs9JREFUeNq1"
                    "ltmW2yAMhk1ZEhYTg+Ol2ZNmlrbv/36VMMbGmZ5Oz5noIrYDfPwSQlB8C1Z8oUXiV2NHdPEE"
                    "biAXz+Aiufgcd1OGB+cftgpdGuO0mJM/BxaEKHxK+UGjMiTaRs3Ayfhu/T3aerdQ9oMQg13I"
                    "JvScN/GEDegHl/iZ0lUySs95j44QgeAa3kuiMl/ATK+1k+F1ST6/0sxez9NYjJ1BsQKRjszA"
                    "PMiMwa3MI5kHWttSejhEdOpAUKdAyRWMqoeoREOVevqscZoMvAPvGfPyyHzDjr+Au9rNekPo"
                    "ShjREWDPNXWJG/MFyWoOXgN4T4+kZd5S6RG8To0IE+C0qCW6XmWChzx8H6czmUMD2HvayqPf"
                    "X8ghBxe8BGElKZ0xIS7ZjOFNj0Knvybw0UsrG38h7YmxDAy5mhIKM5kLFfT1SZ5Kjsgs6Ahm"
                    "J2ZDxnh6tXuWg4PoYH3n8FFHv+sEjjrdGJ0Etvaw/43kN++bCexKV+tOCVFOu0BXPObLuFJV"
                    "Aus8yAA+2Na2CPaN3dME5p2ue+fKTaTOK8IEFilXHsCMne7HxkPfxjbSLkMxxiIEy5Q9j6EY"
                    "45nKU78MBaxX82YvFy/94UTpEqyBFsCcVz3QXYznuB0qMWVgvQDbBraetU3TWsZysMIQkD4V"
                    "Ay5SrvA83cRDuoHik/Wsuf6Ue3vNwFDAYOtJB6WCbPrZOCh6g/Rp8TZDQk5begsiaUuZv96t"
                    "vcPHdjcrYB3UNAmFF8ZV3Wzr6bR8nC/+GEWxwZAdLVUE3nEcIYq6hGomQZ3KipAqsolMvui3"
                    "Fctsdcua31G0qbHs9AWfynkoxy5+Dqm+KMgvN7adGbu9ZA4BDtJWhyDO1ybUBtwyStVDpouH"
                    "owuOpmTLo0m7QFfht8qPQzk/mgz/6NT7h6kFckwNndCy+99TenDJ8b80KIdluK/y4/9pF5bn"
                    "XbGedyl8yjX2D6r3InwGBatjAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQEAAADZdVTY"
                    "a0fr6OoWFhbid1QmJifnuqynp6lVVVbrxbk2NjaVlZbnqZXy2NB3d3hoaGnX19lJSUqIiIq4"
                    "uLribkjFxcjdsaXrzMPhlHvchmrjnYXUYTy9vcDe3uHgi2/cloB/f4HUXTb3493ugl/cknrc"
                    "fmCenqA/P0DpsJ4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAACEiYzAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAiunTMQAAAvBJREFUeNq1"
                    "lumWoyAQhWEAgwKCiLZj9l5nef/3mwIiSJI+p+ecTv3JgvVxuRSF6EcI9I1xIX43dkGjB3AD"
                    "GT2C68noS9y+4r3/bJq7o63gVcVFy1bkr4EHjDv4YBjfjp1shS9RdSyDU5hZpphNmTxBEuQc"
                    "Mff4ZqUMNQnrg4/X8zJNyCYFIZoVwxbjASDYgUIe1F+ijVKVECqir83S76SId51zW5i3wpwB"
                    "pUOMY2yz3iCzPQWrhyC+1GwCra4J2e8v6MWNHmPHvMst6kCPA3193lQgibS43uHoWQ4Jq6fU"
                    "sB01ku4+gLuRyxg8zSfEIcPivis0Wc8N38YJlhWeXRuFUA3gLdmhmhpNmNe/qdc1gQdY9ODw"
                    "iIPZeTHeofhIMBcsKyV7sDGkZjuzndG+BKMJfHUcc1VBnsv/d2mzxCLUi2ivwDvDNJNmRvWB"
                    "0gKMWJcKCvSwqe0CTyV5dllIn8xJYHqgscYMOestLcBRdAhlQ10F3Vl/l3TCKC/BWu+3fz35"
                    "zRiZwYorJ2w3NDyLFsMYJOBUeRkMplQleK9rXfvnjdRbksCNFc4pxReua1OxrcBNqpUbMKWH"
                    "1530xSu1ZPraCjRmxbjiarpYsfg5HtEnVsB+yTc9z4aZ/YGQK3AvgBaP7Nh5k1W5eWwYLl9w"
                    "UTcBrCUcPa2lrDWlBRhOBhYtVkszYMfYIe+UW3tTbqD4oA2V519sq88FGBpY1fRYQavA/M8q"
                    "b1qkp83znSSfeO/rE4gkNaHm/Kr1K/x4kqtGYxlk9F0FB2uw+CWlibR943j1x9KEaAzPvkRq"
                    "yZMdfUaLHPeT9ENO9ccBZk2WeW51Ktrx84YWsflg5RUC2185P4FCY9UU7VjFzs9+8zsNmT3T"
                    "p1XQ54J7xIqBPBG6TItYHnyJF4ez1sWrpL25u+afq5jLMeF7WBM2flx1t3yFpApv0J1b7/MI"
                    "EruIZFdjk8hc2//nLR0J8bjdGeiUb8PqpS+u/4e9sDzuFetxL4UPeY39B99RJjSIes4NAAAA"
                    "AElFTkSuQmCC"
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB78/PwAAADYdVTY"
                    "akcqJyg3Nznt6efid1TW1tZlOy9WVld3d3fIyMi3t7eFhYboqZVnZ2empqb0185HR0jibkiY"
                    "mJnrt6bvxrlmQDVbOC3hlHvjnYXchmrUYTweHiCRSzXxzMDgi2+RVEHUXTb3490mHx7ugl/f"
                    "kHbcfmDpsJ4/P0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABywyf3AAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAiunTMQAAAuRJREFUeNq1"
                    "lul22yAQhUXZjEGIRZFUb/GSre37v18HhDEoyWl6Tjw/LMnAp8tlGNT8iNF8YyTid2Ov6OYO"
                    "3Ehu7sEN5OZr3FbHC6UftjKlhXCKleSvgRlCMlw5/6BRCpSilQU4B92sf6ZYbxbKHhESoQtq"
                    "Y8+yiWZsRL+bEr1gvMqB8aXuYRFiAdzBvUaymgsEH5RyPN4uyZdXXMXr5TY2eCeCWBaQDhVg"
                    "GmUmc3vxnkwjbRwx3u0SOndAQScLknsY1c2upAgq1e2xC6+pwBuYPSGe74mfyP4NuKtN0Rus"
                    "0zDCImCXmuzMtaIVEHImyxK8BvAW79FIvMHcB/A6NwYYg0mzjoep95VgnXQnraKa0Az2Ho98"
                    "77cntKvBDdUgTCPthIi+VG+E6zCDVfFXCd57bvjkT2g8EFKBIVdzQoVMpkxGO4YkjyL6CKvw"
                    "mCahajA5EBPn5PHZbEkNjqJjDNaFS9Sd9UswJOWGm90pwMbstr8D+dn76QZ22nXKSsb0bReo"
                    "nqZ8ua5UJ62d71RtMoB3ZjRjAPvJbHEGU6u6wTndJmpZEW5gK1X/CZiQw9N+8tB3MhM3Syuu"
                    "XkSzhB5oskKl/SdVysJhaQWs1/RsTifP/e6A8RKsgBbBlPYD0F3ys037jzLErxnYLcBmgq1n"
                    "zDSNhpAaLIMFaMjFgLKcK+ER8juotql2sKXig/FkOv/iW3OuwFDAYOtxBxNG7VCMg6IXpSNN"
                    "IUTwtkWoKq2bBxCJR0z8+cmYJ3h42BQFDLRoDoUXxvW22HpqXr5uXleb/yiWhswR2ClyRaCW"
                    "hhGs6TS4yWFzyaoIyaZ6kagX/bgiVayOVfOfIEd0oewMDb2V81iOXXqcU31RkF+O5KEIcnyp"
                    "JgQ4sFJFE8u1ibUBcgM2SDdnOnt3dMHRlGN5NCkX6TL+9vVxyMujSdCPTr1/hFwgr6mhMprb"
                    "/z2l5yk5+kmDdKEMD319/N/tg+V+n1j3+yi8y2fsX62/I/4FGF70AAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQHr6eoAAADZ"
                    "dVTYa0cXFxfid1QmJifnuqynp6lVVVbW1tk2NjeVlZfrxblnZ2nnqZXy2NBJSUp2dneJiYrG"
                    "xsnibki3t7ndsaXrzMPhlHvchmrjnYXUYTze3uHgi2+9vcDUXTbcloB/f4Hugl/3493ckno/"
                    "P0CenqDcfmBfX2DpsJ4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACPd1NsAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAzy1gbgAAAw9JREFUeNq1"
                    "ltmWoyAQhqFGDArIqu2YpdP7bO//fFOAUUn6ouecSd0YJXz+/FQVkm8pyH+Mmfi/sRc0uQM3"
                    "kck9uJFMvsSVNZfx2rafjnaC1zUXHWzIXwP3lAa8AKW3Y4+mpnPUAVbwEm7SS0yunDzgJJzz"
                    "RHnEtxtlpF2wMfh4/V6wVbVboqosFMOG0h4h1KNCntTP0WWgEkLlX9dm2beqiDe7zu3wvTXl"
                    "gJRAgFNqVr1JZjZX9kl8qdklWtNU1fE4oy9uSEo9RJc7ElCPR1fkuqlIEkAgBZGeZs/W0Lh6"
                    "xhwcmNPs8IHcnb6M4b/5QDjOMFSGQpOJXEJEncIQiOSwBTcI3lcH0jBnK4j6d802J2iPi+49"
                    "HWkye11MdAguSZEsKyVHsHNVAwe3n8ixBJMBffWcclXjPL8+D2mzgMz7JkiyDD0rwQcHFrSb"
                    "SHNirAATCEtCoR4YupA2X83yRtoOsqPDvAhRgtmJ5Rxz1dnuWQHOonNimaQv6V70Bw6PdbYI"
                    "R3kJtva4/xPJ787pFay48sKEvuWraNGPSQJdMs8HMf8Syest+Ggb28T/O2331QJujfBeKX7h"
                    "+m5JtgUMxBiBKf4pmLHT80HH5NVWg722goyrYlpzNcxWiLn+gphr7sYK3C/9bqfJgTuequoK"
                    "LAXScsmOIZqstpuHraTF2pa5T23zJoGtxtKzVuvGMlaAsTKo6Ki6NAN4ykV8STcfH3Yp3bqb"
                    "dEPFJ+uYPv+CvT0XYGxgdSupwgVT/mMzb8jSAZta2451LBC+rfjo6wOKrJqKufOztc9486A3"
                    "jcYAzpChxsLqDX1dpom8fT5b/3t5sGlCLEdkz7G05MGMcUZHPI8vkf06NZZDvJMpCERu/Vi0"
                    "45cdK2L3AeURgttf+/gCRca6Ldqxyrfwk3/SkOGFPWyCvRTcJ6qwKUY4mtFhl1xGXnP+eWN8"
                    "Pkq6m7Nr+r6JqRwT2MPQhpD6Ql8MdduTia5r+dphmiSGjISrsUGsXCP/8ZTOhFxunwwEFduw"
                    "epXF8X+3D5b7fWLd76PwLp+xfwG2DSe+Y1/v1AAAAABJRU5ErkJggg=="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB78/PwAAADYdVTY"
                    "akcpJic3Nzjid1Tu6ehlOy/X19d3d3hWVldHR0jGxse3t7empqboqZX0186GhoaYmJjibkhm"
                    "Zmfvxrnrt6ZbOC1mQDXhlHvchmrjnYUeHiDUYTyRSzXgi2+RVEHxzMDUXTYmHx73493ugl/p"
                    "sJ4/P0DcfmDfkHYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAB7tD9iAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAiunTMQAAAtRJREFUeNq1"
                    "ltmW2yAMhk1ZQgCbxR7bafZlZrq8//tVYILBk55Oz5noxgvw+ZeQhKtvwaovtEj8auwdXT2B"
                    "G8jVM7ieXH2O2+hwofThKJNaCCNZTv4cmCGk/JXzB4NKoGiNysDJ6Gb9Pdp6s1B2QEj4KagJ"
                    "M/MhmrAB/cElesF4lQzjSzmjR4h5cAv3GqnCFzDRSWl4uF2SL++4sPfLvNbHTnixzCMNysA0"
                    "yIzBrcVHMg20ccR4v4/oNAF5ncxLrmFVO0Ulmlcp58fWf6YAb8B7QhzfETeQ3W/grjbZbAid"
                    "hhU9AnauqS+5E1nlL9YA3uIdGomzmDsPXqdBD2PgNGu5d70uBOv5qaY+ZLlDE9g5PPKd217R"
                    "vgRXVIMwjbQRIsSl+GKmtlm+msA7xy0f3BWNR0IKMORqSiifyZSpEI4ulwe70E1OyBJMjsSG"
                    "jHH4bLekBAfRwbre+EvQXegXiB+qkDS6BFu73/7w5DfnhhlstGllrxjTcxXImsZ8CTsVCr2P"
                    "wZdlkAG8t6MdPdgNdosTmPay7YzRTaTmHSGCPRJ21VQPwYQcb7vBwdzBDtwuQ3GPRQiW0B2N"
                    "oZATn0G4D1WMu16A8fBmr1fH3f6I8RIsgRbAlNYdj/LMVA5TWdcpA9sF2A5QetYOw2gJKcHK"
                    "hwB1qRlQlnKFxrQzc+9gS8VH68hw/sW39lyAoYFB6XEDrQI1XbbucAf24Els3FNCziX9AiLx"
                    "iIk736y9wcPLJkvQHnoah8YL6+o+Kz15r2B6WLxIoshknh0tdQTaU7+CVa2GzefguCqakKqK"
                    "D4ly008rUtjqVAz/9KJF653uKjq387BvJj5Oqb5oyK8n8pIZOb0WDoVqDbnVFHsT9s2XjFLt"
                    "lOnsw9EFR1Oy5dEkTaCHMsu7m5fJ86NJ0Een3j9MLZD31JAJzfv/PaUnlwz9y4Ayvg13dXn8"
                    "P+2H5Xm/WM/7KXzKb+wf9cojJRkpwZ0AAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQHs6esAAADZ"
                    "dVTYa0cWFhYmJifid1Tnuqynp6o2NjbrxbnnqZWVlZdVVVby2NBnZ2jW1tlISEl3d3iIiIrG"
                    "xsjibkjdsaW4uLrrzMPhlHvchmrjnYXe3uHUYTx/f4Hgi2/cloC9vcDUXTaenqDugl/3493c"
                    "knrcfmA/P0DpsJ4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC6nQ9OAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAF4ELWwAAAwFJREFUeNq1"
                    "lgtz2yAMx4GBic3b2O7svJq+t33/7zcBjjFuetfdLbprfYmiH38kIYx+REP/0Wbi/8Ze0egO"
                    "3EhG9+AGMvoWt614G55Nc9OrDa8qbjRZkb8H7jHu4EEw/uw7uQrPVnUkgxezk1xssmXwAEEQ"
                    "84x5wDcrZahZsMH4uF2XKEp3i1GqSOF2GPcAwR4U8qh+Np2kCmNEQm+Tpd5oYW8qx2pYt8Kc"
                    "AKVDhGPsst4oU59iqvsovtRsI62uKT0eZ/Q1Gy3GnoQsa9SBHg/62lxUIJllc63HKWfZJOye"
                    "MUsOzEp2+ADuTl598Gs+IA4RDrddockF7jqhgdytwTWA9/SAamYVJUH/rl73BO5h073HI47J"
                    "zpsJGbpS+wGdqo3kALaW1uRg9xM6lmA0QF49x1xUEOfz911RLB8WCSL0BnywRBFpJ1SfGSvA"
                    "iHRLQ4EeMugu8sRano5rtpvkAJidWeoxSy9qzwpwEh1NuNhXUfdaP/QNHtJivAQrddz/CeR3"
                    "a2UGCy68cV3f8Cza9GOUgEPnETQ08M/NyTfgL8FHVas6/N5KtacLuHHGeyH4lev10mwJHHb/"
                    "iKCqAt0EM3Z+OcjQvFJJorapQGNWjCsuhjkVJqVVi5SIG6mAesl3NU2W2OOZ0g24NUBLR3bs"
                    "QpLFqniv0ZHal+CibyJYSTh6SklZK8YKMJwMbDQW12FAntOETO1G4nPuZ/2p3UDxWVkmL7/I"
                    "Xl0KMAywqmmxgFGB+e9V3JCkx8qlRIRJkk98yOsDiKQ1ZfbyotQLfHiQq0HjCES0XQU91Tso"
                    "1dVMKh9BzVB8sRpCLFlgz7aM5MGNIUIjz8MibZ9DQ91g1ag1/AVudSrG8dOOFbb7IOUVAuWv"
                    "fFhAoLFqinEs0uQnr/zGQCZP7GFl7KngPmNBQJ6JB0wjkp2P6eLwzvl0lehPd9f0c2VT6TOh"
                    "5k3sqHE13fIVsnR4g27cel9blNglJNn4BpO5rv3HWzoRxPCFoxNhDIvHtrj+7/bCcr9XrPu9"
                    "FN7lNfYv4ScmtFPSCq0AAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB79/f0oJSYAAADY"
                    "dVTYakfid1Q3NzlWVldlOy9HR0i2trfw6ujoqZXHx8dlZWWnp6f01852dnfibkjX19eFhYXr"
                    "t6bvxrmYmJgeHiBmQDVbOC3hlHvchmrjnYXUYTyRVEGRSzXgi2/xzMDUXTbugl8mHx73493p"
                    "sJ7cfmDfkHY/P0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADhzxgjAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAF4ELWwAAAqdJREFUeNq1"
                    "ltmWoyAQhqEaIhFQMG6Z7Et3z/L+7zdVxBgwmZ5cdOqcaBT8/Clqkb0FY99oA/G7sVc0ewE3"
                    "kNkruERmz3ELHU4AD0fzJU2ALCY/B844V3Q25tGo4honZAXPY/BosJr/GGy+mihbcl7TFF6E"
                    "mfEQaJVzx6qSFTwr9d2SYC/EbDQh9umMEhURuMH/+qL+NnJ9E+D78yl4/y4Se9/f/EC+q0ls"
                    "RkjHIzAoaCKWqvBGKjjQ2laI7XZAjxM46SQnspwDay5eGawOa4is5zq5XuHqpfRmI30nN7+R"
                    "O1tdxxBVADqgwGUjm/NIU2kma1dGJddzBK/FhrfSW2E8gee3aEJYBvhrDB5jN6o7l7Jleo/A"
                    "3ovWbPz6xLcpGHee80pz7eqax2vPE/XD3LDNCXjjjTWdP/F2J2UCplC9GkUyZJctAq3vFRdu"
                    "oljupDX0qBdHu5YpOIgO1peOThfd2cNsmoCt3a5/EvnT++4Gdto1VamyTI+iiyq/eKDg6p5b"
                    "cZeCt7a1LYF9Z9diBENZNb1zuhioVRYHW/l/sJS786bz+GhnO2Onrrj6Ijir1j087Qrcr+7T"
                    "nk7e+O1OiCm4QloAA+Q90oOqpXb3m6fdFGw7TD1ru661UqZgRS7gfdAMISyeDzdUvLNedsdf"
                    "Zm2PCRhqSj3jsFTwoo+fe5ggyYauFihStEL649naM14sVlExxj3Shqka4yAv49R7kNLpDZAX"
                    "I/Zg4yKhBPJxxhrsEpggeRRkd0XITYoQO8xkYrNDMvyHRNcNld+eQQFR2ay+LJvs4yAXkcnD"
                    "R7ojPdXPKiRFNm0BXxb60JpGm7amygW6CseJE4uSWlOp6JVV1Jqe7/7qkZyomdZpM30eDA7+"
                    "MYKlg9p/nrb/l32wvO4T63UfhS/5jP0LZNAiFmkhZPYAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQHs6esAAADZ"
                    "dVTYa0cnJyfid1TnuqzX19poaGqIiIrFxch2dncYGBjrxbmoqKrnqZXy2NA1NTWXl5lJSUpW"
                    "Vle4uLribkjdsaXrzMPhlHvjnYXchmrd3eDUYTx+foDgi2/cloDUXTa9vcD3493cknrugl/p"
                    "sJ5fX2DcfmA/P0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACT6WvMAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAF4ELWwAAAsRJREFUeNq1"
                    "lumWoyAQhSkGiKDI5tJj9l5nef/3m0ITFZOe9kenzsnxGPSry7UoID/6IN8YF+J3Y69o8gBu"
                    "TyaP4EYyWcWVpZDxau29UZoXUgja5nRGXgfOAXQkANzBEgUCH6hKyAmdwGP4zozR+fTlAqDE"
                    "d15BRJKlM2or1AmEbJREcCOKm7yOsc0YjDmaDCtAOcRCTchB9OpnI7I99H4VmP+0BLsPlsSH"
                    "G4eqCvOWICipEEkFgBrHWm3rKQ1VjdWpZt/Tsoyx3e6CvrohAWoaXa6IBktqdEWOpDLOYR6h"
                    "d2sKg7Pn3NM994bv35G7MdcxRKF1Al1WIDUAtHMjNKHph9QJOEPwlu1Jxr1jNOrfZPOagNzi"
                    "r4YWerOvHH2SS08LrekC7D3L6N5vO7JLwaRAX2sBIpQlzOeOGW+qAAXYBXjvqaPGdyQ7cp6A"
                    "URpcA8uOFpW2Q0Jxq1gEmYL5kQ815tnZbXkCHkT3EVSIl173oaJ3Vmm1sMK53fZX/O/NezOB"
                    "gwh1o3RuxSS6ydv+5XJWeGM0EFLwzmUui89747ZsBFvV1HUI4sqtq2mmn4BFAub8+LI3sXiN"
                    "M9QtrcClC5NkEYrbWX9iBX4v8+a6zlO/OzK2AMsGaT3UtjqaHFZ/PM6dwaXnnDGZ4zwB48qA"
                    "poIwkPH+dVC1qtxQ8dF5bs5/6NadE7AtobQSArYKEH9xaU8J9VcLxDyhSJYx7s8vzr3gzZOZ"
                    "aVAUe4/UJfaGXM07mJr34HtL2vMhIvsSY0suVBu/dUVqEZPIfKqFr5sQfd7wJDbvNN1CGqyu"
                    "eqjSthxdbJWtp95BdGPVwnX6zJ9mwZ8T7isEiv2z6TVWhNK00Rf/afSEdD9n0S2KHrs82hDt"
                    "a2fd7bI15RAOv5UU6da0ajPtJeoBSVdupuuPFUUoPkmL2395u/0/7MDyuCPW4w6FDznG/gNZ"
                    "EybOof9KUQAAAABJRU5ErkJggg=="
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB78/PwAAADYdVTY"
                    "akcqJyjs6Oc2NjhYWFnid1R3d3hlOy/X19eWlpdISEnoqZWGhodmZmf0184eHiDHx8e4uLji"
                    "bkimpqbvxrnrt6ZmQDVbOC3hlHvchmrjnYXUYTyRVEGRSzXxzMDgi2/UXTYmHx73493ugl8/"
                    "P0HfkHbcfmDpsJ4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAArOTMzAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAiunTMQAAAuZJREFUeNq1"
                    "loly2yAQhkWBBWMOCUmRVN92rrbv/34FBOiwp5POxDuTGAv4/LMHq+JHsOIbLRK/G5vQxRO4"
                    "gVw8g+vJxVe5vAVZ0sdzTLRSdoLNyV8EU0BdJSQSj35SomgNn4Gnvbvtz2jb3UoaRW14ovx2"
                    "tZikGRvQd2eiZ4w32TA+L1Z00v+vSwSVrpq5bhZ4UgnRQRiuyecPvLCP8+xHS6TdR4VAj2dH"
                    "1XQULzM6V8t7Mg20YcD4eIzovKAMqxkEobV/IlAd57zKmX6/tFmAd+70hFg4ENuTwy/H3ezi"
                    "lEJcO5DqaNgZvIhilKrMpTST+Ry8deA9PqCBWIPBevA2OZG7Pwd25+VQscYHT5ZZcBsGn8kF"
                    "zhtyDbYWD3Cw+ys6zsBKFhHMZPCtBlTBKFM7faN/RRI6PZrABwsGentFw4mQDPaMAIaW0jZk"
                    "nXOAjl5K8nh65A8hlmByIiZkjMU3sycJjBKYj4AylEuTzl1mcNTZJe9ksDHH/R9Pfre2n8Cq"
                    "9WAafOxGHQfusyzomyKlM1gsnezARzOYwYNtb/Y4g7WPiqN1I1g5qBs1aglmOX/vwIScXg+9"
                    "dWt704PJ4KKBMc9CUXBV1F6tgOiK5E9Kc3auXOHi1b+b69WCPZ7wpNif0u/2gaMtJLCM/kzl"
                    "oNlUMuUKbHpXesb0/WAImcACYi1zJBmLYKliyKIHcrqxu3Rzik/Gkv72G/bmNgOXUFDl7oPK"
                    "Ry2C9bi5dpRuGbwGIViU9IsTiQdM7O3VmFf35SWVNPeiaDPmWRfAPJ1W5PBRunqQfE9G8+xo"
                    "KRr1lPACAQsXWvYiLEGeK5fX5mVDFra55KkqVSuMJY1mHSpcx138ztoHF/LbhbzMjFze5joa"
                    "riuJVLgsZTffp8fGITgvmzBk921tt822ak10bElh2K4kMZi3Jkkfdb1/m0CyrBSCde+pRUZD"
                    "9d9deuxQErX80QTv/DWs9LL9P+2F5XmvWM97KXzKa+xfAB8mBml1FMwAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIAAADZdVTY"
                    "a0fs6eoWFhZoaGk4ODnid1TnuqxUVFWVlZdISEnJycvrxbl3d3mmpqjnqZXy2NAmJibW1tm2"
                    "triGhojibkjdsaXrzMPhlHvjnYXchmrUYTyenqDe3uHgi28/P0BfX2DcloB+foDUXTa+vsHc"
                    "knrugl/3493psJ7cfmAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABzz/FcAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAUkW4BAAAAwpJREFUeNq1"
                    "lolypCAQhmU5xwsHULPOnfvY93++7QZFcZKtbFWmq5KZRPj4+6Db7Je37AdtJP40dkJnN+B6"
                    "cnYLLpKzb3LbxhFRbT9/VhZOCFeUfEH+JnhLiJKVIAVv+erRWQoymmj4DI5mBx1tsOnmjrga"
                    "PrjC7SrRvY1YNNetJXFD6SYapSYRpkQLv+uKEPkkUXd8UgapqihUQK+jZd5oYm9mIaoiDRwt"
                    "CfGuwpeHRYhAZnn2oe69+FSz9bQ8p3S/H9FTNHjlVwOiANk1ogtSj2lD1Ky/xaUi8VWD94xZ"
                    "fmBWs8MrcDd6fHRPZA+gSr34nQJ8bdEDNInckAU4CohIbpbgHMA7eshyZg3lqH+TT0GU8APg"
                    "EmMgS0FUnYkqKMQ4eIH9GFwuVpIRbC3N+cHuhmy/ACtYh+D7EpgStjaQwVFmE5NVTELhBFKu"
                    "wAfLDdd2yPIjYxGMDA8mbts5LKc2UlSUBzHpoxNFCmZHFmrM0ovZsSuwDICqxbIOPPA7xAS1"
                    "jzrhMJeCjdnvPnD9u7V6BiuH4C5TfuNJQZw5lIjXB+rkGgzuiBS8N7nJEWy12dEI7iGKmLUJ"
                    "DHIB6lQK3sb6vQIzdnw+aCxebTQ3EQx57vylk3ioVFmN4IKMoZji2b1kX4QC8qXfzTBYbvdH"
                    "OitGL/FmQOIgeWQCizR5vO/HLyTGPYKNhqtnjNa5YWwGV+Qh3GVJRF8GMBfqi3Irr8oNFB+N"
                    "Zfryh+/MJQFntSKi9Fk7BXATNtfY65LkcQdOtMsrfQciaU6ZvTwb8wx/3OkYCritW+frrFYI"
                    "bprJ2yKmr+tW/5iaEAuG7NGmJlT7BPE63IwTVAVEcby1eB1CVkOAkSvOSTt+3LDENq9xuRy7"
                    "Jd7lM3qwmFBl6Px+LT+5Txoyf2R3C2OPcycBHUL20N+r2nfKZTVlT2FwVBIHF1llLtjwe2FD"
                    "OvHCSMKzarfq5OVyMvmmej31/mEwQ6EVVw9qPSHgqGLmyvZ/p3SYUIK4hl8/qBuFbVg9tcn4"
                    "v9kLy+1esW73UniT19i/BV4qLP35mXsAAAAASUVORK5CYII="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB78/PwAAADYdVTY"
                    "akcqKCjr6Oc2NjhYWFnW1tbid1R3d3hlOy+WlpdJSUpnZ2eGhofoqZXIyMgeHiC4uLj0187i"
                    "bkimpqbrt6bvxrlmQDVbOC3hlHvjnYXchmrUYTzgi2+RVEHxzMCRSzXUXTYmHx7ugl/3493p"
                    "sJ7cfmDfkHYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABv5A8fAAAAQHRSTlP//wD/////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAA3IK7eQAAAv5JREFUeNq1"
                    "louSmyAUhqXAAUIQUVm1uSd7a9//ActNRZN2tjObM7MbIvDlPzew+BGs+EZLxO/GjujiCdxA"
                    "Lp7B9eTiq1zegqjo4zkmWyE6yXLyF8EUUKelQPLRTwqUrOYZeN672/5Mtt2tpFHUhifKb1eL"
                    "STphA/rOJ3rBeDMZxpfFik74/02FQJe6znWzwAMlZQdhuCZf3vHC3i/Zj1aodB8aQRl9R3p2"
                    "xctMwS3FPZkGWt9jfDgk9LSgCqsZBKGNfyJRk+a8yky/X1ovwDvnPSEWjsQO5PjbcTe7NKUQ"
                    "Lx1IdTTsDFFEKUs6crWohTMeyTwHbx14j4+oJ9ZgsB68HYPI3Z8DO385aFb75IlqEtwm3Umr"
                    "i4ZYg63FPRzt/ooOGViJIoGZCLEtAWmI7peOxsZiiTEZH+XgowUDg72i/kTIBPaMAIaW0jZU"
                    "nQtAmaIU5VFEm7AoqpdLMDkRE3yy+Gz2ZASjEcxjQVShXWKGnN9VasuiSLXRxehkYGMO+09P"
                    "/rB2mMGq9WAaYuxGHQfuJQbJc6YqrlMJymWQHfhgetN7sB3MHk/g0hemo3URrBzUjWq1BGsu"
                    "y7+ACTm9Hgfr1g5mADOBixpinQVFXBWNVyshhUKOpSNTZ6h1KFy+hg9zvVqwhxOeFfs8+90+"
                    "cbSFESxSPOvUf5QhGCuwWoHN4FrPmGHoDSEzWELqZY4EYwksVExl7ODKO8WCT+yu3Jzik7Fk"
                    "OP+CvTln4AoKqtx5oH3WEriMmxtH6XysnTOUCu9FjZLysaVfnEjcY2LPr8a8ui8vY0tzL4rW"
                    "sc66AOajtzKmr4oNoqcH2SFEonl2svEQauaClwhYONCmKMIS5LlieWzeNmRhm9s0pdNW38sh"
                    "mdkNFY7jLn1n7YMD+e1GXjIjt7dcR81LLZAKPSu6fF8Zjx/XIFUdhuz+WtttJ1tdTTReSWHY"
                    "riQxyK8mQR/dev82iUSlFYL13dPICQ36v2/peEMJ1PJHE7zzx7Aql9f/015YnveK9byXwqe8"
                    "xv4BNr0nX2bOnmEAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgLr6eoAAADZ"
                    "dVTYa0cXFxdoaGk4ODnid1RUVFXnuqzW1tlISEnJycuVlZenp6nrxbkmJid3d3jnqZXy2NC1"
                    "tbfibkiGhojdsaXrzMPhlHvchmrjnYXUYTyenqDe3uFfX2Dgi28/P0DUXTZ+foDcloD3493u"
                    "gl/cknrcfmDpsJ6/v8IAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADTHZAyAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAzy1gbgAAAyxJREFUeNq1"
                    "lolymzAQhiUVHRYIkJCIi+/cafv+z9ddCbDAmU46E++MYxyhj39PQX5EI99oI/G7sROa3IEb"
                    "yeQeXCSTL3KrzlFh2s/XGu2EcLphGfmL4JZSJY2gmlVstXSWgo4mOnYFzxYGO9sQlpt76mr4"
                    "Ygq3q4XudsaiuX4tifmi2MxWFH4hTIkK/taGUvkiUfe80iSg0lqlq3W0/HuxsHefiTK0g0dL"
                    "SqOrcHHJQgQyU3CrbRS/1BwirSyL4nAY0VM0mIl3A0KD7BoZmtZj2hAF+lk0UuGtYuGrBe85"
                    "D2zPg+X7D+Bu7Lj0ROUWQEb9IbhTgK8VeoAmkQsPEtFkEtHl4BLAu2JPSh58wVD/ppyCKOED"
                    "4AZjIBtBVU2ESYIxDqB3KgpwT6wkIziEomT7sBvIIQMruA/BTw0wJWztIIMoE6yLyWJkzJuG"
                    "yy18NyvwPjDPbBhIeeR8BiMjgqlre4flVOnJXTXK62lbV00MfJWCk4P5kacaC8Wj3/EbsExB"
                    "NRWCkrvgd4pJ59hZbKeHuSXY+8PuN97/FoK9gpVDcE9UdPCkIM4MSiRSQJ1Md5lOj1c6xjoH"
                    "H3zpSwQH63fFDN5iFIE2gUEuQJ3KwYxIqTvCPgVzfnzeWyxe6y3zMxjy3Memk7hPKlIjWNMx"
                    "FHosnU6PPXcTCsiXffPDEFg4HIurYkw9dgYkDpJHJ7DIk1dDcUBvY9tj6ZkV2FtoPe+tLT3n"
                    "V7Chl9TLkoptk8BMqLzcDKptYrk1N+UGio8+cPv4i+384wJMakVFE7N2SuAuba5x/KBK17dt"
                    "L7BBHDhR5S39ACKLsuDh8dn7Z/jxYOdQQH22LtZZrRDcdZO3OqXPpAa5zP/IhhBPhuzRpiFU"
                    "xwSxOnXGCaoCojh2LbYDgqpohCFXnBfj+HXDF7b5mDtejtMSe/mMHmQnVJNNfnZynwxk9sof"
                    "MuOv10kCOoTcwnw3sWdFXk3kJY0fI/HgoqvMJRt+ZjYsT7x0JMXKcqtJ3uQnUxyqt6fePwzO"
                    "UBjF5qLWJwQ8Sl+5svrfUzqdUIK6jt0u1J3CMaxeqsXxf7cXlvu9Yt3vpfAur7F/AToPK3aM"
                    "itD0AAAAAElFTkSuQmCC"
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB78/PwAAADYdVTY"
                    "akcqJyjs6Oc2Njfid1RZWVllOy/X19eWlpd3d3hISEmHh4dmZmfoqZUeHiDHx8f0186mpqa4"
                    "uLjibkjrt6bvxrlbOC1mQDXhlHvjnYXchmrUYTzxzMCRVEGRSzXgi2/UXTb3490mHx7ugl/c"
                    "fmDpsJ7fkHYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAi2TkxAAAAQHRSTlP//wD/////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAA3IK7eQAAAu9JREFUeNq1"
                    "lgmTmyAUgKXAI4RDUXQ1zZ092v7/H1guCbqZznZm82aSEI7Pd/KsfgSpvlES8buxM7p6AjeQ"
                    "q2dwPbn6KpcPIGr6eI2pQYhesZL8RTAF1DdKIPXokQIl6XgBvp/dbX8m2e5WqlE0hBnpj8vF"
                    "Is3YgP5kE71gvMmC8WWxoxf+u60RNLrpSr1Z4AmpVA9huCZfPvBCPi7FQ2uk3U+DQEfbUXM3"
                    "xauZnKvFZzINtHHE+HBI6LyhDrsZBEVbP6NQm9a8loX+fmu3AO+c9YRYOBI7keMfx93s0pJE"
                    "XDuQ7Gk4GbyIUpSaJTeSeTmxdeA9PqKRWIPBevB2diJ3Hwd29nJoWOeDJ+qs8HCHaPdM5w2x"
                    "BluLRzja/RkdCrAUVQIzEXyrATUQ1dROP1Zo262nIvhowcBkz2g8EZLBnhHAMFA6hKxzDtDJ"
                    "S4V6Lj9kNEItweRETMgYi29mT2YwmsE8JkQdyiVGyNldZ4RA4EPaL7zjwcYc9r89+c3a6Q6W"
                    "gwfT4GM36jlwn2VB5RQpWpqhlk524IMZzejBdjJ7nMHaJ6aj9REsHdSNOlmAPdIldF89BBNy"
                    "ej1O1u2dzAQmg6sOYp6FouCyar1mCpIrVOQz5+529vuwAuPpzZzPFuzhhO8a+zj70z5wdIAZ"
                    "HLTqYznEstY5A+sV2Eyu9IyZptEQcgcrSLXMkWAsgYWMoYwV7HOsv98dbK3xyVgy3X7B3twK"
                    "cA0Vle4+aHzUEljHw+0MdI9ON0CHECxK+sUpiUdM7O3VmFf352Uuae6Vol3Msz6A+WytmiuY"
                    "tquJfAmRKJ6dZL6E2nvCKwQsXGjZi7AEea5YXpvXDVnI5pqXmnTU13JwaNGhQtz69J8NDy7k"
                    "9yt5KYRc30s9Oq4bgWSwV/TlOR0bh+K87sKQfW5ru22WVWuisSWF4bBSiUHZmgR91PX+LQqJ"
                    "upE5/FlaldHQ/HeXjh1KoIE/WuC9v4alXrb/p72wPO8V63kvhU95jf0LAeomfD2KxHYAAAAA"
                    "SUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgLs6esAAADZ"
                    "dVTYa0cWFhZnZ2k4ODnid1TJycvnuqxHR0hUVFWVlZcmJifrxbnW1tinp6nnqZXy2NB4eHm1"
                    "tbeGhofibkjdsaXrzMPhlHuenqDjnYXchmrUYTze3uHgi28/P0B+foDUXTZfX2DcloD3493c"
                    "knrugl/cfmDpsJ6/v8IAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABn0xW0AAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAzy1gbgAAAx9JREFUeNq1"
                    "loly2yAQhoGKw+gAGWSl8p07bd//+boLOkDOdNKZeGdix5L4+PdgV+RHMPKNNhK/GzuhyR24"
                    "gUzuwUUy+SK37QwV1fbze01thDB1wxLyF8FbSpWsBK1Zy1a3LlLQ0UTHFvBsfrCzDT5fvKNG"
                    "wxdTuFxlurczFs3s1pKYK4rNbEXhMmFKtPCpK0rlk0Td850mSlV1rSJ6HS33UWT24RJRFe1g"
                    "a0lpcBX+eUlCBDKbSwh1H8Tnmn2glWVRHI8jeooGq8LTgKhBtkZ0TfWYNkQt+lt8VGS+WvCe"
                    "c89O3Ft+egfuxo63HqnsAVSpP2GlAF9b9ABNIjcNKJK7FFwCeF+cSMm9Kxjq35RTECX8AbjB"
                    "GMhGUKWJqKJCjMMskPWaXMRKMoK9L0p28vuBHBOwgucQ/NgAU8LyDjI4yuyyZFW4SQ+XmhX4"
                    "5Jlj1g+kPHM+g5ERwNRsdwbLqa0nd1UqD+qjik7UOZifeawxX1zdnt+AZQxq1WJZR54IrDEQ"
                    "8EPHzUwOdu64/43Pv3lvF7AyCN4RFRw8KIgzgxLpw6YQFcKI3sIH5DFcA3dEDj660pUI9tbt"
                    "ixncQxQxaxMY5ALAqBmM3j/BVRou3YI5Pz+fLBavdZa5GQwu7sKhk7ipVEQjuKZjKOoY1kbR"
                    "sbZvQgH5sm9uGDzzx3OxKMbU48mAxEHy6AQWSfIO4SzHfDKaxH0EOwtHzzlrS8f5Aq7oSzzL"
                    "koq+iWAm1FJuLHyP9dzclBsoPjvP7fUX27trBiZaUdGErB0iuIuLNQ2RDZmLgWAGnGjTI/0A"
                    "Iouy4P767Nwz/HiwcyhgzdaEOtMKwV03eVvH9EGZ6OxC0oR4NGSPNjUhHQqe6XgyDph/Wo3H"
                    "AvMWs0pC30OuuGTt+HXDM9u8z0dKjt0Sz/IFPUgmVBM7f0QfzCcNmb3yh8T469JJQIeQPfT3"
                    "SodOmVYTeYqDo5I4uOgqc9GGn4kN+cSLIwn30mbVyZt0MoWmejv1/mEwQ6EVVy9qPSFgq3rh"
                    "yvZ/p3ScUIKajt3e0J3CNqye2mz83+2F5X6vWPd7KbzLa+xffXEqW6d1U3gAAAAASUVORK5C"
                    "YII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUcHB79/f0AAADYdVQo"
                    "JibYakdYWFlISEnt6eg2Njfid1RlOy+WlpdlZWa3t7ceHiB3d3joqZWGhof0187Y2NjHx8eo"
                    "qKjibkjvxrnrt6ZmQDVbOC3hlHvjnYXchmrUYTzgi2+RVEGRSzXxzMDUXTbugl/3490mHx7f"
                    "kHbpsJ7cfmAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAKzjRGAAAAQHRSTlP//wD/////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAA3IK7eQAAAsBJREFUeNq1"
                    "llmDmyAQgJlyCImoCLja3Mfutv3/P7BcGjFpNw+beUjk8HOYE/QjCPpGScTvxo5o9AJuIKNX"
                    "cD0ZPctlFTctebxW1MhWiIg5+Ukw4dDJxkDz8JtQCQBhoZiDb+9u1j+TrDcL1QhUYaYEJ2W2"
                    "SCpWQIcaiSwIWd2diZwwXk2C8Snb0Rn/W7fAZSFtprcEQGkvqWGudJTTO87k/TT7aBv2S+BF"
                    "OrqclhhpZyzWuIlc4UAbBox3u4SeNrTu+AQJHhSt/UwDdVoz0OYKllBl4407PaWa76nu6f6X"
                    "464201ZnxBqVHQkfsf4P2GgIvjg74ywbrx14i/cwUK0w1x68jivCQYQHC/+WdL53zjNJT3Zn"
                    "UlTncx6sNR74Xm8vsJuBS4MSWJhg24KD5NF9hbfRMgpc4C3Ae80V7/UFhgOlE9gzAphXhFQh"
                    "nOTofDe+19h2C43pgSru41Tjo9rSEQwjmMWAaEO62GSnR/kiFmCldtvfnvyhdX8Dl5UHk2Bj"
                    "99QF75CosgV2z22gy8E7NajBg3WvtngCF96MjtZFcOmg7smWMdjk12BKD9d9rx24Vz1XExhZ"
                    "HuMsMFiJaq9tw582hfNX/6EuF8317oBvGnvXN9FRznl8BMccr7p751XdEqx6l3pK9f2gKL2B"
                    "G55ymYERIoFN+XS4OY0PStP++Idv1XEGbjkiJVghvdcSuEgvP0yQzKGbN6ckHjDVx6tSVzd4"
                    "20zV1mlFbIyzLoDZVCEepHQ+QWgUz04yHrK+VckGuPBnvVWeuyLULYoQOq9oJqvzrOLGw/lc"
                    "DoaddShXJZv/lk30eaZvM6Hnz3lkWlZIA2UoliaPhK8KfWhNkyxaE4ktKZaHRSAQK31rksy3"
                    "pmbWmp7u/g2YVpbAyb+aqcmb6fPXCtIaqNjD9k9C+y/y9v+yC8vrrlivuxS+5Br7Fw1zJZYN"
                    "eBp9AAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgPs6usAAADZ"
                    "dVTYa0doaGrIyMonJyjid1TnuqzX19k4ODl3d3gXFxfrxbmHh4lISEnnqZXy2NCnp6mWlphV"
                    "VVa2trjibkjdsaXrzMPhlHvjnYXchmp+foDd3eDUYTxfX2CenqDgi28/P0DcloDUXTbugl/c"
                    "knr3493cfmDpsJ6/v8IAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADmX3dgAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAzy1gbgAAAuBJREFUeNq1"
                    "lteWmzAQhjWKmkEFJMoG9/XWJO//fBmBDQg7G1+s5xz7GAs+Rv80kR+9kW+0M/G7sRc0eQC3"
                    "J5NHcCOZ3MnNjQJbVjfXaCFzpWhb0Bn5TnAFoEVpoaE5XWKJAFUA1BYKQifwaKFzo3UhfbgF"
                    "JSNDA5qu5tRWiR2ovBE5gpv+tvS9nrHVaIz5xDFtc/yWJYDYiej3tCQA8nbfqyUlwG4J9u8s"
                    "sXc/06EEg69GhIlE/HEct2Kq0kzeiaYyqc+hp2UZY5vNGX1Rg6Kf0EaVG3RbRnQD56ephTJ1"
                    "UINKrh3unvNAtzw4vv1A7sqdl15AFAgq9R/cbQkWFc7BjEIYQtNAmgScIXjNtiTjwTMa/V9l"
                    "w0oNAj8IrqMGAmOvJbGDn9Ts8qWm0hi6AIfAMroN645sZmBtaQ9+qZEpIgwjCE2/hkkmbyRm"
                    "tQBvA/XUhY5kB85HcGT0YFBVq1SLQjRw3q9U6tpjpfMUzA98yLHATn7Nr8Bi0LTMY1rb/s59"
                    "TW/UaL2QwvvN+nf87zMEN4G1iuCW6DpePWvUmeJ+i3jVa7O0BnQK3vjMZxEcnF+zEVygZDFq"
                    "FzC6i1ClvwCrBMz54W3rYvI676gfwZiqbV90Ir5UaCIjuIHrXf9DCoyX+/RdF2jYHNjkMTFD"
                    "ZWDgMHhwAdu7g8e5d1h63juXec4ncAnHoZYF2KIewNTqu9MNPT74wN3pF137UwImUoOt+6g9"
                    "D2AD9dAbzP8KxD2hkyxjPJzevH/Diyc3SoFeVarPM6kj2JixQ4h5D75V0oEPFtlnuzQh2ZcZ"
                    "lUNlPGNWAJT03iZEX1c8sdUHnTru0C1jLe/jDmYTqhVVWUwY01RioTp95U8z46/TBmkDVhTY"
                    "38v4TG5Tl2Kjl180ekK6nzPr0kgPI4n22RTTOh1NBej9UeQqHU33DVOcodiKy6OGhPvVML3/"
                    "WFHhlFaG3h7/9nr8P+zA8rgj1uMOhQ85xv4FepQqlNpBXjoAAAAASUVORK5CYII="
                ),
            },
        },
    },
    "Codex": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwAAAAmJih0"
                    "dXYeHiDo6ew3NzlVVVbX19dnZ2hwhfdHR0i3t7fHx8empqZqevaFhYaYmJiLmPawqvaxtfnO"
                    "1vkvJ/WMivd5lfhOVvVWaPanmvg5OPWUpflFRfXLy/q3xvrb4/o/P0AjGPWesfY+QvYAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAB98Y2GAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAyrJKCQAAArlJREFUeNq1"
                    "lmm3oyAMhmWgbGoRtU61t+ss//8nThIRwfbcmTnnNh9c4TF5SYLFN7LiCy0Qvxq7oIs3cIlc"
                    "vIOL5CLh7qI9DdzXdOL8JUaJ2hgnVEou/slhxZjGs5QvXmrDgu11Ao7GtRXBrN54tmPM4BC2"
                    "p5HpKx6xhN6GtCtsNsDYItPDMqYQ7OG6ZjqLBYe3QjhJl1uyZRuz61zUzqCzCpGOJWBObgZx"
                    "K/NMDgENt9uw+BwHMPRTocsVzPKzKsHQS7HeevxMvgAEu/UHMoLrZDRIV8MMy4Cd+mQjN+SL"
                    "TyeSwsT96Mk+uoG0WFRGmIKglZcYepU5POfhj+VzJgtoVwiY0B+P3Qda113Ik7h8vIbbmtXO"
                    "GNKlSL9IV2JxdH0UwYemvHTlEW3sZAZepELDTOZqzsc2uqdjIDIVHcGyK8v70JRlg/bYgMlp"
                    "stY6PPkQt4/g4Kdb1FnB43h5XEoin7oIdrXzUDFK1WsViIqHfFlWqopgkYpMUvwax+5xKEdE"
                    "nxq5gLkVvnWu3gdq2hFWsIq58gweyuY+lON9GA7AlhspFi2ovEzd8iDFomdsT+1WCiZPE7ha"
                    "Hg73S9n0W40F0AjMedUC3QU9l3Ko1JqBfgOezufzdZqm0+m0zQqNErA2NgOuYq7wPN3UJt2w"
                    "QPrzd7Dz9fq7Z1mBQL1D6UkHrYLt23QeC66vi7efEzLP0366nn8Cu2FZSSvqSLWExgvzKpuU"
                    "nojjON88WJ2C6KUcmuuEOsikCXHLcYYqfA3dTIJ3OmtCusg+ZJ7apqQVn49p25x7Adwbj+Pa"
                    "gq/tnNqxC7dzqvP/afQccJC2gkRMtzbqDVgyWvs509XT1vXZ1iQc0TUdq3w7lJlD/NWu9xfT"
                    "G+QSrIhoaV/v0p9s/xSSe735Q6wO23Bb5dv/235Y3veL9b6fwrf8xv4Bymgdzfido/UAAAAA"
                    "SUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQHp6e0AAAAW"
                    "FhYmJienp6lVVVaVlZY2NjZ3d3hoaGnX19lwhfdJSUqIiIpqeva4uLrS2PLFxciwqvaLmPax"
                    "tfkvJ/V5lfinmvhWaPaMivdOVvU5OPVFRfWUpfnLy/q3xvq9vcB/f4Hb4/o+QvYjGPWesfY/"
                    "P0CenqAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAByTodzAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAObvB2QAAAqtJREFUeNq1"
                    "lomyoyAQRWkGZFPcomMSs2e2///BaTCCS2YqqXrpSr3kiX24XppG8s0H+cJ4EL8aO6LJB7ie"
                    "TD7BdWTyEjdlPHXfSfJ0VArOGBeSTsivgXMAjV8UYD1WGQaPYJpG8EtRYxLmXIE7fDJRRpKA"
                    "dcHLNz0zADlCwKJC7tU/Qg5SlRBqQCevQ6VElQw4RYomlAOYqNfLlJW3Ovfin2quzudqtTQA"
                    "ljqXJdGox6K+NC4qkkRwJrUweLaM86b1MYfj3bwmHDMMpHqmyTiu/1XW+Fj+3qlRI3e78bFt"
                    "qmVNQI4PnVsowZsdH8Y5NNzizUXL1pI3+32zddE0p0VRoK+WA1cM82y8rsNiiVGoEyHn2W2R"
                    "nZps76JrFpNSHQoK9dBaas9TQZ4ZHyQN5oTcJssuVZFlhYv7qpL5A6yMryuvO+rXQSeO8gW4"
                    "6073U+bJuyZcV1xZYXSe8Cha5KUXCqHyIhhNYXNNv7quubdZ59C7IniRGGGtUnzkWhmKbQJO"
                    "Qq2swVVWXKqsu1RVi+xVMZZRMTCu6ocVo5/llfzDCkJ3PUrN2vZyyorNcpcIpA1bttTOZDVf"
                    "PJrnjx8wqxt/qT8cDse+73e73aIqcGeAkKDGZkCvQ4d8Um5yXW5kc/iOcTgefyz0YgNjSQoK"
                    "WwXw35O8epQeFs91krjjA7k/Hn4iu1hwcYkoZqSa4cbKDdzCkAjLV5aLC5PnxQ+timPvfJha"
                    "UZvSZUhiuZskzWOq2w44a0A4Llu1MUrj3/URgsvPrJtAkZIls3ashs5P//BnDZn+798rKIry"
                    "hO8ycjr5bTg4rDF2OEokWQuexnxMuB6W+IUvJ90tHiGhwhPy1qnnJ9IDcmlVLSLXpG+e0gNh"
                    "2G5PBrRybVjd0tnx/7EXls+9Yn3upfAjr7F/Aa1YHRYoVsM1AAAAAElFTkSuQmCC"
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PsAAAAnJyh1"
                    "dXYeHiDo6ezW1tY3NzlVVVZnZ2jIyMi3t7dwhfeFhYampqZHR0hqevaYmJmwqvaLmPaxtfnO"
                    "1vlOVvV5lfgvJ/VWaPanmviMivc5OPVFRfWUpfnLy/q3xvrb4/ojGPWesfY/P0A+QvYAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABgDRioAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAyrJKCQAAAs5JREFUeNq1"
                    "lumWmzAMhe3a8YbBbAGadTLT9v0fsZIwxCbptD1noh8hJObj6iLJsG8U7AsjEr8au6DZC7hE"
                    "Zq/gIpkl3N0aDwsLTwchnmKM9FoHaVIy+yfBhnOLR6We/Gk1j1HYBLyGsE7GcHajbMe5xiW8"
                    "oJXpX2LFEnqb0o65bIF2LPPDcW4QXMF3z22WC4RqpQyKvm7Jjm/C3a9F7zSKNYgMPAELkhnN"
                    "rfUjOSY0vL0Ni+Z1AUedBiXXcFU1uxIDVcr7aYW3yR8Awd66PQXBbbIarPNwhePATjW5met0"
                    "oSHsTLaZw8Q9dhTHfiAvFpcRZiBpUylMvc4E+6g7atVZQjsm4ffudOqPGH1/JSXr4xMeTj33"
                    "QWvyhaV3hGM7g2XyUwLeN+W1L08YU68y8GIVFQDeyMz12EZ5gosdPIVdTEJmYNWX5W1oyrLB"
                    "eN+ASTRF6wIeSPeq34IhsTbC7E4Knqbr+7Uk8qFfwcGHCjrGGH/vAlmLWC/Lk6qsi/UpU5PJ"
                    "ih/T1L/vywnRh0YtYOFk1Ybgi0hNJ8Id7Kys/wQeyuY2lNNtGPbAVhsrFi+oALRvRbRCxv6z"
                    "MlZhu7WCq8MIUsv9/nYtm27rsQQagYWoW6CH6GcR+08YrpYKrDbg8Xw+X8ZxPBwO26qwaAFv"
                    "12EgzForeAr1japdnB1m0yDd+TvE+XL51fGsQaDfofVUgIR50abX8Vk69wJCo7cF5+qhpbvx"
                    "cv4AdsOzljY0kbyCwQvX1S5pPTmvq9a5JfOWJlGQvVJDcxnRB5UMIeEEXmFY5cFNBc1lsyFk"
                    "WXYj/TA2FT1xpbZjE+MnnusK17VM3Mc5jeMQT+dSF/8z6AXgwEpJJqZbG80GqA1okGqudPOw"
                    "dX22NclAdEufdb4dqkyQeLbr/SXsBrkkK1e0cs936U+2f0opPN/8IdeAY7it8+3/ZS8sr3vF"
                    "et1L4UteY38DJfwer7kevIAAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQHp6e0AAAAX"
                    "FxcmJienp6lVVVbW1tk2NjeVlZdnZ2lJSUp2dndwhfdqevaJiYrGxsnT2PK3t7mLmPawqvax"
                    "tfl5lfgvJ/VOVvVWaPaMivenmvg5OPVFRfWUpfnLy/q3xvrb4/p/f4G9vcAjGPU+QvaenqA/"
                    "P0CesfZfX2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAa+3nvAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAQepjEwAAAsZJREFUeNq1"
                    "lomO4jAMhuNs7qM3dAvDzZ7v/4DrJKUnWjHSYI0G2pCvf/7YTsm3GOQLoyd+NfaBJm/gRjJ5"
                    "BzeQyUtcxbgKn0I8HTWSM8aloRPya+ACwOMHBViPVZZBH8zTEfxSlDgJ59yBB7yYKCNiwIbg"
                    "9Sc9swAFQsChQh7V92ESUEup0zfxOtQYVMmAU6R4QjmAHfVGmclcVUTxTzVX53O12hoAR4PL"
                    "hnjU49AVNW4qkiQlNAZRDpJnyzhvtjHmcPw1LwnHGRaUn2mygUuIZDEsoYHs19yPTYyPplrm"
                    "BBS46MJBDdHscTHBIfpIimjZWvJmv28+QjTNaZEU6KvjwDXDeW687+NmUdLvmyTRMvRsFts8"
                    "OzXZPkTbLB5K/ZBQqIeWxsfN1728GkSpDJT9IuR8bpNllyrPsjzEdZXJvAdrG/VF3YN+z2nF"
                    "kkU4yhfgtj1dT1kk75rhvubaSesLwUfRsqjjkmDIPOdl/01Gr6fxp22b6zZrA3qXD14IK53T"
                    "mj+4zgzJNoApsVZiij8HV1l+qbL2UlVbZK+SsR4VA+O67K2Qff152dfcygpCdx1KzbbbyynL"
                    "N8sqkUhLJVv7YLKebh62EoG1rVKfmuZNBHeHw+HYdd1ut1tkBVYGSAP60QzoPRXxI91cuGli"
                    "upl1upHN4TvG4Xj8udCLDYwJBRoXDPz3ZF6ZpFNsakLULBQIn1b8QO6Ohx/Izhdc3CKKM5Rn"
                    "WFiFhdswJNP2uWT93+HGfL34R6v82AUfplaUtg4zDHE8PEQV49RQDuFKxSA0cNmqjVE6/l8f"
                    "Ibj9zIUHaFIzMWvHOl3SX/xZQ6b/u7yDxqYY4GiGmT78lvLPWevSUWLIWvA05mMSexja4GNf"
                    "KObHwPRkgnEtr5168UE+IZdWlXLkWvXJUzoRUrk9GfA6tGF9U7Pj/20vLO97xXrfS+FbXmP/"
                    "AYyNHhRw0C/gAAAAAElFTkSuQmCC"
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PsAAAAmJih1"
                    "dXYeHiDo6ezX19c3NzhVVVZnZ2hHR0hwhffGxse3t7empqZqevaGhoaYmJiLmPawqvaxtfnO"
                    "1vkvJ/WnmvhOVvVWaPaMivd5lfg5OPWUpflFRfXLy/q3xvrb4/ojGPU/P0A+QvaesfYAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACj0RRzAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAyrJKCQAAAr9JREFUeNq1"
                    "ltu2oyAMhmGgCKjgqU7ttsc5vP8jThLRAu3aM7PWbi5qbcNn8pME2Tcy9oUWiF+NXdHsDVwi"
                    "s3dwkcwi7m6zJ8fa0UWIlxgtXVF4qWMy+6eANecGr0q9+NMUPFhtIvBmwlgZzJossh3nBbrw"
                    "mjzjv8SGJXSe0o7ZxKGwLNHDcq4R3MJ3x02SC7p3UnpFX3Oy5ZnZx1rUrsBgNSI9j8CCwgzi"
                    "lsUzOSQ0Xq/jGvPmwDFOjSGXsKpdVAmGUcrHbYuPSTeAYNdhT0ZwE3mDdA5WWA7sOCabchey"
                    "SRQm7mEgO/QjabGqjDANSetWYeplErB73JUCJYsT2jEJC4aPj/6A1vdnimTbPuHg1nHni4J0"
                    "YfETo2jr/CcC75vq3FcfaHOvEvAqFRpWstBLPXZxeLAL3ZKETMCqr6rb2FRVg3bPwBQ0WWc9"
                    "XijuJP6CK1zgY3UW8Dyf7+eKyMd+A3vnW+gYrd2jC2QpQr3QTlGj2yC+jLMgKX7Pc3/fVzOi"
                    "j41awcLKtvPe1YEaT4QARiTsqmevwWPV3MZqvo3jHtgqk2LVgtqrcJ0IUsiFr0Huxb3LpeDq"
                    "OEGo1X5/O1fNkGssgUZgIcpOhfD80g5LW5dbBbYZeDqdTpdpmo7HY14VBiXg3TYMhN5qRYSy"
                    "84/ZobMGGU7fwU6Xy6+BJw0C/Q6tpzyMCl538boVaCGTMLiXgkzrdJgupx/AbnjS0pomklMw"
                    "eGFdaaPWk6uf2GU/REMIsldqbC4T6qCiISSswBWatQ42X0HiJhlChiUPKp7GpqIdXz7jsYn2"
                    "E++LFv06Jh7jnPbNh9ul1MX/DHpB3Uq1VfP4aKN9w5Yxpl0qXT8dXZ8dTdITndosnm4YpkoC"
                    "Eq9Ovb+YyZBrsnJDK/v6lP7k+KeU/OvDH3L1OIa7Mj3+3/bC8r5XrPe9FL7lNfYPCg4eFqlR"
                    "T7sAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQHp6u0AAAAW"
                    "FhYmJic2Njanp6qVlZdVVVZnZ2jW1tlISElwhfd3d3iIiIrGxshqevbT2PGwqvaLmPaxtfm4"
                    "uLp5lfiMivdWaPZOVvUvJ/Wnmvg5OPVFRfWUpfm3xvrLy/p/f4G9vcDb4/qenqAjGPU+Qvae"
                    "sfY/P0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACsZ6PSAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAObvB2QAAArpJREFUeNq1"
                    "lomSmzAMhi3XN8YcIbChm2PPtu//gpVswpl2sjMbTQYmGH3+Lcky7Ec09o02EL8be0WzB3Aj"
                    "mT2CS2R2FzcTMqO71jdHjZVCSGv4jHwfuAAIeOMA27HaCRhMBD6B77ISndDnDSTh9UwZ0yOW"
                    "TLZfjJkDKBACHhXKqH4wk6Qqa1VC6/uhxqBKAZIjJTAuAdykN8o0dQx1EcXf1Fy/vNSb1AB4"
                    "TlE2LKAej/qyKalIsmNkMg8pZmt72e2jLeH4tiyZRA8HWVhocsSdvcqJHLbc512056Ze1wQU"
                    "uOjCQwsx2NNiKEJXalGyWtyQvHt6ap7Jmua8KgqMq5cglUA/Pz0Pi2R5moREmKX3vsrPTf5E"
                    "1jWrSXkYCwr18NKEyFNzeSbOma2Cg75Nnl/qKs8rsvdNJcsBrFysq6h7rh/rBso0mVyBu+78"
                    "fs4j+dCMz5VU3rpQaDmJtkUbhQJVHmelxosbgm9xfKnpd9c17/u8I/ShGleonfVeKXnlejMW"
                    "WwLT6l8ZZlWx2+A6ry513l3qeo/sTTG2k2IQUpVDKGwKq1EpEDdCwfihR6n5fn8559VuvUss"
                    "0tKWbQMFWc2S9xEHUvlyWNRNfNQfj8dT3/eHw2FVFbgzwBpQ12bA31KHTOXG432oZ7MtN7Y7"
                    "/kQ7nk6/VnqxgQmdgcJWAfLPzK9M0mPmUiCok0w7fiT3p+MnsqsVF1PE0SMLAmuqcJiqq9mU"
                    "Ps50uXiwXC/+eF2deorDPBSla8nDMC9pkqyYXClvOOvgzjhxxaaNcT5dt0cIpl94mkCxVuhF"
                    "O1ap8/MPeash8//9fQPFUZ6NG8zMJ39NB4d3zqejxLCt4LktxyzlXMeKamfdbTpCxgrX7Eun"
                    "XpwoJOQ6VKWduC774imdCKr8x0BQ1IbVa7Y4/h/2wfK4T6zHfRQ+5DP2L5JKHWzHC63NAAAA"
                    "AElFTkSuQmCC"
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwAAAAlJSce"
                    "HiB0dHVVVVZmZmc3Nznp6u5HR0i2trfX19dwhffHx8enp6dqevaFhYWwqvaLmPaxtfnO1vmY"
                    "mJhOVvWnmvh5lfgvJ/WMivdWaPY5OPVFRfWUpfnLy/q3xvrb4/ojGPU/P0CesfY+QvYAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADZoVNuAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAyrJKCQAAApdJREFUeNq1"
                    "lmt3mzAMhq0J3zDYXAKUNZem3fb/f+IkE1LjZF0+NDonEILz8Pq1JSF+xBDfGBfid2NXtHgC"
                    "N5LFM7hMFgm3uMbNQO/iCfEupix4AJqULB4SbAAUn629d1eBowHGQ5mCr4FKy0tolSkrACoe"
                    "Aj6OTG+hUyUEIbXwYLTLp1QIXUESlRYbPzQpYnBL392i/vPO+iSk55f5dDRkoT99YO8qFmsY"
                    "GSABo8I2YSmJ2WRx0Tu8vQ2r5usAYJ1soigBRbu4cokqziGJEVzmP8dbt4sR4VdZhPJIBnia"
                    "FrEBEk3aZnNXVm0djtyXLsZLP0QvVpcZZpA+raVjaqO6sVQU6W+FkPSH7vW1f+Ho+xNdys/l"
                    "Q0eXDlyoKkjnXm7UX8bGZU7Bu6Y+9fUrx9zbDXi1ioN3MpplidC5W8U+bBXbvq7PQ1PXDcd7"
                    "Bo6iY4w68GnRbe5mUw6e59P7qY7kfX8FBxdayhhj3FW0l+XigN/s6EtICFsrfs9z/76rZ0bv"
                    "G7uCUct2DMH5C1WadLPpB8BD3ZyHej4Pw47YNrNi9cLGPe5GfNgKsPuJpNa73flUN13usSRa"
                    "BCOWI9GjqsKF28VzIQdPh8PhOE3Tfr/Pd4ViC2CMmjFui0e3GydId/hJcTge/3SwSRDKd0o9"
                    "G6hUgB+T/91PEHWT0t10PHwQu4FNSptYkZwVqqJ9UOo09e6kdJkXIZq9tUNznNgHmxQh1Mge"
                    "G9FSl6AEKZNNdlOEQlaE2AsbV3w5QraRfvF11fK4UaDHpGzKL8vmfwo9Eo7qp4xJYXJFXxb6"
                    "r1uTDJGu4jEz0WtuTVrxI2XSmh7v/uqenKSZVttm+lj7j9oC/uORVDq4/Zfb9v+0F5bnvWI9"
                    "76XwKa+xfwGJaB0hyQSZqAAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQHq6u4AAAAn"
                    "JydoaGrX19qIiIrFxch2dncYGBioqKo1NTVwhfeXl5lJSUpWVldqevbT2PG4uLqLmPawqvax"
                    "tfmnmvhOVvVWaPaMivcvJ/V5lfg5OPVFRfWUpfl+foDLy/q3xvq9vcDb4/o/P0BfX2AjGPWe"
                    "sfY+QvYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC0fdokAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAObvB2QAAAoBJREFUeNq1"
                    "loly4yAMhpEWsDlsbHBSp+scTdM93v8FV5A0PrPjzjSaSTwG/En8CAH7kYx9o92I3439RLMn"
                    "cBOZPYMbyWwVV2ou49OYpV7Mcsk5hgwH5HXgDEBFAsAClgngNKDUkDHswassB9D0zTvwSDI4"
                    "oAYuTsBlJSSBK55/UTMBFA4z4BjzPEU/6JHBJ71y8n/6ArQsKTINHFlJSOQA4t4XlHG9GxSV"
                    "UYsx+7c3P1s4AIdR5ZIpMMyRKvJO0nEOQ7NJram9bbbJxnBCkXScVBYgFQCEoRCK4Xgh1QL3"
                    "ZZPspfHTnIDM0M9BgCT2J0ed5GydlcJJ0+b1tXmJ1jTHyWDS1XHgVmsYzp08zhSlACbJvq2L"
                    "Y1O8RuuaiVOMClyN0g7zUpmrQz6PmNtxGzZFcfZ1UdTRLvPxN7AVNj5S3L7EhV06acSm646X"
                    "Y5HIu6ZfZm5dJVRmeB90lYX0sR4k3t0qsOOGP13XXLZFF9G7+u7ViMo5a/kn15X9TB+AJwnn"
                    "i/rsi+7s/ZbYszmGPmLQ3OZLs16UguGupVCL7fZ8LOrNdHRFtAQ1QUWR7erFY9ju9/tD27a7"
                    "3W6SFbQzoCrBXsn0/n6Nal26sc3+J9n+cPg7iddo0EaCpVIB/IO2du9QrdkgbNMe9r+JXc9i"
                    "EEi1RypNtSETwwomhjX4wZamfkRfH9qow9BpLkJc65I5Hp3IrM+FdUUIsf+fHyEVZZe7ZmnQ"
                    "dxWDMK6vHUxVRuTzgB+/voNFqp9VirEcOY+FPv9voceRTZKeqjzJEOULg+p2O5oysP6XkHx8"
                    "NK069ZIjdUXiysN0/bUitw9Oynj86/nx/7QLy/OuWM+7FD7lGvsPricdWfFxLqoAAAAASUVO"
                    "RK5CYII="
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx37+/sAAAB1dXYe"
                    "HiAnJyjo6OtXV1g2NjjX19dnZ2iWlpdISElwhfeGhofHx8dqeva4uLimpqaLmPawqvaxtfnO"
                    "1vl5lfinmvgvJ/VOVvVWaPaMivc5OPVFRfWUpfm3xvrLy/rb4/ojGPWesfY/P0E+QvYAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABNbrVtAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAyrJKCQAAAs1JREFUeNq1"
                    "ltly3CAQRSEMzapdIzkaz2Jn+f9PDDSLkDRx5SHTVXZhCY5vX2ha5BsG+Y8Rif8bm9DkBVwk"
                    "k1dwPZkU3FOOJ1NFB6rmzzGSdUr1TJZk8m+COdBeM0XZs3+paIxGFOB1rdAshhY7aZx2+MT4"
                    "5Wbzkmcsovc5nYjeTFCalH70CifVFLTVTalbhumGsR5wuCdrugtdiKqpxSlgQ+7rS44yo7lW"
                    "HckxoenjY0qa84QaZ0tAoZgHoykdr7LQ76c22w1A2Md4xkB42ghDhXUg03NciS6mlzpzOc9k"
                    "sXEYuW8jxtswoRenYKJwPw7s8hWgZeM3T9VZcIeDX8kCl7kqwcyRxvf34c3HMNxRCYKNIhEs"
                    "FXprgWoIMq2bFvxlSej6KIPPbXUfqncf8wAZ7BkIho7zDk+dS89Gl5I8kR4RKE33YBiq6jG1"
                    "VdX6+MxgmsAiAGoslyblXWdw1Nknd1bwPN8/7xWSL8NqRefBHD12o16A8KcM9a07ZTOYlSaj"
                    "FT/nefg8V7NHX1pIYOt3xdH6ADYO6kaN2YJlPr9H8FS1j6maH9N0duwMJg2Ec4ZFIQw5ebUM"
                    "ohXJT87z6dxZQeGyOKnV+fy4V+2YrfBZ+tV+43gHCayin6kcrFxLpt6Bl+v1eluW5XK5FKfC"
                    "iYu1LKiSMoKVSWXFt8dN7o6bL5Dx+t3F9Xb7PdK1QEgNhBt3H2i/axFsw+KTm9ZvN69xCg8l"
                    "PS636w/HbmlZ0sKL4k04Zz2CRcqW5Xmc7x6sl5DLHmBqb4v3AdZL6LQeeHcoJV5o2UXYgjxX"
                    "Ha5NwAs1/C6vTZ2qFUJJ06JD4XXcx79ld7w2v7zomes51r03+Ej15UIbGgcTom5wKI9t7e+t"
                    "iYeWhMNuJ0nCRhB/1vW+DkZVrQ2FQ1djGQ36eZf+sv37DqVoJ569EL2/ho3dtv+XfbC87hPr"
                    "dR+FL/mM/QPkgR+MGs64XAAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgLp6u0AAAAW"
                    "FhZoaGk4ODlUVFWVlZdISEnJyct3d3mmpqgmJibW1tlwhfe2trhqevaGhojS2PKwqvaLmPax"
                    "tflOVvUvJ/WMivdWaPZ5lfinmvg5OPWenqBFRfWUpfm3xvo/P0DLy/rb4/p+foBfX2C+vsE+"
                    "QvaesfYjGPUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAD9h0VDAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAQepjEwAAAshJREFUeNq1"
                    "lomS4iAQhtMsEAIhd8xqPMdjdvf9H3C7IebUKq0au0ZHiXz5+Ru6E/xyEfxgdMSfxt7RwQe4"
                    "jhx8gkvk4EVuYizwKH58TYWWcxsqNiK/CI4BpIg4hCxhs0ul4NAFN2wAvxQZWI3/mKTpcqI7"
                    "7rEUNnvPMskTfNcRgLgK0t1fUV6qDEPp0fEb2DgCg3IFgFsqfriNLEKZqnRWF078Q83l+VzO"
                    "x1jkfo2IEGVrQoegu7QRatCf0E85W3LP1crFBP4FokBQJL/dTI5rTWgFFIK4Pgt4KyQS2Sy5"
                    "28rFth6RFQh8IViRB0JxkDrgkVdIPjiBRWcu4w8kV+t1vaWo6+M4b8yBvxQyBU41mMFOpumT"
                    "Fd6F4h1ATbmrPD3W6ZqirYebEsOBwcaZpe2U9BTZy0NPin4R4TRHdZqeyjxNc4rLEiw8IEpo"
                    "W3sertt7Qto7nXgzOwO37fFyTB15Uw9WWAJngXQT9xJ9ZrhFnD5UJ+ZgXA6fWvG3bevLKm0J"
                    "vcl7Lwp0kbJ2B6NchFo5Bcf9/l2CyzQ/lWl7KssVsnsw5jlzh07QkJCBJnAInRV3P7Pv4IkV"
                    "Ads0KDVdrU7HNK+GceNPBiYOkwd3MJ8mjxVF9wF633tws9vtDk3TbDab0a7APX/zZ1kAL5QH"
                    "My6fbDe13G5BtfuNsTsc/lTj4QgCLYErl7W9Bxs/WVOtmySPWVxEMj95VXPY/UN2Phk1VBhi"
                    "6/aZlgQ25r7asE9fls0GRlbgHyvzQ0M+jKzQLkFM+5Oxx12BLnbX6Tj4rHoEcfmyjLHhfRyi"
                    "q5Z0lktawahDKV/5/cy9fVSQ2dOvqIOLAut7pF2lnOymq28ckaDGBQ8yR1LHMa30viXRoLaz"
                    "Sq7GnckV1be6HvZQLMXRTS47hA4Hrkje7dK+Q3Gw5kF/0EZSGZbXZNL+P/bA8rlHrM89FH7k"
                    "MfY/9pYgE1fswjkAAAAASUVORK5CYII="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx37+/sAAAB1dXYe"
                    "HiAnJyno6OvW1tZXV1hnZ2g2NjiWlpdJSUqGhodwhffIyMi4uLhqevampqaLmPawqvaxtfnO"
                    "1vmMivdWaPYvJ/V5lfhOVvWnmvg5OPWUpflFRfXLy/q3xvrb4/qesfY+QvYjGPUAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAbUd2BAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA9MBYfgAAAuVJREFUeNq1"
                    "lm1zmzAMx+0Zy488E5pBkzTd9v2/4izZgCFpby8W3fWOYPnXv2RJhv0gY//REvF/Yxc0ewGX"
                    "yOwVXCSzjFus9sRVN6BK+RxjRaNUJ2xOZv8mWALvjFBcPPuXiierdQbe9mojkhl9kCZ5Q28c"
                    "bne7RbliCX2MqWBm56AMy/PRKXIqORhv6ly3JXdwQnRAj0ey4QczmaiSe3IBH2PfFiXJTMn1"
                    "6pGcAho/PsZF8+pQkrcFEkpxCL6Egyoz/eha7w+AYB/DiYzgy0E4rn0AuU7STsrismgi16ha"
                    "BdORrHcZJu77QPbej5SLIiZRh78ADvFqMLbGw1PlKrhJupPWELnKwSK8H97e+ne0vr+SEgI7"
                    "xRLYKsqtB24ghu+Dm12KJeZkeZWBT2117as3tKmHFYwMAkMjZUNVF8LzKUtRnuSyIKeoXuzA"
                    "0FfVfWyrqkX7XMF8Aet4FiW1SzyhEHeZ2pKxVBtdzE4Onqbr57Ui8rnfUtEgWFKOw1OnQaNE"
                    "krydVKlNKkGRJ5lS8Wua+s9TNSH63MIC9liYgdZFsAvQ8FS7Pdho4b8Cj1V7H6vpPo6nwF7B"
                    "rIZYZ6RIO1agWgEpFSL1nxapM9wxFRzOc5BanU73a9UOayrwnHE3HpxsYAGrlM869Z+0HJYK"
                    "LA/g+XK53OZ5Pp/PWVUEcamXNVfWJrByS1tROBiUpZjsodywQYbLz2CX2+33wLcGCbuYdGEe"
                    "GDy1BPZxcxHcOsx1CEZKhVHUPCnPW3qYb5c/gd3yvKU1ipJ1rLOOwHqJVkS/cp1bYt/SNIRC"
                    "9ABje5sxD7ANoWIr+FCUlgbamkXYg5CrHsYmUMsDHMemSVuxl+kwsxuKxnGXftvmcWx+O+hF"
                    "uHN8WHf0SnX5Rh/HT2iQsqZH+3itfX01yXgl0WNzkGRhJ0g+u/W+N8FVaRyHh1tNrGgwz2/p"
                    "b69/vKEUb/SzBd3hGHZ+f/2/7IPldZ9Yr/sofMln7F+iKSB6Qu8xOgAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgLp6e0AAAAX"
                    "FxdoaGk4ODlUVFXW1tlISEmVlZfJycunp6kmJid3d3hwhfe1tbdqevbT2PKGhoiwqvaLmPax"
                    "tflOVvUvJ/WMivdWaPZ5lfinmvg5OPVFRfWUpfmenqDLy/o/P0BfX2C3xvrb4/p+foAjGPU+"
                    "Qvaesfa/v8IAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACI29iyAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAQepjEwAAAudJREFUeNq1"
                    "lglzojAUx5Ns7pBwy1br0Wq7x/f/gPteAhqEnakz9Y1VGuDHP++E/IhGvtFG4ndjJzR5AjeS"
                    "yTO4SCZf5OrgKTfF+jmrPOdeWZaRvwguKJXCcKqYZnenGsHpaDywG/hLVlHv4IdJvF3OdBdX"
                    "LJqvHnOZ5Bq+naFUfArUfT1jE1AqJdNR8QC2MDSAXEFp3CocnDMXgczkXN1G8auam7e35n6N"
                    "mXg1IBTIdshQ1I1hQxToZ9GIxks5W3Lfuk20GfydihZARv4leCeHvWrcAZpALjyIRxNJRFhy"
                    "X7tor31GtlTAH4At+kBYTqUj3CTB6AfQOyUFbI+vSO5eXvpXtL4/5nFjEfxugSng1gARRJlg"
                    "IQaLkTFuCg5b+LVz7qYuj335grbrbw9FRgRTX1Qe00mrabtylFfRwmkbHa+Tc/IY9WV5aeqy"
                    "rNE+lmCRnGo0gtJ2Yd/JJ8GzhrfTw/wdeLc7fhzLSN72N1d4BFdExg2eJPiZQYpECqgT6SoT"
                    "1Hikoq9z+7Pb9R+bcofobX31RYteBNoEBrkA9TIHMyKECoStg5uyvjTl7tI0G2BfwRDnKhad"
                    "wCUhiUOwoqMr1Jg6QY01t3AFYdsBpJabzeVY1t1tPaTKgMBB8OgE5nnwHCQH1DaWPaaeuQMP"
                    "+/3+MAzDdrvNsoIYek61LChvbQIzLvN0M6jWxnSzy3Qj3f4n2P5w+N3ly4YSJym3MWqnBA7p"
                    "ZoftB1X6qigqjgXiYRP6vvK64bD/Bex6thowPwsf88xJBIcw7Val8JlUIOfrwtwV8GFNfRjQ"
                    "D5krXAwQc6kyTpAV4MXxPJYDgnQ0wpDLl22M3b5zE2O3xFpucAfZhLJZ52cnv9aQ2X//BR1c"
                    "tNDfTaxZPsumz9R+jMDBRVciR8aeOtm806eRFDPL33Vym0+m2FQfmnowQ6EVm7NcTginblyh"
                    "H53SaUJx6sPKfHBBYhuWn3o2/p/2wvK8V6znvRQ+5TX2H9D1IPpTJlLWAAAAAElFTkSuQmCC"
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx37+/sAAAAeHiB1"
                    "dXYnJyjo6OtXV1jX19c2NjdnZ2iWlpdISElwhfeHh4fHx8dqeva4uLimpqawqvaLmPaxtfnO"
                    "1vmMivdWaPYvJ/V5lfhOVvWnmvg5OPWUpflFRfXLy/q3xvrb4/qesfY+QvYjGPUAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABXxKOEAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA9MBYfgAAAtVJREFUeNq1"
                    "lttymzAQhqWis8RJYOxCbMdp+/6v2NVKgMDU04t4Z5LBIH38exTkBxr5RkvE78bOaPIGLpLJ"
                    "O7iBTDJusdjBUtkKVfFjjGatUp7pnEz+TzAX1BumKDt6paLJapmB173SsGRG7qRx2uIdG7bb"
                    "zUO+YBG996kgZrNAGZLHwytcVFFhnKlz3Tout4x5gZd7sqE7M5moijpcIlz0fX3IUWYKrlPP"
                    "5OTQ8Pk5zJqXBRWu1gKFoh+Mzu4ElZn+sLTeJgBhn90JDeFzIiyVDkDWc9yJUZwfmi03kuUm"
                    "wsj96NA++gFjUcQgSvgDMPgrhdF1SJ6qFsHtinHwTvBc5WAGpO587j+C9f0NlSDYKpLAWmFs"
                    "naBGRJkOlulMbb2/heBTU9768hxs7MUCDgwEi5bzFqsO3HMpSpk8qA8bnWAbsOjL8jE0ZdkE"
                    "+1rAdAbLmIsK2yVmCPyuFoiiImzweXQieBxvX7cSyZd+DUUbwBxjDFdeChmqDCWnTPHcDZZ7"
                    "gaH4NY7916kcA/rSiBnsQmECzUewBShc1TYDByQUtCfH4KFsHkM5PobhBOwFTGoR6wybQlpS"
                    "BGVMpFCwyNcQ7mKO+zYUVFwmkFqeTo9b2XRLKEKew+6QON6KGYyqfGyH2NZuqcBqB56u1+t9"
                    "mqbL5ZJVBYhLvSyp0jqBlZ3biqey8+vs0LsG6a4/wa73+++Org1CKkG4hXlgQtYS2MXNxQyE"
                    "V6cJUIPCp5bupvv1D7Abmre0DKJ4HevMI1jO3rJ5HS92N7IhBN4LMTT3KcRBrEOoWAseilLj"
                    "QFuiKLagwFVPY1PgQI3/87Fp0tbQyxjQ7ITCvPn0W7fPY/PloGdw5jh4bvGW8vlGFw8OJmVV"
                    "46V+Ptb+fTTxeCThZbuTpMVGED869V4bo6oydkn/6ixb0MIcn9Ivj/9wQinayqMH0ocxbN32"
                    "+H/bB8v7PrHe91H4ls/Yv9I5H8dM+MaGAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgLq6u0AAAAW"
                    "FhZnZ2k4ODnJyctHR0hUVFWVlZcmJifW1tinp6l4eHlwhfdqeva1tbfT2PGGhoewqvaLmPax"
                    "tflOVvUvJ/WMivdWaPZ5lfinmvg5OPWenqBFRfWUpfm3xvrLy/p+foA/P0Db4/pfX2A+Qvae"
                    "sfa/v8IjGPUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC2OPIKAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAQepjEwAAAtdJREFUeNq1"
                    "lolyozAMQJHXFzZXOMI2NFfTdo///8CVZBJMYGeamUaTQCLwQ5clkh8syTfKSPxu7BWdPIHL"
                    "5OQZXCInX+RmhQdp8/VrLvVS+tSJiPxFcA6glZWQikzcXaqVhFFkISbwl2QD3uBJaFquZ3bn"
                    "NyyJ3zwWMi0zPBoLoC6K7L5dccFUnaY6oPMHsLmFAs1VAOwq/viMQoRmuppD3bDxqzbXb2/1"
                    "vU5YvhsRKZptCJ2CGdNGqMn+jG6VYsl9a7csM/g7qAZBVv/hlRJ9zcgDEkXceyOKJfe1ZXnt"
                    "IrIDhV8EO4qBchK0SaQNFlIcbgaKxiS1XDG5fXnpXkm67hTnTTD43SFT4fICMziaWcySZekh"
                    "DarcnLutylNXvpAM3fRQYjAYfL7xVE5ZenVXx+ZhfdjgRDrPUVeW57oqy4rkYwlWIag2o7IO"
                    "PMmscT3+MeFh/g48DKePU8nkXTeFwhN4k2h28KAxzgJLpOGHYlQSkZgcD5hH1qE7ch6K38PQ"
                    "fWzLgdC76uZhg1GkrF3BaC4CvL6ByfsLaoFVK+C6rM51OZzreotsEbm44U2nSKV0YgicwhiK"
                    "NITVaRhrexGKROx6NLXcbs+nsmonfRF2BiYOkwdXsIySd+C9HPIpIIr7CO73+/2x7/vdbhdV"
                    "BVbRZ9jLCmTjAlhIPZWb4PNYz25Zbkm7/4myPx5/tbHaQmI0SMdZOwRwERYb4Mhy5kIghEcn"
                    "svud1/bH/V9kVzNtQWtyz3VmNIGL4uptGtKHZWJminko8CPq6thTHKJQGC54YcLOOFD+wY7X"
                    "KW8hq7xCEFcu25iYjrGosVvSXq7Jg2hCudD5w8qDX2vI4r9/0Q6pGuzv1nCnnFXTJQwOq2hw"
                    "wUrmyNRY5p0+jCRSGn/XyV08mbipPjT1cIZiK7afejkhTDpxVfbolA4TSoIvVuaDKTS1YX3J"
                    "ZuP/aS8sz3vFet5L4VNeY/8BxZogM91S80EAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PsAAAAeHiAm"
                    "Jid1dXZXV1jo6exmZmdISEk2NjeWlpfY2Ni3t7dwhfeGhofHx8dqevaoqKiLmPawqvaxtfnO"
                    "1vmMivdWaPYvJ/V5lfhOVvWnmvg5OPWUpflFRfXLy/q3xvrb4/qesfY+QvYjGPUAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAA9RmYmAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA9MBYfgAAAqxJREFUeNq1"
                    "llubmjAQhjMlJ0IC4SBrYdV12/7/v9jMhENA6uNFnQsVEl6/zBH2g4z9R5uI/xs7o9kbuERm"
                    "7+AimSXcbLGDrdIpW/FjTJ4x7xg3KZm9JpgraLSwII4WJTgDYDzkKXh9VmoxmZY7aRwc3Skg"
                    "WLFZ5E7m0DChmQej3f5MGdMWErOapf5oLG2qQOlc+41uDcAmGs8gFb2sb00noirar0Hl09H1"
                    "ekxeJSwp+O6wPOrtv776WfOyoQpXnBlFQukcAubjWKi2AgtwO/+jfXUnMoLLZWtwYsaKhtOf"
                    "ePxaFrXanV0qufUwcT87ss+2J1+QLBMgBsEGn9Ih9iF4dtIpH1zKsvReFg4H0H18tJ9obXsN"
                    "lyKCC8smsLHk21yBVjF8Ofpon5gh8bbgU11e2/IDbWjVAkYGgZXj3FE66Tn44fpRsW+2ilVb"
                    "lve+Lssa7XsBwwyWMRYVlYuPD5qjejF78DBcv68lkc/t6gqHYE4+Dr8aig6Pkj3IR66AZuuK"
                    "X8PQfp/KAdHnWs3gHN0YaE0EFwEafvkiJpt+AdyX9b0vh3vfnwJ7ATOvYp4RQxYsQ7VCvewK"
                    "UOcxSC1Pp/u1rLvFFRh6EQMVgqdmcKxx1zwGzzV78Hi5XG7jOJ7P5yQrgripliVYYyawLV5M"
                    "NyyQ7vIz2OV2+93BWiCsUowX4I3GqE3gfHr4sEDkQ0l34+3yJ7BrSEtaoiruY541BJZLhzgo"
                    "6XzfhMLplerr24h+UGsTytYuGZLS4FnXzvPQhJpdE0JfKIVC42faNvUkHmuZHJtMqNAlxdO2"
                    "+bTRC/AyD+sF3bLNXtHTRv90NPE4kmJ72CUC9xpHk5Y4mkQyml6e/gJspQtQ/F/D1G6H6avj"
                    "HyeUBScPxz+n8Z9vx//bXlje94r1vpfCt7zG/gX0IR8RtH/sjQAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgPq6u4AAABo"
                    "aGrIyMonJyjX19k4ODl3d3gXFxdISEmHh4mnp6lVVVaWlphwhfe2trhqevbT2PGwqvaLmPax"
                    "tfkvJ/WMivdWaPZOVvV5lfinmvg5OPVFRfV+foCUpfnLy/qenqC3xvpfX2A/P0Db4/o+Qva/"
                    "v8KesfYjGPUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAio6wGAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAQepjEwAAAptJREFUeNq1"
                    "lgdzqzAMgC3VNh7skddAM9qkfeP//8AnmTSM0B65a3QXLhj4rC2LpyDiB+VC/GnsJ1o8gBvI"
                    "4hFcJouV3MhqcD5ZfIaxirTGIsYReSU4ATDSO0gxwjlWSNAxQOYgFjiAV0kBWjHDAIlJxtRC"
                    "ywPoKJURgdPw2h1iXERX5QHkQbLewyMJEBVV8JZSAId7sIkHS8oRwjKR/pyvptjE20F/mSZ2"
                    "Uefq9bW6CQ3pCQV7OSW1FaNTuHyNDvzMNtAL3Nd6E2QCfwcZE8ibP2StB0cejsBeHWEFTgNp"
                    "F7gvdZCXZkTOQNKPwBn7QFLsjRKu1xPtIZpDlLXzvKmfn5sXlqY5juOGAfyeEVMyjCIIaXhG"
                    "SaYWEnOW7ZsyPzb5M0vXDJsyI4BBJ4XWBTkihYu9SutbjbWZrmGT56eqzPOS5eMWLHuf+ojT"
                    "2oWdqwwXanS2iE3XHT+OeSBvm8EVmsGFMBnfvRnyM5K9Md8F38wlBTNd+Nt1zccm7xi9La+7"
                    "xuQyjtonmNQlqDbfgGcJV+Xlqcq7U1VtiH0FU6oWoegkL0kjFINTWLJ60RUCty2pmm82p2Ne"
                    "1sO67SuDAkfBg0+wWx08ge1ut9u3bbvdbkdZITyc+1qW4OKsB6Mz69NN1LtfJLv9/nc9XvYg"
                    "lAGXhai99WALWd8b7JoCEXW73/0jdjlZtaxVokOeKcNga68dQo578BclTc8Rq3Lfsh9GL6tQ"
                    "Zqj6ynijrADweE8TQhyuY5GXbsm1XLEFowlVyMTHI+vSRKpbhb+4xRScjKm/e/4mcnq2LUTq"
                    "20aPE5lGuh9JGLKJ03o6mmIw1VlGejqa1k09mqHUiv3ZwIT73TB9umNCOdAWl8e/ux3/Dzuw"
                    "PO6I9bhD4UOOsf8Bos0gRfSTE0QAAAAASUVORK5CYII="
                ),
            },
        },
    },
    "Cursor": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz8/PwAAAAnJyc4"
                    "NzZ3dnVWVlVISEbX19bIx8dpaGfo5+e3t7eGhoUPDQampqaYl5glJB0eHiA/P0BDQjyjop9A"
                    "PjlgX1qBgHxlZF8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAQSRPJAAAAQHRSTlP//wD/////////////////////////////"
                    "/wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0rNurQAAAkxJREFUeNq1"
                    "lumS2yAQhEXmAHEYNtnN8f4vmhmEEDpcZVet+GFd5lPTDI2mH7VN39ga8buxK3q6gVvJ0x1c"
                    "JU+vcedYDwCXT5Gic5lwJL8GRmNYjyFcPGRnWpt5AL/UfhrjVLCZq+7xEXRsRcObnnljUMFJ"
                    "zqPh3VikuUKUQz19Rn48zj6od07FoiKzGcBQZTZzrXtKfkwI0xFtVCeqZCu90uJKa6qStsuk"
                    "r7kCg2WLH4ebqVoXpYc3wh41+c5t9aJkvpCrXDyJVhjKoDEFHbrdCV7q8Nf6Orcb0MLFv4mI"
                    "vDbckyGKsGhidq76sntjPaNV6HZr+NtXaEUT+Ogz94LSSgbkqq90edwHEnamL2DrY+0aynlm"
                    "ITZw8VkPqY07dXDTmVd3BjBbS7NxbuZhMDnmRJ4R47YKyEKrl3WmbAfT2WTLzNbPzhEOYPCU"
                    "Ss5xbtQxETYw9lq5Bnui2UX7G6dnXtRpcHExy21+9ngqF1b8E66Ak+czmIRWwQC2CD03P9fl"
                    "YHGrwHQKPyk2clHweC4KQlN6GAD2WoF9ueFFuT0+WBWL4I9DGTtdeiFLVJi5DP0k9Bbp2+TN"
                    "S0EeAwj/pC/GQxCJCC+ZFiR4pZ/1w9KjPn0AhxvHVf35eQoh8KA9cEpR0iyIOt6FEE+7F7mX"
                    "Y3PJAhHtksZOmWCL8xrHuV0upf5W1IPgpGypmojHgNIlw5yWSse3NhDKlc711+5nIIxbk4N3"
                    "d71aXPZyP6SODv7dXXrxIz+xDzhrDBe73/5v+2C57xPrvo/CWz5j/wMK1RBJGyiVPAAAAABJ"
                    "RU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgEVEw2lpaUA"
                    "AADo6Ot3dnU3NjRKSkdVVVQmJiZpaWjX19iVlZaIiIm5uLnHx8i+vsDe3uElJB1/f4GioZ9D"
                    "QjyBgHxlZF9APjnAv8BgX1qenqA/P0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACDkR9uAAAAQHRSTlP/////AP//////////////////////////"
                    "//////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAknIXDgAAAqFJREFUeNq1"
                    "lteS5CAMRREsObrDbP7/31wBJrk9Vb1VY710l2UOF0lIJt+KkS+0nfjV2IYmF3ALmVzBzWTy"
                    "FtdR4/IvY6derw2lRns+kd8DBwCBPxzg1XezFHajgg9wt41NtvFlccJF+GQDk/FsdrKOzWbi"
                    "y3EUXUy7xW0BAkJAoUJT1O/mq1SptazoQ7B44+JL1VSX5T36KRiOFEG4AbBDb5Hpb0VbKOJX"
                    "zXHHUmnojv7VzgKAm2CUPRGoR+HWbiQVSZqPY0ON2XSiCjNKK3mv//uZ8G2TiMEVFpxYNNnM"
                    "rcoSHqu8Owcqp6DIzVy5i2ZzTUDAQwcFEUqwx2FyhOor5X1OD5IRDPJ7EELYbBJmMEkYV2XA"
                    "yJwDNZ6LnizdhGYR/qBYPdtWXK9gwkUvKNTDkxfFK7s82w7ienAmsLIlepx7Q1dwFV1M2lJX"
                    "RffQL7pO9JojWCslEnEuaTnA0mDkrQjMDNE6xCIUeuUNMAaFvoC1VjY5J+QEZhb3w4Q2rvLj"
                    "6gww67VyDrZCJBfVT/kSChKHYqBGpj0ULZ5xI5+H4gdyERysfgE7TGclsyhykOWaPB7C/geW"
                    "uilgmYtNuIj4AxhvBmgPsjUDvtUOeVJu/qTc4K6zYhR8Xy8INjDKHEhsFWD+TutSk96TlzsJ"
                    "nfvXszYg+Ts8c5tarjQmxnJc4QTFixUsPPoy3dMX4+HBsQl9fLQm1DtCyuWtUZEyeRMXxtJ8"
                    "HXBXMi4Wirq92zbrTcX0U5U3kKiCLe1Y1s7P/5iThnxs9Gpp9BtIjvJ06TIe72b3POrgUNaq"
                    "Okr8cYTwOI+muI4mnXsYK4mPU3cbI6RXOCMnU+9zKxJFRfKDL+nBte4/p3Ql1Ot24sC6z3Pv"
                    "4Zbxf9kHy3WfWNd9FF7yGfsPnXYWpIYLQc4AAAAASUVORK5CYII="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz8/PwAAAAnJyg4"
                    "NzZ3dnVWVlXW1tZpaGdJSEbIyMjn5+e3t7eGhYUPDQampqaYl5geHiAlJB1DQjyjop8/P0Bg"
                    "X1plZF+BgHxAPjkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABLC85tAAAAQHRSTlP//wD/////////////////////////////"
                    "/wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0rNurQAAAmBJREFUeNq1"
                    "ltmS4yAMRc1oAQwEku6e5f9/dCQgDl7SlVS19eC1OFwuQjD9qjH9YHTiT2Pv6OkEbiVPZ3CV"
                    "PL3GnWO9ARz+RYrWZsKR/BoYjWG9h3Dwk63pMfMAfimuxlgVbOaqe/wFC7ai4U3PnDGo4CTP"
                    "0fBqLBKhEOVQH5+RL5e9D+qdVbGoyGwGMFSZ3Vxvn5IvE8K0RRvViSrZS6vUXOmhKunxmrSb"
                    "IzB49njbfEzVuigtnBH2qMk1rrOzleBG5gO5ysWdaIWhDBpT0KH7leDYdXetdjWgxsW/iYic"
                    "Bq7JEEVYNDFbW31Z9Sj30sA0fFoJ++w9m8Bbn3lJKM1kQK52lC4PDFxlFq59ELQFexdr01D2"
                    "Mwuxg4vLequ6F/0shvTcyM2dFZi9p9lYO/MwmBxzIseI8bEKyEPPl/tMJXauPdHeZM/M3sn8"
                    "Eg5gcJRKznHu1LEiPMCOyX8HdkSzjf4fTs+8qNNgYzPL3v2UlUM9C8uBFX+EK+DkeA8moVUw"
                    "gC9Cz93Pua8/QBPuGZh2xU+SjWwUPO6TgtCUpRgALrmir5Lfqtr12rFtfbmxKhbBt00aW116"
                    "IcuAzVyGdlL0qnQTQcKqt7Mx4aAA4e/0ybgpRCJCtMQghVfaeTcsPWrTl9q8uuXDQRH6+NgV"
                    "IXCgLXBKUdwMsrh4VYR4WnVkXy6bGl8qxyYtO2WCRzmv5Tj315bqb5V6EJxYSdVE3BYoyQ1Z"
                    "IKllOr61gVCudK5Xv56BMG5NFt7d9Wpy+cP9kBZ0cO/u0s2P/MQ+4KxluPj19n/ageW8I9Z5"
                    "h8JTjrH/ARB5EWCCoAwKAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIVEw6lpaXo"
                    "6OsAAABWVVRKSUc3NjV2dnXX19hpaGgnJiaVlZfHx8i4t7iIiIm+vsDe3uElJB2ioZ9/f4FD"
                    "QjxlZF8/P0DAv8BfX2BAPjmenqCBgHxgX1oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADLK2+QAAAAQHRSTlP//////wD/////////////////////////"
                    "////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA464ouQAAAsRJREFUeNq1"
                    "ltmSGyEMRREQsdN4xp7s+f/PzAV6gbZT5VSN9WK7ZQ5XQhItvjQTn2gr8bOxG1q8gNvI4hXc"
                    "ShZPcb2Mvn4q9dCbOEoZOemB/By4EDl8aKJ731uQtJp0+gDvdlGDXfS0OGMRnlwoVrwanWrH"
                    "VovLXThGTsZ+cgeiAggZKIxN/WqpAy2z7d9OydIbF9K6mV1WSvBLihoUJ3QkCofeJrMn15cm"
                    "fta8rFhpo1zR37dYiLAJspyEgx6Drf1xqCCxFroZwqaesyGiDouGjX3v3/eY8O+YRcSKQN5N"
                    "mkLlCsF9RUDgRGOi6hE0uZVrV9FqrAkqCLoYWqgl+wimZkhvRdFSNksGmOzv4pwL1SyNYJGR"
                    "VxMp2noG5nju2mFpsZ4bi5Yy5GxWbK7bVppnsNBuLyjo0Tm55rWrvIVU9onyGgSfwSa07Gmd"
                    "opzBXXQvrND0Nd27fhf1m+wpgjeewWyMy8L7zPYA24jMB1dUPERzWVpwtFeecbx+45brE5jZ"
                    "hOy9swNYBeyHA924Jh2ts4G1CIFR4v8GB+eyX8xPe5cKsRyKSUab11Tw2n+O1557lIqv4AJc"
                    "At+BPY6zk9XiapLteHgYJQq97fucGuumgW0tNucX4E9gdAZxIrsNA33pTbyVm6kPUyu39KDc"
                    "6J2rYgh+nxsEA0wqTxYBU/w2rMtdusZQUwpTQbRJIsf5de0DyP4p1zqmppZGzwWNFd5JNFYJ"
                    "dNuXcT8+01P/Y3/wYAh9fGxDaJ8IuZY3Q6iJdRNfjqW1Heov36w2FkS9PTs2+7jA8UtTN7BQ"
                    "oaZxbPtP/Ss+GMjnQW+mQX8hi6FY4UhGQm/unluvPxOC6VdJOl8hehmvpmW+mhgzDGlwbS6U"
                    "yZXGm4mGI3/qMm0SXUfqky/zwQ3+P2/pTujt9sCBuodYe/PT9f+yF5bXvWK97qXwJa+xfwE1"
                    "qhe1Af2hNwAAAABJRU5ErkJggg=="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz8/PwAAAAnJyc3"
                    "NzZ3dnVWVlRISEbX19bHx8dpaGfo6Oi3t7eGhoampqYPDQaXl5geHiAlJB2jop9DQjw/P0Bg"
                    "X1plZF+BgHxAPjkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAJPff0AAAAQHRSTlP//wD/////////////////////////////"
                    "/wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0rNurQAAAlJJREFUeNq1"
                    "ltmS4yAMRWEkxE6cXmb5/x8dSRAH20lVUtXmxSvHVxdJ2PzSYX5wDOJPY29ocwJXyeYMrpDN"
                    "a9xY9ADw8Cm6EkJzOJNfA6O1JMecHzykYMeINIFfGldrgwi2UXXPj2DFKhre9CxZiwKufF4s"
                    "bWLhERbnWtbTZ+TL5eiDeBdELAqy2QkMKnOY68NT8sUgmD3aik4UyZ5n1e7KGKLS3S+rfOYR"
                    "GDx5/NjdrGpd4RnJMnvWlLbcTqYHcoWLB9ECQw4aa5bQ/UZwmd4DsWwOqHPxb3XOJRm4JUNh"
                    "YcWWFoL6svnipDbub43XPvNImkx7n2lNKMlkQFI7llker8LSg3B7sE9Fp+bluLJQBnhJTQ6q"
                    "e6M/2Hw1mjRlDybvXbQhRJqCaaVVlwix3KvAeRj5oiulhZ6G+e5osicin2IIDicwJFeX1koc"
                    "1LkjDLAgeVWbeQ5OzsVQ/D80z7zQZQilmxW6n7JebPfVDN8PVvxhLoNroiPYMU3BAH7JQ17r"
                    "5dDL2q8ZWA/Nj5PNhcJ4PCaFQ7uszQBwzRUYadfuvWM/+/JBopgFf+zSOEjp5catwsZlmne9"
                    "ARNHMhp3T8h9A8Lf9ZNw14hYROKelrnx8jyfptJztwqG6+7Goaq/vg5NCBLIDDS18OJnDpw2"
                    "TYjM5kPh5bYp41tEhypBLwbu7VzXrY3LnupvtXrQatXcitu18aNkiGrPdHxrA3FN6Vpmc3cT"
                    "mXnemgK8u+tpcvmH+6Fb0Tm9u0t3P9oT+4CatOHFb7f/035YzvvFOu+n8JTf2P9+BRDLTtMv"
                    "qAAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIVEw2lpaUA"
                    "AADp6exKSUc3NjVVVVR3dnUmJiZpaGfX19iWlZfHx8iIiIi5uLm+vsDe3uF/f4ElJB2ioZ9D"
                    "QjyenqCBgHxlZF9APjlgX1rAv8A/P0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADW83YWAAAAQHRSTlP/////AP//////////////////////////"
                    "//////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAknIXDgAAArRJREFUeNq1"
                    "ltty4yAMhhEsiDPOqd3j+7/mCmQbcJKZdKbRRd2xwsePJCSLH83EN9pK/G7shhZv4DayeAe3"
                    "ksVL3Ch9rE+lHnqT9VJ6m/RAfg1cABw9NMC97xwkrCad7uDdTmqwk54WZ1pEb07gK16NTrVj"
                    "q/nl7jhGTmbj5A4AhSBgSKFv6ldLLBWtRUYfgqU3Lv2IzeyyUiK/BK+J4oT2AKHrbTLTuWkr"
                    "TfyseVmxEr1c0X+3swDQJhTlJBzpMbR17EklktX92MAxG07EMG+swSv/v5+Jfu2z8LQiQHST"
                    "plC508EBxkDVFDS5lYuraDXWBBQ6dDGwQAt2P0yN0EYtWZzlQTKBAX8W51yohjCCRaa4Gg8e"
                    "aw5Mf++mZJm6SRWRDorNbdtK2xkstNsLivTonFzz4igvtT3jITgNbEKLntbJyxnMopthaHXV"
                    "dI/6qW4g82b+CLbGuCxizBY7GD1FPriifBdty9KEQq08LbKiP2ENviX/HdhaE3KMDgewCrQf"
                    "JXTjmtSvTgPX018EZRXFc3BwLsfF/Ma7UIilKwbpMa+hsBzWhByIx6H4RVwCl2DvwJHSyWS1"
                    "uBpkHJL32RxcvhqmumlgrMXm4kL4A5huBtgEuDUDfeIOyeWm23Ot5/Sg3OBqq2ISfJ0vCDUw"
                    "qSIgtQrw/4Z1maW3zHEgaieRY/+6cQPCP+VW29R0penOBU0ropNUUyVQqjaznD4tVJ5ePGhC"
                    "Hx9bE9o7Qq7lbUmo8XWTWPrSmjfatWnli0Wizq+2TW4XlH5p6gZIKtTUjpE7v/70DxrysdGb"
                    "qdGfADXJs+2CJbqbu+fCg8OEYHiUpOMI0cs4mpZ5NNmac9Uqahm6Wx8he4Ur8WDqPbcm0TFS"
                    "H3zZdm6IX5zSTMD8xEF1X+feJU7j/20fLO/7xHrfR+FbPmP/A5vdFwmGrKsoAAAAAElFTkSu"
                    "QmCC"
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz9/f0AAAAlJSY4"
                    "NzZWVlVISEd2dnRnZ2a2trbIx8fY19anp6cPDQbp6OiFhYWXl5ceHiAlJB2jop9DQjw/P0Bl"
                    "ZF+BgHxAPjlgX1oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAApzADlAAAAQHRSTlP//wD/////////////////////////////"
                    "/wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0rNurQAAAiZJREFUeNq1"
                    "lotupDAMReO1nTcEpt129/9/dO3AdAPJSFQqlgZGBE5uHD9iflUzP2g78aexT7S5gVvJ5g6u"
                    "ks01bgz1hjgctau+gNSSr4EJgPU+z6NRhiAvUATbgi/ZCpBUMMSqux3CwBaycd5EIB/wmz7z"
                    "okjBRf6HTf3/kedMKPPbV4Rp6v2gvksqlhSZoQEjY2lY7OTBEGsIzRkNqlOdaCygKZtXdkt1"
                    "DY0tEEZgtGzpcXooqIjigCjLFjZAo8nPp7XzzCO5yqVOtMII5VdmubZu5N6la/dsos/inPNq"
                    "dCRjAHABQk4J2rXbg/r93brNR2FvM2w289SF6tM0kpG2LcLQO3SNucsf60P9dF76ncWwgxef"
                    "9bbppmE2dWC21kVIKXIzlkMuzjNR+BIdnd1mjtDvlHGQOzCz9TElRw0YvStLziHuVEdtsPmL"
                    "YO9cTMH+GSxx90XdhhR2Z110xYdwBVw800BH2jYA0S5Cr6rWkPvN65+RBptLQfDUB4UjWKpm"
                    "rGHxnXCbHqyKRfDjFMZJU2/OUiogLu13wwQZpN5Ef8sb06kQSZmQPQqz4SRxYH2beoOUtuMi"
                    "9P7eFSH0qD4mU6RLSILYJsi6IpTHRWhYNtV+q+hUtPwuBiM2ZdNdKZuvDAUn9dPVpKBzC7hU"
                    "6MfmcqVzvR6/xei1NXnWKV3Tmq53f34lZ2+m6dhMr4Mxv3KflA5t//bY/m87sNx3xLrvUHjL"
                    "MfYfaEwPuRjv7ZkAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YVEw0CAgGlpaUA"
                    "AADp6ewnJyd2dnVpaWnX19lLSkc3NjTGxsiIiIlWVlS5uLmXl5jd3eB+foC+vsAlJB2ioZ9D"
                    "Qjw/P0BAPjnAv8BgX1plZF+BgHxfX2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADRCKWUAAAAQHRSTlP/////AP//////////////////////////"
                    "//////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAknIXDgAAAnhJREFUeNq1"
                    "loly4yAMhrFYJEAcztHu/f6vucL4ADtpvTONZjJt6vrTj5B+UN+mUF8YM/GrsQtavYA7kdUr"
                    "uIWsTnHdgK781PrRUxODQzQ5moZ8DhwBqBAAHmCVBZR/GAeIymzgNW66iZvpXg4Ag/zlBlhI"
                    "2jTUjPYK6JJ1Ak4YDov1QxfJdY8tiBylwSt1wUl988Tly4QIkv+6X8/CFWk1/CprHOX5AGjU"
                    "KEiDAHZ9L5P2Wxpjk6Zec56xA+Mwo38uawGQJKWIikArL6nX1Ug+3yvkqVpbjBWGPnm+19/X"
                    "BhCUlA6lyhYcAUBuC0HK9BtJHVhXuYXLs2jd9gRELR8PGaZiLxy6uv1mBSLTg4H/RCKyJRha"
                    "sApSV4+AXPbAd1146AIRoHeK/duSyqQeLNJgCWk7E0bSNSEeFSO7PdjbqXrGjDj04Cp6Cpbl"
                    "wKz7MpoHUzqaPTh5L83iXEi8gRml8paixk10inl6eWgab40EfACn5G1wjrgBayv5ZEMXrh+3"
                    "lT4B4xFsiYLL/gcfSiGjC5tk5HBc9fNS/BaugKNNB7CT7axknakUmf9j87g0G7ks+B1YJgPS"
                    "CFzJ8v1WVZ1sN7inolgE3/sB0TIw2gGLVQB+l9HeEtJnA/JWDYh/xbfE1Yh0o8Ea8R5Hg3hD"
                    "tK2D2daDH430akLv74sJrY4QSnsnEeqxJHFx64XPTegj26zVTNJdvnZp3laTrfabdyhK2oaP"
                    "jd53xbsBG/HPNGkcZTZ7ow/PjV7eyO3RlPsGTeLyUoZSvty423w0ReDLX+uwP5pOHaaTRKpI"
                    "c/IwPX+tCByepJXjfzge/y+7sLzuivW6S+FLrrH/AJ2TF6BtLKCxAAAAAElFTkSuQmCC"
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz8/PwAAAA3NzYn"
                    "Jyh3d3ZYWFdJSUjn5+fY19doaGeWlpeGhobIyMe4t7cPDQYeHiCmpqYlJB1DQjyjop8/P0Fg"
                    "X1plZF+BgHxAPjkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACE5x6AAAAAQHRSTlP//wD/////////////////////////////"
                    "/wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0rNurQAAAmhJREFUeNq1"
                    "ltmS4yAMRc0AQmKLSfds//+jg2TA2HFPJVUdHhKC7ZPLlZC8/JCxfONoxO/GdvTyBq6Ql3dw"
                    "mbw8y4WMFM31NW0zUbF6Jj8JNqiKt6Ts1V+SaiPBBH6Oq7KIDfx4OOg2Ayto85plhfhzjQq9"
                    "82nWrYVHwdqCMv2KfLtd6I3K1S+v0G17V37fCsts5jr6knxbtFnO6Ch3axShK69YtbZrrNIe"
                    "b02XXjpw+n5cCwpcBYVi5ElxUbUo+cE1ZpDhQi5z9VG0rjdqBtf9AnqdOHgUh+Ask5/dguoG"
                    "nbn6T7TWeh56JwdaGliTeOtQedxkuqpv89d2ofvSPtwntqRB2MHMEDBmY7JkXTXANZe6POhL"
                    "vIlzsjvns2BxzlTVwbABohyX1PcdB7jpLN2dCQzO2aSIEkybCZnBRjyuswIInGWib4+UG2D7"
                    "aLIDgJr+RFZPYMdRqbSygUOF1lkKR7Ae+XsN9tYmyu7vbH/CLc/kUEBYVlZrsVnR/TRmZOej"
                    "Fb8rt4Kjn63gXfLTHDiTsYOp+dmPg9P7kYkPxa8mm6Vc8TPYYjvLoEjrBqbQQtYcGOmmL9Lt"
                    "dgdWXAXf51MdcTGh1gPPUWtgtz28Vko5Bi/VrLooQPpX/AR9LETAokza8qwIGPpu7QifMaeF"
                    "86n++DgXoXVPeKtQS0EbLuIRxFx6umz6flpxO9Jq6lBSjkv7rfP/CvLFsLXnOE8qSLGkshxz"
                    "hkulBYhJpvqVBmK2liTTfJKkcW5NZF7teqy7ZnhQeN7qagca/ctdeutQpPJVyA0ULsPBHdv/"
                    "215Y3veK9b6Xwre8xv4DSngSvWN+GPoAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YDAwIVEw2kpKQA"
                    "AADp6Os4ODZpaWhJSUdVVFN3dnbX19jJycqVlZa3t7gmJiaHhoeenqDe3uG+vsA/P0BfX2Al"
                    "JB2ioZ9+foBDQjzAv8BAPjmBgHxlZF9gX1oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAARVg2uAAAAQHRSTlP/////AP//////////////////////////"
                    "////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAnF2oTQAAArtJREFUeNq1"
                    "loly4yAMhhFexA1O0ibtXu//mJUAY0zSne5Mw0ynjoGPXweSxY8yxDeORvxu7IYWT+AWsngG"
                    "l8nii1zvMkizPJ6LNkuZbVQD+YvgBQC1kWCVV9PUWUtoQzq1g/u4LMO4HLcnyIH+KeTteNC9"
                    "dCyPnO7MMfIwrB9nUfLPYAD0m2bdfSZWqWgtVvTkLbVxaVEdZt+8GHC0RAMUU+nhdXARyYzn"
                    "om0t4o+aU8NKzLKhf/Yzy2pCsBWB0RZCs5NRu37PS+XBi7HCsrEGr/V5s+kF9Eogg5eyk997"
                    "toCHZm5VRkcRkcnuGIIil7nYRC+bEzX9ETiyD3SUgEFIUxWyH4rAtTlXyUkygQF/r845zQOh"
                    "g5HWMfglElPTVkcRbDJdD5bdhNIJECfF5rYdpewOZkYBQ15S5nTynYJdHvlk7UbYGWx0iahS"
                    "Mct7sK4A4zmtK4/srj5h7U0nHZZnsDXGBeF9sLiDMTM4CSwbT0h+VpQiRR+p0zOYzJF3YGuN"
                    "Dt47HMAreZGjtoFJLkEzHsFLz9/HYO1c8Mn8GsAU51QunWbrNYrAYAvNFZs/00V87oo/xCXw"
                    "qkdXsJV8MyhwFDzYwPIYPLWu7QG63zsYOdmcT4QfwQZe613WINdYwUriJ+kWH6QbXC0rJsFX"
                    "OIBFoLyOJWqnCnZ1c+BadwieymTEWL9utQDh3/XGZUoeXEG3dcklzwIy2LnNWtvDl9L0Yi5C"
                    "7+9bEdqqVCgBUqHejBNlBXmxXSW+DjWq28UiUeevlk3dqiXf5TNbMHSoWCt/WatO+UFBngu9"
                    "2R3FF1yvVN9NKJVyzCbxVhuH0dy4YIpc3Z7G1pQORXWpLYnfhTxV8jh2JpCLeND1/jGoh1Ip"
                    "Nq84dwg6yu5c7f+3S9cOJSE7dT8RKO+57735Q/t/2gfL8z6xnvdR+JTP2A8LARkmc8X3hwAA"
                    "AABJRU5ErkJggg=="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz8/PwAAAAoKCk3"
                    "NzZ3d3ZYWFfX1tbn5+dJSUhoaGeWlpeGhobIyMi4t7cPDQYeHiCmpqYlJB1DQjyjop9APjmB"
                    "gHxlZF9gX1oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAzPzR/AAAAQHRSTlP//wD/////////////////////////////"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJWM+qwAAAnxJREFUeNq1"
                    "lul24yAMhWGQEHtM21ne/0lHEjjBqduTnNPwoyWO/eXqasHmly7zg2sSfxq7o80LuEo2r+AK"
                    "2TzK9S1QceffATaijrCSHwS7YHtCsnj2k2Tnqn4BP8a1TcVmeTwfdLsrVtHuOcs6yd+t2JBi"
                    "qqtuUF7IiD3o9ivy5XKit9jI/5INccRu0y0UkTnNjfQl+WLAmXt00bshqNBNrqDd5neiEo+3"
                    "1lMvo4/wdryWrY8Myt3pk+qinVlKg5uoEi8/yP5ErnDhKBr4RhAwx+tDgirJo3IV3KbuqZXd"
                    "oHsu/CmImGTBjZzJTDCQehuDTWGEH5kGe7EMT/ZL64of85dt8DewMBQcmnNNq44NiNOlIc9Z"
                    "t+lNQ/19sceYmmLDWql2B/tREEXbZWSI4y6zLY2ZtdGHOwewjxGrJap+CSY3ATv1mHfdBy8S"
                    "VfItU8WnWYL42eTovefyJ0JYwFEKk2l9gDNDeVfzEZw8xu/ACbFSi79X+2sYdaaKfDabqMUw"
                    "rcC9dHB2Rj6x4i9zGVzSaoXkWZ6WxLkWdjBNP+vsPwc27BVYPg0/LjakxvgVjGH2srcEMMGU"
                    "RypHBxcJCjQmOCm3y5sXxSz4be3qEozLPA+SZG2C43h4Y0oXrzkY50iiqHYqPw4g+Fc+PBwH"
                    "kRdRro466wr2e7Q40ldG+afrhZMh9P5+P4S2W8GjDaAD7epiOIKESw+PzTQflV7WZC4nlI7j"
                    "Pj9D+24gnyzkMycmsll7lro51oyMH26QUnULzxwgbhxJum13kiCsRxO5Z0890c0Vnm24D3XD"
                    "Kzqkp0/pcUKRbWcpd77LGM7xePy/7IXlda9Yr3spfMlr7H86jBPVyZMtYgAAAABJRU5ErkJg"
                    "gg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YDAwIVEw6lpaXp"
                    "6esAAABpaGhJSUc4ODdVVVR3dnXX1tjJycqVlZcnJia2treHhoeenqDe3uFfX2A/P0CioZ8l"
                    "JB2/v8F+foBDQjzAv8BAPjmBgHxlZF9gX1oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABB1OHFAAAAQHRSTlP//////wD/////////////////////////"
                    "////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA464ouQAAAtpJREFUeNq1"
                    "lomS2yAMhhFQSZx2kj17vf9jVgKcYG/a2c5sNJMJic3nXyc235qZL7RB/GrshjYP4DayeQRX"
                    "yeaT3BQrWHL3r5VQra2h+In8SbADYCQLwSd/uHRGC8Ns9Dfw1V7dZK/77QvULF+edTvvdLsr"
                    "Vq0uH9whu7OQ5qts9WcmAHxD1X29UjqQQ+C+OkTLb1zxpxvdNjuCKLcgQHNVFpcpRCKzBzet"
                    "Tfxe8zKwlqsd6O/XZ7a7BaFeZGUEyMNPRYl+30zc1kDvolg6rFIgfunrzacnwFVAxK8aMND/"
                    "k3qghsqVB/Ud2EXEfQqaXOXyEO22IKJ8BFw0BlgscDaWumCNg+jdikLcswfJAgb+ucYYUY3h"
                    "Cma5T8FPRZgoW6NkUGWKxZYsb0begixX+S4HxfS8PcqHG1gZDQzVLVXLKYXNXR7yFnA5lRb4"
                    "1IOzBxO2jHpfqv0Ixh5USgrq7orfPSax+rNdt4fVIzgQxWxSyoFvYK4KXgw3B08scfZSIo0i"
                    "6rDfRTGMVWixPoBDIMwpRZ7Aq0ZRaBtY5Aq08gz2BjFE4/8OxhhzWujHBJY8L63pUPchm6zg"
                    "ACMUYZRODKPn7oXil3AFvOIcCk29doYkTpIHG9jOyctSHNLb2vZaenQAsxZbTIvgZzDBpfcy"
                    "gl1LB3vLc7mRqi2t3MqdcoOXoIpF8AvswCZLXZeWtVMHx7456/hRlXVxTqaCLKs4Mc+v5z6A"
                    "+Pf6rGPK7kIh9elqq7PMCo5x8zb09FFvkMv1jztD6P19G0LblMotQT73zjhJVUgURytpOygo"
                    "NdPGElHnz45NHNNSe/msHkwnVJkmvz/VOwP5OOjpFihtcFxlvlPrWTtXk3nr44dQDy44ZK5v"
                    "X+ajadkNVdePpFZZ9TDJy3wygXXmzqn3D5MzVEYxXfh4Qsijwo2L6X9P6X5CWajRf7yQpe5F"
                    "LL+l3fH/sBeWx71iPe6l8CGvsX8A1jEaGFv63fUAAAAASUVORK5CYII="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz8/PwAAAA3NjUn"
                    "Jyh3d3ZYWFdJSUjo5+jY19doaGeWlpeHhobIx8ceHiC4t7cPDQampqYlJB1DQjyjop9APjmB"
                    "gHxlZF9gX1oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABCJBmCAAAAQHRSTlP//wD/////////////////////////////"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJWM+qwAAAnNJREFUeNq1"
                    "luty4yAMhWFBgLiYkHYv7/+kKwlsY8ftJDONfyTEgc8H6UhY/ZJL/eA1iD+NXdHqDVwhq3dw"
                    "maye5boaMNvr/wxUxAZmJj8JtkG3CKjh6pGox1XcBH6Oq6uITbw8HXTbDSto+1rIGvLnknWI"
                    "PpZZtxEeJoAWZPgV+Xa70Ju1p6+og+9713HfCsscwfX4JfmmjFVndJbZJojQhe+AXsZ/rBKO"
                    "U8tlLL3z5n68l7TzBErNykqJoh5ZikduJ7sLucw1R9GGJhoG035diKZw8jBvgus+1dMzKRp4"
                    "5po/GQAiX2YnJ1QDbFBi64OOocv0pM9Masv51njgZximCW4HM0PAoVpbxXUUAD+iNMkjf6S+"
                    "ibPZvY9VsGF2ql7BrhsiS7n0DNG+8zYRdeCUtkN0Oth5D0UjFjdtJlUGW4kxjZoLjl0mkkem"
                    "7LwNeAyyd86R/RHBTGDPxiRa6+BEUBqVNIEZSYZu6mtwBChY/e85/CV0n0lRuKQWVgZhhAI6"
                    "31C4lzXuD6H4S1wC5ziHgvPMqzlxtoYVLKpaL4de1n5zYH5ofmQ2wEr4GQxh1LLTaMwAY+qp"
                    "7BXMHmt77zjb7XZ3rJgE3+eqzkHZRP0gctYG2PfFywqkRw8jFXLVRQMy//KnM8dG5FiULd1n"
                    "TcBu3S2sFWyX042Hqv74ODehZTc86GCkoW1RDEcQc/HpthnHUq5lCeh0Qkne2vht6ncN+eIC"
                    "OnN8RJ1kv9jU0TPcKsG5XGRoXjlAbD+SZFhPkkyYjya0r556rJscnrb07zmADR3iy6d0P6FQ"
                    "16uUW9e4DSd/PP7f9sLyvles970UvuU19j9jRRMvjlqNAQAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YDAwIVEw2lpaUA"
                    "AADp6ew4ODZJSUdoaGhVVFN3d3bJycrX1tgmJiaVlZe3triGhoaenqDe3uE/P0B+foCioZ9f"
                    "X2C/v8ElJB1DQjyBgHxAPjllZF9gX1rAv8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADLa5ryAAAAQHRSTlP/////AP//////////////////////////"
                    "////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAnF2oTQAAAs5JREFUeNq1"
                    "loly4yAMhhFexI2dNG3Svd7/MVdC2AYnu5OdaZhpk4D5+HUgWX2rQ33haMSvxq5o9QJuJatX"
                    "cJmsnuSmkEHb6fFa8Vnr7IvpyE+CJwB0VoM3yRyWzk5DGzqYHbyN69SN67h9hhzpwyBvx0H3"
                    "tGF55PnOHKuH4VO/ipp/RgvgLo51bytFpKL3KOiDt8zKpYdk2H3zZCHQIw6gmkpfPjoXkcxy"
                    "rtqWKn7UPDesxqwb+vd2Zn2aEGxFZLSH2Oxk1K4/8aN68GIRWLbe4k2+rza9gVsIZPFad/J8"
                    "Ygt4OOYOhtNEGENQ5TIXm+hpdaKjPwIX9oErGjAqbUUh+2ETaJaozvogmcCA35cQguOBsIGR"
                    "nmPwWyGmo+2BIthkhiFYlg9ZaKocFNv39SjjdzAzKhjyNGdOp+RXc7GXR/lhxQh/BFtXI2pM"
                    "yfoe7MSpNnFaC09XVhNDP6Iclo9gb22IKqXocQdjZvCssBp4QvKzoRRZ6qHkFWVUnOgfxbHO"
                    "kTn6Duy9dTGlgB14IS9y1FYwySVAxg3M1l9oFurUX8AuhJhm+6MDk4lzvXSOrXeoIoM9NFd4"
                    "cWtBaLn9yBU/iUvgxfWu4NDzzaDAUfBgBesueKd6lyWeBjq/NzBysoU0E74HW/iQu+xAL0XA"
                    "RuOebqZ+tnwuD9INbp4Vk+AbDGAVKa9LjdpJwEE2R6ierZETR5hMRvT1610KEP5a3rlM6cEV"
                    "tGfKNc8iMjiE1Vov4aM0icPEgyL0+bkWobVKxZrwJsrNOHH8YS19HDeJqjJysUjU+dmy6Vq1"
                    "5Lt8Zgu6DlWk8gv6lB8U5GOht7uj+IK7heq7jbVS9tmkLtI4rOPGBYfIyfa5b03zUFQnaUk8"
                    "F/Ohkpe+M4Ge1IOu949BPZRKsf3AY4ego/zOdel/u7R0KA05mPuFSHnPfe+Shvb/sheW171i"
                    "ve6l8CWvsX8AtKoZbcAzQrEAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGxz9/f0AAAAmJidJ"
                    "SEdYWFc3NjV3dnVnZ2bo6OiWlpa3t7fY2NceHiDIyMeGhoYPDQaoqKglJB2jop9DQjyBgHxg"
                    "X1plZF9APjkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADhW45kAAAAQHRSTlP//wD/////////////////////////////"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJWM+qwAAAkBJREFUeNq1"
                    "ltmS4yAMRVGDxGoI6Z7l/790xOIONkyVU9XmIbHBPhbSlYT4qEP84OjEn8buaHEDt5LFHdxC"
                    "Fle56G1Icr2mNhG9kDSSL4KlhWx0AL38JngCoAhqBF/jgq/GOuDhDnZLjwqy0EZEIOPley7L"
                    "ofxuCaxRJh7sNgCi0+QGo9HH8Xgs7E31eQNW9a2b7yWUaWCh5oklVpAUZ3Ti7UtBthq6lRkN"
                    "W18LkI4PO/BLXypU9Dw/yk7chMuyfiSWP8DdEfa0d7S4Mrdw6Wg0MYQKmMpbhmPPwQvdTpxd"
                    "uk1zD/qVtNamDHqRXRAdTKH6VlkwtoVPFR9NAgI6y/zTQhsWX+DCqGDrpfRVTmYPPt/PFsc8"
                    "5Y8yvmLtqFTYwdgEkWq6xO6nVaCmSXaw0hFCiDisOV/AsvqYr3KNjmwmR5gjxZLJExiR5R+C"
                    "pgGsihuZlhvYMZSvomtiMxfBRusYvPo77ibaprPKQCe2Yq2277jiD3MZnMzoihJ63QLFwbM7"
                    "uOW4z3Pw5jkqYtPBM34Ea9tzGSEQdXBwb8jt8cRiMRv8HLM6WSEdRDIlah2s+svLBFmk3oN+"
                    "p0+kYyHCYpWMTWe5gvG7QixSWq2L0NfXuQhtryqpwVLZ66vyTEUor4vQsmyartaSy9WxQ4fi"
                    "KqmvlM310BBZ4AFcLZYhnz57rdCvO1NrSa08nIQgoymtyWBpTXpoTR/X7WaFO7Dyf800HJvp"
                    "9WOFTAE8Ltu/rO1fHdv/bQeW+45Y9x0KbznG/gMOnhJTJxn0AwAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YVEw0EBAOlpaUA"
                    "AADq6uxpaWnIyMp3dnY4ODZKSUfX19knJydVVVOHh4i3t7iXlph+foDd3eA/P0BfX2CenqC/"
                    "v8ElJB2ioZ9DQjxAPjmBgHzAv8BlZF9gX1oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC2j6tMAAAAQHRSTlP/////AP//////////////////////////"
                    "////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAnF2oTQAAAo1JREFUeNq1"
                    "lglv6yAMgME8DOZMu+561///mbMhaULadZ20WqoUQvni26hfTdQPykz8aeyCVg/gNrJ6BFfI"
                    "6k5u9Al0MFf3bHExJVuL3ZDvBBsAwqAh22j3WIWQCsCkoSi7gs/yYjbyMh6vkJwwCFjIbKk1"
                    "4QFSzBgZnNvfRlODHiTH7S5pWboAgAcUvdctBIj12BDOARz29ixcgPkhrIdNAM9/YYSXl/xw"
                    "OpviTfArBbPxo851xmpKekb/PX+Tza/iZbHCCTrDfNpqCKOGBGlYTx2WQg702p8XTz4BFgYF"
                    "ehGHgbyP4M+O8MqOgfRj1Lu6wqVZ6Rk8AfKPwZP4ADn25JTuelp/iPtgOe/tCAb6U7z3KEJw"
                    "BpO2Dfw0MRMFxhGE3PY4ydyVxDQ7jcPb8imbV7AwGhiSqSlVdkSG2V6X0qXGieIeHLA2rJ2S"
                    "vgRj92mIkta66XCc7JUanewenEPgZInRZVrBlARcFU2yeib2s2V7i6yab/aSgS7AOQd0MXra"
                    "gAu7TKK2gFldhia6AU6XYPTexRp+b8CcqrUVHYqFSMoJOMOl1Z+74h9zGVxw6wrle2Vw4Dh4"
                    "sID1N4JHkmw+VsZvwQFOvZYRdJk62Gr6RrrBaxaNWeFXGMDKcV5PLWrPHexh6r3Bf1Ugb70B"
                    "0f/ylqk3otUVrJVJLc8cCdj7c4fAbQ++VtLnJvT+vjShuqjQysy6XhnPnBUAS+v7ugndaps4"
                    "d0up5aNYsJlQFU0oK8Zng+52ow+r86TAsXB/D3Im6lElafTu80bPx+t2NNUhQU0fSbZlE1S1"
                    "G00F6HjCmMbRdN8w5RnKrTicCAburWF6/7XC8JRO3l4f//py/D/swvK4K9bjLoUPucZ+ANIj"
                    "Gf458WeMAAAAAElFTkSuQmCC"
                ),
            },
        },
    },
    "Ghostty": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwAAAAnJylX"
                    "V1YHHJQZGyN2dnY3NzlGRkhnZ2fX19cOI0qXl5bm5+impqa3t7fHx8eGhoYFDTouNEkKI5MN"
                    "JasFG2w3Q25GRT2YyfxolM4IHqAJGkwaITGOuvSox/NeXmGo1f5XiMmbstpWdqQpOmRiYVsM"
                    "ImxgX1sjIh0mLkjc5P8JDB2qvuRNaJtCbqA/cKRqg7WYqtOAf30iSaE2VILG1/h9kL9oe64z"
                    "ZKQTOYoAAAAAAAAAAAAAAABgHwHoAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////////////////////////////////8AAAAAd3Aa7wAAAtlJREFUeNq1"
                    "lul22yAQhUWHiC0swnaS2vXSbG3SLN3X93+vDgghkNPT9pz4/ogUS3xchmFGzYuo5hmViM+N"
                    "HdDNAbiR3ByCG8jNyG0LTV/sXLwAPInhzCllGS/JzT8Z5oTQcDXmiYdUkaSOFuAsIVmWnNdj"
                    "W0JUMEy66Lt8BBkb0XtLAmucznLK1m9IQngAe7x3hFZrQSnNmDXxdkq2hgJsAObv5iigRo9j"
                    "Q+xUMMsD0pICDNFmCq5Q+2ShKJw9fv9xlrShKm8FCT55sCxwlO+jkhRcsvFfH6YpQ9hIC9cf"
                    "7+7M5XV0jJ6tbNr8NobO4QhJkF16kpmb8iWQaQlmevP14dPu4dvVca/WZ3CEcVw09yYsXVSG"
                    "+zycD9OpakEI9u3V9nG3ulwdb3rw5xHcgENjjjirVIxLU84Y79hgdPwphcK375fL3e58lRw3"
                    "rABjruaECpkMnEZ/OtujeSGmDDqCWfthud1ulxG8Xh+3NTiajtLShotP6/YZnHzaIToZ/HOJ"
                    "Or84DVq/beXrHmyd9UxSzt14CpiAlC/DTokMZmWQESzbXxcr1JcIPh3BIJnX1rouUcuKMIJ5"
                    "zpUJmMp2cbRAHfVazG5oFYohFvF4KachhWKIZy5Pug4FfdMOzASmNZghLYIBhEa6TfEcjoPg"
                    "Ywb6skLR9T64SgrGic7FAHjOFajTjVfphhPS9UkJPinBWMDw6BmLpYJ0ukxTkqyPm9f1CTmC"
                    "MRRIfhWF3MX9jSgKmMSaZrDw4jghi6PH8vYBTH4YTDGYLU6yFrPbcfdBQhjBG++wmhl0R6si"
                    "RJtqIjVpAxLaWdb9razmxVqAppUPZUc3MJbzWI6H2t2n+qQgb4Sk4mWSoJLWbYKE8hxzq6v2"
                    "JtaGcGQo9X2m873WBXwusiaPmY10Gv+Kuh2asjUpeKrr/UV0ghxSg2W0kf/bpVNbhD88oDaU"
                    "YS3q9n+wD5bDfWId7qPwIJ+xvwGsUieyacv28QAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQEAAADn5+oH"
                    "HJQWFhYmJylWVlWVlZXX19qnp6l2dndoaGk2NjYOI0qIiInGxslJSUm4uLoFDToNJasOFioK"
                    "I5MqMksFG2w3Q25olM69vcGYyfxGRT3a3OQIHqAJGkyox/MZIjWOuvR/f4GenqCqvuRgX1tX"
                    "iMmo1f4MImxWdqQpOmQkIx5NaJtiYVubstrc5P8mLkgTOYpqg7U/cKRCbqA2VIKAf32YqtMz"
                    "ZKRoe659kL8iSaEAAAAAAABVnBjrAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////////////////////////////wAAF+0o8wAAAu1JREFUeNq1"
                    "lmlT2zAQhrUV1mFJ2HJwgCRNcwCFchR6n///b3Ul2ZLsZDowQ94vyUTeJ69Wq12TN17kFdUR"
                    "Xxvbo8kBuJ5MDsF1ZPIsriyYdJ9luXdVcFYUjAuakZ8HrgAUflCA3bWlLqBToWgCP0s1BmHM"
                    "HJjDl5kzUkasE2t2g2kjoho6WtQAFULAokPm3XcSwarh3AT0TrIoZ2zD2OmnUxTjGVkIXC2A"
                    "UaQoQhmATn69TbH0qa68+ZFnydn5w5/f5502XPZoCWCpy7IgCv1Y9CfToSIpmZAWQs7yVLG7"
                    "L09P9PHOO0bPKZH4NKsJwwgNUg08acf135oat+WfzRPl9rv5fvVzfbW8PQ5qK5rXBFS46cpC"
                    "Az7ZaTMuQ+ERn1xM2dAyrdrb7cN69jg73gTwj2y5xrxaBswUGGfT7yoeFu+NOhNiCP62WKzX"
                    "N7POcVaSblnFgkI/tBbK80y0p/uNyJicPrL9uthutwsPXq2OWzU8A2fay2hfV9538q+iT1xl"
                    "Q/CvBerm+sxp9aHVy+5BZizXqipZMs2rUOgQKy+BMSlFDtbt5+sZ6q8HnyVwqbm1xrCea0Us"
                    "tgxcxloZgaVup0dT1FHQdPJeDgu9SY6hYKbuUtHns5mTvamQH9ue2YG5HN4fpIUr2yiXZDM8"
                    "PFp15UlhUDcYuPoPGG8GcAGmbwZ0HjrknnITo3Kr+eokB5/kYGxgRSnBYKsAdprF1b31eHiu"
                    "kxT5XmtMBZLfeSF3CinHeDCaYoRUBV6sSsNFDOPx+Jpm9EOfCsUm05Oo6eQ+1XGtGxchiGXu"
                    "T2SVQt11wH9NHdKd7HJw6JeatZMouNflaITg8RfW/YEhTVEO2rEJDYtesj0NmV5ivb7tZLka"
                    "TAkcHYaiPe67jCA0LV6EwWG1tmGUiN2RWM+b+Xxee42KmLseVvqDb7LulkZIrPCSvGjqeYsq"
                    "IMdTq+aJq+ULp3QghOu2Z0EZ14bNhRyM/4O9sBzuFetwL4UHeY39B3pOKtuo+LqyAAAAAElF"
                    "TkSuQmCC"
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnJyoAAABX"
                    "V1YZGyMHHJR2dnc3NzlnZ2jW1tZGR0jn5+iXl5YOI0q3t7empqaGhYbIyMgFDToKI5MtNEkN"
                    "JasFG2w3Q25GRT1olM6YyfwIHqAJGkwaITGOuvSox/NgX1sMImxXiMnc5P+qvuRiYVsJDB1e"
                    "XmEpOmQjIh0mLkhNaJuo1f6bstpWdqRCbqAzZKRqg7U2VIIiSaGAf32YqtMTOYp9kL/G1/g/"
                    "cKRoe64AAAAAAAAAAAAAAABIvUpGAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////8AAAAA6hjChQAAAu9JREFUeNq1"
                    "lul22yAQhcV0CJsISLaT1K6XNEnTpFm6r+//Xh0QRuC4p+058f1hSZb4dBmGGTUvoppnVCI+"
                    "N3aLbg7AjeTmENxAbkYuL7T7YO/iAWAvRgqntReyJDf/ZFgyhuHYtntuomZJPRbgLKNElprV"
                    "YzljOhhmffRd3oKMjegnUwLfOpvltK+fUIzJAO7o3DGs5kJqrRC+jae7ZN8iwApg9m5GAmzt"
                    "ODbETgezMiA9K8AQbabgGv2UbDTC2d33X2dJK9R5KVjwKYNlQ6O6ISpJwaUYL7vwmjKEjfJw"
                    "8/Hysn24iY7Js1cNz09T6ByNUIzYpSc1cJXuNQkHMpZgYVefrr5urn48Hg/iXQZHmKRJy64N"
                    "UzeVYZd8J6+6mhCBO/64vtvMH+bHqwH8ZQQ34MiYY85rHePSlG+kox3Aovgrh6Lj7xeLzeZ+"
                    "nhw3ogBTruaECpkMEmM4bLIHDDitAk+TECVY8J+L9Xq9iODl8pjX4Gg6yiofDtF39o8UkJQb"
                    "fohOAf6wIN1fnAYtX3P1dgB75zuhUEo37gJhIOXLdqU6VGo4E2WQCaz4t4s56XMEn45gUKKz"
                    "3rs+UcuKMIIVCrMXjIpPj6ako0HTyTlWodjGIiaAdhZSKETafyhSFto6FPiGb5kJjDVYEC2C"
                    "AYwluk/x7NP+A8nabQZ2ZYXC5VNwlRRCMpuLAcicK+GS8ju4Vql2lOXT4PKkBJ+UYCpgtPVa"
                    "TxNmvS3TlA3WmQOSDrHtGatKq6FQEPlVFHGn1+emKGDkxbVUeGmcUcXWE8PydcO6qvxHsTQC"
                    "JtOTrOnkdlx9UBBGyKZzFM2WNhdWRQib6kV6pw0o4JOs61tVvbeZBTu6C2XHNjCW81iOt7V7"
                    "SPWdgrwyCs3LJIMK6zbBQnmOudVXaxNrA+UGbZBuyHT5pHWBnJmsndvCRzrGX1O3w7ZsTRr2"
                    "db2/CHeQ29QQGd2q/+3SqS3CH26gD2XYmrr9H+yD5XCfWIf7KDzIZ+xvY70o+nPbcbkAAAAA"
                    "SUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQLn6OoAAAAX"
                    "FxcHHJQnJynX19lWVlWVlZWoqKpoaGh2dnc2NjdJSUkOI0rGxsmIiIm3t7gFDToNJasKI5MO"
                    "FioqMksFG2w3Q25olM6YyfxGRT29vcHa3eQIHqAJGkyox/OOuvQZIjV/f4GenqAkIx5gX1uq"
                    "vuRiYVsMImxNaJvc5P9XiMmo1f4pOmRWdqSbstomLkh9kL9oe64/cKSYqtM2VIJfX2AzZKQT"
                    "OYqAf31qg7UiSaFCbqAAAACoJl6bAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////////8AGaEN6wAAAw1JREFUeNq1"
                    "lul22jAQhTWqM5KsJbYTnAQKZUnStNm77+//WB1JxpKB00PPCfMHbDEf11czI7NXIdgLRkd8"
                    "aewazQ7ADWR2CK4ns724okDhP8ty56qSWBQoFc/I+4FrAEMfHGB7baYL6KIwPIH3ioqSKGcK"
                    "6PFlpoyVPdYHNtvJvFF9NHxjUQPUBAFHCjGo70JFoJXSxm9bZnGJuEI8+3BGgTIjK0WrBSAn"
                    "imEcAXTSG2RGc0UdxG9oFhIvbj9+uuhiJcUaLQAc9y4rZkiPI1dE2lQikQgeggkH0bPcKnz8"
                    "dXfHnx+DYtKcjKRfY8WQMjQIM9CkPZcxWYTQjHuyGYDV6uvNl8XN7Ok4RlvzvCagpoeuHTQQ"
                    "zE4P4x3i66IIlg0l87p9Wt4uxs/j41UE/8yWK/LVIaAtKM+l+yZsFmfdvkkWLCPPBuA/k8li"
                    "8TDuFGcl6ZdNX1Ckh1fKhM23nbwGykooqLqHkIPM9vNkuVxOAng+P27NcA+86FhYOugLunv9"
                    "BvmsiBbRKg7B3ycUD/fnPubvWz2LKxatk9rUJSbRso6FDn3lOSO7bzJ4ncC6/XE/pvgdwOcJ"
                    "XGrpnLW45jrVF1sP5kxrSSW+Ayx0OzoaURzFGJ28FcNCb5JiKNBWnRWy6z8ju57bsEK8a9fM"
                    "DizFsH+IFlu2Md5km28ejZKSelvEOZXXDSXO/wGmzgCpwK6HAZ/GJl6Xm/M3VSg3tVFulZyf"
                    "5uDTHEwDrCgFWHpgwLMsr4rSOQ21smwK3yCYd7z/CVlB5DchiDuC5DH1nOaUIUxBjVVruOzT"
                    "ZNw+F63/1t/IrDB4MjrtY3Ryneq40o3PUMyh/xNRp1TfDv5KhKAJ6Xd2Ntj0K43tSR9wrcuN"
                    "I4S2v3D+DyxrinIwjm285Fe4YyDzK6rX1104aQanBB0dloaih5MZiqZkv3IZ689p7eJRoraP"
                    "xGraTKfTKsRGEUuaYWSDCXOhHo7F/GSC9Cz7nXpBoonIzVOrkomrxX+e0pEQ223HgrF+DNtL"
                    "MTj+D/bCcrhXrMO9FB7kNfYvJZUsBEEmyVYAAAAASUVORK5CYII="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwAAAAnJykZ"
                    "GyMHHJRXV1d2dndGRkg3NzhnZ2fX19eXl5bn5+impqYOI0q3t7fGxseGhoYFDToNJastNEkK"
                    "I5MFG2w3Q26YyfxolM5GRT0IHqAJGkwaITGOuvSox/Pc5P9XiMleXmEMImwJDB0pOmSqvuRi"
                    "YVsjIh1gX1smLkhNaJuo1f6bstpWdqQ2VIIzZKRqg7VCbqAiSaGAf32YqtMTOYp9kL/G1/g/"
                    "cKRoe64AAAAAAAAAAAAAAAAspPoHAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////////////////////////////////8AAAAAd3Aa7wAAAt9JREFUeNq1"
                    "lul22yAQhaGjsIORHDuxWy9Jm6RJs3Rf3/+9OiCMQHZP23Pi+8eLxKc7w8wg8iKKPKMS8bmx"
                    "OzQ5AjeSyTG4gUwGblNofGPr4wfAQYzkXmvHZUkm/2RYUsrCp7UHLjJNk1pWgLOE4llqVq9t"
                    "KNXBMG2j7/ISZGxE74UEznqT5bWr71CUygDu8LunrIoFpQ3nzsavY7KzDGANMHs3QwGzZlgb"
                    "cqeDWRmQjhZgiDZTcoXeJwvN4Pz++6/zpDXTeSto8CmDZYGruj4rScElH3524TFlColycP3x"
                    "4sI+XkfH6Nkp0uS7MXUeVyiK7NKTqrk9mZVgbtbvb75ub348TXo1XQZHmMSgZWdD6KIy7Iuw"
                    "IaSsDAjBXfO0ud8uH5eTdQ/+MoAJeDTmqXdax7yQ8omF23b8F6aiaz4tFtvtwzI5JrwAY63m"
                    "ggqVDJLFdJjSHu6C6YPgJZg3PxebzWYRwavVpKnB0XSUUS58RN+Vf01tWODK7ETwhwXq4fIs"
                    "aPWmUa97sPOu44pJ6Ycu4AJSvcSdio2uUvJ5GQWCVfPtcon6HMFnAxgU74xzvk3UciIkcEDi"
                    "rjpyAMxUMz+Zo056zad3rErFLhexvbQ3kFLBe77EdPe3mzoV7G2zYyYwq8EcaREMIIxN9lzf"
                    "Dn1bi1yBXTmh2GofXBUFl9TkYQAy1wqksnPD7CjHp2Cr0xJ8WoJxgGHrWYejgramLNMdUGEk"
                    "aXD3BTmAMRVIfhWF3PnVnSgKVOFMszh4cZ1QRevxXQdDM/ojm+IwnZ9mzae3w+6DgrBCks7j"
                    "5lsMnFVDiJHqQXp0DChopllXt6p6LpkF07oLQRsCwziP+7ab3X2pjwbyWigmXiYJplh9TMRu"
                    "jbXVVnsT9y20DGNdX+ly7+gCORNZo8vcRXpss3K6BZu2PJo0HDr1/iI2Qu5Kg2e0Vf97Sqdj"
                    "Ef5wgbkwho2oj/+jvbAc7xXreC+FR3mN/Q2v5SgZzYvLVQAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQHo6OsAAAAH"
                    "HJQmJykWFhbX19mVlZZWVlWoqKpoZ2g2NjbGxsl3dncOI0pISEiIiIkFDToOFiq4uLkKI5MN"
                    "JasqMksFG2w3Q25olM7b3eRGRT2Yyfy9vcEIHqAJGkyox/MZIjV/f4GOuvSenqCqvuRiYVuo"
                    "1f5gX1tXiMkMImxWdqQpOmQkIx5NaJubstrc5P8mLkgTOYpqg7U/cKRCbqA2VIKAf32YqtMi"
                    "SaFoe64zZKR9kL8AAAAAAAC8wmjSAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////////////////////////////wAAioXwmQAAAvpJREFUeNq1"
                    "lmlz2yAQhlkaBIgjyJJz2LVrO3aapjl7n///b3UBSSA57aQz8fshmvGGh5fdFSvyKoi8oFri"
                    "S2M7NDkAN5DJIbieTJ7F5QXj/lmWT0aFZEXBpKAZ+XngCsDigwLsx+aqgFaFpQn8LBlchGuW"
                    "wDy+zJyRssd6sWZ/MW1Er4aOggqgQgg4dMiC+1YiWtVS6ojeSxaVjG0YO/1wimIyIwuB0QIY"
                    "RYollAGo5DfYFPOQ6iqYH3nmkp3f//553mojeYfmAI76LAti0Y9DfzwVFUnJBHcQc5anit1+"
                    "enykD7fBMXpOicT/ZoYwXKGA24En5bn5sT3ZDsBi8/Xy+/pyfnMcVVc07wmo8NCVgwZCstNh"
                    "fIY6amXIvBhZplV9s71fzx5mx5sI/paFDebVMWC6wHUu/W4HxXJ+E29CDMFfFov1+mrWOs5a"
                    "0odt31DohxphA0/n9kTYk4+TY+vPi+12uwjg1eq4tsMaeNNBWoW+Cr5z/9g3YOJmbAj+tUBd"
                    "XZ95rd7Xah4jmmknla1KlkzLKjY6+M6jxJT4R7XJlxjPwar+eD1D/QjgswQulXROa9Zxneib"
                    "LYL96S8IVlWTJ8Bc1dOjKeooajp5x4eN3iTHUDBt2lTImFahYyL2UsHf1h2zBUs+fH+QFl/Z"
                    "xvok66x4uxCI7Uth0De4cPUPML4ZIAXo7jKgy3hDxnaj4dn2sxi1m5Grkxx8koPxAitKDhqv"
                    "CmCn2ToTrYfKxUT4m6TIz2owFUh+E4TcKaQc4zunKK7gtsCeqhSWqpOM5aOkNIMfslRYNpme"
                    "9JpO7lIfG9X4FYI45jfhVVrq64a7Bq/hhvSVnQ+KvlOsnvSCO1WORgiWv3B+A02aohxcxzpe"
                    "WHTHnriQ6Q779XUrJ+1gSuDo0BTtyfCCCUJT8CIODqeUi6NE7I9Es2yWy6UJGjWx9DUvQ0c1"
                    "2e2WRkjf4SX5r6kXLNqIHE8tIxNX8f+c0pGgzV8CVvtrWF/wwfg/2AfL4T6xDvdReJDP2D9N"
                    "pStLu3V6jwAAAABJRU5ErkJggg=="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f0mJigAAAAa"
                    "HCJXV1dGRkgHHJRmZmc3Nzl2dXa2trYOI0qnp6eXl5XHx8fn6OnX19eFhYUFDTotNEkNJasK"
                    "I5MFG2w3Q26YyfxolM5GRT0IHqAJGkwaITGOuvSox/OqvuTc5P9XiMleXmEMImwpOmQjIh0J"
                    "DB1iYVtgX1smLkhWdqRNaJuo1f6bsto2VIIzZKQ/cKTG1/iAf32YqtMiSaETOYpCbqB9kL9q"
                    "g7Voe64AAAAAAAAAAAAAAADkI7ALAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////8AAAAA6hjChQAAArRJREFUeNq1"
                    "lulW2zAQhaWpRmhBtuUskLRZgBYoZem+vv97dSQ7tmSnND/IPQebYymfr0ajGbNXUewF1RJf"
                    "GrtDsyNwI5kdgxvIrOeKRMOJpY03gL0YKcIE0CmZHWRYc47hXlX7RpFbmqBLLlNw/1plOqlp"
                    "/lPBuQ+GeRl9p0NgUXLHjGIl18qOlgSuskUn610+Q5GjAK7pf9u470d2bwJ6vxyCXYUAa4Dp"
                    "hykJsCr6OITY+WBWB6TjCRgQ6oSFhh7k4fcI5w+//py3WqPvtoIHnyGITHJgdROVVj6uIVHB"
                    "bRpCphxc/768rJ6uo2Py7BRrc4NQJVAASlo2sTlPPKlqsHasMAObYv3x4tv24sv9aSNRd+AI"
                    "00B/dUXXNIw4CikT6TMC1+J+87BdPi1P1w34aw+mnefcWG6d9zxdu8zct3PjNvehqMXPxWK7"
                    "fVy2jkP+iDRVdwqZDLrZIrB27Lh0WYyN+LzYbDaLCF6tTkUOjqajCuXCrfGt956mAfjTgvR4"
                    "dRa0eifU2wbsrKuNQq1tZ7o0solAmWV0K8NdClbix9WS9D2Cz3owKFMXztmypRqdJpv6HxiV"
                    "mJ/MSSeN5pM7zEKxi0UVLt4WcGAo8L3YMVsw5mBDtAgGkAXRoyth3Xjz8meIqzE4SwqjeRE9"
                    "Q0yLw9KNpuBqloJnKRh8OHqVo1LByyL93d4Dkm2opFAQ+U0Ucee3dzIpxrRHtmLoKQ+kSo/e"
                    "niOdPwADk/ms03xy0+8+KAgx1qymLkEHRCZJNipCjg/ODCoQk063NyrP0Gkw7etQfgsGJSRl"
                    "0zxbNtlaKpSvW0lU+TjwUJ65iYdCD1vAs4U+7PRUdhokqHGRjvE6CGKpQmuiFdIrTdKaDu/+"
                    "uM9O0kx93kwPB4ODf4xQ6QjtX+bt/2gfLMf7xDreR+FRPmP/Auv3JoG59toFAAAAAElFTkSu"
                    "QmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQHo6OsAAAAn"
                    "JykHHJTX19rGxslpaWl2dneIiImoqKqWlpZXV1UXFxgOI0o1NTVKSUm4uLoFDToOFioNJasK"
                    "I5MqMksFG2w3Q25olM7a3OOYyfxGRT29vcEIHqB+foAJGkwZIjWox/OOuvQkIx5gX1uqvuRi"
                    "YVsMImxNaJvc5P9XiMmo1f4pOmRWdqSbstqenqAmLkh9kL+YqtM2VIIiSaEzZKRfX2Boe64T"
                    "OYqAf31qg7U/cKRCbqAAAAA8vv1tAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////////8AGaEN6wAAAr5JREFUeNq1"
                    "lul22yAQhRk6RSAEQVhxEttV4yVNmz3d9/d/rI6QLCHZSfUjvuf4+NhI3wyXYYC9CmIvqIb4"
                    "0tgtmh2AG8jsENyKzEZxhUVRfWu9b5SniUDkPuUReRw4BVAVAWAPlklAeiCzkDLegUcpAbD0"
                    "TglYkTSPqB7lOaAwUhDYYLInss9aeT4YlEDpMA05Y3MM2Ucjws+DXwnFP9/lGsQV4snHExKa"
                    "iJxlNGoBOcsIyRFAtmNe6bwLw6XRapCzMHh29enHWaOVEVu0AMh55XLGFGiWkyuiJdlqDrFc"
                    "cCuSxrvf19f84S5kTDl3RhKKrENyWYJQAOBjIxTj/YVUfXC2+nL5eX05vz+qVUS1Q9lCqumT"
                    "g4dg9pajzsXOOivFe6VY3G+u1rOH2dGqBv+KhhPyNUdAZy3Ec6eIO1VACeg++O9isV7fzpqM"
                    "bT9u5UAtKjueZErXAXE3Y3Si92bxZ7HZbBYBvFweFYoPnm/ATrrqK+Q9z/ieXZr1Uyq+LUi3"
                    "N6eVlh8KOW+WGV1upEo1dkmbtC50GxVeKwMuBsvi582M9DWATzuwlibPncMtN8+6mT4BjgtO"
                    "yGL6ekp6XWs6eTdwz3cZg0WX7M56rxXifbFlNmAj+vuHaAGqvapMdiMXT5jlM2DaGWAycDWZ"
                    "fpd1ViPKLTHL4xh8HIO1BasFOGoVgCe0tbuA6n8bJCEriPw2iLhT6DymHCSn3iOUpd6QyriD"
                    "ybgH79vSQuFketxqOrno4ibSV2udsRyrICLtamFEE3qUWExawYXUgyPEUHXldZV62w56qfOu"
                    "dzBltBy4zh+pXt80yo3SvUIqwXHqnybkmDHO+40+ea7R0/+lL8syCRqsiaEuTzZU9vmouzVH"
                    "Uwpu/l0K7B9No069kKKqkXzkYTr+WpG45ImwdPzb3eP/YBeWw12xDncpPMg19h/dKCsdcwY+"
                    "PgAAAABJRU5ErkJggg=="
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwAAAAnJyoa"
                    "HCJYWFh2dncHHJRHR0k2NjiWlpZnZ2fn5+jX19cOI0qHhoempqa4uLjHx8cFDTotNEkKI5MN"
                    "JasFG2w3Q25GRT1olM6YyfwIHqAaITEJGkyox/OOuvSbstpgX1sjIh3c5P8mLkgJDB2qvuRi"
                    "YVtNaJtXiMmo1f5eXmEpOmRWdqQMImxCbqA/cKRqg7WYqtOAf30iSaE2VILG1/h9kL9oe64z"
                    "ZKQTOYoAAAAAAAAAAAAAAAA6IWNHAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////////////////////////////////8AAAAAd3Aa7wAAAutJREFUeNq1"
                    "luly2yAUhUXhEtaAZDtJ7XrJ0qRJmnTf3/+9CpdFiz2ddqY+f6yR0OfDucBV8wLV/Edl4v/G"
                    "FnRzBC6Sm2NwI7npuWyg/aHQGuvoYYzkrbWeyyG5+TvD1BCvuSX8wDOwJKuDAbhKaF6l5xMu"
                    "adGsiq+rkW9asYjemxP1plVVrfWjEd5iVo4YLXQ39C2RZxXn3uDllOwNULqmdP56HkTBqMGf"
                    "OiLCjyZGpLkT3U8l2szhCrtPFhboxf33HxdZa7C1FA5HS4NGsayclOpGlwP/cWg3wLJGe/r0"
                    "8ebGXD6h4+DZ6wQJuYIIIIXhuJQiyVXSlUtpJcMQzNX6692n7d2359Mk5jJYhoEygsMMwGjZ"
                    "xeJZVw23eDEvEYQ07Ajs2PPufru8XJ6uE/hzBivbZLC0mK0wRJtkUwR/KTFejPa3chSOvV+t"
                    "ttvHZXbc8AyODASbltIWV10IQOSUij0ot+Ik+BDM2YfVbrdbIXizOWUFTAoYEsDhdunKvF0F"
                    "Z5++pFPBP1dBjw/nUZt3TL/NUbQRTDHjcOXBQFxl6K+vlKhgPgw5gDX79bAM+oLg8x4sYlUC"
                    "zSewCtBw1akxWNb1OwGDZouTRdBJ0mJ2BXm5dSatM9wUoBoW3XKToyh50rIv1DgKeMMKM4Oh"
                    "gEV6OxaOtqaAbc6zbAch+y3jhicUbPbBeW4m72UgVsoMtiqXLCdQl5scLbfwh7A5G4LPerAz"
                    "DVXhPNCxahks0sssUPy4eB0hZnRWhCgC+RUqcBe3V6JMJpqiXVpnHsFQZstr+Sid3CjZczpb"
                    "nFUtZte1H7B+wXNiJB5oNUUzBkWunbQBTdms6vZa98N12a0mbWky6FB4HJezW7YHDuS10CBe"
                    "ZgnQQIc+OhDaEoXLxPpRhqlxcADX4aXcb2tyLqrGj2lqSXjZTixJM2xNlh7qen8WJ9ZpRcy0"
                    "9zBe0Ub/c5dOHcqSFg49AB+PYSXG7f9oHyzH+8Q63kfhUT5jfwNZDSmUgH79OQAAAABJRU5E"
                    "rkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIAAADn6OoH"
                    "HJQWFhZoaGlVVVSVlZXIyMvX19omJyo4ODl3d3hISEinp6kOI0q2triHhogFDTouNEgNJasK"
                    "I5MOFioFG2w3Q25GRT2YyfxolM7a3OSenqC9vcEIHqAJGkwZIjWOuvSox/NfX2B+foAMImyo"
                    "1f6qvuQpOmRWdqTc5P+bstpXiMlgX1smLkgkIx5iYVtNaJtCbqAzZKSYqtMiSaETOYqAf319"
                    "kL9oe642VIJqg7U/cKQAAABZH7PtAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////////////////////////////////////8AhMnVgQAAAwRJREFUeNq1"
                    "lolS2zAQhq0K3bIcOyGG5moOKDel9L7e/7G6K8nyEZiBGbIzkEwkff730K6zd96yN7RIfGts"
                    "g84OwPXk7BBcJGcv5OrcEOaKp9ekMIwZIWmH/EJwQYjljhFBNR0sLTgj0VhOW/CLrCRGwQe1"
                    "eNz2dBcJi2bK/cO0lMnKgSzLNPxXjhB+zlF3WpFBqhXCBvRetKgwZmPM5NMEzIguuXAkhw2c"
                    "EO8qfDnrhAhkyoUPdeXFDzRrYU4ff/0+jbYRKZbU+d2AECBb4c+CqHgKUa0IjVtZ39vCXP29"
                    "uaG3V14xaC6a9QnhFYCcXfmTDHzV6AEaR27IAjwKTiA574Hl5s/lt93l4noUrK5oE0QOfwCW"
                    "GAMuGbEqYy4oxDj4fVUMLmUDybSqr7ePu/ntfLQJ4J+0yRv14IkEJoedOWQwysxTskQjFJ5A"
                    "ZB/8ZTbb7S7mUXEqSWR4MDFFabCcdKLYJA9iUiUnRBec1/9m2+125sHr9ajeA/MAcBrLOvDA"
                    "7xAT1B51wsNMH/x1BnZxd4K2/ljzRdxoEFxm1h9cWogzhRLx+kAdH4LBHdYF8/rH3Rzsuwef"
                    "tOAKoohZa8AgF6DG9sFFqt8BWPN6ejQFOwo2Hd/r+EiGR+DScfSe20whWJAYiiae5Sp7MhT6"
                    "c90wI1hEMHqJ3yFxkDzSgFk/ebSK5UlJinu8eOvnwI6chbvMCatkAFNmnyk3OSg3JdbHXfBx"
                    "F5wpS5j0WVsGcB4OK+x1veRRA07oLhhCAeQP3oA7JfdtKBTeeF9nyiI4zxtvRUpfWQ5+aAfE"
                    "eHqcbDp+SD1b+QRRFW7GEqoCohgX8TqErMYOiU1o0esVS27qcTLywNu+ymO3xLu8QA86E0qG"
                    "zu/RdGmeaMh0yYV7H82JvKCdTk0Yr6C/O+U7ZbeasvMwOBzHwUUGmYtuqVW5Wq2UN93vqWEk"
                    "4bOUGXRy2Z1Mvqm+aurBDIVW7M7s3oSAgmq5XL92SocJxYjJ6f6Cyi22YXuue+P/YC8sh3vF"
                    "OtxL4UFeY/8DyyIuE3ccWJEAAAAASUVORK5CYII="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnKCoAAAAa"
                    "HCJYWFh2dncHHJRnZ2hISEk2NjiWlpbn5+jW1taGhocOI0q4uLjIyMimpqYFDToKI5MNJass"
                    "M0oFG2w3Q25olM6YyfxGRT0IHqAJGkwaITGOuvSox/NeXmEMImxXiMmqvuRiYVvc5P9gX1sJ"
                    "DB0pOmQjIh0mLkhNaJuo1f6bstpWdqR9kL8zZKQ/cKQ2VIKAf32YqtNqg7UTOYrG1/giSaFC"
                    "bqBoe64AAAAAAAAAAAAAAAA3es40AAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////8AAAAA6hjChQAAAwJJREFUeNq1"
                    "lmlz2yAQhgVlCXck7DixWx9t0lzN0fv8//+rsKxkZHs67Uz9fgljoSfvHrBqXqCa/ygi/m9s"
                    "j26OwEVycwxuJjdbrqi0vxVa5wM/jDGy9T5KU5ObvzPMHYtKeiYPPAPPSB1U4EFayUFqtsNl"
                    "LZq1+XU78s0HLKL3YuLRtXZQ6+NoR/SYq8Cc0qqrfRvkOStldLjcJUcHnK84n72bJXFwtvqn"
                    "gen0RzGnS+xMbUPJNim52u+TtQd+8fD+1wVpBX4oRcDdxqFRLKtkfXWzy8p/3tpVWNGoyO++"
                    "Xl+7pzt0nDxHVSApr6ATyGJyQskioyqpwlW+80lQyFCDpV19uvqyufr+fFokAoFN2mgyOEUA"
                    "TpkuF8+HwXBLvslryoYfgYN4Xj9s5k/z01UBfyaw9Q2BjcfcaseUK+HrRDN9s5Sc9D8NqQji"
                    "52Kx2TzOyXEjCZwZCHYt5y12XUqApiwVe5xxgZuKe1mDpfi4WK/XCwQvl6eiB7MeDKUhAh6X"
                    "UqEUd6Bj2TTUG7FkpwJ/WCQ9Xp5nLd8I9ZpS0WYwxxynVQQH2SJa3lYqgKIWlHWSE1iJb5fz"
                    "pB8IPt+CdW7MRIsFbBM0rTo7BiuQ+iAYlJieTJNOiqaTG6B261zpM3QEthHZrXSUCknnDySd"
                    "DDtOBbwVPZPA0IN1eTsXjreuB3vKZ0fnjxvm+g4M9Q0Fy30wxeboLAPzxhDY21LKcoJDDspg"
                    "TGbUbskWLM9q8NkWHFzDbboPVK4agXV5WSRKzLlOwXDucxQdI+c9OKUikV+hEnd6e6P7YLIp"
                    "3pU+iwiGPlpZyhfKAVHDD9UFJvlkejZoOrkf5oHYNrxkzuCFNmTRjUGZ63fGgOJiMuj2Xm23"
                    "K3o1n2UsZjWh8Dru727THriQV1qBfknSoIDXPjrQyjOLbeLjKIfl+kkHJHS4NPtjzcz0oPFj"
                    "XkYSLtsdS8bVo8nzQ1Pvz5LMB2WZ2509Qg5op/55SpcJ5VkLhx5AzNew1ePxf7QPluN9Yh3v"
                    "o/Aon7G/AbdSKt35lxPWAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgLo6OsAAAAX"
                    "FxcHHJRoaGlVVVXW1tmVlZXIyMs4ODknJypISEinp6l3d3cOI0q1tbeHhocFDTouNEgNJasK"
                    "I5MOFioFG2w3Q25olM6YyfxGRT3a3eSenqAIHqAJGky9vcFfX2AZIjWox/OOuvR+foCo1f4p"
                    "OmRWdqQmLkibstpgX1tiYVvc5P8MImxXiMlNaJskIx6qvuR9kL9oe64zZKQ2VIITOYqAf32Y"
                    "qtNCbqBqg7U/cKQiSaEAAABoYXU/AAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////////8AGaEN6wAAAyNJREFUeNq1"
                    "lodu2zAQhkmW4aYYyY6VxLXrmdGkaUb3ev/H6h1JyZKcAglQ/4BtwRQ/3abImyjyH5WJ/xvb"
                    "oMkBuJFMDsFFMnkhVwVHuS+eX9PSce6kZh3yC8EFpVZ4TiVTbLA0E5xm8cB24BeppM7AD7O4"
                    "3fbsLlosypX7m1mpW5UDsyxX8G08peJCoN3tik5AK6VNV3vRYtK5tXPj92OQk11y4WmAGwSl"
                    "0VW4WHVCBGam4KoqGj+wWUl39vDp81nWWraxZD7eDQgJZhv8W1KTdyEKjGBRROGtvO9t4a5+"
                    "3d2xm6toMdhcNOtjKioAebskuJODrwo9QAnkwoN4lEhGhB5Yr78+/dg8za6Pk+qKNUEU8AGw"
                    "xhgIzak1hPtkMMYB7G2KAtzjA5NZVV9vHzbnN+fH6wT+zpq8sQgea2AKuDNABtFMUIjJYiTn"
                    "TcJlBb+6D/45nW42l+fZ4rYkkRHB1BWlw3JSsnHXZvNKWhilY+BVCs4OHOrf0+12O43gxeK4"
                    "3gOLFFSvEJTcBb9TTIJjM141D3N98Jcp6PL+FLX4WItZDoVDcElsdHBuIc4MSiRSwDqR7vJB"
                    "5isZY70Di/rb/TnoTwSf7sAVRhFoDRjMBaizXTAjQshA2DNgJerJ0QR0lDQZ3ar8SI4lD00n"
                    "cJ+wxCBY0hwKmUsnyNxzg1CoD3XDzGCZwZh6vIbEQfJoA+bd5BkoDuht3IGl53uNt/gX2NNV"
                    "6mVBeaUTmHHbLTeP1upYbnpQbkYuTrrgky6YGEu5jlmbJ3BImw2OH7TSlUVRcmwQB06oLhhC"
                    "AeR3UcCd0NtdKAx2fKwzYxEcQuOtTOnzqUFW7R+9A2I0OWk1GT22M9vEBDGTOmMOVQFRzIvY"
                    "DghSUTAhsbFnvVkxF64etaKPYjdXRZ6W2Msz9KBzQunO5Gdz98xAZnMh/dssL0PBOpOaclHB"
                    "fPexZ3m3mshFGj9e4MFFB5nLbplluVwuTZTqz9R0JMXKcoNJrrsnUxyqrzr14AyFUexXdu+E"
                    "gILacYV67SmdTihOXWD7CyZYHMP2QvWO/4O9sBzuFetwL4UHeY39C9o0LxOMZHM3AAAAAElF"
                    "TkSuQmCC"
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwAAAAaHCIn"
                    "JypYWFh3dncHHJRHR0mWlpY2NjdnZ2fn5+jX19cOI0qHh4empqa4uLjHx8cFDToKI5MNJass"
                    "M0oFG2w3Q25GRT1olM6YyfwIHqAaITEJGkyox/OOuvSbstpgX1sjIh3c5P8mLkgJDB2qvuRi"
                    "YVtNaJtXiMmo1f5eXmEpOmRWdqQMImxCbqA/cKRqg7WYqtOAf30iSaE2VILG1/h9kL9oe64z"
                    "ZKQTOYoAAAAAAAAAAAAAAABhrTYWAAAAQHRSTlP//wD/////////////////////////////"
                    "//////////////////////////////////////////////8AAAAAd3Aa7wAAAvVJREFUeNq1"
                    "luly2yAUhUXhEtaA5CWpXS9ZmjRJk+77+79X4YIQsj2ZdiY+f6yx0KdzF7hqXqGaF1QmvjS2"
                    "RzdH4CK5OQY3kpuBSyvtL4XWWMcOYxRvrfVc1eTm3wwzQ7zmlvAD98CSrA4qcJHQvEjPd7ik"
                    "RbMyPi5HvlnBInovJuZNK4ta60crvMVcOWK00F3tWyHPSs69wctdsjfA2Iqx+dt5EAMjq5c6"
                    "IsKPJkak2IkeQok2c3KF3ScLC+z87uev86wV2FIKh6uVQaNYVk766kaXlf+4tKuwtNGePX6+"
                    "vjYXj+g4ePY6QUJeQQSQxOS4lEWSq6TH3ESGGszl6vvtl83tj6fTJOoyWIWFKoJDBGC06mLx"
                    "rCuG2yrs8M6QDTsCO/q0vdssLhanqwT+msHSNhmsLOZWGKJNsimCP1W57Xb/Cqlw9ONyudk8"
                    "LLLjhmdwZCDYtIy12HUhASJnqbIX+kOmIHgN5vTTcrvdLhG8Xp/SHkx6MKSGcLhdUoVC3K5A"
                    "LDHxAV9nB8G/l0EP92dR6w9Uv8+paCOYYY7DlQcDscvQcq4Uq8PgdRQBrOmf+0XQNwSfDWAR"
                    "GzPQfALLAA1XnazAERka2jcHwKDp9GQadJI0nVxCbrfOpD7DTQGyodEZNzkVPPFVSHffnaNU"
                    "wDvaMzMYerBIT8fCsdb0YHTl03ZI21qUDnT1CQXrfXASN3kvA7FKZbCVqZRpB8ce88PZUR+f"
                    "AtazGjwbwM40TIbzQMeqZbBID9MeGF6dj4iOEDM6K0IqAvkNKnCnN5eiDyaaYl3qM49g6KPl"
                    "/Q5mdOePckpxNpnOiqaTqzIP6NDwnBiFB1rJohmDItfujAHN6KTo5koPy3V+NO5lTGg1obBu"
                    "/dmt2gMH8kpoEK+zBGhgtY8OhLZEYrzWj3KYBgcHcB1eqv2xpuaiaHybpZGEl+2OJWXq0WTZ"
                    "oan3vDixTstS/mET8II2+r+ndJpQlrRw6Ab4eAxLMR7/R/tgOd4n1vE+Co/yGfsXL8Ip8P7m"
                    "BQAAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgLo6OsAAAAH"
                    "HJRoaGkWFhbIyMtVVVTW1tmVlZY4ODkmJypISEh3d3ioqKoOI0q2treGhocFDTouNEgNJasK"
                    "I5MOFioFG2w3Q27b3eSenqBGRT2YyfxolM4IHqC9vcEJGkyox/N+foCOuvQZIjVfX2AMImyo"
                    "1f6qvuQpOmRWdqTc5P+bstpXiMlgX1smLkgkIx5iYVtNaJtCbqAzZKSYqtMiSaETOYqAf319"
                    "kL9oe642VIJqg7U/cKQAAABIxUIRAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////////8AGaEN6wAAAxZJREFUeNq1"
                    "lgtT2zAMx2MPv+2EpKWBdmV9Mt6Msffr+3+sSbKTJim7Y3dUd7TFTn6R/pKlZG/Isle0RHxt"
                    "bIPODsAlcnYILpKzF3JN7pkMxfN7Vnspvba8Q34huGDMiSCZ5oYPtqZCsmQy5zvwi6xkXsEX"
                    "d3i76/ldtFg0X+7fzEvbWjlwy0kDnyowJtYC/W53bHTVae0iek8trr3fej/+MAbzuksuAsvh"
                    "AsEYhQo/LjoSgZt2SlJX5PzAZ6P92dOPn2fJtrrVkge6GhAa3Fa4rJlKdyFq54TBS2U/2sJf"
                    "/7695XfX5DH4XDT7YyYqAAW3oDslxGowAjSB3G7YSM57YLv9dfVldTW9OY5WV7wRUcAfgC1q"
                    "IKxkTmUyRA9Rh9ZBXqlsKgcu86q+2Tytzu/Oj7cR/J03eeMEHltgCrgyhwwmN/NesgI+pIIl"
                    "2wd/ms9Xq8vz5HFbksggMPNF6bGcjG7CdV33oD5CDKInTl7/mW82mzmBl8vjeg8soqjBYFlH"
                    "niRWIsA/Kj7M98Gf52CX96doy/e1mCYpPILLzFGAMwc6cyiRih4KqmQ8UwV8QB5pDcKRXbCo"
                    "v92fg30l8OkOXIGKmLUGDO4CwLsWjNGvYZXR0hBsRD05moAdRZuMHkwbYkmHTmD0wmUKwZol"
                    "KXSU1TqWansghflYN8wE1gmMqcffkDhIHmvAspO8GZ3lmE/OOrrTwVv+CxzYRTzLgsnKRjCX"
                    "bldunL5TPdtBuSm9POmCT7rgTDkmLWVtFsF5vFkxUpYyF4XgHoIwXTBIAeR3ZMCdsIedFApP"
                    "PNWZcgjO8yZaHdMHZaJ6C70BMZqctDYZPbY9W1HBcxVPxgzzz0LaxLzFrGbU95Arp71eMRO+"
                    "HrXGHsXuqIrULfEsTzGCzoSysfNH9Mw/05D5TOjwNlnQecE7nZpJUUF/D4o6ZbeasnUcHEHg"
                    "4GKDzKWw1KJcLBaKzPR7ahxJ+CzlB53cdicTNdX/mnowQ6EVhwu3NyGgoHZcYf53SscJJZnP"
                    "+f6Gyh22Ybc2vfF/sBeWw71iHe6l8CCvsX8BIyguWOR+ItsAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f0mJigAAAAb"
                    "HCJYWFhHR0kHHJRmZmd2dnc2NjiWlpbn5+i3t7cOI0qGhoanp6fY2NjHx8cFDToNJasKI5Ms"
                    "M0oFG2w3Q25olM5GRT2YyfwIHqAJGkwaITGOuvSox/MjIh0pOmRgX1smLkhiYVuqvuRNaJsJ"
                    "DB2o1f5XiMnc5P8MImxWdqSbstpeXmETOYqAf31oe64/cKQ2VIKYqtPG1/h9kL8zZKRCbqBq"
                    "g7UiSaEAAAAAAAAAAAAAAACaTVw4AAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////8AAAAA6hjChQAAAs1JREFUeNq1"
                    "lmlj2yAMhhFDlMu1cZq0XZaj7dZz7e77//+vicMOjrM1H5r3S2wMT4QkJNirKPaCysSXxnZo"
                    "dgBuJLNDcAOZbbii0HgqVs7XfDdGCtZWjJuSzPYzmDtolPagd31EqAyAaUGW4M3fKt1Lzba4"
                    "UEVjLZDswG5eoYSGacVaMKoa7Yk3rrK9Kt8MZjQ++qoGp6RqB3YrAJbncgGl0XmpQ86XnM/e"
                    "zkgcnS3+tI7zFTiZt676T8jrgoWaBobu98jPbn58Pstaou9DUdP2OTMuGhrDqqGLrod6aKCF"
                    "qngTTDX88vfDg3u6jBaTzY1KEJpKThTMRufU0IYfwM4Rbmvv6HAA1nb59frb+vr743GSqDPY"
                    "EMQEsAmrFMWegueznThyKRPlGIFr8bi6WZ8/nR8vE/hTBlvPMtj46FvpQLkUPhl8tJ0FlHil"
                    "K2rxaz5fr+/Os8UhfyI4MCLYVZxXMZ1UF3x6H1vcNgMfa/Flvlqt5hG8WByLDgwdGFNC1PG4"
                    "tGmh2XVezBb455x0d38atHgv1IfsiiqAefQxPTUxOjyZ3AKOuRqaEqzEx/tz0p8IPt2AZXAj"
                    "0ZoEtgSlp9amZFPPgVGJ6dGUdJQ0nVxhTrfWpTyLDLRMBGu129MV+E50zAzGDkyh1ylQFDzX"
                    "gdMZr5px8IZjiIsxOG/N5bOM4I3JYG/3SjeagouTEnyyAdeOcQutUSFqGSzz4p0HZBBQSa4g"
                    "8pso4k5vr2Rfbckq3qY8ayIY+wqx40gPB7jmk+lJr+nkQncbEpsqqcGZsNdN5RkVoQa2zgwq"
                    "Lia9bi8UFhU3PYezHB1bdCiqkvq/ZZMtpUL5OkuiKr9raFEqDzamiR9mwnOFnobNTPYaJihP"
                    "LSmVh61E4K0KrYl2SK1JF61p7+6vwdfKguP/aqZ+2Ez3v1bw2kOFO9s/j+1fDtv/wS4sh7ti"
                    "He5SeJBr7F+kRCiT7L2eCAAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAwPo6esAAADH"
                    "yMppaWkHHJQnJyrX19p3dneWlpZWVlU4ODkXFxiHh4hISEioqKoOI0q2trgFDTouNEgKI5MO"
                    "FioNJasFG2w3Q26YyfxGRT3a3ONolM4IHqCenqAJGkx+foC9vcFfX2AZIjWox/OOuvRNaJuo"
                    "1f6qvuQpOmRWdqRgX1ubstomLkjc5P8MImxXiMliYVskIx6YqtMzZKQ2VIITOYqAf319kL9C"
                    "bqBqg7U/cKQiSaFoe64AAAAhJ9kZAAAAQHRSTlP///8A////////////////////////////"
                    "//////////////////////////////////////////////////8AGaEN6wAAAthJREFUeNq1"
                    "ltl22jAQhj2qFEmWZAkBTkKh7GRvmqb79v6P1ZG8YBua+iL858AB2f48mlXJm6jkFVUSXxtb"
                    "oZMTcCM5OQU3kJOeXCE5GOuOXiMZFZwTn5EGuSfYAWhmDSgiSBebMOAZQGogS8ge3EseOA0M"
                    "DSjtmlTP2RK4UEwgWMXbum/2aS3fMUsbgd/UArAlC3bvLzEA4afRW5QCLA+5ivMN56P3IxRv"
                    "Ppo4CxJvQIQMy/hjXW9FOiv3DKac7NgsFL+8+v7nstRG1b4kaCf44GWFZtOwrKB8mhiwnb0B"
                    "70SH3/58fCQPt9FitNlV4BGwDEFWr/D1Fgx6WICsHSET0g6kbIPTzZebT9ub6d2gUF7lTgoM"
                    "PwhOgw8Yxl7TxBR2ErkUXZdSKUkrFfO73dV29jAbbArwN1LFjUTwKEUmCzCMIKh4DZOMHklM"
                    "1wb/mEy22+tZabGp3hsYEQzcec59CAeU+6WcH1rMdXONyPzXZLfbTSJ4sRjkB2BW+NSKkNYm"
                    "Xp2m5EiNthYR/HmCun66CFp8yNm0dAUPYJ/oNPyba/Qzwf1m4V/0TVcKdBPM8q9PM9TvCL7Y"
                    "gzN0WYhaBUZzEcr1C+BmwgmWj8/GqLNC4+G9qFPVx6JjYYdMJzSAFRzu+qgrxMe8YpZgVYVA"
                    "FpWBgcPgQQU2PYMn1OJfYAvropYZmCwtwMTonulG1eK8CT5vghOqwaQxavMCLCEtIiP/VyAU"
                    "XYHkd1HIHcP93hU0VHzMM6oDWMq6Q7BmDz5W0jgghuPzWuPhc/1eGsuM0KIy5pgVAJb0bkJz"
                    "xvNhLXhmrtFxi24ZankadtCYUJ45m+0pUjnW8TqZM2XflrJKOtLo1GBYhv3dhmeEaZsUGj19"
                    "qdHj+sqvVisa1Y6JK0YSidkU0ro9mjLQ0zUTvD2a+k09nKHYiu1aQ4v70jDtf6xwOKW5JMfH"
                    "vzkc/yc7sJzuiHW6Q+FJjrF/AbjzLi0VmncLAAAAAElFTkSuQmCC"
                ),
            },
        },
    },
    "Terminal": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnJyg2NjcA"
                    "AABHR0h3d3hWVlfX19e2trfGxsfo6OimpqaGhoZmZmaXl5geHiA/P0AAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAA0ipl1AAAAQHRSTlP/////AP////////////////8AAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEmRTUgAAAjhJREFUeNq1"
                    "lomO4yAMhsFeroSj8/4vu7YhHCmz05UmVtWEID5+G2NQf8TUL1oj/jb2QqsHuEJWT3CZrD7j"
                    "HkEeANtetMH7aHEmfwZGrR0/z3PT6bxudrgJPAZ3A7gre2ntWbA+RPfcBR0r6DeXwBicDV5L"
                    "d9YaGZzoPWi3+ELmi7XxlNe7JIMgUgd5coX+PItFRkY9gUFktuAa/05GAwgV3WyQNetElmxo"
                    "VKpRacYq7WgmnmYFEzKjgh04SegCjcia2LOm3LltVZjs7mAbMszWOxmG5DSmk103i+Cah1/X"
                    "dH5xqIIVHkntwAoCCQs6RO8lLsuM8mYvoePTAJuScKuYc7UnFGcyoJPe0uW57si5BF0x0WZJ"
                    "0S1YRIuVHPmRmt+pg5vOeEVngJlpyFZwDDHZ7BDD2AXWQMuXa6VMB9s1yFcMkPbJCoZsU4kx"
                    "HI06V4QBxp4rNzBh+Hc1anMXC9lePhRoobADUK2soXgvW7cvlmgC5kUmemzxvLaDwZGB6XOw"
                    "4xDo0osBYM8VWNMNb+n2XmcnMBUw2npnpFKhjzKNo6JXpY/FO2pCfgQmEZSI4aTCS+NMnrae"
                    "7csHcPvwrQ0wZOARqFKganaSOrcUIaeWifxPh8ZSkL9YtE9cdoqCA5ZyHFuzpjr8Wy+uxwTh"
                    "KG2tBHFeG6kNvGWcSzXTcct77fezslHoTv7N6tk5H00edqfeD+ZuyEuM7egz/+8pXeMRvwkf"
                    "uMhluJj1+H/swvLcFeu5S+Ej19i/M0AONYWMpHkAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQEYGBgnJycA"
                    "AAA2Njbo6Oqnp6m3t7iVlZdISElnZ2mHh4lVVVZ3d3jW1tnGxsi9vcDe3uF/f4GenqA/P0AA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABSLEwIAAAAQHRSTlP/////AP//////////////////////AAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA7uSkRgAAAntJREFUeNq1"
                    "ltuS2yAMhhGqOJ+SbN//VSvABuO423RmrZtkAH38CCFZ/GomftA24k9jd7S4gdvI4g5uJYuP"
                    "uFlirr9aX87aiFJitOpA/gwcABL/KID3uaeXsJlMaoKHqVIeZre8Ohd2Yp8HYF2oD8qEHthq"
                    "aN421o6OFtQy6wECrwHHCrGp38x2qRQjdfQ5WJaQCBEneU5ZVikBFVOSUAjgp5wm0z5bqEMT"
                    "v2pWDgk7uhvRHo0M4FSNshWJ9TjWl+elMimOw2UHPWaHIDLSk8RpNHbm1VgEsoeHnBZNvnLb"
                    "P1NYXVt7DBSPMzgZfwCjPuYEBD50cGCgBXsepkaoL2nrOWSr5AqWVIK8AovCcXUISJL93BxP"
                    "47LiLrSKsCews4EuFbOQNBKK9ahiU5ulIc/vB8kjOBuYScnz85HyCtxFNyPf8qrpnvrT0Mmz"
                    "eAJXpmNbwZyGLvoUNE7RMZgmFEbmTTAHRR6fx54LztEK1j7yGA9uVGfnq5xgPXLlDcxyayCq"
                    "Vaw8hYLPNB+uRCpbKPZ4moe4DIXemNNWcI5M60/WpBpkWi9Pha0GKFjy5h9gfhkQLdBeDNSj"
                    "V8iLdLOndNPyGzAXMKkzEJcKwN8Hv7JLH5dXK4nM34PNodB4xR45SX5YwcNruMVxfcacBva9"
                    "38Fj3+JN9bDCYd0kh+lanwPvOkJWufK5FtwzOJxaCF+/dHUDEkbqpRxTr/zqCy8KslrJuBb6"
                    "B5BiebFVGSvUnHz1xuG8d72V2LcWUrRl+9LVzq0p1hqm28UbWM9ij50J5lk+a6ZNYupIdRYU"
                    "J9fn/+zSWzMof5lIVMswvfLS/m/7YLnvE+u+j8JbPmP/AKlsFUDa8aHVAAAAAElFTkSuQmCC"
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnJyg2NjcA"
                    "AADW1tZ3d3hWVldHR0jHx8fn5+i3t7dmZmeFhYampqaXl5geHiA/P0AAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACbphsUAAAAQHRSTlP/////AP////////////////8AAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEmRTUgAAAlBJREFUeNq1"
                    "loty4yAMRUEq71f2/3+2ksA2JDTNztSaTGzMcHy5CGH1JaH+MAbxr7EHWt3AFbK6g8tk9Rk3"
                    "JLkAbHvRJmOqxZn8GRi1dnyNcdPpjB4R3AS+Bp8B8KzsobVhwTqI7rkLTqygX6YE3uMc8Fi6"
                    "s9bI4EL3SbtlLhSxWVuj3D5L8ggi9SJPU6E/w2KRkVVPYBCZw1xvXsnoAaGjR1xkzTqRJXsa"
                    "VborI1ilvZqFX7OCCZlRwQ5cxLpEI7Im9qwpd242wVC4TnbPYJsyzHF2Mgxp0lgiT90vgtPQ"
                    "PbSaZUIdrDAUtQMrSCQs6VSNEV+WN9K1dbCdHs1g3wpuFXOungnFmQzopLcNeaApiVA/xiTs"
                    "DCaQzZKiW7CIlmi58kV0n/odGTJyo3Z3ZjAzPcUKrqkWmx1iunaB9TDy5Vip4nLud3Y1+fAA"
                    "aZ+sYMi2tFpTGNS5Ilzg7KzfggnDv6PRmzsvJAFMajCssGP/OTuysK1WvJatpyeWaALmRSZ6"
                    "HX6Gsf8AdTwysHwOdmyBbmcxADxzhZuU36w6j9oxp9trnZ3AVMBo68VKE9ahTeOo6Il0ndg9"
                    "w94GrZfS+g5MIkhLilR4aZzP09azfflKX9d8PngXFxgy8AhUJZGbkTaXW4qQU8uLzG+HxlKQ"
                    "/7EcU7jsNAUBlnJcR7OnOrzXi+sxQTiy0oqJ89pIbaDcoA1SeqbjlvfY72dlq9Cd/Pt1ZnE+"
                    "mgzsTr1fwj0hDzH2RMf8v6d096P+YB+4ymW4+fX4v+2D5b5PrPs+Cm/5jP0GbpMPXPkOvM8A"
                    "AAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQEYGBgnJycA"
                    "AAA2Njbo6Ounp6nW1tlISEmVlZe2trhVVVZnZ2h3d3iHh4nGxsm9vcDe3uF/f4GenqBfX2A/"
                    "P0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABCpMqnAAAAQHRSTlP/////AP///////////////////////wAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1gFXgwAAAp1JREFUeNq1"
                    "ltm22yAMRRGqmMyU5Lb//6kVgw04bpuuda2XxMZsHw6SsPhRQ3xjdOJ3Y3e0uIFbyeIObiGL"
                    "j7hGoim/Wl+OOo9SondqIn8GTgCRfxTA+9gzSOghoxrgI1TOj20Ps07OPInnPADLg3pSJvSB"
                    "LYHb24u1pTmSWkYDQOJnwLJCrOp7uAYk76n9O5vlCIkQcZDHkGOVElAxJQqFAGHIqTKbuSZV"
                    "8atmZZGwoVsQ7W4YAKuKy05E1mPZFTM2lUleCVVDGAvNs8lERgaSOIKON/PTmAXyjAAmLppC"
                    "4QrhZY3A+gBmo4TYGBy3MIFRzzkBiRedLGxQzR6LKQ6pPSmqZavkApaUk7wCi8y+WgQkyfPs"
                    "uB/rZinR982Lahl7toKtS3SpmIXEI6FYj8ou1lHq8jbQ2TjIfRF+BjMpBi4fKa/ATXRLrFD1"
                    "Vd2H/ojqKZtFPIoncGFajhXMaWh9iEnjEO3TVn2EI/Ns9P2fr16PfNxzwVpawTp4vsc3O9W6"
                    "UZU7WIkQPKf4JZjlFiNKFKw8WcFrGoUrkXK3wvf6i77X3MkK3ZkjVrDxTGslu8ViMs2bx61E"
                    "c22b1qfmvPkHmCsDvAPam4F6tCLe082Wm66mmzulm5Z/AXMDk9oA8YIBf03zcpOuuKlpvclS"
                    "IDhX/DV4mxpNUDzDRMmFlQK8jmm+bZ9t1v88bkxp+g4+3pvDVmY4YbG8xKQxtZRDuTI1hCpc"
                    "+Vwb7hmcTkcIb7+05QUkNqmXdkztUn3hRUNWKxnXRv8A4qZY4GyG4y55jLxa/tkQbDtK3NsR"
                    "krXj+NIlzkeT5x7GNsTaF9a1uPlkgrGWzw7TKjE2pDoL8oMbzH+e0v0wyH8YiFTaML3Mcvzf"
                    "9sFy3yfWfR+Ft3zG/gZSixZ/M1WtjAAAAABJRU5ErkJggg=="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnJyg2NjcA"
                    "AABHR0jX19d3d3hWVlfGxsbo6Oi2trempqaGhodmZmeXl5geHiA/P0AAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAferckAAAAQHRSTlP/////AP////////////////8AAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEmRTUgAAAkJJREFUeNq1"
                    "louW4yAIhhXW+637/i+7gCbR1JnpnjPh9DSx1i8/CBj1R0z9og3ib2MPtHqAK2T1BJfJ6jNu"
                    "SHIB2M6iS9ZmhzP5MzBq7fka42bSWz0s+Al8LT4N4K7spbVlwTqI7nkKTqyg31wCY3A2eC3T"
                    "VWtkcKH7pP3iC5ltzuUot3dJBkGkXuTJFfqyLBYZmfUEBpE5gmvsOxkNIHT0sIusWSeyZEOr"
                    "So/KMFbprmHhx6xgQlZUsAMXCV2iFVUTe9ZUV24n+zvYpQqznZMMQ3IaS2TXzSI4XSMDHLLZ"
                    "oQ5WGIragRUkEpZ0ytZKXJYnTmrD/acONq3gVjHn6plQnMmAXmbbLI92oXUn5uAw0VVJ0S1Y"
                    "RIu1mvkiuhf9VkdO0bxER8DMNGQrOKdcXPWI6aoCZ2Dki+yU/LWO4Ls1yEcMkOpkBUN1peWc"
                    "wqDOHWGAGUm7mtUGTBj+HIM+3MVCysumBiMUrvORwt1rta2heG9bt18c0QTMmxyHvNzLoZe1"
                    "OTOwfA72HALdzmYAeOYKjLTLV++Y0+29z05gamBUejFTq9ChTeteB7CSJ6Nx94T8CEwiKBFT"
                    "pMZL60ydSs8dFXw0Q3cvafUNGCrwClQl0eZHctwvTcir5UH2p0Njach/WbQt7HRTEGBpx3kM"
                    "e6rD93pxPSakWiW3wrI3sm9cMt6Xnum45b329axcFrqU2dzdWGacjyYLu1PvB/M35CHGnehY"
                    "//eU7vHIX4QPfOY23Mx6/D/2wvLcK9ZzL4WPvMb+A2z4DrDuVlYoAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQEYGBgnJycA"
                    "AAA2Njbo6Ounp6lISEmWlpi2trhnZ2jW1tiHh4jGxslVVVZ3d3ne3uG9vcB/f4GenqA/P0AA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC4oj0CAAAAQHRSTlP/////AP//////////////////////AAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA7uSkRgAAAoxJREFUeNq1"
                    "louO3CAMRTGueYTnPPr/v1obkgEy6XYr7VjajRSGw+Vi7KhfLdQPxk78aeyBVh/gNrL6BFfI"
                    "6lvcqDHK09rLUV9QayzeTOTvgRNA5YcBeB+7BQ176GoG+BUm5/t2RFwnZ57Ec+6A8kM7KVP2"
                    "hZXA7W1h62iOZJbRAJD4N+BYITb1e/gulUqhjj6b5QmJEHGQx5BnlRrQMKUqgwBhyGky/a1Z"
                    "nZr4VbNxSNjRPYgONyKAM+KyV5X1ONYXx6Eyqbw2Fx10zyYTGRlI4wh6rcy/xqyQZwSIddEU"
                    "hLvoA5iNUmpjcN3CBEY75wQk3nRysEEze2xGHDqoKaubPkkWsKac9BVYZfbVISBpnufG+7oc"
                    "lpNFRIQ/gZ1PdKmYxdRXQrEek31tozTL823NeDJnY1INfH20vgJ30S0otLxqumf9nDeQ+2J4"
                    "AgvTcaxgTkNXQk0Wh+iStiYUJPOMypb/hd38wuPz9ThywTlawTYUfscvd6rz41Y2sOz+ofhU"
                    "SV2CWa4YISFYfbKC9zQurkbKuxWl2+qpG/Fmhd2ZI1ZwLEzrV3arYjJNh/dsAz19DSx58w8w"
                    "3wwoHugoBubeK2RPN9Oeez77U7pZ/QWYC5i2EYhLBeDvaV7u0tvJdSOkkuj4NXibCk0wPCNW"
                    "zTmVAh/VEaUfn1E2Ly+mNH0Hv9bNYZMZXjmURWIaU+XceNWmVf6Eq29rwT2D06mF8PFrJwuQ"
                    "2rRdyjH1ym+eeFGQzUrGtdDfgQzLK+2CeWXG4KM3DheC663Ev7WQbD3H00qcW1ORM7ctozZY"
                    "9+LnzgRjL99rpk1i7UhzFlQGN8T/7NJ7M8h/GagkZZgecWn/H/tg+dwn1uc+Cj/yGfsHkWkV"
                    "pHCAHXsAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx0nJyf9/f02NjcA"
                    "AABHR0dWVle2trZlZWXGxsenp6d3d3eFhYbX19fp6ekeHiCXl5g/P0AAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABWjEytAAAAQHRSTlP/////AP////////////////8AAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEmRTUgAAAhlJREFUeNq1"
                    "lut6gyAMhkkyzqDu/m92CdoZhLX+mHlabUFfP0IOmq9m5h/tIP439oU2D3Ab2TzBFbK5x42+"
                    "nQCms3aVC4A0+R6YEJ2cl2U269DzBRTRavB5868BXJWtiFUEY2y69RR4ZzGZHExECn5YElhL"
                    "2mDtpgMrEnDh335Xf868ngT8fHvhrpagST3Jail8qCKWBJlQgcFBUSyXeaB3ogWCHX3YSUbR"
                    "KU40FsGU3SuH1bYGZRv6HszIQAZmYEZFYAdEXjazEZWmsFzW7hY3gLMPoO2MJoYR8LcsfNRu"
                    "dHYIkbUfE7ChWMwMzDuPmD36VCvqtdtO/XFt2+YObLdCU8UtVF8mkQy0bxF4PyqOqVPMIA5F"
                    "CZwpuIlutoUkp103TbPpChamZevByaeSgyPyv6JjtvtkRDdyM6YLuMUC50kPhpDLlpKPBzWT"
                    "DrbwCcwY+bz+7H9nvljkUP0GN10xlq3LSGZaA8smM72pWn0aN68f+wB24gLcmmZoYXE33MY6"
                    "q8BQJfWWxKUC46bvmyZIt6HvwFwmeI/8YlzlOLBBp94kpe2H2n6CIYD4mEzhLsEJYlWQDUUo"
                    "of/UNLqC/C2ia5HyuxmIoMpmfls2B73UtwnGcf3MLSno2gLeFfqX5+f5bHJqdNeO/b0Qg7Sm"
                    "4OSRWbWm+93f/SXnaKa1b6b3wZD+ch+XDmn/tm//j72wPPeK9dxL4SOvsT+76A03MNxd4QAA"
                    "AABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQEnJycYGBgA"
                    "AAA1NTXp6eynp6mHh4loaGnGxsjX19m3t7h3d3hJSUmXl5lWVlfd3eB+foC9vcA/P0BfX2Cf"
                    "n6AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACihrfZAAAAQHRSTlP/////AP///////////////////////wAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1gFXgwAAAlRJREFUeNq1"
                    "luuSpCAMhUnYcL919+y+/6NuwFZAnR5/jKnqslr043AMCeJPC/GL8Sb+NnZFixu4jSzu4Fay"
                    "uMT1En29an02qozziCoYNZCvgQ1ArASAE6ywgPxAlmCE6uD+gHOvsIafX3YAkt95AdYHtRqo"
                    "Ae0T0BfrGVzQHSbWicYYl8VhgeUIDUmIBzb1w4gPj+aX4/mfe24mJELETu5DmZVJQCUyIxUC"
                    "2G0sRJ36NMoWHWfNKiHhgl6CaHXDAyRVXc4ighaJXdmM4vnSLJCaW4OJjLQksQeFdYxRbB2y"
                    "yxZ8BIAwGhGFmj9knMCBwTHYAYx6zAkwmn8JAjSzV058+r2nLka1A0tyRp6BhWNfEwKSlDCu"
                    "nWc8ZAEL0DtwyoZOFVdpsAannXI56mVCPCpGGu8FJkUrJUp5Bl5EtyBL9dJ0P7I62aXTzQqu"
                    "zMQxgzkNU7HRaOyiiwntZTkk3hYFaHRmzYWUaAZrW/ge33xTU+4r/QaME5jlViNqVKzcWcFr"
                    "gi4ZyR1XfWqFfjN7zGBfmNagOsRqMl38eJ/BvDOgZKCFzP9fi6oL6ablB7CWILUH4lIB+MVb"
                    "u08Yf9ogJ+AwaLCKa4+PkmuDsWMFs2MNPtvS7gjetDgb6rfOImGdxJueCz8XIWH3YLNrIYWz"
                    "Ky1ZGuRmU7A6DU/Gou3OdTWTcS70LyDF9bM0jVkoNRd696HQ84p15vina+xbU+EqzzZU+wKY"
                    "qeFxazJAj7/W49yaLjXTJjEuSHWxmV4/Vjhy30zL7V8e2/9tB5b7jlj3HQpvOcb+B4xLFefV"
                    "bqYoAAAAAElFTkSuQmCC"
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnJyg2NjcA"
                    "AABISElYWFl3d3jn5+fX19eWlpeGhodmZmceHiDGxse3t7impqY/P0EAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABCVjJVAAAAQHRSTlP/////AP////////////////8AAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEmRTUgAAAlZJREFUeNq1"
                    "loly3CAMQEEKIA4b+v8/GxCnsdNuZ2LNbsJi+6FbFl8s4helEX8b29HiBS6TxRvcQhafcnUk"
                    "6+H5GppobTK4kj8EA8kUjJXm6Ugrm5x6Ac9jh0CWjSsj77jyuLtchIFl9M0mUApXgWO9mmz5"
                    "e3hJQYVz1RuZZ50xiXi5kQ+FwKpO8nKolyr/C5JUtV2GaUpRszlX2TsZFSBUdJNJ9nw3EivK"
                    "hhjZ7SlaLvqXW88rOCMDCngAO6lVBrkE/CR7UbYohcFtUSlkvYNNDLBKd6LO3wzO9moKeJbg"
                    "WT8Ujrz4012QvWF3sMDTixvYWdHAaNm3imSgqqbK+lX/mq7o3Jpg5TzeNS4MBlMEiJx12QGq"
                    "eamrp/tWMWJN9kI0WR8QYgPLDtYV4Llczm63H+CmZ+remeDCVFk2V8QCBvZxXiVNumQZ6zcj"
                    "pQbYXJ3cfYC5Tq5gVaKSaamCXYbm1emuYBz5u4Ezpnz6j/qzykk1z7gotBNH0dZQc4WZgJ6d"
                    "F1fAvcb7jqpPl8BBpA62zZ+9HBTOkvGfgQ21WtbSIjawdS1kzQMj3XBLt3ujHWBPAlzuB6FE"
                    "rYFVffjIlHQN3iklic/AuigFZ82zxGDdrTUjfADbxs+tvYOPmfBGEnJDG16kK6hw7T9mBo6G"
                    "HHq1Ui1puUwobsept5X40JB3fdd+bPLMUcFKx2fZtN6o6uAwWvuTl/jIO7Z67sfUkcTLuHdy"
                    "WkeThaep93cx0vrgJO2mHmagKfz3lK4Tysr4FHLQqbRhp67j/7UXlvdesd57KXzlNfYb/hwQ"
                    "hf39j4kAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIYGBgnJyc3"
                    "NzcAAADo6Ounp6loaGm2trhISEiVlZfJyctUVFV4eHnW1tmGhoeenqDe3uE/P0BfX2B+foC+"
                    "vsAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADztIfaAAAAQHRSTlP//////wD//////////////////////wAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAqfLXdwAAApZJREFUeNq1"
                    "louOozAMRbHjDSEvaKft/3/q2nlBQqc7Kw2WRhoVcnJ9ndhMf1JMvxiF+NvYip4u4CbydAVX"
                    "yNMPuWtUgHZ+/8wFhaiC0wfyD8EzABmLEPSqh0d3g1ACo97BLbT3r6XG2i9eQHl5hWQ5dbrn"
                    "hpVQy1mTpWNsnTBC2clbAPM0ors9cVkqhUAZPbrlSBEppXbycU+ILNcApFT5n8fBIpbp7snq"
                    "LYnvNWurSGV0DqK1PUpvMyLwT17QAXwpm6B2/au8il2unpGGUO1BdecvMBuDLL3SSuRcV8lA"
                    "wgg3V4G3YqKQY1cdBsfFHMBqriYa/mOwEw+MQyA/oc0KxYckcCvmahwkCxjJb3gCE78n4C/H"
                    "TMNLI1ewyIytWKEK5R3ADWDrNjorFkYCg5oXJcdpbRRq8tiTrSURjmAmRcPXB/E7sMkAu8qx"
                    "zjzOO3si2otO3kwNYGFajsEKJeBlorTwRuyz5iOS9LE6M4I5HTxej3oWrKUevLGLUrUKZrkM"
                    "VdSD53Z+T2CWK0ZICBYrmOu8pEtnJHtDkxdwgGJF9XN5TW+tmAtzjwqWLOVmsFlcPKhg7Iun"
                    "t9IDNDTf/wW28Mh32QBuLoM10jfHzQ3HbcYPYL6XgC5V7ZbBMS/20uu64mnFSayfwUuzwktG"
                    "6Zx5EnCMNdvQyrcsww+1V5zBdV+fCqR9vhk3PhXsYrm1ch1yVbPBwsV7197MCN7bpindUu7y"
                    "XTI4TCiXO39C65t605B1T1aHRs860Gzc361PnfJ4mqZnHhzWyOCCoXIl5dlx3GaJYTTNeSTJ"
                    "Xl4NndwdJ1Nqquep9yF4hnIrtg8aJwRvFXauWf93SucJhaCiPj/wkaQN03Ptxv9lHyzXfWJd"
                    "91F4yWfsX+ypF+UPmcEEAAAAAElFTkSuQmCC"
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnJyg2NjcA"
                    "AADn5+fW1tZISElYWFl3d3iWlpdnZ2eGhoceHiDHx8e3t7impqYAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAyNcn6AAAAQHRSTlP/////AP///////////////wAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH/QjAAAAAmxJREFUeNq1"
                    "lomSrCAMRYG8sCv8/9e+LKBoW7NUjanuGVrlkFySoPknZv7QBvGvsRNtXuAK2bzBZbL5KTc2"
                    "9NU934PUvO8JVvIPwQ5tL8nb9LSkt8P2uIDPZQ9zZDeubXIl8/R8uekOrKA/YnIhwGpuW+92"
                    "z3+3arGEsq9+g/Awp9RRhjfyFsCJqyd5WbTaQP+KxaCx23KGwm4OcYP/JENw4BQ97CRXeRpQ"
                    "HJVAkp3xsJeL//zofgUTsoBxD+BsYyBQ7k5miop27FJRbvG7J4tKjndwasWtNkWM9CUwxRux"
                    "wM6b5+vhcBt+D19JDX8HG9ir+QBnbwYYvGgb0BbU8APRYCaLajIvreCQK3x6zAwBY3OuSdaR"
                    "AGGopO45S0kEKjxeRDdMTOQPzbuB7QRHTYgq5aI7RHHXUZbGjNzoqs4KZmYgu0nRGOxEYxr1"
                    "iJFdFJfPnaqxjBRMV5GnBkB1cgUHTkyidQVngtJoz1dwiSk8ggnDn/lDf6rtqHkmHsVsNvY2"
                    "4ZAizdRJozLyVQr3WePzStDZvHGu4QT7oec+6s+BxZmB9WfghKOWo/UAA+yzbqVWcOWgQGKC"
                    "W7p9NtoDXNG4TP2g8K4NcNDJG1E6a91YPc9R7HZ4/j04slNu1zzrAo4z2qTbV7VAynHhy9Y+"
                    "wduZ8MkiSEM7VMQriLn+mzMDjoZcxlSuZdnM5YSSdtxnW2kPDfnu79qPE505oXibZS3f1weD"
                    "th8qkLrLEB55262e5zJ6JMmw3Ts5rkeTd0+n3teWrK8lW7yHuqUDjeXXp7SeUN62py13sXMb"
                    "zuF6/L/2wvLeK9Z7L4WvvMb+B13jEa/CMS7xAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIYGBgnJyc3"
                    "NzcAAADo6Ounp6loaGlISEi1tbeVlZfW1tnJyctUVFV3d3mFhYeenqDe3uFfX2A/P0B+foC+"
                    "vsEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADdT74vAAAAQHRSTlP//////wD//////////////////////wAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAqfLXdwAAArNJREFUeNq1"
                    "lomO4yAMhsF4wYQrbWfm/R91bY6G0G7VlaaWRhOV8PH7JOpPNfWL1om/jR1o9QFuJatPcIWs"
                    "3uTGYjR493wtZANgcrAT+U2w0xrJg8422mXpSqC7QbEH+G42pZ9tWDxv3rRJ8grKdjzpdnes"
                    "mNkeNXmcbT8JQ5CTkteavkl031dCA2LO2J7WaAU0iMaYgzyfqQvLJa2rq/xwm0LEMltw417F"
                    "nzVbb9A0dDPEeF+qbzMi809JGFmnnjZBsX5bTUV5FU6+JkYSgjkMx8lfmnYGefxRshPY1yge"
                    "iJFw+SCoRk1EOWWHwWWjCWzcCCLxH4ODxIACaEwKfBMscWC9oyjYPVgkCxgw7fAARn5PwF+B"
                    "mcRbC2dQZLKVmiyret4yP+78PyxgH3Z8VCyMCtbGbUbKKebhLnZ5m3Yphhr42IJzgJlUiNsH"
                    "4F9gakH1UUDNXfa7xaQYe4V9HGYWsDA92xIKI+BNYXXwghxnyyVSKayO2lu+5P6Ua6yPUh21"
                    "4D2ewbtEkWkDzHIZanAGW0WUi7JPwSxXAiEmWBhgzvNWm45kH6FKAs66hyL30im599wSCteZ"
                    "hw2wpF46g4PFydMDDHPyEhcH97a0lJSefw/s9a31MmnYQwNbwLncvKgNtdzCUm4OXoC5LzWE"
                    "mrVLA5e2Ocn4EZVmc24DaRDDTsTX4O0eiiQe1TpLKOBShre5pc+3Brndf5hmxSN4nJtqgmxq"
                    "nXHhquAo9q6VdhBQrKascOF6Gm+0go+xSX1aSi9fxYPphgrT5LcX82Qg2zPZTIOedQDtPN99"
                    "7VmYq0l9t/HjSS4uvWSuu+wC28WJLVeTa1dSrSyzTPIw30x1qD7eei+M71Aexf6G6w3BR+WD"
                    "S/F/b+l2Q4E2xT4upIIyhvE7nq7/j32wfO4T63MfhR/5jP0LGDAZGhOU9hAAAAAASUVORK5C"
                    "YII="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/PwnJyg2NjYA"
                    "AABISEno6Oh4eHmWlpdZWVnX19ceHiBmZmeHh4fGxsa3t7impqYAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADvsSVyAAAAQHRSTlP/////AP///////////////wAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH/QjAAAAAmBJREFUeNq1"
                    "loeS2yAQQClhKaL9/9dmC0gIK4kzc9qx77AkHttX6heL+kEZxJ/GTrR6gctk9QaXyOpbbmyQ"
                    "inm+Z31LqXu7kr8EG9A9+6T905FJD6lxAV/HnmJQNq5ufCXQ9nC7aU4soz9sMs7ZVcyx3u2J"
                    "/h5FQ3a5rnpb5qXgfQdebuTDWcOqXuTl0KId/ssanNiu82UKqTmc69In2TpjjaCHXOTCT1tg"
                    "RdkQr6c9pOWiPz1a72BEZqvMAzjo6BAUuuGd7EU9opTvXCHHHexbNqtMJ0b8IhjtjZBtpeCl"
                    "circLojDLeiNtIOVrUV9gENSA2wT+9aBziBqOtTPLtrW/ZKAXSj2U2NiMBiaMY2zDh3ghpcW"
                    "9TA/ghixOoeIHvXBfRtYT3CUhChcLhIhtLuciKSBQtpv3mEwMR3K5opGYMM+xlWPECnLWOUR"
                    "KbOa4e9Onj6wWCd3sKPERFoXcEAormpYwITEhO7qAYwY+swf8lOkguQZF0UM6iDNPAxXeOFb"
                    "dPcx/d7u4L3G5xUnuylwpsEEs1ZdykHK2p0ZWL4Dexi1HHWydoBTkFBKBVOO9at3rOn22WhP"
                    "cAFlAvaDTFEbYCebjwnEo8fjVWtQ34EjKWWq5FlncJzW+lnBsxn6vaTVn8HHlfBeg+WGdnoR"
                    "7iDipn/MDHs25Dy2Ui2zQ5cJxXHrs620h4a867v2Y48zx+WkA5+V+vqgk8HhYyyVl/aRd2z1"
                    "PI+RkcTLtndyWEdTMk9T7+/idSo5nOG/lPEnGvJ/T2mZUEm3p5Cb2KkNB3cf/6+9sLz3ivXe"
                    "S+Err7G/AesaEO0mRvB0AAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIYGBgnJyc3"
                    "NzcAAADp6eunp6lnZ2lHR0jIyMu1tbeWlpjW1thUVFV4eHmFhYaenqDe3uE/P0B+foBfX2C+"
                    "vsEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAFJMZwAAAAQHRSTlP//////wD//////////////////////wAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAqfLXdwAAAqpJREFUeNq1"
                    "lg1z5CAIhgU5o8Zost32///UAz8SNb2d3kyXmWuvJj7CC0LUn2zqF60Sfxvb0OoN3ExW7+AK"
                    "Wf2QuyUN6Jbvn4WoEXUMpiP/ELwAkHUI0WxmevSwCNUwmQt8mvH+a222jZtX0F5eIdlOg9/L"
                    "iRXT690nR73tg2OEcpJ3APZpxe/zSSiuUoxU0LNagTSR1voi92dCYnctQA6V//PZScRuhkeW"
                    "es/Ojz4bp0kXdDGi7XyU32ZE5CUv6Ai+pk1Ql/+bvIpDrJ6RllBfRu3kD7A7gxx95Z3IsW4S"
                    "gZgV7uAfL6QhOwxOq+3AemkiWv7H4CAa2IBAXqErHooOp4Nm9+qBk8sCRvI73sDE7wn4IzDT"
                    "8vbEGaxupiFZTg7ZeSlMYBd2unssjAwGvaxaymmLLVzq3eP6cCWIXhzeopPl64P4L7AtorpN"
                    "yrrwMLOqEPyHL4fpCSxMxzZJoQW8KsoBHsQ6Gy6RXI3snVVG+YV/cB7zGoeD/fVoteAcjeCd"
                    "VZSsNTC7ywBNJ1iif/Iq5KU7mN0VIcQEiw3MIa750lmJ3pLyAo5QpYhF1kBQa3uSYqnMyxpY"
                    "Ui83g8Xi5EEDY5e8I9/lkk8Dne6vwQ4+y122gHsoYIN0lZvJv2s9h6ncFnwB5nsJGHLWjgJO"
                    "ZbOHrGzOXBHCaA5iew1eTym8RJTrzJOAU2rRxpI+LhM/LHS94g5u5/pc8MaXm3FI/sHVayF5"
                    "K1lVue8JFx9De7Mz+GqbtnZLucsPiaCbUKF0/oI+9DcN2Yxk3TV69gPtzv3d+dwp+2pSzzI4"
                    "nJXBBVPmashLYDsWsWk0LWUkyVleT5089JMpN9X71HthPEO5FbtPmicEHxUvrt3+d0qXCYWg"
                    "k7k/8ImkDdNzG8b/2z5Y3veJ9b6Pwrd8xv4FLRUYGwSje/YAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx0nJyf9/f02NjYA"
                    "AABISEhYWFllZWbo6OiWlpYeHiC3t7d3d3iGhofGxsbY2NioqKgAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABKMeUmAAAAQHRSTlP/////AP///////////////wAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH/QjAAAAAjFJREFUeNq1"
                    "lllywyAMQJFUNrPe/7QVWwLGTfxRazJpDPVDuxA/VcQ/Sif+N3agxQPcShZPcAtZ3OUqZ1KA"
                    "6z15iOgE0Ey+CQaDWfuE/vJMdIRIEeUMfgm9BFhOXHR1xSKLXTbBKYlZeC0iknabTSAlzQLH"
                    "vJtT+T4CGi11XPTWiKLT4MBZ6SqHJKiqvsnToaH+v0Yju+n6taUgTCzleWEBkwSChu7yJgc2"
                    "HwSZqmg1xOOwJ2FYNbToVjAjNQm4AFtkJx7CZqiHxPIH1XCEOdmujNrA3mmYpe8whAqYylua"
                    "Y8/BS11PJbcUOda1AhYUg9jANokOplR9Kw1q08Ini4/OWcCJdwJLG2jXuDAq2DgAV9NJj+Dz"
                    "865xzIvGDOJULIlzAuMAq5YQoZZL7K9d1QudwYUpWU6ucAUM1cf8K9foQFM5otq5HvMJXHOB"
                    "62QFy+JGpuUGtgzlX9G2ZNPfwIwpn/HQHptE0/KsMpQVR9HWm5uugD26Y4VD71ugOHhmgFuN"
                    "u7wHb137APam17LCRNTByd5Mt73RvsDBCLAYSZeodbDsL18WyBLQD2BVtILY8ixXsHp1iIuS"
                    "lt9a+wAf7y7p0VCx9d15tiaU0X0B0/HuuM24UsvVsdOE4i7pP7bNTd+5H3uMSuqEtp6V1kz4"
                    "0uiH2ad6Hse0kdTawykRIOoymrQqo8lPo+n29PeYgrZo4K9hmtZhev9aASGhU5fjH+r4l+v4"
                    "f+zC8twV67lL4SPX2F+zuw+CCax0zAAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YYGBgnJycCAgM2"
                    "NjcAAADp6exoaGnIyMqnp6m2trjW1tlISEmGhoh3d3iXl5lVVVaenqDd3eB+foBfX2A/P0C+"
                    "vsEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAA0mXo3AAAAQHRSTlP//////wD//////////////////////wAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAqfLXdwAAAmxJREFUeNq1"
                    "ltlyqzAMhm1JtbxDlvb9H7WSgYCBk8NFo5nMBAwf0m8tNl/NzB/aTPxr7II2H+A2svkEV8nm"
                    "IjdVQJvD6ZorPgG4WNyGfBEcEImzxdElt8caRiiIg8Vi3ApeH/D+Jy6W+pcjgtdHCMUobKkR"
                    "+IGQRk4CHttjO58yba10jpHVL/mMyA9Wv9clRkzx3tTyHvGx5w4ERACwkrffxCrOCaIqUf48"
                    "X6HUkOvqP4+h9j67DAQTejKi9FqS8KOqPMotr+gR57edxdw7SAjdtRckk4XVKM5L38hFQJl+"
                    "JNqMVhROWF9CVOP6jaz97gi4Rt6AYd6iAVl+Ah5UA5a9J2/s5Kerj7TX1NfqdmBLvtgDmKxr"
                    "4O9BmKww2UEc25okmT9JzLAD56HQ0WNlNDBCiAAiUBpxjtcDHD0G2t6TV6CytWDtv8A8aZqT"
                    "prVt4d4Hd1Kj3U0FKzOL7aQABUdDg17dSHR2Em/LxqbN3kakrTJLLuRMPbiIZLprC1jcFSjQ"
                    "GzB0YHFXhVBTrF3AkqqxFR1rhEzGK3jEY9SnUoSZudoCNnWqDBFLNg8XsL24eW/AGZ9TLTPa"
                    "MkxgZ+liugX7Bix1iXZou3abwBWHqTfU/xXICTi+pPAaUcszTwqu9dUheNuDz0raH8GLL76V"
                    "mfNTZdwkKxCzu9qEDO/BZdNxp26ptXzXCDYTKnLImwZbx8A71V1Phk2jdyNaLtLfs76TbO+S"
                    "Nnr/ptFLyGEQuwW13WgK00hyLZswmt1oKkj3JyfoR9O1YSozVFpxfhJ23HfD9PqxIsiUhurO"
                    "x789jv+PHVg+d8T63KHwI8fYX31aGEv0/klLAAAAAElFTkSuQmCC"
                ),
            },
        },
    },
    "Visual Studio Code": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f0AAAB0dHUm"
                    "JigeHiA3NzlWVldFRUfX19elpaYWl+3n6OnHx8dmZme3t7cBdsqFhYap1vOYmJgGZqoAW6NK"
                    "mM8qiMs9qPBrpMqdxuJBqe4Absjb5u5Dh7k/P0Cc0/bL2+V5t+Mjn/Icca83gLOgvdBPs/ap"
                    "y+OnwtSMudsAU54ZgswUj+RhnskAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACFGwomAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////////wAAAAAAAAAAAAAAAAAAAAAAYnmBDQAAApNJREFUeNq1"
                    "lmuXmyAQhqGMIIgiUZMmm2y72+398v9/XgdEBPWk3XPW+RCNyuM7L8MgeeeDvGEE4ltjJzTZ"
                    "gevJZA+uI5OZWySxfLA2/gCwieHMSKkYT8nkvwRzSoU7ar1xU0gaohYJOEbZsBhNmY8tKJVO"
                    "MK297vQWRKxHr1ICRbNQ+RMNpdyBLZ4bKrJcMGTHmNL+dEEuFlwkR5+58046sdwhFU3A4GUG"
                    "c0u5JpcJ8nLwh+gGdTq5k1ziKDu6EsKpZPNf616T6nWpTnEefn916IYU8Wm0zuCIhnoFkDoU"
                    "uKFeHFmkYBa5f4a+qqqDG1Ak6XBMmlsNaSpe8FiHH6bXySyhEewdOA79UH2qPqZgAgb/GmqU"
                    "lN6X1MDRXzYJnS9F8PP1eNOPQ9+f3y8Uu1qNGblKBi68vi7KEzERnZruwZf2ejoh9zt9WIO9"
                    "aB9d4wvIhrxtBAedanInseJX2/aPN0ozxcooyxrBuZlXASsh1Ms0U2UEs9TkEfz00rbtkS7A"
                    "0DDbKWXqQE07wgzmsVbW4G/98HJq29Nh04rJC7+8pOkgWDH5GdtTt7Li/PPz0w3J7fPG5OED"
                    "0pM1QNkhXQU/p+VQ8rkC7cKKL3hNH5G8Kjc3N4zTLjYD4LFWIC83vi63EEheLhBsYLj0tMJW"
                    "QesuHUeD9Hny6rEgN5f0tXq4pEuau3NiNDZeHFc2ydJjcfoAFhc2mtCPS96EoAE3ghNrsJtp"
                    "fFZkTUiQ7EUy75t32ubYC1C0tK7tdATmds7T3j2WOrym0QPisGyZN5GvU62ZEHasdL7auu5t"
                    "TUx5uvC/+T2uUz0Stna9f4RYIKeZZxGtm81d+t72P7q1vfkTEMq14a7Mt//dPlj2+8Ta76Nw"
                    "l8/YvyPeGnlov6TuAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQEAAADo6esW"
                    "FhYmJienp6lVVVY2NjaVlZZ3d3gWl+1oaGnX19mIiIpJSUoBdsq4uLrFxcip1vMGZqoAW6PX"
                    "3eK3vcNKmM8qiMtrpMo9qPCdxuLb5u5Bqe4Absh/f4Ejn/JDh7kcca+c0/Z5t+MAU55hnskU"
                    "j+Spy+MZgsw3gLOenqCnwtRPs/Y/P0CMudsAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAFgzA/AAAAQHRSTlP//wD/////////////////////////////"
                    "////////////////////////////////AAAAAAAAAAAAAAAAAAAAd7KOlAAAApFJREFUeNq1"
                    "lumWoyAQhakBZAmKGs026U6me3r27f2fbgqIIJruk5zT1p8Ykc/LraKQfPBB3jEuxPfGDmiy"
                    "ANeTyRJcRyY3cUvGS/dbFFdHleCMcaHoiHwbuALQ+EMB5mMbw+ASTNMEvilanIRztsAdvhgp"
                    "I0XEuuDNnZ4ZgAohYFEh9+ovoYJUKYQM6OJ2qFKokgGnSNGEcgCT9HqZauOtrrz41zUf13lq"
                    "ACx1LiuiUY9FfWVKKpJEdKa0EDy7Gof+258MjU/zlnCcYaDUmSbjuP6qaXFZ/tmxUeP42ner"
                    "1Wo9qQmocNGVhQa82WkxzqHwiDcXLZtL9rBd3/WrT6vPeVGgr5YDlwzn2XRfx2SJQagToXLu"
                    "y2n3QJ/6rjt8nChGIToWFOqhrdKeJ6M8MyykjOaknNWn/R65v8njDBxE+5DG15XXnfTrqBNH"
                    "+dSKH3XdPT0QkimWXFphdFXwJFpUjRcKsfISGE1h0735XNf1jkzAhRHWSskHrlWx2EbgItbK"
                    "HPy965/3db1fX7WCNEkxMC7bixWDn82WvGbF4cvPzQOS65d58kgpkBa2bKOdyTJPHq2qywVk"
                    "deNji0N0h+RZueHOAKFADs2AbkOHvFJual5ul0DydINgA2NFCRJbBfC/o3ntID0mz3WStOMn"
                    "lpxWj8eMiymiOKPUDDdWZeAch0RMX9NMbgyLHV3/O+a3WtO4GYpY7l5SVmmq2w741khxXLa5"
                    "px9XLv3MuhdI0rAia8cydH76i19pyJS+9X8LkqI84buMGg+ew8FhjbHhKJlnjmaRjwnXwwqf"
                    "+GbU3dIREiu8IHedev5FOiCnTbEViWvKO0/pQAjb7cqAlq4Ny3OZHf+LfbAs94m13EfhIp+x"
                    "/wG6SBnviNtUyQAAAABJRU5ErkJggg=="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f0AAAB0dHUn"
                    "JygeHiA3NznW1tZWVlfn6OhnZ2empqZFRUfIyMgWl+23t7eFhYYBdsqp1vOYmJkGZqoAW6NK"
                    "mM8qiMs9qPCdxuJrpMpBqe4Absjb5u5Dh7mc0/bL2+V5t+Mjn/Icca9hnsmgvdBPs/apy+M/"
                    "P0CnwtSMudsAU54ZgswUj+Q3gLMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADHNYB3AAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////////wAAAAAAAAAAAAAAAAAAAAAAYnmBDQAAAqVJREFUeNq1"
                    "lud22zAMhYUS4pRMKfKos9N0r/d/vAIkTZOSkrbnxPhhLfLzxRUIqnkXonnDSMS3xp7QzQW4"
                    "gdxcgsvk5sxti5gP7H04IK5ipPBKOSFLcvNPgiWA5qMxKw+1ghS9LsA5Oity2K6e2wIoFgx9"
                    "0F0+wowN6EVK6KAKV4+wAJLBA5170FUuFGYSwplwOiO3My6Rs8+SvVMsVjLSQQHGIDOZ26kl"
                    "uSuQx6twyG4A65QsuaNZQ3QlBasU58uB/6bUy6me4nr36zOjbdPm0WSdpxkWggIsHWKuVb2i"
                    "0JGsS7DI3I+77WazueIJbZGOpKTlYLBMJQj2SXfSqqqEIjg4sN9td5sPm4cS3KCnSw/eKRV8"
                    "KQ1kf6cIFsWtAvx0u78z97vt9vr9TDHXas6IKxmlDnZMSR4CtvQW2pSEqMHH8fZwIO43uFmC"
                    "g+gQkw0FFHRn/ZoMSbXhojuVFT/HcXt/B1Apdt4Nwmop/XkViA5TvZze1KCtjWeiNDmCH5/H"
                    "cdzDDIxWDJNzvk/UsiOcwVaL7iXwl+3u+TCOh6tVK05ehAJQfsJkhUjrT4tUhdPCiusfvx/v"
                    "iDw+rbw8GqAC2SB2E9Fd8rNP6w8lmFMFDjMrPtE9syfyoty4KISEKTcDlLlW+JLqm1Xb1Dvk"
                    "6gIh8nyBUAOjpWccJQz9VM6DKB08Uij2tgcwLy3p283NsVzSks8bb6jx0rzOFktPxNc3xJk2"
                    "31hvQt+PdRNCizxDNoMnNw2N1VUT0k31R6rum6+0TY6vLEcN3HamBs/tXJa9O5Y6/k+jR8KR"
                    "lSKYKJep9rRAhljpcrF1vbY1CRfoOvzWz6Qp9Shc2/X+EnqGPL15kdHGru7Sr23/0a31zb9B"
                    "7bgNT129/V/sg+Vyn1iX+yi8yGfsH82dG5u44x5NAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQHo6esAAAAX"
                    "FxcmJienp6lVVVbW1tmVlZc2NjdnZ2kWl+1JSUp2dneJiYoBdsrGxsmp1vO3t7kGZqoAW6PX"
                    "3eIqiMtKmM+dxuK2vcRrpMo9qPBBqe4Absh/f4Hb5u5Dh7l5t+Mcca8jn/Kc0/ZhnslPs/YU"
                    "j+QAU56enqCMuds3gLNfX2CnwtSpy+M/P0AZgswAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABcRJ5fAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////////////wAAAAAAAAAAAAAAAAAA9om/dAAAAqxJREFUeNq1"
                    "lumWoyAQhasYdgJqmz3T++z7vP/LTQFGRe056XM69Scq4fNyqSqEdyngDaMjvjX2jIYrcBMZ"
                    "rsGNZLiIq7nQ8VfKxVGjBOdCGTYiXwauEAP9MMT52NZx7IIHNoAvioYm0ZwjioiXI2Uge2wM"
                    "0b7SM4dYEQQ9KRRJfRcmA61SNl/Jy6HGkEqOghElABOIbtCbZGZzdZXEv6z5cFNuDaJn0WUD"
                    "gfR4ckUPm0okxYClAO0xe7YYp82XzwWa/i0aEDTDoQ6FJhe5AIqncMAiOSxzP23Wq9XqZpIT"
                    "WNGiK48tJrOHxUSH2DkpkmVzyQm236w3qw+rj2VSkK9eoLCc5vnheUibxaDbNwXJMvKsiKe7"
                    "/QN73KzXp/cTxSQk9AlFelhjQtp828lrUTbaYNMtQk32rL7b7Yj7C25n4Cw6J5ZL+pLuXn8Q"
                    "bMuzRTQqplb8rev14wNAodgK65ULlRSDaFW1yUfsM88H1V2p5HVZm891Xe9hApZOeW+tOHO9"
                    "6ZOtBzNwTlGKL4N/rzfPu7re3SxaAe2gGLmwTWeF6uovqK7m5lacvv7YPhC5fppvHmhFtFyy"
                    "bYgm2/HmUSuRVNs696lx3qQ40n/YnsizdKPKQGXQnpsBO+YiPqebjw9NSjczT7cuiDwtEGpg"
                    "XGq0tGAUf0bzmiydUVOTsuWxQMS44ieW3K1uDwWXtojRDB04FVbl8L4fUnn7fLb+Z/9gtNjR"
                    "9fdD+ahxbZxhwIv4El0NU2M5xDudAljk8u1r+nEVt5/7+AILLZdFO7b5ln0TCw2Zsf/dH9FS"
                    "U4xwMsOMB+9z/nnnfD5K5jvHiijHFPUwsiGkvlCVx8D4ZMJhLZedeulFISOnTbFRA9fpV57S"
                    "mZDLbWEg2NiG7b0ujv+rfbBc7xPreh+FV/mM/QfXoRsBF/GoawAAAABJRU5ErkJggg=="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f0AAAB0dHUe"
                    "HiAmJig3NzhFRUelpabX19dWVlfn6OkWl+1mZmfGxse3t7eGhoYBdsqYmJip1vMGZqoAW6Mq"
                    "iMtKmM89qPCdxuJrpMoAbsjb5u5Bqe5Dh7mc0/bL2+V5t+Mjn/Icca9hnsmgvdBPs/apy+M/"
                    "P0CnwtSMudsAU54ZgswUj+Q3gLMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABZbO3OAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////////wAAAAAAAAAAAAAAAAAAAAAAYnmBDQAAAplJREFUeNq1"
                    "lul24yAMhWEImCUY29kmSdtpp7Nv7/94IwniCNunMz2n0R87Nny5uiBh8Y5CvGEU4ltjL2hx"
                    "Ay6RxS24SBZX7orFdGCb6KL1Isao5JxVhpPFfwk2Unq8hrDw0jtZovUMPEYT1RixqeeupHQo"
                    "WLakm7/SI5bQs5S0lVXYekSU0iC4g/skfZULhBuUsoFuJ+TVhAvk0WeD3jkUaxBpJQNrklnM"
                    "bdyc3DDkcUOX0Q2JOg1KbmBWl10pgSrV9WeHf8P1YqqXOO1+f0F0FKtxNFiXYEaUpEBzhzg3"
                    "kz0Hq5H7abddr9cbnLBi6RhI2nRB81RIcGJpa7SMJ5TB5MBht92tP64/cLDQCX4mmaxz5As3"
                    "0DC17fQRgZ/Oh7vwsNtuT+8ninGvjhnhTtbGkx0DlwerMOQkVA0+9uf9Hrjf5f0cTKIphkgb"
                    "iHRX+p0MOMFyd4oVv/p++3AnZaXYJtup6I1J1ypQjS77hVaKCj0W8xXPIoMfn/u+P8gJWEfV"
                    "DdamtlB5RyhgRMKqWrEM/rrdPe/7fr9ZtOLiBZWXS4MuVqjMN2B3Hj7MrDj9/PN4B+T+aWHx"
                    "YIAjctC6GUKRZ3M5GF5QgfuerfgMz8IByLPthptCGTmMzUCbca/osu3stXeYxQIB8rRAoIFB"
                    "6QULrUK2A593AUbIpDTuvCEXS/q8vj/ykjZ4L1KAxgvzmshKT10qWK8mD5aa0I9j3YR01DjD"
                    "iC7B4gcY66sm5EX1R67umy+0TYxvKNp1mPQg9LWdG96781bXr2n0mqqV9lYrzTzVVnnf5Z1u"
                    "ZkfXS0eTskSnMpP1OxO4HqeXTr1/hJ8gLyuvRnSIi6f0S8d/dmv58BfaW2zDQ1Mf/zf7YLnd"
                    "J9btPgpv8hn7FzmMGsGcOD55AAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQHp6ewAAAAW"
                    "FhYmJic2Njanp6pVVVaVlZcWl+1nZ2jW1tlISEl3d3iIiIrGxsgBdsqp1vO4uLoGZqrY3eIA"
                    "W6MqiMtKmM+dxuJ/f4FrpMo9qPC2vcTb5u5Bqe4Absh5t+Oc0/aenqAjn/Icca9Dh7lhnslP"
                    "s/Y/P0Cpy+M3gLMAU56nwtSMudsUj+QZgswAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACapByxAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////////////AAAAAAAAAAAAAAAAAAAA6tpW/gAAAqFJREFUeNq1"
                    "lul2mzAQhTWqVoQEGIy3OnGaJt2X93+6jiQwCEiPfU6YH8FB6NPlzmgE+RCCvGN0xPfG9miy"
                    "AjeQyRpcTyY3cXPGc3+VcnFUK84YV5qOyLeBCwCHFwowH9saBl0wRwfwTVHiJJxzBu7xcqSM"
                    "yCvWB6/v9MwAFAgBiwp5UN+FjlKFUiKi5e1QrVElA06R4gjlAGbQG2TqbbC6COLf1nzYpKkB"
                    "sNS7rIlDPRb15UNSkaSuzuQWomeLcWr/fE3Q+DQvCccZBnKXaDKeO3qUerJb5n5pmyzLNpOa"
                    "gAJfurBQQzB7eBnvUE8tSrJlC5IDbN82bfY5+5QWBfpqOXDBcJ4d7rskWdYv4kXolPt02T/Q"
                    "17ZpTh8nilGMuxYU6qGldoEnxvJ0WDOfmONzVl12O+T+Io8zcBQdQphQV0H3WD/WDZRxMT61"
                    "4m9VNa8PhCSKBRdWGVdIPohWRR2Egq88SkqJf0xnvsLx6d58qapqTyZgaZS1QvCea/W12CLY"
                    "v/2RYFYFWQZ/b9qXXVXtNotWkHpQDIyLsrNCRVu1iEYsWXH6+WP7gOTqaZ48kiukxS1bO2+y"
                    "GCXvOQzE8qWQ1E2IMz5D90ielRvuDFAaRN8M6Dl2yFhuNFy7etbzcusCydMNgg2MyRwEtgrg"
                    "30bzyig9ZC4a4TvJsOMnllyyx0PCxRRRnJE7hjVVGExVHyqmjxJZJjdGLzv6/fuQ3ipN7Wdo"
                    "YrlfJC+GqT5vuGr/OPVctr2nHxc+/cz6BQSpmUzasYidnz7zhYZM6f/+P4OgKE+FDabHg8d4"
                    "cFhjbDxK5pmjSaRjyudchoqqR91tOEKuFS7JXadeWMhF5LQplmrgmvzOUzoSRPnGgBO+DYtj"
                    "nhz/q32wrPeJtd5H4Sqfsf8Aca4aUSn3MToAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f4AAAAlJSce"
                    "HiBzc3RGRkc3NzlWVle2trenp6dlZWYWl+3Hx8fo6eoBdsrX19eFhYWp1vOYmJgGZqoAW6Mq"
                    "iMtKmM+dxuJrpMo9qPAAbsjb5u5Bqe5Dh7mc0/bL2+V5t+Mjn/Icca9hnsmgvdBPs/apy+M/"
                    "P0CnwtSMudsAU54ZgswUj+Q3gLMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC6EpdXAAAAQHRSTlP//wD/////////////////////////////"
                    "/////////////////////////////wAAAAAAAAAAAAAAAAAAAAAAYnmBDQAAAm1JREFUeNq1"
                    "lolyozAMhq0VvmOOAjk26bXd+3r/x1vZQGIb0mZnimYaUlA+/5Z1wD4EY+9oI/G9sROarcAN"
                    "ZLYG15PZhVtEljs6HS6IixhReAeUMZndJFgCKH8ty6WnCjQ5SAciBl+WtfxsVqQ/LQBqLxhc"
                    "0B0/Qq0EGMYtcyCtnm0JDSRmUg9Lijy4oe96UH95Mq2EtH6miBUZl8jnOEsfu9qLlR5pIAKj"
                    "wiZiKU430vBHyP1duJz9wev0QSQvZM0QldHqsIfIWtCxXr+hyQ79n68ebdmomVAOKQCOvIKC"
                    "SJMts72rUiVgfuZ+7rvNZkNkPoEDTCL9NSVCEkYlZilSxPcGcIjAru/6zafNYwymk6d/NWhT"
                    "1xDvXSTqR99wzDH4+bg7lQ991x0+ZopDqk7mMxnlcESo9VyxM5nifXXcbon7A+7n4CA6WGtD"
                    "Ag265WI1zULxu6q6hxNAotho03CrpNRn0Y6LIQIuyejROJgM/PRSVdUOMjBa3rTGaDdSuYyT"
                    "zd4A/tb1L9uq2t4thmKKRek/at3i7aE4/Pr7dCJy9bxweORQB3KJKFqiB1WFNvPD0/nhwRfy"
                    "L3dEnqWbTwouoQ2aMaTFf6TbaETOCwRrX3qloVYBro1+t1wg6lpJHzf3+7ikpf/OdMlUTXkg"
                    "bFx6CyUtrjahn/u0CaFFH2PJGpoSVCAiSrJZEzKg8xtX26a371503fj22zJ0GLVN/mrbfKPR"
                    "I+Gof/JQFDIfAa81+jdGEzeBrsJn+gyd9aPJKr8kj0bT7dNfLcmJhmmdDtPbxv8QLbyyJLUO"
                    "P/5FOv5Xe2FZ7xVrvZfCVV5j/wHQnxmEWuefKAAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcBAQHp6ewAAAAn"
                    "JyeIiIrX19poaGrFxcgYGBh2dneoqKoWl+01NTVJSUqXl5lWVlcBdsq4uLqp1vMGZqoAW6PY"
                    "3eJ+foBKmM8qiMs9qPCdxuK2vcRrpMpBqe7b5u4AbshDh7mc0/Ycca95t+Mjn/JPs/anwtQZ"
                    "gsw/P0CMudupy+MUj+QAU55hnslfX2A3gLMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACT2iBTAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////////////AAAAAAAAAAAAAAAAAAAA6tpW/gAAAmdJREFUeNq1"
                    "lg2ToyAMhgmHIFAQqdr22m67t3vfn///113AtiraHTuzZqbTEfRJeAkJ5EM08o52Ib439oom"
                    "C3AjmSzBDWQyi8sl4+Ff66lZmuWcMeoz2iPPA2cAKhAAJrBEAMMXCgkZoR14luUAEr85AAsk"
                    "TXtUz8QGGHeCI9ix/EHNBGA4RIMlpGEx+t4M903UK0f/mwegRYGRSWCUFIikDEDc5rzStnND"
                    "hdPqfsz79XDjACwNKhdEgSYWVeE3kgxr6JuJak3asf71ZYBGFErHUGUBXAGA7wuhCB1upLrD"
                    "/VNXq9VqneQEZBp/FjxEsa8cteGjfVaKJkMRtq2revV59Wn4MupqGTAjJfTXjh5HimIASbK/"
                    "nrcn+lxX1fFjEnEIDa6GaUfzQunWIRtHzEwyti/Pux1yv5KnEbgNOpoRJvzFuJuCTpzS0eD6"
                    "W1lWzydCBhEbZqwTKtOsC9plPn4se4l3MwcmGWleyrLckgSshbPWGHbl2qJb6R1wknA/q/pl"
                    "V5a79aQUeHShC5mZfHrVU4PH3/+aE5LL1/HmEe6QFqHaqyCymb955ICe6BbJo3TDkwGuANOS"
                    "8fnQRjUv3S6G5PSAaAlSczBYKoB9x6PdOVRzDshVkvPqaZ/EICjWHq4k1oZM9CuY6NfgqSPd"
                    "d/JjPxzKhQ97XRDLghOedbnwUBG600IcZpdts9TLm4peaNvVDqKcFonqlL71fABDsX66GGMx"
                    "mAyFPn+z0NOBJUmPVR5lCPL5XnW7tKYMTPNXcDZsTbO6XnSkWiSd2UznXytyc6frhPYvx+1/"
                    "sQvLcles5S6Fi1xj/wN5xhoxRHtE6wAAAABJRU5ErkJggg=="
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f10dHUAAAAe"
                    "HiAnJyg2NjhHR0jn6OhYWFnX19eWlpdmZmcWl+2lpaaGhofHx8e4uLgBdsqp1vMGZqoAW6NK"
                    "mM8qiMs9qPCdxuJrpMrb5u4AbshBqe5Dh7mc0/bL2+V5t+Mjn/Icca9hnsmgvdBPs/apy+M/"
                    "P0GnwtSMudsAU54ZgswUj+Q3gLMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACop8TpAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////////wAAAAAAAAAAAAAAAAAAAAAA/xFZZwAAAqZJREFUeNq1"
                    "lutymzAQhZGK7rIEGOzaTtKk6f32/o9XaXVBAurpj7AzmWgwfByd1e7SvINo3jAi8a2xCd3s"
                    "wAVyswfXk5uZ2xaxvlX0XFqyjaG4l9JgWpKb/xNMODIaS4Q3fhMSxehEAc7BNM6h2YKLehCr"
                    "/OOq0k0yFtCrPRGDqjDVHUaCVxZxzXRX6qZwt1QYGw7LBbldcB159plY5HegEQ8bEUjPW/Ey"
                    "o7lMrsmsQF6O8C+7YeFuykEovA6j9FavstDvb+1KvU5Njuv0+4tH6wBxvgrmQArMscFFFLOk"
                    "M5eQTBYlGGfup2k8HA5H/0AbTBTuz4HdfgXXtPPJkzYL7mHxLVng3JBLMDhwmsbp8PHwIYOV"
                    "bCKYSvCWcaQ5zgYGf3ESOl/K4JeH040/TeN4fV8q9gwA856QHk6dTglQWZ7IOeGl6QC+DA/n"
                    "s+N+R48lGCWwCAAL5dKlfdsMjjpNcqew4tcwjE83hCrFqvdgAh67lRFc+FMG+uZMsQzGpckB"
                    "/Pw6DMMJLcDMZ8XRTAArB3WrTtVgms/vGvx1nF7Pw3A+1lY0HQ/nDIpCqKb1ajGPViQ/SaoL"
                    "tbLi+vPP882Rh5dKsd+lf9onjvQ8gWX0M5UDo3PJ2IUVn901fnLk6rg5cbGWBZKURrBUMWXR"
                    "gXzc6Pq4xXDkqkAayxuiXD/QPmsRzMLDre8pdfI6J+5fJf1weLwUJS28KNKFc2YALNJucU4f"
                    "IYsLG03ox6VqQu184DHiFBpadpHXIM+Vdd+80zZ1qlYeShoVE4qWvZv2Gw35XqPHbuYwLZGC"
                    "d0mz3mqHhbAdLOlqdN0ZTSSMJFj2C0mUl3ok2Zp69wMjabVCfDnVWpzRXG9O6fvj308oiXqx"
                    "9YMwvg0rVo//3T5Y9vvE2u+jcJfP2L8Eixxe/apyfgAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgLo6esAAAAW"
                    "FhZoaGk4ODlUVFWVlZfJyctISEl3d3kmJiampqgWl+3W1tm2trgBdsqGhoip1vMGZqoAW6PX"
                    "3eJKmM+enqAqiMs/P0CdxuJrpMo9qPBBqe7b5u5+foBfX2AAbsi0vcac0/ZDh7l5t+Mcca8j"
                    "n/I3gLNhnslPs/apy+MUj+QAU54ZgsynwtSMudsAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAD3zmA4AAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////////////wAAAAAAAAAAAAAAAAAA9om/dAAAAqpJREFUeNq1"
                    "lodyozAQhiWdOqIaXGJf3C7J9fL+L3e7EmBEYMaeCZpJjJH18W+HfPKLfOBqiR+N7dBkAa4n"
                    "kyW4SCZ3cvPEUW7T6T2tHOdOaTYg3wlOKZXCcqpYzkZba8Fpu3jCbuC7VkGdgQ8m8biMdKc9"
                    "FpcrHnOZ5Dn8N5ZScRKou9/RQapUSgZ0+gA2tTQBuYJSbypcnAcuApl67V1defHzmver6Cuz"
                    "/teAUCDbIFpR04YNUTf9Of6Usxnuofn1Y4h+oqICkJVXf5KDrTlagEsgN0QBHgVEJCfT3O9N"
                    "nWXZauBEAX8A1ugDoTmVhnAbFKIfvMCqdS7jE5I9bNPUTfY1+zKIG/PgJw1MAUcTiGArM+mD"
                    "pTqh8ASqY+7rZXNkL01dHz4PFSPDg6lLC4fplPcU2csDn1S9EWoUs/Ky2wH3L3meBIsAsDmm"
                    "deCB3cEnqL3VCQ9zY1f8Kcv65UhIpFg6BBdE+oNbCX5mkCJeH6gTYzCYw8e1+VaW5YaMwBV4"
                    "EaPWgUEuQJ2MwWmfv+/BP+vmbVeWu1XsCohz4YtOoPVCEoNgRVtXdP4srmTOFYff39ZHIJev"
                    "kWK0EisDAgfBox2Yx8FjVdVe0N7v/brCFtsAOUo3yPlzqGVBeaUDmHE5k276fbq1C8hRgQCY"
                    "GEm59lHbBnASDhvsdVHwmAMj8pmSvmTPezJ0BVRr6nyeGYngJOmsVX34imJ0owvR4PrfPrpl"
                    "fICYCZWxhawAL7abWA4hquEIcvn63q4p2m6JtbxGCwYTSofO79Fs6yYaMmOz30EHFxX0d2t8"
                    "p4yy6RQGhxU4uOhk5Fi04k4fRhLeNG7UyfVwMvmm+tDUgxkKrdie5fsJYdSNK/JHp3SYUJy6"
                    "ZGI+mERiG5anPBr/i72wLPeKtdxL4SKvsf8B9sgc8HweVrMAAAAASUVORK5CYII="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f10dHUeHiAA"
                    "AAAnJynn6Og2NjjW1tZYWFlHR0hnZ2eWlpeGhocWl+2mpqbIyMi4uLgBdsqp1vMGZqoAW6Mq"
                    "iMtKmM+dxuI9qPBrpMrb5u5Bqe4AbshDh7kcca+c0/Yjn/LL2+V5t+NPs/YZgsw3gLMAU56M"
                    "udsUj+Rhnsmpy+OnwtSgvdAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADaEjA7AAAAQHRSTlP/////AP//////////////////////////"
                    "////////////////////////////AAAAAAAAAAAAAAAAAAAAAAAA1MJKQwAAAr1JREFUeNq1"
                    "lmmT2yAMhi1cLgPxkbPJXt3tffz/v1dJYAdsN9MPG2Z2lnHg8asXSbj6wKN6x5GI740d0dUd"
                    "uEyu7sElcnXl1tlYLrW9MkGuY7TojWmFzsnV/wmWClonDIiV36yBNDqbgafRODEN18y40LNY"
                    "T9t9oVtOWEYvYpItFKMtVrSGvQqgXOO6XLfm1coL0Sqezsj1jIvkq88yAEXgQMVALLhrKCQz"
                    "mduYJbnJkKct/5vcCLxaKxbKrxMwvpVUZvppaZfrRTXTOO9/fCa0ixD01TYI8mxOiC5COiUX"
                    "uc50BoeNZJuDxcT9ud9tNpstbaijiRb/EIzxWuV0R4dnwiS4T7qTVnTDzMHswHG/228+bV4m"
                    "sDdVAmvD3jYKnBKTgXpMlujJ+CgDvz0eL+ppv9udP+aKicFg1UvZc9a58QB8kidB1rwoqhcl"
                    "+DQ8Hg7I/Q0PORhGsI0JEbhc4glh3CGVZVWl3GijO4UVX4Zh93QBKBT7nsCSPcZZa5UliSz5"
                    "elLBupSCIjc5gl+fh2E4wgzcUGIirY1gj1Ccdb4EOyuaf4F/7fbPh2E4bEsrqk7FPGNF1lc1"
                    "qRUqWSFS/VmRKsMvrDh/+/p6QfLwViimc6bddHCyVyPYJD+7VH9SgxozMMys+IPP1BHJRbqh"
                    "uFTLFozWCWx8PMpYwYGC0hyTXqZbGkguCgR3VdJjP3B0agncxM019RTyGoOR0lAUHSTlKyX9"
                    "uHk4ZSVtSZTsYp61DLZjtCIeX4g73fRgvQl9PxVNqL4mvACluaFNLqoSRFxT9s0bbdOlrVTL"
                    "rCG7oXTeu3W/0pBvNXqBd07jDHh+l2mXoXZYIKHjqV5cXTeuJhmvJJ72M0la5XqMXLv1bg8B"
                    "JjgPan6r1WJCK7d6S9++/umGMtDbtR9sS23YN+X1f7cPlvt9Yt3vo/Aun7F/AWD4HWtjMTQP"
                    "AAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgLp6esAAAAX"
                    "FxdoaGk4ODlUVFXW1tlISEmVlZfJycunp6l3d3gmJicWl+21tbcBdsqGhoip1vMGZqoAW6PX"
                    "3eIqiMtKmM+enqA/P0CdxuJrpMo9qPBfX2Db5u4AbshBqe5+foCc0/avvsl5t+Mcca9Dh7kj"
                    "n/IZgsxPs/YUj+Spy+MAU56Muds3gLOnwtRhnskAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC5IvX8AAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////////////wAAAAAAAAAAAAAAAAAA9om/dAAAAspJREFUeNq1"
                    "lomS4iAQhoHlJpBD4zG66rhz7H28/8ttNyQOOLFKq8auUtGEz58+Qz5FIx9oA/GjsSOa3IEb"
                    "yeQeXCSTK7naB8pNNX3NqsB5UJZl5CvBFaVSGE4V0+zs0kJwOhj37A18lTU0OPhgErfLQnd1"
                    "wqKF5jaXSa7h3RlKxVGg7tMVm4BSKZlW1Q3YylAPcgWl8aiw2GUuApnJubqL4i9r3syKr8zE"
                    "uwGhQLZDhqJuCBuiQD+LRjTeytkF7rb/8T1HP1DRAcjIJ4I7OZxV4wnQBHLhj3g0kUT4ae6/"
                    "fl7X9SxzooAXgC36QFhOpSPcJMHoB9A7JgUcj09IjrBVP+/rr/WXLG4sgh8sMAVs9RBBlAnm"
                    "Y7AYGeKmYNnBpy25z4fVnr328/n2c64YGRFMQ9UETCetxuPKQV5DK6dtdLxOzili1h6WS+D+"
                    "JY+TYJGcajSC0nHh3MknPrAF78Y/C+eu+Na289c9IYViGRDcEBkPuJbgZwYpEimgTqS7jFfD"
                    "SkVfl7X50rbtipyBO/Qi0EYwyAVokDmYESGUJ2wa/HPevyzbdjkrXQFxbmLRCdwnJHEIVnRw"
                    "hRpSx6uh5t67Yvvrz2IP5Pa5UIyhx8qAwEHw6AjmefAcJAfUNpY9pp45c8UT3MNWQC7SjRi6"
                    "S7UsKO9sAjMu83QzqNbGdLPv020wIBcFAruIk5TbGLV1Avu02WH7QZWhqaqGY4EEOIS+UNKH"
                    "+nFDcldAflYh5pmTCPZ+PK1K4TOpQHanH7JOk61/b4qfXAwQc6ky1pAV4MXhIpYDgnQ0wpDL"
                    "F9d2TTF0S6zlBZ4gm1A26/xsHSYaMmMXv4MOLjro7ybWLC+y6ZjajxE4uOhk5FhhZadPIylm"
                    "Vjjr5DafTLGp3jT1YIZCKzY7+X5COPXGFfrWKZ0mFKfBT8wH5yW2YXnUxfi/2wPL/R6x7vdQ"
                    "eJfH2P/YBB3WVt3GqwAAAABJRU5ErkJggg=="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx38/f10dHUeHiAA"
                    "AAAnJyjn6OhHR0g2NjdZWVnX19eWlpdmZmempqYWl+2Hh4fHx8cBdsq4uLip1vMGZqoAW6Mq"
                    "iMtKmM+dxuI9qPBrpMrb5u5Bqe4AbshDh7kcca+c0/Yjn/LL2+V5t+NPs/YZgsw3gLMAU56M"
                    "udthnskUj+Spy+OnwtSgvdAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAFxgKMAAAAQHRSTlP/////AP//////////////////////////"
                    "////////////////////////////AAAAAAAAAAAAAAAAAAAAAAAA1MJKQwAAAq9JREFUeNq1"
                    "lmeTmzAQhpGIhKopxiX2tdyll///97K7EkLCxJMPh2ZujsHo4d13i6g+0KrecUXie2MndLUB"
                    "l8jVFlwkVzO3ztbto3qQphXrGMUHYzxXObn6P8FCMu+4YXzlN21YXJ3OwGk1jqflmgWXDSTW"
                    "4nZb6BYJS+ibmIRnxfLFE96QVy2TrnFdrlvR08Zy7iVdLsj1ggvk2WfRMozAMRkC0czNoaDM"
                    "aG5jbslNhjzv6V9yo6WnlSSh9DrOpreiykw/PtrlekFNWpfDz8+IdgECvuoGQJbMaYOLLGbJ"
                    "ldxA1jmYJ+6Pw7jb7fa4oQ4mavgDMMSrpVMdJs+0SfCQhQ3vBDfMEkwOnA7jYfdp95LA1lQR"
                    "rAx520jmJE8Gqkxtt7xF4LfH01U+Hcbx8jFXjAwCy0GIgarOTQmwuTyoDxuC4CX43D8ej8D9"
                    "zR5yMJvAOhRES+0SMgRxtwlimMQNPncnWvGl78enK2OFYjsgWJDHcOW11FhlJDlmSuRh8DyK"
                    "AH597vv+xBbgBgsTaD6ALUDhqrMZGJFQ0L5aB/8aD8/Hvj/uSyuqToY6o6bQtqpRGZfRCh74"
                    "CuyeqnNpxeXb19crkPu3QjHmGXdj4sQgJzCp8qEdVN5QMvc9WPEH7skTkItyA3GxlzUzSkWw"
                    "sSGVoYOxxvw8O9RqgwC5aJCqlZWwMA8cZi2Cm7C5noDw6jgiOhD3r5Z+3D2cs5bWKEp0oc48"
                    "gfUULZ86WNSLG2tD6Pu5GEL1XPCcSUUDLbkoSxByTTk374xNF7diL5OG7IRS+exWw8pAvjfo"
                    "OZw5jTPM0ruMvw2141q3HV2qm6PrztEkwpFEl8NCkpK5HiPWTr37izPTOpvSP2eeJ7R0q6f0"
                    "/eMfTyjDBr32g/Y4hm1THv+bfbBs94m13UfhJp+xfwG5XRyK8vcX7gAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgLp6ewAAAAW"
                    "FhZnZ2k4ODnJyctHR0hUVFWVlZcmJifW1tinp6kWl+14eHkBdsq1tbeGhoep1vMGZqrY3eIA"
                    "W6OenqBKmM8qiMs/P0CdxuJ+foBrpMo9qPBfX2AAbshBqe7b5u4jn/J5t+Ovvsmc0/Ycca9D"
                    "h7mnwtRPs/Zhnsmpy+MUj+QAU56Muds3gLMZgswAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABYvIZsAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////////////wAAAAAAAAAAAAAAAAAA9om/dAAAArxJREFUeNq1"
                    "loly2jAQhrWqbtnYmDuQkqRJk97t+79cd1cGJMfMwEzQDIdl+/O/t8UnXuIDV0/8aOwBLW7A"
                    "ZbK4BZfI4kJu1URQrh4/F3xUKvogM/KF4BrAaKfAy0oOTi20gn6pRp7AF60pRIs/0tDtptBd"
                    "H7G04vQ6lxlV4bd1AHqnSffxTEhSjfcmoesrsLWDBuVqADYV/+wzF6HMsGBXdyz+vObtrDiU"
                    "jq9GhEfZltAebB82Qp30V3Spkme498tf33P0HegOQc688J0Kba3IAlqauEMRzTj3x3I+mUxm"
                    "mRM1fhAcyAc6KDBWKJcUkh+OAmVnxUKNSGbYejlfTr5MvmZxkwy+C8jUeHuDEexlNkWwHD2k"
                    "w61Qcp8f1k/ybTmf33/OFRODwRDraaR0qvzBXJPLw/xwyQg/iFn7sNkg95t4HAXr5FRXUVon"
                    "nmJW7wg8sOlhceiKf207f3sSolBsIoGnwrCBK4N+lpgiHT8UvSKksDV+YRx5D81Rw9p8bdt2"
                    "LQbgDr1IUTuAUS4CojmCyfod7gJvjYB/zpevm7bdzEpXoIlTLjpN1msjLIE99K7wya3BQJ/b"
                    "711x//vv4gnJ7XOhmEJPlYGBw+DBAayy4K24llM8JWR+79cLXiPXSC7SDbNon2pZg+pCAktl"
                    "Tukm+bfP5/A+3fqF5KJAECysARU4aqsEbtLNFtizHLnkCBnRiOpMST9MHrcidwXeU0fOM2sI"
                    "3DQHa30KH6aJLTayIs/+/9kWW5YTXtpUGSuKP7j+JMUtRZUvl8RVi0u7pu67JdXygizIJlRI"
                    "nT+hV3GkIUt59hh1KN1hf3eWO2WRTbs0OJymwQWjkZPFKjt9Gkm0aeOgk4d8MnFTvWrq4QzF"
                    "Vuz25v2EsP7E1dW1UzpNKAWxGZkPtjHUhs2uKsb/zV5YbveKdbuXwpu8xv4Hv6YdDEUF23wA"
                    "AAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx39/f4eHiAAAAB0"
                    "dHUmJidHR0hYWFk2NjdlZWbn6OmWlpe3t7cWl+2np6eGhofY2NjHx8cBdsqp1vMGZqoAW6NK"
                    "mM8qiMs9qPCdxuJrpMpBqe4Absjb5u5Dh7kcca+c0/Yjn/J5t+PL2+UUj+SnwtQAU55hnsmM"
                    "uds3gLOgvdCpy+NPs/YZgswAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABDldg5AAAAQHRSTlP///8A////////////////////////////"
                    "////////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAbm2PxgAAAn9JREFUeNq1"
                    "ltlioyAUhjkOO4hgNZmkSTvTzr68/+sNixpQ0/GicuOGHz9nBX2IA73jGIjvjR3RaAduJKM9"
                    "uIGMbtwqG8upTEvbkHUMrZDTiPCcjLYJJhKMwBbw2kcGmgNwBzQH35YVeBqCzrigo1gFfqhC"
                    "N9GMgkFYIAdc6MWeiIFimGKGsdFWDUhBhSt0CwA0zCUVwEwRqmZcT77ZmTRxvgBJh62L6RMj"
                    "TcZi2L8ozZ8hTw/xMs1v/ANBXEahcTkM46oWmlKhAp3rDRsax7n/8iegRYL4qd6IFVLROA24"
                    "cAE2GkLO9s4kK8B44n7vu7quPRknMPcQHsA8/CW8773z7KCT0UWIVPm7BI4WOPRdX3+uP01g"
                    "ZdEA5jbalkoQEo8GXEaBD7wS/HI5XOVT33Xnj7niwIhgqQnRMZzE6AD/vFTszEzxqb0cj577"
                    "FR5zMIxglgKiieni0o98LV/4whR/27Z7ugIUipUOYBJt7O9M9A5Jkh2wJReDmYFfn9u2PcAM"
                    "TIMZPc0ksPJQf+dUCjaxAfy765+PbXt8KE2BnExxFhlMoSqoxXK7Kc7ffr5ePbl9KRQH1+Pk"
                    "KO88OYJTjmuzdJ6eOw9+SQB58OQi3Ly4IZcZWM4HsFXbw20YnlwkCGokIgocF8FrA5gOP68m"
                    "CLuX0pf68ZSlNAuqiEtxZiKYTRViJaXp3SL041QUoepWJTFIHvZ6qzyLImRAz1/cLZtiiNaQ"
                    "y1FD1qF8lcRvls03Cz0Gx6iwoOJatoyE/xT6t1sTSS0plYdZIBAnQmsSLLQmnLWmzd0fg22E"
                    "AknuNVNbNtOt7T90KAuarbZ/Ets/Ldv/bgeW/Y5Y+x0KdznG/gOdMBufa1g/LAAAAABJRU5E"
                    "rkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEX09PcCAgPp6uwAAABo"
                    "aGrIyMonJyjX19k4ODl3d3gXFxdISEmHh4kWl+2np6mWlphVVVa2trgBdsqp1vMGZqoAW6PY"
                    "3eJ+foBKmM8qiMs/P0CenqCdxuJrpMo9qPBfX2AAbshBqe7b5u5Dh7kcca+c0/Yjn/J5t+Ov"
                    "vslhnslPs/aMuds3gLMZgswUj+QAU56py+OnwtQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADDMbnkAAAAQHRSTlP///8A////////////////////////////"
                    "/////////////////////////////////wAAAAAAAAAAAAAAAAAA9om/dAAAAoRJREFUeNq1"
                    "loly4jAMhi3Xt3M4IQlQ6EJpd9u9d9//5VZ2OGITmLBTNANDHPJF+iVZJg/ByAfanvjR2AOa"
                    "3IEbyOQeXE8mE7lSCzC2GL1HMy6FoFVGB+SJ4AJAMWvAUUlTLGEgMoDSQEboCTzJKhDcMxSg"
                    "qWJIrQSbg5COSQS78LcbTBmJ39wCsDnzfp9uMQBZdUEtzgHmt2ALCxqdQ4T2RPyxOYaiC6tP"
                    "/jNX6Ms+r2exhugnVF5lh25zj3awf5oasElsIC5xn9pf34boR2AZgqx6x2gtGFRYgj4KoQmN"
                    "E6kvcL+2TZ7nJ3IJDD8ILr0GDHOvODG9n1TPZfo81zqtmwBbtE2bf8k/D/JGA/ixRCbzMMwg"
                    "uHAPi4yPFGZS7W/bxY6+tE3z9GnosWcEMIiiEqJCIRzs4+VCnHssVLK2rrfLJXL/kudRMOs1"
                    "tdKXtQnhdiUd6dGzxdnPum5edoREHivhwRVRpb9aKdSZYryZvwrapOZAJSvda13XC5KAM5TM"
                    "Z+0ARncRKtQVcFJwf5r2dVnXy1ksBZZqFZqO+QiZItyDHYxHPbb49PtHt0Ny/RZ5THTfGZg4"
                    "TB4cwGZ68sg7vokukByVG7Gw6XuZgcnKHkyNml5ue0Ny1CAIJlyBKUPWVj1YQ9nvDXpKgxwk"
                    "2ebPazKUAr0qRKgzrjxY6+MOwYZ78FhLD1/yfR0t8dBmlPedscKqALD0PzahM2P73dL3cucj"
                    "GEyoihU2G0TnCpaoTunFa+rAsAz3d+ufkUYkrwXJr270NLI40/1IoqGafFnHoykD1W2YFPFo"
                    "mjb1cIbiVmw3CiLutWH6cMOEMiDGasmPf3M+/u92YLnfEet+h8K7HGP/ARjOHRmLyMEyAAAA"
                    "AElFTkSuQmCC"
                ),
            },
        },
    },
    "iTerm": {
        "claude": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwYHSAnJygA"
                    "AAA4ODoAAQFUVldGR0lzdnfX19fo6Oi3t7fHx8empqaFhYZVXmGYmJhmZmdbZGY9QUIQNRt4"
                    "h4oUKCEMVBgGbA4NSxkLgxxvfIARJRw/P0AMehwKmhsAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADdMPpzAAAAQHRSTlP/////AP//////////////////////////"
                    "//////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAi66AUwAAAohJREFUeNq1"
                    "louSoyAQRcHetgEVo0aTee7+/1dugzxNtma2auxKRUU5XK5No/jlQ/xgBOJPYyNanMD1ZHEG"
                    "15FF5jZFHB8cen8AeIpB3RtjNZZk8S3BKCW5Y9c9uUlGhhioAKdQi06xqLpvI6VxguXgdZe3"
                    "IGE9+mFKsG3L5cI/H8u21U/MUqIDj3zeS6rmwmEmrW3nT4/kjdoqaMt9nXfGiUWHtLIAg5cZ"
                    "zFXmkXx9b8XtdlvX+PLa92u8J51OdJIV9xp3V0I4lTpfjm6Y0kJxofb25/7xcm9CgrR0EU16"
                    "mq3ruccsmV1qmhM35IsjUwleGPz7vr68xpaWlgj2MORJ49i5qatK8J6Hn3E4U02Iwaq9vb2t"
                    "L/cEVhksoGdhveytMd4XUY7oz3QUmpsSuHldP+5r8wzMuZoSymUyIHl9U5JHaSJdaTp7zGDv"
                    "bVZ8KcFetI9ptu4whnmPCRx02ujODqYdnFtaRTvY9nbUMyH2eRVoBSFf4ptSCawrkwVdj+Br"
                    "6AKzHidr+yFQy4qQwZhy5dvggxd+eZl+gmBF9DOVp6m0gsF4BGMN1kzzYAA1Md0GP+NyUJgz"
                    "sMgbbn8Al3WInAVySsUAMOUK1OmGVbp9AeYCxkuvs1wq5DCVaSqD9Pzyhj0hK3BBbkowi5i5"
                    "pnVceLmfmoulp9PrAzg0xM6qrXeQNpnGeQGuB4qx52rWsTqqihCJaiBTV3IFddkEVVnz6USb"
                    "0ZWdSUAu574c23C5p/qhIINCKAJVvU0wjtNWexOx8jAsGaJxz3R82LoAizgMq62nk/+vty3s"
                    "yq3JwLNd74ugAzK6qBO6m/93l96nZOEfN8i6Mjypevs/7YPlvE+s8z4KT/mM/QsT2BcVP+8q"
                    "SgAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQETGhwAAADo"
                    "6OsUHSAnJyjV19k2NzdTVVZHSUqVlZenp6m5ubp3d3hoaGmIiIrGx8lVX2La4+cQNRsUKCHb"
                    "3eEMVBg9QUJ4hoq9vcAGbA5/f4FYY2YLgxwRJRwNSxkKmhsMehw/P0CenqAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACFgFB4AAAAQHRSTlP///8A////////////////////////////"
                    "////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAp/cWmQAAAplJREFUeNq1"
                    "lomOozAMhuNsyEkSMqXt9Jhjj/d/xnUSyEG7q440tSoVEfzx89s4kB8pyDfGQvxu7IomT+Am"
                    "MnkGN5LJQ1xBuYj/43h31ShOKVeGNeTHwBOAwz8GcLt28BSWoI5V8EMRMAlzzsAjfmyUkbFg"
                    "Y3B7k7s7vb284C/F22nXr3qACSGgUSFP6pcwWapUSmb01iyhoAslaq5BlRQ4Q4ojjAP4qjfJ"
                    "NIeEmJL4jeYT0P1+P89DDgqncksAzaLLhjjUo1GfqEWNGoozQkP2rAZTsP95/LgcBxpjGJrL"
                    "8WoeCMcMD8J1mnzkpiMbEJKubY1ChxH86zhfPmkOBO/anoAJH3rSYCGZXR8mOpQvSeaiZb3k"
                    "nYb9+/t8ORawbsoX0FfNgUuKebqed6VYahUaRZgePHzOH8d5uAcmzJWqoh4WjEs8WeT59UFE"
                    "MWfpcgQnb6viQ9/JfAFLn/oq6a76XdGJq7zttgwugeCl9JJLrbybRl5Fq8kmoVA6r4LRFNqC"
                    "X7fg1wU8eqW1lHzlalM7vILH0isPg9ewVTFQLsNixeqnPZP7VsgtWIrNi0kzebQumiz74rFp"
                    "Wg6g6xsSbsGhaQosujIg12HAznlC3mk3s2m3/4JxgNFRgMRRAfx3kxdW6aV4cZJQsbGiIQ+t"
                    "FVgYzzBDOIov1uThWtJUKZ+1mxPrw2qgQxM0Dp5Vl7cxwxDN403EVFPj64B3bSYOCu5fAMP7"
                    "scnNZgvB8lMdbyCJpWM3jmWe/OwPvzOQmdG8CW264XcGyVCeSlPGEFYXr1mG9l7nrcTcbonB"
                    "lgibJlZxho2p8LaZbnULKR0+ki/tekmiy0i23Q+bvceLL+7SS0uGfyw4GcewvIpu+3/aB8vz"
                    "PrGe91H4lM/Yvy0NGghG4BTIAAAAAElFTkSuQmCC"
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwYHSAoKCkA"
                    "AAA4ODoAAQFUVldGSElzdnfW1tbn5+jIyMi3t7eFhYZnZ2empqZVXmGYmJlbZGY9QUIQNRt4"
                    "h4oUKCEMVBgGbA4NSxkLgxxvfIARJRw/P0AKmhsMehwAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAeV4otAAAAQHRSTlP/////AP//////////////////////////"
                    "//////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAi66AUwAAAp9JREFUeNq1"
                    "louSoyAQRcFebEARotFkXjv7/1+5zUNtTLZ2pmrsSsUHcrxcmkbxK4X4wSjEn8auaHECN5HF"
                    "GdxIFju3YXF8sHfpAPAUo4yzNhjFyeJLgpWUGI9d96QRrSzRIwNvoSezxaTrvo2UNgqWfdLN"
                    "m2DDJvTDkGBZpsuFfimmZamf8FKqCB7o3EmsxkLRjcaELp0eyQu2VeCy943e2ShWRWSQDAxJ"
                    "ZjFX20fy9b0Vt9ttntfJa9+va5uMOlWUrKnXkF0pEVWa/XKIr+EWigu2t8/7x8u9KQnS4kU0"
                    "29NknaMeXhKba/KZ621vKTCTkYMnAv+5zy+v650WpxWcYIoGrYYuDl1Xgl3RXbTaakAE1u3t"
                    "7W1+uW9gvYMFOBLmpAvWJl8EfyMdxww27BYDN6/zx31unoEpV7eEipkMCpMdY5EHEhqahaYM"
                    "wnCPCZy83RVfODiJTjH6EA9J96YfyZCSGyG7s4Ixg/c7rcYMDi4MxqNSbl8FRkPJl3WmBvQ+"
                    "n5nKZIHXI/hauoA3wxiC6wuVV4Qd7NHob4IPXqQEsG6EYoUp6w9NycKRW0FgdQSrGmyIlsAA"
                    "eiR6KH72Zf2Bkt2agSxvhH4E8zqE0QI5bsUA1JYr8ZLyO6r2pXaor4KpgNHS6wINWPYjT1OZ"
                    "pUsHFDZ620tZldYIZuSGg0kEaXEdFV7qpz1beiZP35Dn1W83WPXTbb2DtHoTBh5iDyUGR252"
                    "tLiwKkIoqhfZupJrqMsm6Mqa31GOHWLZGQXs5TyV41Auc6ofCjJoBSyUrrcJwpGVJpmoKg/z"
                    "kqEFMuRMVw9bFygWh9eakOiY/uttS3V8a7LwbNf7T+ABubpoNnTnv7tL5yEF+EcDhliGR11v"
                    "/6d9sJz3iXXeR+Epn7F/AeqhGFbHmyDYAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQEUGhwAAADo"
                    "6OsoKCgUHSDV1tlUVVZHSUo2Nzenp6mWlpdnZ2m4uLp2dnfGx8qJiYpWX2Ha4+fb3eEQNRsU"
                    "KCEMVBg9QUJ4hooGbA69vcB/f4FYY2YLgxwRJRwNSxkKmhsMehw/P0CenqAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADVsGJ8AAAAQHRSTlP///8A////////////////////////////"
                    "////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAp/cWmQAAArpJREFUeNq1"
                    "VgeS4yAQZDhGBIFAjuvNF/7/xhuCBMi+K2/VustVtoWm1fQExH4ksG9EYfxu2oWaPYA3MbNH"
                    "8EZmdhev5Cjj9zjeXLUKOUdlRcN8H/EM4OlLAFyvHRyHAu5FJb4LgYIo5gUw0o+NMjautBE4"
                    "XcXuL2+7HX0S3i77ftUBzEQCmhRiUl9gM6FRyuRfW7Okgg5K1lhLKjmgIBbPBAK4qjfJzObK"
                    "OYnfaL4AP51Ox+OQweGyPhJAi+iyZZ70aHJF1qRGDYKJBCY1ZM8qhILTz/PH83ngEcMQby+g"
                    "uzEwpAgH0neaXORlTKUg7piIzL5zmIh/nY/Pn/kWTsT7tiZgpk3PGiZIZtfNRIfEUhTJsl7y"
                    "XsPp/f34fF6JdZO+QL5qBDSc4nS97lOyBCt5UyxZRp51xMPn8eN8HG4RM+HXrJIeEaxPyTdF"
                    "3gRjkBZC2YRqq5yIk7dV8aGvZCzExiV9Sfeq36M48GwRrWJbbZl4BRGX1Bs0Wjk/j1hFq3lK"
                    "PsJaedqr8kslryvx05b4qRCPTmltDC682tYKX4gFc05RiX+JeMFUFQNHE4oVqvSfV6XntlaY"
                    "LbGRm8bkmXmcfDTZtMmjUTJSb8s8p9q6YeGaODRFQY2gLJhlGIiX3MRLuel40aZys5ty+y8x"
                    "DTA+SjC0YcDfTVzI0gUNtXGceGwQbDu+WNEwD60V1HNOUIT0nBprdvC6hqmcPp2t360Xms1q"
                    "4EMDHgfPostNMcIyjfEhcq6hsR3iP5kQJw4J7hvAYj820W6OEEo/1/EBhk187MaxyX/FH7wx"
                    "kIXV2EDbbvi9gKGhGMnJDEtTcl15zfWnndP5KLHXR2KYVoRNESuaYWSDT3Nh7rfabbTu5b5T"
                    "L0n0mVJsz8Pm7HHyi6d0KcnwjwVv4hg2r7I7/h/2wvK4V6zHvRQ+5DX2L8w5GwDQXeLZAAAA"
                    "AElFTkSuQmCC"
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwYHSAnJygA"
                    "AAA4ODkAAQFGR0lTVldzdnfX19fo6OjGxse3t7empqaGhoaYmJhmZmdVXmFbZGYQNRsUKCE9"
                    "QUJ4h4oMVBgGbA5vfIALgxwRJRwNSxk/P0AKmhsMehwAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABXfYFZAAAAQHRSTlP/////AP//////////////////////////"
                    "//////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAi66AUwAAAo9JREFUeNq1"
                    "lomSoyAQhsFe5FBQo4mZa2ff/ym3aRAbk62dqRq7UqVR+fz7RPGLTPygZeJPYze0OIFLZHEG"
                    "N5LFzm2YHR/sPR0AnmKU9c4FqzhZfEmwktLEY9c9uWmczNYbBi6mZ1ts1vXaRkoXBcuedPNb"
                    "ULCEfnAJ1nW+XPBHNq9r/cQkpYrgAc+9NJUvaG60NnR0eiSvpq3MrPvaGDsXxaqIDJKBgWTm"
                    "4Gr3SL69tuJ6vS7Llrz29bbdk1GnipI1rhpSVLJFlXb/O8TX8BCKi2mvn/f3l3uTC6Q1F9GU"
                    "pzF0HldMEtlc01RzE9lw8IzgP/fl5WO70pp5AxNModNq6KLruhLsWfohhow7hGDdXt/elpd7"
                    "AesdLMCjMC99cI7iIvgbmdr+eInAzcfyfl+aZ2Cs1VJQsZJBGQrHyOVhFsbkhOUxRjDFdld8"
                    "4WASTTZOIR5Id6XfyS4uCDw6jTAJvF9ptUng4MNgJ6OU37vAasj1QpmiRp9y8G0VZGFuR/At"
                    "JxcmO4wh+D5T+UTI4IjErAbxLfAhFtRezo+QQ2ETX2G4E2CsCkUYdQSrGmyRRmAAPXZZXkjt"
                    "kNpalwpkdSP0I5jPIRNDIMcyDECVWoFcdmGfHeqrYBxg2HpdwFEh+5GX6Qac0JM8uFNBVmBG"
                    "bjgYRUw40zocvLhOT6z17NbB0BwulMW6rXeQVhdhMEFcocTgMfkdOm6qIWRE9SJXT3IN9dgE"
                    "XYXmdxTthuj0KGAf55S3kP+mUj8MZNAKmCldbxPUrVRbfZUbyltsGWOGVOnqYesCxezwWhuI"
                    "Tm0m621LdXxrcvBs1/uPmQNyi6It6G767i6dXArwjxsmxDE86nr7P+2D5bxPrPM+Ck/5jP0L"
                    "4PsXZn5PFvgAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQETGhwAAADp"
                    "6ewnJygUHSDU1tk2NzdHSEpTVVaWlpinp6rGx8lnZ2i5ubp3d3iIiIpVX2La4+fc3eEQNRsU"
                    "KCEMVBg9QUJ4hop/f4EGbA69vcBYY2aenqARJRwNSxkLgxwKmhs/P0AMehwAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAD5wB/jAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAp/cWmQAAAqdJREFUeNq1"
                    "louO4yAMRTEL4U3ItGmnj3nu/v83roEkQNpdtdLEGk2kEh9u7BsT8isF+cGYiD+NndFkA24i"
                    "ky24kUwe4grKRbxae3fVaE4p14ZV5MfAA0DACwO4Xdt7ClPQwAr4oXCYhDlX4BFvK2XELtgY"
                    "vL/J3Z3eXl7wL8XbadeueoABIaBQIU/qpzBZqtRaZvS6WEJDE1qUXIMqKXCGlEAYB/BFb5Jp"
                    "9gkxJPErzSegh8NhHLscFE7LlgCKxSobElCPQn2iNDVqWCojFOSalWAaDt/Hj8uxozG6rrod"
                    "7+aOcMzwIEKjyUdujYnk0FQYwX+O4+WT5kDwrvYEDPjQg4IeUrHLw8QKzdTBkT1dSd4pOHx9"
                    "jZfjAlZV+xzWVXHgkmKeKr+HplkqbhJFmBbcfY4fx7G7ByYsLF1FPcyZkHiylmfSnmJVnD2C"
                    "U22L4n3rZD6BpU++Srpr/egbcHkzXrstg5dA8NR6yaXSPgyWF9F66JNQiM5jxFn856fio21p"
                    "DX5dg18nsPVaKSn5zFWmODyB49OfCXZVkqfAc/RFMVAu3VQKnctqZC7EbSnkGizF6sWkmWz7"
                    "EIssq+a9p4VsXwaNb4i7BbvKFFhAbUDOw4Bd84TMdmPpOvnZrOz2XzAOMGoFSBwVwH9XeS5L"
                    "T53Lt8dJQsWqFBW5q0uB75xnmCECRU8NHls1h87tY8S65ofmJaddFTQOnlmX72OGIYrHTcRQ"
                    "UmPfcNdEyBMHBbcvgOHt2ORmdYRg+6mKG0jSU9uMY5knP3vndwYyM4pXoUwz/K4gGcrT6QUz"
                    "hJXFc5ahvFf5KDG3R6Lrl3ArE+vYc5sc1VfTrRwhi8MteerUSxJDRrL1eVidPV48eUpPlnT/"
                    "WAgyjmF5Fs3xv9kHy3afWNt9FG7yGfsX7X0aVP1n5JgAAAAASUVORK5CYII="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx79/f0YHSAmJicA"
                    "AAA4ODoAAQFGR0lUVle2trdxdXbHx8enp6dlZWXX19eFhYXp6elVXmFbZGaYmJgUKCE9QUJ4"
                    "h4oQNRsMVBgGbA5vfIALgxwRJRwNSxk/P0AKmhsMehwAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAYyC3bAAAAQHRSTlP/////AP//////////////////////////"
                    "//////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAi66AUwAAAmNJREFUeNq1"
                    "lomSmzAMhm1UI98QSCDZo+37P2VlA0EGus10Fk0Wdnx8/i3LEuJHNvGNNhO/G7ugxQncTBZn"
                    "cBNZrNyK2XagM/kFcIjBKg0AxcniJcFKSp3ebXvUq6WhAcpJ5OB12c4+rcNyaiVlTIKly7p5"
                    "FxiN0gsbhJMqmN2WYBi6y4V+2bphKEcEUpTADf1vJvVrz7IS0Pq4BQ+6LkwPqx+S72ISqxLS"
                    "SwYGDQ1jaUsNBff2Vovr9TqOy+HVb7elTyadyYkCJYhm8spsMe+BWS8Nd6G46Pr66/7xuFdz"
                    "gNT6IubYIJQDcoCjbRNbSqYptJu961YX4I7Av+/j43NpqXW3gDNMAf01LT25G/XOpaLibQTG"
                    "+vr+Pj7uTzCuYDp5Ka2Rxsco+d6xUD+PzcfMwdXn+HEfqyNwCtXFUiSDmo4IjNkrdr7wMYGz"
                    "b1fFFw7OorP1wafXpFsd3iYO1hN4balRT2BvfGODVso8RTuLkwdcEdGzWen5Yd624Ns8B4Jt"
                    "eu+Nm6lW8WAL/w/e+KJNj2h6eMkVBFZbsNIbHTGTWwDsiZ5VVcbvD69swz0Yi/xFLpB91gw5"
                    "LF4Lt3+AIaar13pKFdL1fN7hBdFbxYxccTClCToj0wodKQ4w8Kt3cKXLBoV1WUFqfAqDAMnH"
                    "SjRUJeiCIAuyXRLysrwzFUKZNgEL1/xMomOT0m8vwAFLm/bLtCkAFTBTWJYJwlH+tPlSqG0J"
                    "+DLRp5NmtlnW+kzX+VnOBRdSaQo6LWlZaXq9+usjOayYxrKYvg4GD3/pIb+l8o9l+T/tg+W8"
                    "T6zzPgpP+Yz9A0uEFghRziWfAAAAAElFTkSuQmCC"
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YBAQETGh0AAADp"
                    "6ewoKCgUHSDV19nGxslHSUqIiIpoaGo1Njd2dneoqKq5ubpUVleXl5lWX2Ha4+fb3eEQNRsU"
                    "KCEMVBh+foA9QUJ4hoq9vcAGbA5YY2YRJRwLgxwNSxkKmhsMehw/P0AAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABWQm33AAAAQHRSTlP///8A////////////////////////////"
                    "//////////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAANDC2kgAAAnNJREFUeNq1"
                    "lmuXoyAMhglLBOTeattpO7fd//8fN2ireJkZP4w57fEo+iS8CQH2pzP2i/Yg/jb2iWY7cDsy"
                    "24ObyWwTV3KU+er92qiolUQUoRYFeRu4BnCZALCCZQaQXkgcaiZG8CZTAJy+uQFmkhcFNaC5"
                    "AMpoJIEjqsW3x+vr4UC/zl6vx+moAQqHedCMNdhFX4zI0HR6KfJ/WagYYWJRDkMpUWQcULBE"
                    "SIEAZhgLzuvRjTDRu1nMV+Cn06ltq944XAeXAFpklRNz4JkmVQan5E9PObZTq8hAhNPn+e1+"
                    "rni2qoI4CEkokg5JZQPS0WxCKYRjYppIN1WYwH/P7f2D90bgY1kTUHv6awjQif3kuItc5Nk5"
                    "UYI1nN7f2/t5AOsifYp01QhoOYdy7uRxUQUUgJ+Cq4/27dxWa2AKbcgqlZ1QyfneIS4jRls+"
                    "awjcaTtG3Mzef4CtsfnSxd0ksbJKJw9lDx6MwA+/Fq2OxtUex6BjHbqPeVF4g0WwJfhlDn55"
                    "gL2JWluLT65O40y/AOMm8LAUxoiBo1XLWa9LYefgSQrywuQ92QeXRbYbk6eW4KKQaGVATGB7"
                    "Mt3f+qg2lNu3YM+BewmWWgXgP1rao0P30wLJUhTkqpSCYjCCeo90nHpDbcoOZsoevLakhQZe"
                    "FcZz43nGYELOdWIasxNZj7XwcxNiCadtE9NsC4lUXbqv0sAHFYPxeuwdzEVvZqqLpLEwPS2k"
                    "G1hB/TN2MSYmxLTRq28afX4eBlOznETq8iRDli8U3e2xNdVgm4ORON2aNu16XYiuR4qNm+n2"
                    "Y4Wy6gu3tP3z5fa/24FlvyPWfofCXY6x/wGiJhq3+L416QAAAABJRU5ErkJggg=="
                ),
            },
        },
        "codex": {
            "asking": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwZHSAoKCkA"
                    "AAA3ODkAAQFWV1lHSEl0dnfn5+fX19eWlpeGhodmZmfHx8e4uLimpqZVXmFbZGYUKCF4h4o9"
                    "QUIQNRsMVBgGbA4NSxlvfIARJRwLgxw/P0EMehwKmhsAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABlc5QUAAAAQHRSTlP/////AP//////////////////////////"
                    "//////////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAi66AUwAAAptJREFUeNq1"
                    "ltmS4yAMRcEaLDZjk81JLzPz/185IBaDk+rqqWqr8kAwHF+uAJn9omA/GJn409iCZgdwicyO"
                    "4EYy27hDE89DcdFmgtcYoRZjvBItmX1PMGjunTJcvXiGhueYsQHXkGdV4yx3XL6QWBun2043"
                    "VCyhn9YE1+v5dAo/ivP12o3whryauHbSza1uQTxjlfKamnvyFccu8Nq8dOJxBY7rtBDkbltK"
                    "lJnNleaZfHsf2eVyWdeSvPH9Vp5NNFpoEkppVbxkN6ps9Mehc4Md2AnHy9/7x+M+5A0y4ilB"
                    "gq8oA8h6oJnkIs9ZcpULUMnYgs8B/Oe+Pt5Kz4jnBBZhoIjgsF7UTswxeWaqghdq/C4WBDdM"
                    "B5bj5fNzfdwrWGawNSyDhSFvpeZOJ5ky6Ev+qiJ066rg4W39uK/DHhwZBNYLwEK7Lhggs0tF"
                    "HpauuAjVehzA5O2mOHvMCxgTYKLjMpd1TxWcdfriTgJjAm89o8RsxRLBQB6HlkeNcZeRvi1T"
                    "soJVZzLD2x58q1MgZg19AtsADa3Z9mBR9++3wWzWaZ/RoUDLhqhW6WxF8RPKubCtFWGC2INF"
                    "Acs0OyYOFl3AJvtZjoMU25GZGpB8Bpd7SOl8lpEbITLY2Jyy7EDdbqLbbl+CJ83AhvvAxaxl"
                    "sEyTh0DxffJmzjXbgRvy0IAxioI57TNPYCyrVTV9ALuOcvvJsa8gYzVt2Da84lrQhVZd1D0o"
                    "ck13uw0S+msTZF2AK6dVpyPNmwpF17HP/8Xy4kIGKaAJIaHVMaN0hlt6l/Gdh6lwKMRppqZ4"
                    "Lmuiif61kEoSNZedJKHb0mTgVdX7OhQ3k7Nc72vPoCpau/+u0qlCGb7gqwfo4zVsZV/+D/tg"
                    "Oe4T67iPwkM+Y/8Bgo0ZGbYDH4EAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgITGhwAAADo"
                    "6OsUHSA4ODlHSEkoKChoaGnV1tlTVFXJycuVlZe3t7l3d3mmpqiGhohXX2Ha4+cQNRvb3eEU"
                    "KCEMVBienqA9QUJ4hoo/P0B+foBYY2YGbA4RJRwNSxm+vsELgxwKmhsMehwAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAC9I4r1AAAAQHRSTlP///8A////////////////////////////"
                    "////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAp/cWmQAAAq9JREFUeNq1"
                    "loly4yAMhi0t5sZH7qZ3d9//GVcCmwDJ7qQzjaZNM8V8/DqQ3P2K1v2gLcSfxq7o7gHcSO4e"
                    "wWVydyfXjhpQTbfXgtOI2gVRkO8ETwDSKAQnrGiW9gZhMRzFBXyXDaA9/RGSt8tK95SxbHq4"
                    "2rs9v2429BPt9bytFiVa+vQKwLwY1p1XQpIqnZMJ3UbLOqjM2UKUgpHkGoDoKn3ZFCEimWEf"
                    "EXMU32g+Ax4Oh92uT4ZwXleEik9P6TDPaAd+kcOoi37Lj2KVBOHg8Pv4/nbska3vL48/g5kJ"
                    "pORn3Inkq2UP2AxzUxboKNrB5LGKMIH/HHdvH5iMwNs1iIZ+CRw4BiYgSN+hSgo5DlHAvARX"
                    "YCN5q+Dw9bV7O2aw2q55ExH8HIhpaOtIGVxkjjlZbhVKJ0Cowf3H7v2461swMyIY9DRoLieb"
                    "KTLLo5jM2QlXVjmBY2wvivcN2CSAslzWiUd+p5iw9kUnHabLakvgbH0ExAc1g4dOxo0nSXEW"
                    "VCJRH6kzLZjcwRL81IKfFvBMUeSsrWCSS1Ata/CU6/duMOV5iJfOsPdGdp7BDpZQrPEcPrvb"
                    "oZAtWK5Xb0w3gxJHyYMVjHXyxDwvXyDHPZq/Bi+Xi2p+k+6yAZxDAguU/yi30JTbf8G8iiFm"
                    "7ZTAY9rsuddVyROanLBNKApyX4WCjph0LBMvGTyOq7cup28Ymn9cOg32hSGo9WL6mCDh0804"
                    "UVVAXuTrkLK6dhwSvK+6W9B129SXQJmlW/Jd3rMHxYQKqfNHtDjpGw1ZBKULU8X8Ih1oZurv"
                    "ysdOWVZT95JkKMODC5rMLW75IZu39cRLI4nP8rrp5KFyFKfue1OPZihlQW3k1YTofDF7jP3u"
                    "lE4TCkGP4nrBj5LbsHyx1fh/2AvL416xHvdS+JDX2L94lBzh7jdvZAAAAABJRU5ErkJggg=="
                ),
            },
            "working": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwZHSAoKCkA"
                    "AAA4ODkAAQFWV1lHSEp0dnfn5+fW1taWlpdnZ2eGhofIyMi4uLimpqZVXmFbZGY9QUIQNRsU"
                    "KCF4h4oMVBgGbA4RJRwLgxwNSxlvfIAKmhsMehwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAABtoLhXAAAAQHRSTlP/////AP//////////////////////////"
                    "/////////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAaGrZRQAAArJJREFUeNq1"
                    "luuS2yAMhcEUi7tN7ntp+/5vWZCEA05mpzuz0eSHY8PnoyMBFr8wxA8GE38a29DiBVwki1dw"
                    "K1ncuVMXj0NhdT6p5xhtV++z1T1Z/J9g5WSO1kv75Bl4ybFAB97CHO0WR7PjyhXFhjo9DLrV"
                    "hkX0Q07qej0eDuWHcbxehxHZo1dJumji0uvWyHPB2uzwck++wjwEXLuXJlkziNJRIiDjPZUq"
                    "k801/pF8+j2Ly+VyPrfizb9P7VnC0dqhUCyrla26VWWnvw5dOuwkDjBf/tze324TN8gMB4IU"
                    "X8EUUMgKZ6KLkqsUiRv94ksAkaEHHwv47+389tnuzHAksC4DdQWXfMFFvdTi+bQJXlk3ay1u"
                    "+AFs5svHx/nttoENg4MXDNYevTVORkfpm0LTrVnIk3arA0+f5/fbedqDKwPBblVqxa4rBhh2"
                    "ieQpqSYcROpt73EBo7d3xeyxbGCghki4XKhCJe/Ey1II7o1M7jQwEPh+ZzbAVqwVrNDjcpXB"
                    "QZWIku+VShC5Be1gsoDTHnziKaY2ZqFlAocCLVdLGMERrPkmWCyO+gwVQRBTVWsdW2F5/YHl"
                    "lRF6K8oEvQfrBjY0uxZOra6BPfu58PpTWrrWgakDmUdw24es47UM0mvNYB+olLSCU01KY056"
                    "aLcvwckJFcp+EGvVGGxo8lQouXpdklHK1ywWyco7cEeeOjBUUWqhPssIhpatpfIlWiBxu9Ht"
                    "fmYeT5DZtISme8Nb6TRuaJuLbgRVrh92t8mocdtUZksg8tS6lrGY3QmF23Hm/3p9siEro1UX"
                    "2qhexwImehnwXT4PHtL2UxZIWvBSPx5ruovxtYqOJLxcd5K0648mr56del+HlT7FIN3+7Jns"
                    "hnbx26c0nVBervDsAeS6DQczHv8v+2B53SfW6z4KX/IZ+w8i4BpOonQzxQAAAABJRU5ErkJg"
                    "gg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgIUGhwAAADo"
                    "6OsUHSDV1tk4ODlHSElTVFZoaGkoKCjJycuVlZe2trinp6l3d3hYX2GGhoja4+cQNRvb3eEU"
                    "KCEMVBienqA9QUJ4hoo/P0AGbA5YY2Z+foARJRwNSxkLgxwMehwKmhu/v8IAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAACtLQjdAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAp/cWmQAAAtNJREFUeNq1"
                    "lolu2zAMhkVO1i1bcc6m6bXt/Z9xpGQ7slIMCdAQTWNE1uefpyx+ZRM/aBPxp7EzWjyBm8ni"
                    "GVwmizu5KnlAM3y/Fq1H9DbKinwneADQziBYqWSztHcIk2GSV/Bd1oMP9CU1b9cr3cOCZfP9"
                    "zd7t+W2zob9sb+ftalGjov/BALh3x7qXlViA2lpdrtpoKQsrs6oSZSCRXAeQXaWLTRUiklmC"
                    "q8YsvtF8BjwcDrtdVwzhPK9Ik+8eysMCMyyESQ6jSL/MJhTfiqskSAuHP8ePy7FDtq67uvsK"
                    "biSQ0X8F70TyVbEHbI659KC8CV0RkVYRJvDv4+7yWW5BAm/nIDr6EDhyDFxE0EGgKYI5DqR3"
                    "LgoSiI3krYHD19fuclzAZjvnTWbwaySmo62JMsgyyVJOlhRT3ixdjvQd1+Duc/dx3HUtmBkZ"
                    "DH7oPZcT57m4qyd5PQxBxRx4VYJzrXIC59heFe8bsCtBNYpBxV3yu8QkebnHcX6Yr6utgBfr"
                    "MiDf6BncC50dPGmKs6QSyRRS58pdJtnpyuZYX8EvLfhlAo8cRaLNYJJLUK9rsBTO2STkQ2DK"
                    "c5+bzvE+p0VgsIUpFHYqnWSnnmtDoVuwnlsvlc6gxFHyYAZjnbxAxUG9zTu49EwFDrfgqbmE"
                    "gU3pZQc4xgKWqOtyM6w25nKLTbn9F8yrGHPWTgWcyubA44dV+n4YeuQG8eSEakJRkbtVKOgR"
                    "g89lEjSDU5q9tSV9pjTIZvmhmhU0BLrKEMzcmCEnSIbSGSeqClgWuR0YpLLxxCHB+9V0i349"
                    "Nv01UG6altzLe/agOqFiNfnlyX8zkGU0vjJTnV+kA91I893knsW6msR7GT/G8cEFTeYmt0K/"
                    "WFDrE68cSbmyfDPJ48pRHMRjpx6doZQFs9E3J4QI1dnj1KOndDmhEHyStwshaR7D+l2tjv+n"
                    "vbA87xXreS+FT3mN/QcdnB3QG4fsUAAAAABJRU5ErkJggg=="
                ),
            },
            "waiting": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx78/PwZHSAoKCkA"
                    "AAA3ODkAAQFHSElWWFl0dnfo6OjX19eWlpeHh4dmZmfHx8empqa4uLhVXmFbZGY9QUIQNRsU"
                    "KCF4h4oMVBgGbA4RJRwLgxwNSxlvfIAKmhsMehwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADb8WXHAAAAQHRSTlP/////AP//////////////////////////"
                    "/////////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAaGrZRQAAAqZJREFUeNq1"
                    "ltmS4yAMRVkGIxZjHMdx0sv0///lgFgMTmqqu6qtygOx4SBdCWTyB438omXib2MLmpzARTI5"
                    "gxvJZOeyxp6nwqzNyF9jhJqN8Uq0ZPI9h7mm3ilD1Yt3YGi2CRpwNbmoaos8cOmMztq43HZ+"
                    "84pF9FNMfNuWyyX80JZt62Z4g1qNVDvpptZvgTxjlfIah0fyBkNnsDWbjjRG4KhOgQB1eyjR"
                    "zSyuNM/k6+dAbrfbupbkDZ/X8m7E2UKjo5hWRUt2o5eN/3Hq1GAZucBw+3t/e9xZLpABLgkS"
                    "dAUZQNZzXIkq0pwl13MTGVrwEsBf9/XxUZ4MsCSwCBNFBId4QTsxxeSZsTo8N+kPewY1TAeW"
                    "w+39fX3cK1hmsDUkg4VBbaWmTic3ZfBPNN5Ox0cIZh/r231lR3BkIFjPnM9YdUEAmVVq3Av1"
                    "YVMQqtU4gFHb3eOsMS1gSAUx4nFJGQpxjxViqI4LfKsOI5DA+5NBQpZijmCOGoeRBw2xytDl"
                    "nCnehqE6kQlcj+BrTq6MhRloPoFtgIbRZBtwRIaC9uRHYDLpVGd4KMASFj1TOkuhEl8EuUt1"
                    "toVCQBzBooBlWh0Tx2ddwOiVT8chHWtZK3BsQPIZXO4hpfNZBmqEyGBjUyrTCY415ve7Q3wP"
                    "PGrCbbgPXMxaBsu0mBVg2DpfEROlmhzADZk1YIhO8SnVmUcwlGhVOcGcHR7U6pZD30EGWQJi"
                    "e8ErqgVeaFVF3YMi13S3G5O8vza5rAG4vDSeZRS06VCYN5//i/nFhcyl4I0JyVs/JpDOUIt7"
                    "Gd9pmBqHAhgnHIrntiYa67flqSXhcD64JHTbmgx/1fX+b4qa0dma/l1FVdHa/bhLpw5l6Ayv"
                    "XoCP17CVffs/7YPlvE+s8z4KT/mM/Qff1xld9hM18gAAAABJRU5ErkJggg=="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgITGhwAAADp"
                    "6ewUHSA4ODlHSEkoKCjU1tnIyctnZ2lSVFWWlpi3t7mnp6l4eHmGhodXX2Ha4+fc3eEUKCEQ"
                    "NRuenqAMVBg9QUJ4hoo/P0B+foAGbA5YY2YRJRwNSxkLgxy/v8IKmhsMehwAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADlW/FeAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAp/cWmQAAAsRJREFUeNq1"
                    "loty4yAMRZEWAwYT24nzaJr0sbv//40rIdsB0tlJZxpNm7TGHEtXQrL6lUz9oM3En8YuaPUE"
                    "biKrZ3CZrB7ktoMDtP3Xa7FziK6LOiM/CO4BTLAInW51tTQFhNlw0DfwQ7YB5+lLG95uCr/7"
                    "FcvmNnd7d+e37ZZ+kr2dd8WiwZY+vQUIl8B+rytRXDVdZwRdq9V2UFjXZk5ZGMjdAJBCpT+2"
                    "mUTkZpwSYkzOVz6fAff7/eHQiCGclxVt0929PMwzugM/u8Oom/8t34pFEnQH+z+n9+upQbam"
                    "ud3+CmEkkDW/006kWFuOgC0wN8cweSgUJvDf0+H6gWIE3i0iBvolcGQNQkQwXqEVD1mH1UE9"
                    "ejVh5fLOwv7z83A9rWC7W/KmE/g1EjPQ9oEyOLs5FMmy/JCRLsUS3Hwc3k+HpgYzI4HB9RvH"
                    "5cR5lnBN7h7Vh5UgcnEmAidtbx5PFTiIqLblshYeJtYsBP3j5WEurzYBr9YkQPLKMXijTArw"
                    "aEhnTSUypoeSKkor39MH5TFdo3AwB7/U4JcZPJKKnLUFTO4SwJkVzNFf6CqkS4+DKcRNOnSB"
                    "ow9GeQZ3MEvRiazRwFzbtRSmBpvl6A1yMihxlDxYwJgl75jOquRTQ6Y7t4F7sF+raCtnOQCO"
                    "UcAaza3cdPqe6zlW5fZfMK9iTFk7CniQzR6Ssilzcrt2FERbSZGRm0IK2tO7VCbeMHgYlmg7"
                    "SR+ViS8uFIccm8wQ7FL5PhW89nIyjpz/dZHzJllVWjoOOTwV3S26sm26m1Bh7pZ8lieOIJtQ"
                    "UTq/oI/ui4aso3WZ2Wx+kR8YRurv1qdOmVeTuogbNvDggipzc1h+s5pvy4knI4mf5V3VyWMR"
                    "KPbqe1OPZihlwW7N3YRQPps9of3ulJYJheAGfb/gB8Nt2FzaYvw/7YXlea9Yz3spfMpr7D81"
                    "eR0BTxDFyQAAAABJRU5ErkJggg=="
                ),
            },
            "none": {
                "light": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEUbGx79/f0ZHSAnJygA"
                    "AABHSEk3ODkAAQFWWFlzdnfo6OiWlpdlZWa3t7eGhofHx8fY2NioqKhVXmFbZGY9QUIQNRsU"
                    "KCF4h4oMVBgGbA4RJRwLgxwNSxlvfIAKmhsMehwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAADZz6iGAAAAQHRSTlP/////AP//////////////////////////"
                    "/////////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAaGrZRQAAAnpJREFUeNq1"
                    "lomSoyAQQGl6wQZRMMaYzLHz/3+5XEZQdza1NXalKorwaPqE/YrCflAy8aexC5qdwI1kdgY3"
                    "kNnK5YXsp1KnXY/HGMGZ7RjKksxeUxg1DEY5UEcfCToJIC2IErxuO6qnjGLDhS4q24KXttIb"
                    "OxIwMGWYBWm63ZlwnsfLxf+ijPNczRhctFUP2ghjK70NAMtzkQOILXimphKai037ON+AFvno"
                    "5vmJsC9YpPxAxb1+Nux2u03T4rzm87p86/3xkUkdFY1uVbB410FfK9hCV7xxdqHm9vv+9rjz"
                    "HCANXRLET/VG5KwdMG5iwx/QYgi9OTtpqsCjB3/dp8fHMtLQmMDSQ2QAy7DKeN9757msJ+1M"
                    "yng55sGiub2/T4/7EywyuHUsg6WLthUajE7uE8FG2yjwgVeD+cf0dp/4FhwYEaw7xC6Gk1mc"
                    "79/3GtuhsrEHR9uuGmcbwwKmFBB9TBebpsmjfJElmBJ4HWkEZVN0AYzRxv5piN7BpLIF2nMV"
                    "DKUzr1vwNa8RwYyeNiRw66H+ybYp2Mz/g5nVKc4ig1rGg7ZKv2QKv0BuwXIBe9er5CjvPL2A"
                    "U453w9559ZjYg5dwVDrnMoGTMoNd+1K4fQvuNcMWrDTBaxks8uLDBKGtxgWZF2AKWqFNcTZE"
                    "MD0rxEFK1wNSNHUHacRyIL5WSQVahrOulWdXhAaoc4YLrMsmCr5W3HS4kMvRsEWH8lVSfVs2"
                    "GQqJhUiBZWRaEsZBG/dydST8q9D7YVlIvS2mlpTKwyYQ0JrQmgyF1qSK1vRy91fgetOCxr81"
                    "U1c309evFdg76Oiw/WNs/6Ju/6ddWM67Yp13KTzlGvsHd4EYPoZZ10AAAAAASUVORK5CYII="
                ),
                "dark": (
                    "iVBORw0KGgoAAAANSUhEUgAAAFgAAAAgCAMAAAChOk+qAAAAwFBMVEXz8/YCAgITGh0AAADp"
                    "6ewoKCnIyMoUHSDV19loaGo4ODlHSEm3t7l3d3hTVVaHh4mXl5mnp6lYX2Ha4+fb3eEUKCEQ"
                    "NRt+foAMVBg9QUJ4hoo/P0CenqAGbA5YY2YRJRwNSxkLgxy/v8IKmhsMehwAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                    "AAAAAAAAAAAAAAAAAAAAAAB8SGTsAAAAQHRSTlP///8A////////////////////////////"
                    "////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAp/cWmQAAAopJREFUeNq1"
                    "lmuXojAMhkm3Lb3QQhHRcZzr7v//jZu0Ai1yHD6MOepRCo/JmzRp9Sda9Yt2I/42dkJXT+BG"
                    "cvUMLpGrnVzlBDAbNtd4K5UQvGl5Rt4JDgBGWwaeK77GVhpEC9AxaCu+gHdZA0ISwwCaCTm1"
                    "EfoEQnmtEOzjbaUdLu99j69o75dDsWiYwk9pAfRJk9/LkgZQzRDVkhLgdKehh8K8ynSw4NA5"
                    "RDgi4pd+DsUF6xb/tQ9u5fMF2PF4HMc6GYPLfDf6CQ2pTH8mCe3h9jRnYEuOAVFmwMPx7/nj"
                    "7Vwzsrpewn0F3SLImm+MywJDhRW4WQhX8TKRrlQYwf/O49snS4bgm8odaHwjuCMNNObeyIol"
                    "P7k7qbWm0rm8bg4Wjl9f49t5BtvDlDcewa8dMjXBMIPg4xoWmdwozFCC68/x4zzWazAxIhhE"
                    "aIRoUp5TvFKIe4+Fya8NCI7aLh4PK7BOmlpFZc1iuEPHN/ZocVEl8Gx1BEQpBIGbynT062pQ"
                    "Z47xtvQrarM2DyYHv6zBLzdwi5JR1iYwuotQYR6AxS4wlmoTN52mCLWpJIE93Ee9LYVZg+cU"
                    "uLQzMHGYPJjAbGfy5D14KiQLfdrLGljbJTBnZme5PQTTKuti1q4J7KBLvcH9tEFIioxcF1Lg"
                    "XwQRy0QaAjs3dwid9+CtLY2dhtWZYXOZ7pdxm3GZdsYVqwLmxZ+bUNWJsm2KLuu4qVvSXh4o"
                    "gmxCNTrYdqE4H/RKdd5ZkZnNagY7H9Mt9ndLzyhWukSNXj5o9HS9mU2WOQlpJPFYTVTW5Whq"
                    "wQy9VqIcTfumHs5QzILtDRTcR8N0/7Ei4JQWjm+Pf3Y//p92YHneEet5h8KnHGP/A1yrHWHG"
                    "JjxCAAAAAElFTkSuQmCC"
                ),
            },
        },
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
