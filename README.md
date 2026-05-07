# claude-code-swiftbar

> **Know what every Claude Code session is doing — without alt-tabbing through ten terminals.**

A featherweight macOS menu bar status light for [Claude Code](https://claude.com/claude-code), built on [SwiftBar](https://swiftbar.app). One glance tells you whether anything needs you. One click drops you back into the exact tab.

---

## The problem

You kick off a long Claude Code task, tab away to read something, then forget:

- Which terminal is it running in?
- Did it finish?
- Is it stuck on a permission prompt I missed?
- Are the *other* two sessions still going?

You end up sweeping through every iTerm tab and VS Code window guessing. claude-code-swiftbar just tells you.

---

## What you get

### 🟢 A status light in your menu bar

The icon reflects the **most urgent state across all your active Claude Code sessions** — refreshed every 500ms.

| Icon | State    | Meaning                                                       |
| ---- | -------- | ------------------------------------------------------------- |
| ❗    | ASKING   | Claude is waiting for permission or input — **act now**       |
| ⏳    | WORKING  | Claude is running                                             |
| ✓    | WAITING  | Idle session, or Claude finished a turn — your move           |
| ⚪    | (none)   | No active sessions                                            |

Pure SF Symbols, no color — the menu bar adapts to light/dark automatically.

### 📋 Per-session dropdown with what each one is doing

Click the menu bar icon to see one row per session, with the **AI-generated task summary** (the same one Claude Code shows in `/resume`):

```text
ASKING   Investigate menu icon display      (12s ago)
WORKING  Refactor auth middleware           (3m  ago)
WAITING  Plan database migration            (15m ago)
```

No more guessing which window has which conversation.

### 🎯 Click to jump back to the right window

Each row's click action takes you straight to that session — with **per-tab precision** when possible:

| Where Claude Code is running | What happens on click                                      |
| ---------------------------- | ---------------------------------------------------------- |
| Terminal.app                 | Focus that specific tab (matched by tty)                   |
| iTerm                        | Focus that specific session (matched by tty)               |
| VS Code / Cursor / Windsurf  | Focus the workspace window for that project                |
| Ghostty / Warp / Alacritty…  | Bring the app to front                                     |

### 🔔 Per-session desktop notifications (opt-in)

Don't want to glance? Get a macOS notification when a state transition happens — **configured per session, right from the dropdown**:

```text
WORKING  Refactor auth middleware           (3m ago)
└ in my-project
└ Open folder
└ ─────
└ Notify on:
└ ✓ ASKING       ← only ping me when this session needs me
└   WORKING
└   WAITING
```

Two sessions in the same project? They keep independent notification preferences — one stays loud, the other stays quiet.

Banners can carry the AI title in the body, and clicking the banner jumps back to the right window (when [`terminal-notifier`](https://github.com/julienXX/terminal-notifier) is installed: `brew install terminal-notifier`).

### ⚡ Lightweight by design

- **~5% of one CPU core** at 500ms refresh — well under 1% of your machine
- **Stdlib-only Python** for the runtime — no `pip install` nightmare, no virtualenv to manage
- **Tiny disk footprint**: 4 small files in `~/.claude/scripts/`
- **One JSON config** — every knob in one place

---

## Install

```bash
brew install --cask swiftbar              # if you don't have SwiftBar yet
git clone https://github.com/<you>/claude-code-swiftbar
cd claude-code-swiftbar
./install.sh
```

That's it. Open a Claude Code session and watch the menu bar update.

The installer is **idempotent** (safe to re-run), **non-destructive** (your existing hooks in `~/.claude/settings.json` are preserved), and **respects your customizations** (your config file is backed up before any update).

---

## Customize

Everything tunable lives in **one file**: `~/.claude/swiftbar-config.json`.

The seed config sets sensible defaults for refresh interval, icons, hook routing, and notifications. Want a different SF Symbol for the menu bar? Want notifications to play a sound? Want to silence permission prompts? Edit the file, run `./install.sh` again, done.

The most common knobs:

| Setting              | What it does                                                                  |
| -------------------- | ----------------------------------------------------------------------------- |
| `refresh_interval_ms`| How often the menu bar updates. Default 500ms. Floored at 100ms.              |
| `icons`              | SF Symbol per state, used in the dropdown rows.                               |
| `header_icons`       | Optional separate icons for the menu bar header (often simpler/template-ish). |
| `notify_icons`       | Icons used in the per-session "Notify on:" toggles.                           |
| `notifications.sound`| Play a system sound on banner. Off by default.                                |
| `events`             | Which Claude Code hook event writes which state.                              |

Full schema and field reference: [OVERVIEW.md](OVERVIEW.md).

---

## Why a SwiftBar plugin?

Several other macOS Claude Code menu bar apps exist (see [Related](#related-projects)). They're nice. They're also full apps you can't easily change. This one is **~700 lines of Python you can read in an afternoon and bend to your taste** — fork-friendly, hackable, and zero surprise behavior.

If you live in SwiftBar already, this drops in next to your other plugins.

---

## Requirements

- macOS
- [SwiftBar](https://swiftbar.app)
- [Claude Code](https://claude.com/claude-code)
- `/usr/bin/python3` (system Python; ships with macOS)

Optional: [`terminal-notifier`](https://github.com/julienXX/terminal-notifier) for click-to-focus notification banners.

---

## Uninstall

```bash
./uninstall.sh
```

Removes the plugin, the deployed scripts, and our entries in `settings.json`. Your config and per-session state files are left in place; the script tells you exactly what to delete if you want a clean slate.

---

## Related projects

- [gmr/claude-status](https://github.com/gmr/claude-status) — full menu-bar app with desktop widget.
- [VibeStatus](https://www.vibestatus.app) — closed-source paid app with push notifications.
- [PiXeL16/claudecode-macmenu](https://github.com/PiXeL16/claudecode-macmenu) — menu-bar app with usage analytics.

This project is the **"small SwiftBar plugin you can hack on"** alternative.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Architecture, internals, dev workflow

See [OVERVIEW.md](OVERVIEW.md) for repo layout, the hook → state-file → plugin pipeline, the full config schema, and how to run the test suite.
