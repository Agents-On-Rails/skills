"""Markdown subset -> Teams-shaped HTML.

Deliberately NOT a general markdown library. A generic renderer emits whatever HTML
it likes; we must emit only what the Teams compose box provably accepts, and we must
emit it in the shape Teams keeps. The accepted set lives in capabilities.json and is
enforced separately in validate.py -- this module's job is only to produce it.

One finding shapes the code more than any other: a code block written the obvious way
(real newlines, space indentation, inside <pre><code>) is normalized by CKEditor into
Teams' own <br> + &nbsp; shape on paste. So we emit the simple form and let the editor
do the conversion, rather than hand-building the shape Teams stores.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from enum import Enum

from .images import IMAGE_NAME, NAME_PREFIX, ImageError, ImageInfo, sanitize_bytes, sanitize_data_uri

__all__ = ["RenderError", "render"]


class RenderError(ValueError):
    """The draft uses something Teams cannot represent. Carries every problem found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# --- inline ------------------------------------------------------------------

# Raw HTML the author may write directly. Everything else is refused rather than
# escaped, so an agent that reaches for <div> gets told, not silently ignored.
_RAW_INLINE = r"u|strong|b|i|em|s|strike|code|br|span"

_RAW_TAG = re.compile(rf"</?(?:{_RAW_INLINE})(?:\s[^<>]*)?/?>", re.I)
_ANY_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)(?:\s[^<>]*)?/?>")
_SPAN_OPEN = re.compile(r"<span\s+style\s*=\s*([\"'])(.*?)\1\s*>", re.I)
_STYLE_DECL = re.compile(r"([a-z-]+)\s*:\s*([^;]+)", re.I)

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_IMG_TAG = re.compile(r"<img\b[^<>]*/?>", re.I)
_MD_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]*)\)")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_ITALIC = re.compile(r"(?<![\*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)")
_ITALIC_US = re.compile(r"(?<![_\w])_(?=\S)([^_\n]+?)(?<=\S)_(?!_)")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
_UNDERLINE = re.compile(r"\+\+(?=\S)(.+?)(?<=\S)\+\+", re.S)
_HIGHLIGHT = re.compile(r"==(?=\S)(.+?)(?<=\S)==", re.S)

# ==highlight== emits BOTH properties, and the pair is the point. The background alone
# was arbitrary and harmless -- Teams snaps it to its own palette anyway, which is why
# the exact hex barely matters. What was NOT harmless was setting a light background
# and leaving the foreground to the theme: under a dark theme that inherits white text,
# the result is white-on-pale-yellow, contrast around 1.14:1, i.e. an unreadable bar.
# Nobody had probed a dark client, so that was an untested assumption sitting under a
# tool whose whole promise is that it does not degrade silently. Pinning the foreground
# makes the outcome theme-independent, so the probe is no longer load-bearing.
# Rationale of record lives in capabilities.json -> supported.span.
HIGHLIGHT_STYLE = "background-color: #FFF1A8; color: #242424;"

_SENTINEL = "\x00{}\x00"


@dataclass
class _Ctx:
    problems: list[str] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    # Names bound on argv by `--image NAME=PATH`, already read to bytes by the
    # caller. This module still never opens a file; it only looks names up.
    bindings: dict[str, bytes] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)

    def fail(self, msg: str) -> None:
        if msg not in self.problems:
            self.problems.append(msg)


