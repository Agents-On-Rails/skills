#!/usr/bin/env python3
"""Turn a Markdown draft into a Teams-ready clipboard payload.

    python md2teams.py draft.md              # render, validate, copy -- ready to paste
    python md2teams.py draft.md --print      # show the HTML, do not touch the clipboard
    python md2teams.py draft.md --image shot=C:\\work\\shot.png    # bind an image file
    python md2teams.py --capabilities        # print the verified formatting vocabulary

Exit codes:
    0  clipboard set (or --print/--capabilities succeeded)
    1  the draft uses something Teams cannot render -- nothing was copied
    2  usage error

Refusing rather than degrading is deliberate. A silently downgraded message is only
discovered after it has been pasted into a real conversation; a non-zero exit with the
offending construct named is discovered immediately, and an agent can correct itself.

THIS FILE IS THE ONLY PLACE AN IMAGE FILE IS OPENED, and it opens only paths given on
argv. The draft refers to a bound image by NAME (<img src="name:shot">); the name is a
dict key, never joined onto a directory, so a path written inside the draft cannot
select a file -- not "is rejected", cannot. See teamsfmt/images.py for why that
matters.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from teamsfmt.console import force_utf8_output  # noqa: E402
from teamsfmt.images import IMAGE_NAME, VERIFIED_IMAGE_BYTES  # noqa: E402
from teamsfmt.plaintext import plain_text_fallback  # noqa: E402
from teamsfmt.render import RenderError, render_with_images  # noqa: E402
from teamsfmt.validate import ValidationError, load_manifest, validate_html  # noqa: E402
from teamsfmt.winclip import (  # noqa: E402
    CF_UNICODETEXT,
    ClipboardError,
    html_format_id,
    set_clipboard,
    unwrap_cf_html,
    wrap_cf_html,
)


def _capabilities_text() -> str:
    m = load_manifest()
    lines = [
        f"Teams compose-box formatting, verified {m['verified_on']} ({m['verified_client']}).",
        "",
        "Markdown you can use:",
        "  # H1   ## H2   ### H3          headings (h1-h3; h4-h6 refused -- Teams silently downgrades them to h3)",
        "  **bold**  *italic*  ~~strike~~  ++underline++  ==highlight==",
        "  `inline code`                   ```fenced code blocks```",
        "  - bullet      1. numbered      indent 2 spaces to nest (depth 3 verified, ul and ol)",
        "     A long line may wrap: a following line with no list marker JOINS the one above",
        "     and renders as a line break, so **emphasis** may span the wrap. A blank line, a",
        "     new marker, or any other block (heading, ---, >, fence, table) ends the list.",
        "  > blockquote                    ---  horizontal rule",
        "  [text](https://url)             | GFM | tables |",
        "",
        "Raw HTML you may also write inline:",
        "  <u> <strong> <b> <i> <em> <s> <strike> <code> <br>",
        '  <span style="color: red"> and <span style="background-color: yellow">',
        "",
        "  <img src=\"data:image/png;base64,...\" alt=\"...\">   inline image, base64 you supply",
        "  <img src=\"name:shot\" alt=\"...\">                  inline image, file bound on argv:",
        "     ... --image shot=C:\\work\\shot.png            (repeatable; name is [A-Za-z0-9_-]{1,64})",
        "     A path written in the DRAFT is never read -- only paths given on the command",
        "     line, and that path must be LOCAL: a UNC path is refused before it is opened,",
        "     because opening one offers this machine's credentials to that host.",
        "     PNG/JPEG, metadata stripped, NO size cap. An http(s) src makes Teams reject",
        "     the ENTIRE paste. Past ~8.8 MB of images you get a NOTE, never a refusal:",
        "     that is the largest payload ever verified end to end, not a limit.",
        "",
        "Refused (the converter exits non-zero and names it):",
        "  <div>, any other raw tag, h4-h6, ![alt](src) markdown image syntax,",
        "  <img> with an http(s) src, any span style beyond colour/background,",
        "  a name: with no --image binding, and an --image binding the draft never uses.",
        "",
        "Known limits:",
    ]
    for key, value in m["gotchas"].items():
        lines.append(f"  - {key}: {value}")
    lines.append("")
    lines.append("Not yet verified (avoid or test first): " + "; ".join(m["untested"]))
    return "\n".join(lines)


class DraftError(ValueError):
    """The draft could not be read at all -- before formatting is even considered."""


class BindingError(ValueError):
    """An --image binding is unusable. Carries every problem found, like RenderError."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _is_unc(path: Path) -> bool:
    """True when the path names another host rather than this machine.

    This runs BEFORE stat(), and the ordering is the whole control: on Windows the
    stat() call is itself what opens the SMB connection and offers this machine's
    NTLM credentials to whoever answers. An exception telling us the host was
    unreachable would arrive after the handshake, which is far too late to be a
    safeguard.

    The draft grammar already makes a path unexpressible, so the draft cannot reach
    here. The command line can -- and a drafting agent composes the command line too,
    from content it may have ingested from anywhere. Argv being gated and displayed
    by the host is a real difference, but it is a procedural one, and a credential
    exfiltration primitive is worth closing structurally instead.

    PureWindowsPath().drive is the discriminator rather than a leading-backslash
    test: it normalises both slash directions, and it tells an extended-length LOCAL
    path (\\\\?\\C:) apart from an extended-length UNC one (\\\\?\\UNC\\host\\share),
    which a string prefix check cannot.
    """
    drive = PureWindowsPath(path).drive
    if not drive.startswith(("\\\\", "//")):
        return False
    # \\?\C: and \\.\C: are local devices in UNC-looking clothing.
    return re.fullmatch(r"[\\/]{2}[?.][\\/][A-Za-z]:", drive) is None


