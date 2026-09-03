---
name: aor-format-teams-message
description: Draft a rich, nicely formatted Microsoft Teams message and put it on the clipboard ready to paste with a single Ctrl+V. Teams supports far more formatting than it appears to - headings, nested bullets, numbered lists, tables, code blocks, quotes, colour, highlight and inline images all survive a paste. Use whenever the user asks to draft, write, format or prepare a Teams message, chat update, status post or announcement; asks to "format this for Teams"; asks to put a screenshot, diagram, chart or image into a Teams message; or asks to copy something to the clipboard for pasting into Teams. It prepares the clipboard only - it never sends a message, uploads a file or attaches anything.
license: MIT
compatibility: "Windows only. Writes CF_HTML to the Win32 clipboard through ctypes; needs Python 3.9 or later on PATH as python, standard library only. Paste target is Microsoft Teams desktop or Teams web in a Chromium browser."
---

# Format a Teams message

Write Markdown. Run one command. The user pastes with Ctrl+V.

## The formatting you actually have

This vocabulary is **verified by live round trip** (clipboard → paste → send → read back
the rendered message), not taken from documentation. Microsoft publishes no allowlist for
this surface, and the tables that do exist describe the Graph/bot API, which behaves
differently — it rejects tables and horizontal rules that the compose box accepts.

| Write this | Get this |
|---|---|
| `# H1` `## H2` `### H3` | headings — h1–h3 verified. **h4–h6 are refused because Teams silently downgrades them to h3** (verified 2026-07-29): the text survives, the level is lost, and you would only find out after pasting. Use `###` plus bold text to carry deeper structure. Leading whitespace before the `#` is fine |
| `**bold**` `*italic*` `~~strike~~` | bold, italic, strikethrough |
| `++underline++` `==highlight==` | underline, yellow highlight |
| `` `code` `` | inline code chip |
| ```` ``` ```` fenced block | code block, rendered with line numbers |
| `- item` / `1. item` | bullet / numbered list; indent 2 spaces to nest, verified to depth 3 (both kinds). An item may wrap across lines — see "Wrapping a long line" |
| `> quote` | blockquote with a left bar |
| `---` | horizontal rule |
| `[text](url)` | link |
| GFM `\| pipe \| tables \|` | real bordered table, with real `<th>` header cells |
| `<span style="color: red">…</span>` | coloured text (**approximate** — see below) |
| `<img src="data:image/png;base64,…" alt="…">` or `<img src="name:shot" alt="…">` | an inline image — see "Images" below |

Also allowed inline: `<u> <strong> <b> <i> <em> <s> <strike> <code> <br> <span>`.
Emoji, arrows (`→ ⇒`), dashes (`—`) and Danish characters pass through exactly — write the
literal character, never an HTML entity.

**Everything else is refused, by name.** `<div>`, any other raw tag, `h4`–`h6`, `![alt](src)`
Markdown image syntax, an `<img>` with an `http(s)` source, and any `span` style beyond
`color`/`background-color` cause a non-zero exit listing exactly what and where. Nothing
reaches the clipboard until the draft is expressible — a silently downgraded message would
only be discovered after it was pasted into a real conversation.

## Wrapping a long line

You may wrap a long line, including inside a list item. A following line that carries no
list marker **joins the line above it** and renders as a line break:

```
2. Four coloured bands sit above four striped areas. **Under which colours can you still
   see separate stripes, and under which does it look like flat grey?**