def _resolve_named_src(name: str, ctx: _Ctx, where: str) -> tuple[str, ImageInfo] | None:
    """Look a `name:` reference up in the argv bindings. Never touches the disk."""
    if not IMAGE_NAME.match(name):
        # Deliberately does NOT quote the offending name. Refusal messages in this
        # tool name constructs and never echo draft content -- that is what keeps
        # them structurally unable to carry anything the encoding layer has to
        # survive, and what stops a draft from writing into the agent's stderr.
        # Everywhere BELOW this guard the name has already matched IMAGE_NAME, so
        # it is ASCII by construction and safe to name.
        ctx.fail(
            f'{where}: this <img src="name:..."> does not name an image. A name is '
            f"1-64 characters of letters, digits, underscore and hyphen -- a path is "
            f"not a name, which is why a path cannot select a file here. Bind the "
            f"file on the command line (--image shot=C:\\path\\to.png) and write "
            f'<img src="name:shot">.'
        )
        return None

    data = ctx.bindings.get(name)
    if data is None:
        known = ", ".join(sorted(ctx.bindings)) if ctx.bindings else "none"
        ctx.fail(
            f'{where}: <img src="name:{name}"> refers to an image nobody bound. Add '
            f"--image {name}=<path> to the command line. Bound names: {known}."
        )
        return None

    ctx.used.add(name)
    try:
        return sanitize_bytes(data, f"{where} (--image {name})")
    except ImageError as exc:
        ctx.fail(str(exc))
        return None


def _render_img(tag: str, ctx: _Ctx, where: str) -> str:
    """Rewrite one <img> tag, or record why it cannot be emitted.

    The tag is REBUILT rather than passed through: that is what guarantees the
    metadata stripping in teamsfmt.images actually reaches the clipboard, and
    that no attribute we have not considered rides along.
    """
    src_m = re.search(r"""\bsrc\s*=\s*(["'])(.*?)\1""", tag, re.I | re.S)
    if src_m is None:
        ctx.fail(f"{where}: <img> has no src attribute.")
        return ""

    alt_m = re.search(r"""\balt\s*=\s*(["'])(.*?)\1""", tag, re.I | re.S)
    alt = alt_m.group(2) if alt_m else ""

    src = src_m.group(2).strip()
    if src[: len(NAME_PREFIX)].lower() == NAME_PREFIX:
        resolved = _resolve_named_src(src[len(NAME_PREFIX) :], ctx, where)
        if resolved is None:
            return ""
        clean_src, info = resolved
    else:
        try:
            clean_src, info = sanitize_data_uri(src, where)
        except ImageError as exc:
            ctx.fail(str(exc))
            return ""

    ctx.images.append(info)
    # alt is emitted even though its survival through Teams' ingest is unverified
    # (capabilities.json 'untested'): costs nothing, and is the only accessibility
    # carrier we control. SKILL.md tells the agent to add a caption line regardless.
    return f'<img src="{clean_src}" alt="{html.escape(alt, quote=True)}">'


def _attr_value(value: str) -> str:
    """Escape a captured value for a double-quoted attribute -- quotes only.

    By the time a URL is captured, the whole line has already been through
    html.escape(quote=False), so &, < and > are entities already. Running
    html.escape over it again turned &amp; into &amp;amp;, which decodes to a
    literal "&amp;" -- publishing a different URL than the draft contained,
    silently, on every link carrying a query string.

    quote=False left the quote characters alone, so those are exactly what is
    still outstanding here.
    """
    return value.replace('"', "&quot;").replace("'", "&#x27;")


def _check_raw_html(text: str, ctx: _Ctx, where: str) -> None:
    """Refuse any tag outside the allowlist, naming it and where it appeared."""
    for m in _ANY_TAG.finditer(text):
        tag = m.group(1).lower()
        if tag == "img":
            continue  # rewritten and validated by _render_img, not passed through
        if not re.fullmatch(_RAW_INLINE, tag, re.I):
            ctx.fail(
                f"{where}: <{tag}> is not supported by the Teams compose box "
                f"(see capabilities.json). Supported raw tags: u, strong, b, i, em, s, "
                f"strike, code, br, span."
            )
    for m in _SPAN_OPEN.finditer(text):
        for prop, _value in _STYLE_DECL.findall(m.group(2)):
            if prop.lower() not in {"color", "background-color"}:
                ctx.fail(
                    f"{where}: span style property '{prop}' is stripped by Teams; only "
                    f"color and background-color survive."
                )