def _bind_images(specs: list[str]) -> dict[str, bytes]:
    """Read every `--image NAME=PATH` file. The only file-opening code in the tool.

    Every problem is collected rather than raised at the first one, so an agent
    fixing a command line sees all of them at once -- the same bargain the renderer
    makes with draft problems.
    """
    problems: list[str] = []
    bound: dict[str, bytes] = {}

    for spec in specs:
        name, sep, raw_path = spec.partition("=")
        if not sep:
            problems.append(
                f"--image {spec!r} is malformed: expected --image NAME=PATH, "
                f'e.g. --image shot=C:\\work\\shot.png, referenced as <img src="name:shot">.'
            )
            continue
        if not IMAGE_NAME.match(name):
            problems.append(
                f"--image name {name!r} is not usable: names are 1-64 characters of "
                f"letters, digits, underscore and hyphen. The NAME is a label the draft "
                f"refers to, not a path."
            )
            continue
        if name in bound:
            problems.append(
                f"--image {name}=... was given more than once. A name binds exactly one "
                f"file; which one won would decide silently what gets published."
            )
            continue
        if not raw_path:
            problems.append(f"--image {name}= has no path after the '='.")
            continue

        path = Path(raw_path)
        if _is_unc(path):
            problems.append(
                f"--image {name}=... names a network location. This tool reads local "
                f"files only: opening a UNC path makes Windows connect to that host and "
                f"offer your credentials to whoever answers, so the path is refused "
                f"before it is touched. Copy the image to a local folder and bind that."
            )
            continue
        try:
            # There used to be a stat()-before-read size guard here, so a mistyped
            # path pointing at something enormous was refused rather than loaded.
            # It went with the cap on 2026-07-30, deliberately: it shared the cap's
            # constant, so leaving it would have kept refusing --image files over
            # 2 MB and made the removal inert on the argv path -- the one path the
            # flag exists for. The residual hazard is a typo costing memory, which
            # is local to this process and loud when it happens. If it ever does,
            # the guard comes back sized to a mistyped PATH, not to an image.
            bound[name] = path.read_bytes()
        except OSError as exc:
            problems.append(f"--image {name}={path} could not be read ({type(exc).__name__}).")

    if problems:
        raise BindingError(problems)
    return bound


