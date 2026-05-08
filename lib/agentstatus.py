"""Shared utilities for agent-status-swiftbar.

Single source of truth for filesystem paths, default state taxonomy,
process-tree introspection, transcript parsing, and config loading.

Imported by:
- hook/agent-status-hook.py     (writes per-session state files)
- plugin/agent-status.py          (renders SwiftBar dropdown)
- scripts/install_claude_hooks.py      (patches ~/.claude/settings.json)

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
HOOK_PATH = SCRIPTS_DIR / "agent-status-hook.py"
PLUGIN_PY_PATH = SCRIPTS_DIR / "agent-status-plugin.py"
TOGGLE_PATH = SCRIPTS_DIR / "agent-status-toggle.py"

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

# 16×16 fully-transparent PNG. Used as an icon-column placeholder on
# continuation rows of the per-session id footer so the wrapped text
# aligns under the id (not under the brand logo).
TRANSPARENT_ICON_16 = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAEklEQVR42mNgGAWjYBSMAggAAAQQ"
    "AAGvRYgsAAAAAElFTkSuQmCC"
)

# Brand templateImage PNGs (32×32 black-on-alpha; NSMenu auto-tints).
# Used on the per-session "logo + session id" footer row in the dropdown
# so each session is clearly tagged with its source CLI. Rendered from
# assets/{claude,codex}.svg via rsvg-convert.
SOURCE_LOGOS = {
    "claude": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADSElEQVR42q2XV2gVQRSGv3ht"
        "SVSCUTQqKqKCBX2wYcWGHcWoWIINBFGwBCEQsSBYwILGFwuIBXxTRCRiAyXBXhD7gwUsiRpN"
        "QtQkGs368i8chru3bHJguTunzPl35sx/5kJsaQU8Ar4Cu2L4NQEWA11pZNkFeHr+Ab0D/LbK"
        "5wUQSSZBkzj2Gsd3bYDfIv12EtBGk35AnVmFn0DbAKAecDeKrTmwFJgTFsReA8ADNjr2NsZ2"
        "0rG1BK7KVg9MDQOgDVBqkpRoYl96GtsGo48A5xzwe2IlGqolLgVmO7blzkTrjW2E0Y83+oNO"
        "TD0wPBaAfMd5N9DUFOA9Y/8MpMs218S0DQDsATvjLfUAoNoJugF0kH2UY8uTPlfjd2Ylaxzf"
        "K4kez74iHs/Z8wmynzX6MqA1sE/jc0A74L0T/wFon0zRtQD2a0ktCW0H+gB/jD4fOKX3bUCh"
        "k7wOGBn2+I0F3jgTFptj5QFPTNKSKPue31AiSgcKtAJewPMjQH8rgGm7AzOAlUBmokDGRFmN"
        "WM8/YAjQC8jRRxQDlY7feYAsFV9H0WasznjIqY1YAMrj+LwEZqUAn9REfPml4HIhrlI7/qh9"
        "nhSC1yuAB8BD/d4Ul5ACZANrgAzRboaoNq0BtfMeKFKiYrVpL5pjSgJ9ICIwLYBUVXZOnLi3"
        "ovQyHUW/Y5YD3/UUAc+T+aqBwLUE9v8PcAx4GqdefgMLEkmcpQn9o1gNnIgD4rqKNhOYqS54"
        "2yExT7pASQM2q0P6AUXqfq/NOAjE7SjnPA0YB2xRL2kVlHyyONyfrApYre54Wbo7wDq9Fzq3"
        "plfmqHVLlvncs37R3Hb9y2cl0EME4wHTgQsmJtdQ9kegf6IACswk33TV9mWSqYP50l3SeLBY"
        "r1bjCn35YY2/A8MSAbAEuA8cNfcAn7+/abIjRv9Fui4abzIfUChdHvBXPWNiGFJJFYN5Olqp"
        "0nczNyGfwpsBjw2IZdKPFy/UAqOTBXDcFFdfo882W2VlkLnKVwCdpe+oelqYTPIVhjSmOLYd"
        "sj2LEme34gAN5PS/wLwotitKcC2KLWI44kxDAEwGpgX8pfN7++kYDLpKp6PRJV1t29OlNLRE"
        "QsbVqdOlqxbKwgL4D4WtcTe8bgE+AAAAAElFTkSuQmCC"
    ),
    "codex": (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADn0lEQVR42rWXW4iNURTHf8Zh"
        "cosZKS+Y3CW5JuQychuXBy8epVxjmpASGbcUknELpRTmjYjEjEikJiKRu9wTmmHIJTPnnBmf"
        "l/83Lbt9zvnOaHadZr611157rbX/67/XhuijO7AaqAI+AL+B58BJYDbQihYci4DvQJDmdw/o"
        "n63hKF7vBtbo/wfAcWXhG1AAFAHLgA6SlQLtgBjwUbqvmxv5UkX3B9ggo74xGYinyc51YEy2"
        "m3cEPsvAthQ6nYFdQL3Z7AlwFigH7gINkjcAa6Nu3gM4pIVvPZG3VtqrzcaXgHEeWwOA00Zv"
        "ZbqN+wPnlfJwQamjM1VYCOefArMiYG2H9OPAYJ/SHOCXMdyov3ONzlEzXwuUAG0cO8OBvimc"
        "uKG15e7kWCCpyZvAMOCFvm1aE/rtB/IdG32AM1qT1BF2dXQmab4OaBsKc1UmAVChb1I4kPQA"
        "0gLxK3DNZOmrzjzMUhvgp+ZGhQaWSFDtROVzoAHY7AFiEjhoIp4BPHIqo0hzbySbEhq9LMEW"
        "J7J0DrhAfA1Mc9bHgBWmlAPgosjqH7s1EkyI6ECNE9V0E+15Dx13AfY4RJUUczadawAMiujA"
        "F6DY4YYYsFzOJYAybeyW+B3jwHg3A4URHdiYpt7XaU1CqV8urFjAV0jnDZCbo1sMCwqNOv0d"
        "5NRypgssAIYAt4DDwH0TRBxYIOAWAPMBFpsqyDOGxpmzvQAMFDltypCBP+Z7oda/c/R2Gvr+"
        "hwcuOMxmkZyQzuYsHBhrWNOOiSbopmjDDap0gbhILlMKax1ycR2N4sBgyetDQXvjQAiivc6R"
        "APQDzknnme4PSzrVER0olPyTzUAgkqg0jvhKLgTsS6P3UHwQ9Qi2Sl5pDQZqMgFmimjCDR4r"
        "UkTXB5SlGpVaLAsM5Ok7EEgBGCnBdyDHnGuJshA6ck2L4+oVXbLZmMGBXAE9AF6Zi4+2qnsf"
        "JecD+wxGznru+zzRbUKl6nNgqOkH4sBot4ROaPJKCrIpULPhXjjFTpaOmfmZprkJG5wfnour"
        "ifHqMzShdhQJGz6chGO9mW/QI6Z3OqMlZsEpRe2OUYbPA9X+Ck+lxNQvBuoVukXtiNeYG7IR"
        "uK20nnIqI6Fz75LCzgYD7Pxs3wSjgatOd2x/cQ9Y7YW11px58f88zXqJpHoqK3XAdkX9DTgi"
        "MnkPdBLqlwIjtP5wJgeaM4boxZPuofoLWNWSr+UcYJ5w8VbVU6uLrFRP+UjjLxfEfc6DyjWY"
        "AAAAAElFTkSuQmCC"
    ),
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

# Full official Claude Code hook surface. install_claude_hooks.py iterates this so
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
    return f"agent-status.{suffix}.sh"


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

    source = (record.get("source") or "claude").strip().lower()
    source_name = {"claude": "Claude Code", "codex": "Codex"}.get(
        source, source.title() or "Agent",
    )
    title = f"{source_name} · {STATE_LABELS.get(new_state, new_state.upper())}"
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