def _inline(text: str, ctx: _Ctx, where: str) -> str:
    _check_raw_html(text, ctx, where)

    # Protect literal code spans and allowlisted raw HTML from the emphasis passes,
    # so `**not bold**` inside backticks stays literal.
    stash: list[str] = []

    def _protect(rendered: str) -> str:
        stash.append(rendered)
        return _SENTINEL.format(len(stash) - 1)

    text = _CODE_SPAN.sub(lambda m: _protect(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)

    # After code spans (an <img> shown inside backticks is documentation, not an
    # image) and before the emphasis passes, so a base64 payload containing * or _
    # cannot be mangled into markup.
    text = _IMG_TAG.sub(lambda m: _protect(_render_img(m.group(0), ctx, where)), text)

    # ![alt](src) is refused rather than rendered, so there is exactly ONE way to
    # write an image. It used to fall through to the link pattern and emit a literal
    # "!" plus an <a href> to a path the recipient cannot open -- exit 0, on the
    # clipboard, discovered only after pasting.
    for m in _MD_IMAGE.finditer(text):
        ctx.fail(
            f"{where}: Markdown image syntax ![{m.group(1)}](...) is not supported. "
            f"Read the image with your own tools, base64-encode it, and write "
            f'<img src="data:image/png;base64,..." alt="..."> instead -- or bind the '
            f"file on the command line (--image shot=<path>) and write "
            f'<img src="name:shot" alt="...">. A path written in the draft is never '
            f"read: this converter opens an image only when argv named it."
        )

    text = _RAW_TAG.sub(lambda m: _protect(m.group(0)), text)

    text = html.escape(text, quote=False)

    text = _LINK.sub(lambda m: f'<a href="{_attr_value(m.group(2))}">{m.group(1)}</a>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    text = _UNDERLINE.sub(r"<u>\1</u>", text)
    text = _HIGHLIGHT.sub(rf'<span style="{HIGHLIGHT_STYLE}">\1</span>', text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _ITALIC_US.sub(r"<i>\1</i>", text)

    for idx, value in enumerate(stash):
        text = text.replace(_SENTINEL.format(idx), value)
    return text


# --- blocks ------------------------------------------------------------------

_FENCE = re.compile(r"^\s*(```+|~~~+)\s*([A-Za-z0-9_+-]*)\s*$")
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*)$")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

# Block constructs decidable from ONE line. A table is deliberately absent -- see
# _starts_a_block, which is the thing to use rather than this tuple.
#
# Every pattern here tolerates leading whitespace. _HEADING did not until 2026-07-30,
# which made "  ## Sub" render as the literal text "## Sub" at exit 0 while "  > x" and
# "  ---" worked -- an asymmetry nobody chose, reading as an oversight in the pattern
# rather than a decision. Fixed by operator call after it was found and reproduced.
#
# Safe against false positives because a heading requires # followed by whitespace, so
# a wrapped line like "#123 was fixed" is still text. The visible consequence is that
# an indented heading directly under a list now ENDS the list, exactly as an indented
# quote or rule already did.
_BLOCK_STARTERS = (_FENCE, _HEADING, _HR, _QUOTE)


def _starts_a_block(line: str, next_line: str | None) -> bool:
    """Does this line begin a block of its own, rather than continue the one above?

    Used by BOTH the list classifier and the paragraph accumulator, because the two
    disagreeing about where a block ends is exactly how a line lands in the wrong
    one. They were separate copies until 2026-07-29, and the copies had already
    drifted: neither knew about tables.

    `next_line` exists for that one construct. A GFM table is only a table when the
    line after its header is a separator row, so unlike every other starter it
    cannot be recognised from a single line. Pass None at end of input.

    Safe to call from the paragraph accumulator's first iteration: render() checks
    this same set of constructs before falling through to a paragraph, so the
    predicate cannot be true for the line that opened the paragraph, and the loop
    cannot consume zero lines.
    """
    if any(pattern.match(line) for pattern in _BLOCK_STARTERS):
        return True
    return "|" in line and next_line is not None and bool(_TABLE_SEP.match(next_line))


class _ListLine(Enum):
    """What a line inside a list block is. See _classify_list_line."""

    ITEM = "item"
    CONTINUATION = "continuation"
    END = "end"


def _classify_list_line(line: str, next_line: str | None) -> _ListLine:
    """Decide what a non-first line inside a list block is. Read the ORDER.

    This function is the whole of the wrapped-item fix, and the order of its tests
    is the substance of it -- get the order wrong and either nesting breaks or a
    heading gets swallowed into an <li>. Four cases, not the obvious three:

    1. BLANK ends the list, as it always has.
    2. A LIST MARKER makes the line an item -- and this is tested BEFORE the
       continuation fallback, which is what keeps a nested item nested. Whether it
       is a sibling or a child is decided later, by indent, in _render_list; this
       function deliberately does not care.
    3. ANY OTHER BLOCK STARTER ends the list too. This case is easy to leave out,
       because the three-way split (nested / sibling / continuation) reads complete.
       It is not: without it, a heading, rule, quote, fence or TABLE written directly
       under a list item is swallowed into that item, trading one silent degradation
       for five. _starts_a_block is the same predicate the paragraph accumulator
       stops on, so a line cannot be a continuation here and a new block below.
    4. Everything else is a CONTINUATION -- the wrapped remainder of the item above.

    Note what is NOT consulted: indentation. A human wrapping a line in an editor
    may or may not indent the second line, and both spellings mean the same thing.
    The presence of a marker is what distinguishes an item from prose, never the
    leading whitespace.
    """
    if not line.strip():
        return _ListLine.END
    if _ITEM.match(line):
        return _ListLine.ITEM
    if _starts_a_block(line, next_line):
        return _ListLine.END
    return _ListLine.CONTINUATION


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _render_list(items: list[tuple[int, str, str]], ctx: _Ctx, start: int = 0, indent: int | None = None) -> tuple[str, int]:
    """Build one list level; recurse for deeper indents. Returns (html, next_index)."""
    if indent is None:
        indent = items[start][0]
    ordered = items[start][1] == "ol"
    out = ["<ol>" if ordered else "<ul>"]
    i = start
    while i < len(items):
        level, kind, text = items[i]
        if level < indent or (level == indent and (kind == "ol") != ordered):
            break
        if level > indent:
            nested, i = _render_list(items, ctx, i, level)
            out[-1] = out[-1][:-5] + nested + "</li>"  # graft into the open <li>
            continue
        out.append(f"<li>{_inline(text, ctx, 'list item')}</li>")
        i += 1
    out.append("</ol>" if ordered else "</ul>")
    return "".join(out), i


def render(source: str, *, spacers: bool = True, images: dict[str, bytes] | None = None) -> str:
    """Render the markdown subset. Raises RenderError listing every problem found."""
    return render_with_images(source, spacers=spacers, images=images)[0]


def render_with_images(
    source: str, *, spacers: bool = True, images: dict[str, bytes] | None = None
) -> tuple[str, list[ImageInfo]]:
    """render(), plus what was embedded -- so the CLI can echo it.

    The copy path must be able to say WHICH images it put on the clipboard. A
    draft names an image by filename; what gets published is bytes. Echoing the
    resolved mime, dimensions, size and hash is what makes "the operator did not
    know an image was in there" impossible. It cannot tell anyone what the picture
    depicts -- that gap stays open, and is documented as such.

    `images` maps a name bound by `--image NAME=PATH` to the bytes md2teams.py read
    at the invocation boundary. Passing bytes rather than paths is what keeps this
    module unable to open a file even by accident.
    """
    ctx = _Ctx(bindings=dict(images or {}))
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0] * 3
            i += 1
            body: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence
            # Real newlines and spaces on purpose: CKEditor converts them to Teams'
            # own <br> + &nbsp; shape on paste. Verified 2026-07-28.
            blocks.append(f"<pre><code>{html.escape(chr(10).join(body), quote=False)}</code></pre>")
            continue

        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if level > 3:
                ctx.fail(
                    f"line {i + 1}: h{level} is refused -- Teams silently downgrades h4-h6 to h3, "
                    f"keeping the text but losing the level (verified 2026-07-29). "
                    f"Use ### or bold text instead."
                )
            blocks.append(f"<h{level}>{_inline(heading.group(2), ctx, f'line {i + 1}')}</h{level}>")
            i += 1
            continue

        if _HR.match(line):
            blocks.append("<hr>")
            i += 1
            continue

        if _QUOTE.match(line):
            quoted = []
            while i < len(lines) and _QUOTE.match(lines[i]):
                quoted.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            blocks.append(f"<blockquote>{_inline(' '.join(quoted).strip(), ctx, 'blockquote')}</blockquote>")
            continue

        if _ITEM.match(line):
            # Each item collects its LINES; they are joined before anything is
            # rendered. Rendering per line is what leaked "**" as literal asterisks
            # when emphasis opened on one line and closed on the next -- the inline
            # passes have to see the whole item text at once, exactly as the
            # paragraph path already gives them the whole paragraph.
            parts: list[tuple[int, str, list[str]]] = []
            while i < len(lines):
                kind = _classify_list_line(lines[i], lines[i + 1] if i + 1 < len(lines) else None)
                if kind is _ListLine.END:
                    break
                if kind is _ListLine.ITEM:
                    m = _ITEM.match(lines[i])
                    marker = "ol" if m.group(2)[0].isdigit() else "ul"
                    parts.append((len(m.group(1)), marker, [m.group(3)]))
                else:
                    # Safe by construction: this branch is only reachable after at
                    # least one ITEM, because the block loop entered here on one.
                    parts[-1][2].append(lines[i].strip())
                i += 1
            # <br> as the join, not a space: render.py already means <br> by "a
            # newline inside one block", and this is that same rule applied to a
            # list item. It survives _inline because <br> is in the raw allowlist.
            items = [(level, marker, "<br>".join(text)) for level, marker, text in parts]
            rendered, _ = _render_list(items, ctx)
            blocks.append(rendered)
            continue

        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            header = _split_row(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            # <th> verified on the desktop client 2026-07-29. Teams adds its own
            # <thead> around the header row on paste, so emit <tbody> only and let it
            # normalise -- same bargain as the code-block shape.
            cells = "".join(f"<th>{_inline(c, ctx, 'table header')}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{_inline(c, ctx, 'table cell')}</td>" for c in r) + "</tr>" for r in rows
            )
            blocks.append(f"<table><tbody><tr>{cells}</tr>{body}</tbody></table>")
            continue

        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not (
            _starts_a_block(lines[i], lines[i + 1] if i + 1 < len(lines) else None)
            or _ITEM.match(lines[i])
        ):
            para.append(lines[i].strip())
            i += 1
        blocks.append(f"<p>{_inline('<br>'.join(para), ctx, f'line {i}')}</p>")

    # A bound image the draft never uses is refused, not ignored. The failure this
    # tool exists to prevent is "the message went out missing something and nobody
    # noticed" -- and an operator who typed --image chart=... and got a message with
    # no chart in it is exactly that failure, arriving silently at exit 0.
    for name in sorted(set(ctx.bindings) - ctx.used):
        ctx.fail(
            f"--image {name}=... was bound but the draft never references it. Add "
            f'<img src="name:{name}" alt="..."> where the image belongs, or drop the '
            f"binding. A bound-but-unused image would be a silently missing image."
        )

    if ctx.problems:
        raise RenderError(ctx.problems)

    if spacers:
        # Teams renders consecutive <p> tightly; its own rich messages use an empty
        # paragraph as a visual spacer. This is what makes a long message readable.
        spaced: list[str] = []
        for n, block in enumerate(blocks):
            if n:
                spaced.append("<p>&nbsp;</p>")
            spaced.append(block)
        blocks = spaced

    return "".join(blocks), ctx.images