def _read_draft(source: Path | None) -> str:
    """Read the draft as UTF-8 -- from a file, or from piped stdin.

    stdin is read as BYTES and decoded here rather than through sys.stdin's own
    decoder, because that decoder is also built from the locale: cp1252 with
    errors='surrogateescape'. A piped UTF-8 draft therefore arrived mojibaked,
    and -- unlike the --print crash -- did so SILENTLY. Exit 0, a corrupted
    message on the clipboard, discovered only once it had been pasted into a
    real conversation. That is the exact outcome this tool exists to prevent, so
    the input side gets the same bargain as the formatting side: if we cannot
    read the draft faithfully, we refuse it rather than degrade it.

    Decoding strictly here is also what keeps force_utf8_output's error handler
    (teamsfmt/console.py) unreachable: surrogateescape is the only way a lone
    surrogate could enter the text, and nothing downstream (wrap_cf_html, the
    utf-16-le plain fallback) can encode one.
    """
    if source is not None:
        raw = source.read_bytes()
        where = str(source)
    elif hasattr(sys.stdin, "buffer"):
        raw = sys.stdin.buffer.read()
        where = "piped stdin"
    else:  # a caller replaced stdin with a text object; trust what it decoded
        return sys.stdin.read()

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DraftError(
            f"{where} is not valid UTF-8 -- byte {exc.start} ({exc.reason}). "
            "Save the draft as UTF-8 and run again."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", type=Path, help="Markdown file (omit to read stdin)")
    ap.add_argument("--print", dest="show", action="store_true", help="print the HTML instead of copying")
    ap.add_argument("--capabilities", action="store_true", help="print the verified formatting vocabulary")
    ap.add_argument("--tight", action="store_true", help="omit the blank-line spacers between blocks")
    ap.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help='bind an image file to a name the draft uses as <img src="name:NAME"> (repeatable)',
    )
    args = ap.parse_args(argv)

    if args.capabilities:
        print(_capabilities_text())
        return 0

    if args.source is None and sys.stdin.isatty():
        ap.error("need a Markdown file, or piped stdin")

    try:
        text = _read_draft(args.source)
    except (DraftError, OSError) as exc:
        print(f"REFUSED -- the draft could not be read:\n  - {exc}", file=sys.stderr)
        return 1

    try:
        bindings = _bind_images(args.image)
    except BindingError as exc:
        print("REFUSED -- an --image binding is unusable:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    try:
        fragment, images = render_with_images(text, spacers=not args.tight, images=bindings)
    except RenderError as exc:
        print("REFUSED -- the draft uses formatting Teams cannot render:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nRun with --capabilities to see the supported vocabulary.", file=sys.stderr)
        return 1

    try:
        validate_html(fragment)
    except ValidationError as exc:
        # Reaching here means the renderer produced something the evidence does not
        # cover -- a bug in this tool, not in the draft. Say so plainly.
        print("REFUSED -- generated HTML failed manifest validation (this is a bug):", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.show:
        # Image details go to stderr so stdout stays pure HTML for redirection.
        for n, info in enumerate(images, 1):
            print(f"  image {n}: {info.summary()}", file=sys.stderr)
        _note_if_past_the_evidence(images)
        print(fragment)
        return 0

    _note_if_past_the_evidence(images)

    payload = wrap_cf_html(fragment)
    plain = plain_text_fallback(fragment)
    try:
        set_clipboard({html_format_id(): payload, CF_UNICODETEXT: plain.encode("utf-16-le") + b"\x00\x00"})
    except (ClipboardError, MemoryError, OSError) as exc:
        # Reachable in practice only for large payloads, which is why this arrived
        # with the removal of the size cap. It must not surface as a traceback: by
        # the time an allocation fails, EmptyClipboard() has already run, so the
        # operator's clipboard is GONE and they need to be told that in words.
        print(
            f"REFUSED -- could not write the clipboard ({type(exc).__name__}). "
            "The clipboard was cleared before the write was attempted, so whatever "
            "you had copied is gone. Nothing was pasted anywhere. If the draft "
            "carries large images, that is the first thing to suspect.",
            file=sys.stderr,
        )
        return 1

    # Verify in the SAME process that set it. The clipboard is ambient machine-wide
    # state; a check performed in a later process proves nothing about our write.
    # NOTE: this unwraps the payload we built, not a read-back of the clipboard, so
    # it proves the CF_HTML framing is self-consistent -- NOT that the write landed
    # intact. A truncated write would still pass here.
    check = unwrap_cf_html(payload)
    if not check["offsets_consistent"] or check["fragment"] != fragment:
        print("REFUSED -- CF_HTML payload failed self-check; clipboard may be unreliable", file=sys.stderr)
        return 1

    # Echo every embedded image. A draft names an image by filename; what goes on
    # the clipboard is bytes. Without this, "there was an image in there and I did
    # not realise" is possible; with it, it is not. It still cannot tell anyone
    # what the picture SHOWS -- that judgement stays human.
    for n, info in enumerate(images, 1):
        print(f"  image {n}: {info.summary()}")

    print(f"Copied. Paste into Teams with Ctrl+V. ({len(fragment)} chars HTML, {len(payload)} bytes CF_HTML)")
    return 0


def _note_if_past_the_evidence(images: list) -> None:
    """Say so when a draft's images go past what has actually been proven to work.

    This is a NOTE, never a refusal. It exists because of what replaced the size
    cap: the operator's policy is to fit limits to observed problems rather than
    guess them, and that policy needs something capable of producing the
    observation. Nothing else here can. Image quality is size-invariant on this
    surface -- Teams re-encodes to roughly 800 px whatever you send -- so a size
    problem reaches a human as "Teams is slow" or "the paste was flaky", and the
    sending client provably cannot see what a recipient got -- copying a sent
    message from the sending client can return that client's own session-local
    blob: reference, which establishes nothing about delivery. So the one honest
    signal available is: you have left the evidenced band, and if this message
    misbehaves for someone else, start here.
    """
    if not images:
        return
    total = sum(info.emitted_bytes for info in images)
    if total <= VERIFIED_IMAGE_BYTES:
        return
    print(
        f"NOTE -- {len(images)} image(s), {total} bytes total, past the largest payload "
        f"ever verified end to end ({VERIFIED_IMAGE_BYTES} bytes). Not a limit and not a "
        "refusal: Teams has never been observed rejecting this, but it has never been "
        "observed accepting it either. If a recipient reports a broken or missing image, "
        "suspect this first -- and note that your own client cannot show you their view.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
