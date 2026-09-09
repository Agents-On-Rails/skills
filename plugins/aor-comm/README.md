# aor-comm

Preview `0.x`. Communication skills for agents. Today there is one: it drafts a rich Microsoft
Teams message and puts it on the Windows clipboard ready to paste with a single `Ctrl+V`. It
prepares the clipboard only — it never sends a message, uploads a file or attaches anything.

| Skill | What it does |
|---|---|
| `aor-format-teams-message` | Turns Markdown into the formatting Teams actually keeps — headings, nested bullets, numbered lists, tables, code blocks, quotes, colour, highlight and inline images — and writes it to the clipboard as `CF_HTML`. Refuses, naming the construct, rather than silently dropping anything Teams cannot render. |

## Requirements

- **Windows only.** The clipboard is written as `CF_HTML` through the Win32 API via `ctypes`.
  There is no POSIX path and no cross-platform fallback.
- **Python 3.9 or later**, on `PATH` as `python`.
- **No third-party dependencies.** Standard library only, so there is no `requirements.txt` and
  nothing to install.

## Using it

Write Markdown, run one command, paste:

```
python <plugin-root>/skills/aor-format-teams-message/md2teams.py <file.md>
```

The vocabulary it targets is **verified by live round trip** — clipboard, paste, send, then read
the rendered message back — rather than taken from documentation. Microsoft publishes no allowlist
for this surface, and the tables that do exist describe the Graph and bot APIs, which behave
differently: they reject tables and horizontal rules that the compose box accepts. What was
verified, and against which clients, is recorded in
[`capabilities.json`](skills/aor-format-teams-message/capabilities.json); it was probed against
Teams web in Edge and Teams desktop on Windows 11, and other Chromium browsers were not.

Ask for what the tool knows it can do:

```
python <plugin-root>/skills/aor-format-teams-message/md2teams.py --capabilities
```

## Refusal rather than silent loss

A construct Teams cannot render is **refused by name**, at a non-zero exit, with nothing copied.
The alternative — dropping it quietly — puts a message on the clipboard that looks finished and
is not, and the person pasting it has no way to tell. A refusal is recoverable; a silent
downgrade discovered after sending is not.

## Checking it runs here

The plugin ships a smoke test rather than its development suites, so an adopter can confirm the
install works on their own machine without inheriting a test tree they did not ask for:

```
python <plugin-root>/smoke-test.py
```

Five falsifiable checks, `GREEN — 5 of 5` when the plugin is sound.

## Not in this preview

- **No sending.** The clipboard is the whole surface. Nothing is posted, uploaded or attached.
- **No POSIX support**, and no cross-platform CI.
- **One client family verified.** Teams web in Edge and Teams desktop on Windows 11; other
  browsers are unprobed rather than known-broken.

MIT licensed — see `LICENSE`.