```

That is one list item, and the `**…**` spanning the wrap renders as bold — the emphasis
passes see the joined text, not each line separately. Indenting the continuation is optional; what makes
a line a new item is the marker, never the indentation.

Three things end a list rather than continuing it: a blank line, a line with its own list
marker (a new item — indented means nested), and any line that starts another block — a
heading, `---`, `>` quote, code fence or table. So a heading written directly under a list,
with no blank line, is still a heading.

A blank line inside an item is not a paragraph break — it ends the list. If an item needs
two paragraphs, make it two items.

## Images

**A path written in the draft is never read.** There are two ways to get bytes in, and both
put the file choice on the command line rather than in draft text.

**Either** read the image with your own file tools, base64-encode it, and inline it:

```
<img src="data:image/png;base64,iVBORw0KGgo…" alt="Build status chart">
```

**Or** bind the file on the command line and refer to it by name — better for a large image,
and the only practical route when a human is writing the draft:

```
<img src="name:chart" alt="Build status chart">
```
```
python <skill-folder>/md2teams.py draft.md --image chart=C:\work\build-status.png
```

A name is `[A-Za-z0-9_-]`, 1–64 characters — no dots, no slashes, no colons. It is looked up
in a table built from the command line and is never joined onto a directory, so `../..` and
`\\host\share` are not paths this tool rejects, they are strings that cannot be names.
`--image` is repeatable, and both halves must agree: a `name:` with no binding is refused,
and so is a binding the draft never uses — an image you asked for and did not get is exactly
the silent failure this tool exists to prevent.

**The bound path must be local.** A UNC or network path (`\\host\share\x.png`) is refused
before anything opens it: on Windows, merely asking for that file's size makes the machine
connect to that host and offer its credentials. If the image you want lives on a share, copy
it somewhere local first and bind that. This applies to the command line you compose, which
is the one place a path can still enter — so it is worth reading twice if any part of it came
from content you ingested rather than from the user.

Four things you cannot infer and must not guess:

1. **An image from a URL kills the entire paste.** Teams answers an external
   `<img src="https://…">` with *"Cannot paste this image. Try different source."* and drops
   the **whole message**, not just the image. Only a `data:` URI is accepted.
2. **PNG and JPEG only, and the type is read from the bytes.** A `data:` URI whose declared
   type contradicts its content is refused rather than trusted.
3. **Metadata is stripped and cannot be preserved.** The converter re-emits only the chunks an
   image needs, so EXIF, XMP and PNG text chunks — including the pre-crop thumbnail some
   editors leave behind after a crop — do not survive. This is not configurable.
4. **What the recipient gets is Teams' copy, not your bytes — and not your format.** Teams
   ingests the image and **re-encodes** it (a pasted PNG came back as a JPEG). `alt` survives
   the ingest, but only the *sending* client's view of it has ever been checked, and that
   question is now closed as permanently unverified — the sending side cannot evidence what a
   recipient sees. **Write a short caption line next to every image.** That sentence is the one
   description verified end to end; `alt` is not.

**There is no size cap.** A 2 MB policy cap was removed on 2026-07-30. Teams itself accepted
8.83 MB end to end when probed, and the real ceiling was never reached. Above that figure the
converter prints a `NOTE` on stderr — never a refusal — saying the draft has left the evidenced
band. If you see it, nothing is wrong; it means that if a recipient later reports a broken or
missing image, size is the first thing to suspect, and that the sending client cannot check
their view for you.

**Do not bother sending a high-resolution image.** Teams re-encodes to roughly 800 px on
the longest side, confirmed at a recipient on 2026-07-30: detail finer than about 2 px at
that width is gone. Resize to ~800 px yourself if the source is larger — you keep control
of the resampling instead of taking whatever Teams does, and the payload gets much smaller.
If fine detail is the *point* of the image (a dense table, a small-text screenshot), crop
to the part that matters rather than shrinking the whole thing, or say in words what the
image would have shown.

**Tell the user what you embedded.** The converter prints each image's type, dimensions, byte
size and hash. It cannot say what the picture *shows*, and neither can you if you did not look —
so if the user has not seen the image, say which file it came from.

## Procedure

1. **Draft in Markdown** using only the vocabulary above. Write the draft to a `.md` file
   rather than passing it inline — shell quoting mangles dashes, quotes and code blocks.

2. **Show the user the Markdown and get a yes** before copying, unless they have said to
   skip that. This skill formats; it does not originate technical claims.

3. **Convert and copy:**

   ```
   python <skill-folder>/md2teams.py <draft.md>
   ```

   Exit 0 means the clipboard holds the message. Exit 1 means the draft was refused —
   read the named problems, fix the Markdown, run again. Do not work around a refusal by
   removing the formatting wholesale; the message names a specific fixable construct.

   Useful flags: `--print` renders to stdout without touching the clipboard (good for
   showing the user what they will get); `--tight` drops the blank-line spacers between
   blocks; `--image NAME=PATH` binds an image file (repeatable, see "Images");
   `--capabilities` prints this vocabulary from the manifest.

4. **Tell the user it is ready to paste** with Ctrl+V, in one short line.

## Things that will bite

- **Colour is approximate.** Teams snaps every colour to its own palette — a bright red
  came back as a duller one, a pure yellow as a pale green-yellow. Use colour for emphasis,
  never for exact branding or for meaning that depends on the precise hue.
- **A message ending in a code block cannot be sent with Enter.** The caret sits inside the
  code block and Enter adds a line there. End such a message with a short closing sentence,
  or tell the user to click the send button.
- **The clipboard is ambient machine-wide state.** Anything that runs between the copy and
  the paste can overwrite it. Copy as the last thing you do, and do not copy something else
  afterwards "to check".
- **No boxed callouts.** `display`, `padding`, `border` and `border-radius` are all stripped,
  so a bordered note box is impossible. Use a blockquote or a heading instead.

## When formatting looks wrong

Teams changed this surface before (`<div>` and inline styles started being stripped in late
2024) and will again. If a paste that used to work stops working, re-verify rather than
guess — `capabilities.json` carries the procedure under `reprobe`. The Playwright harness and
clipboard tools that procedure names are maintainers' tooling; they are not part of the
installed skill or of the public repository. Update the manifest with what you observe and
re-run the offline tests (the plugin's `tests/` directory in the repository; a bare-skill
install does not carry them); the manifest is the single source of truth that both this file
and the converter's validator read.
