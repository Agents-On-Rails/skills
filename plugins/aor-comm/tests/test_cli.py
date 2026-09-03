"""CLI-level behaviour of md2teams.py, exercised as a real subprocess.

These run the CLI the way a host agent runs it -- `python md2teams.py ...`, with
whatever encodings the interpreter picks at startup -- because that is where the
failures these cover actually live. Importing main() and calling it in-process
would not reproduce them: the bugs are in the encoding of the stdin/stdout
streams the interpreter builds from the locale, not in any logic a test could
reach directly.

Note "locale", not "console": outside UTF-8 mode a redirected pipe gets the same
cp1252 treatment an interactive terminal does, and redirected is how every host
agent actually invokes this tool. Nothing here drives a real interactive console.
"""

from __future__ import annotations

import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "aor-format-teams-message"
MD2TEAMS = SKILL / "md2teams.py"

# Everything here drives the CLI as a subprocess. The one thing imported in-process
# is VERIFIED_IMAGE_BYTES, because a test that hardcoded that threshold would still
# pass after it moved -- and would then be testing nothing.
sys.path.insert(0, str(SKILL))


def _incompressible_png(min_bytes: int) -> bytes:
    """A real PNG larger than min_bytes, from a SEEDED PRNG so sizes reproduce.

    Random pixels on purpose: a flat image compresses to nothing, so "a big image"
    built the obvious way is a small file. Zero-padding a small PNG does not work
    either -- the stripper drops the padding as unknown chunks.
    """

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rng = random.Random(20260730)
    side = 512
    while True:
        raw = b"".join(b"\x00" + rng.randbytes(3 * side) for _ in range(side))
        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )
        if len(png) > min_bytes:
            return png
        side *= 2

# Characters SKILL.md advertises as passing through exactly. NOT representable in
# cp1252 -- which is the locale encoding on the Windows box this skill targets.
NON_CP1252 = "⚠→⇒"  # warning sign, right arrow, double right arrow
CP1252_SAFE = "—æøå"  # em dash, Danish ae/oe/aa
# Undefined in cp1252, so decoding it as cp1252 yields a lone surrogate under
# surrogateescape -- which nothing downstream can encode.
INVALID_UTF8 = b"# H\n\nA\x81B\n"


def _run(*args: str, stdin: bytes | None = None, encoding: str = "cp1252") -> subprocess.CompletedProcess:
    """Run the CLI in a fresh process with the locale encoding pinned.

    PYTHONIOENCODING pins stdin/stdout/stderr; PYTHONUTF8=0 is needed as well,
    because if the parent was started with -X utf8 the child inherits UTF-8 mode
    and masks the very thing under test. Together they make these tests
    deterministic and machine-independent rather than dependent on this box's
    locale -- the defect reproduces on a clean env here, but pinning means the
    tests stay valid if UTF-8 mode ever becomes the interpreter default.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = encoding
    env["PYTHONUTF8"] = "0"
    return subprocess.run(
        [sys.executable, str(MD2TEAMS), *args],
        input=stdin,
        capture_output=True,
        env=env,
    )


class _DraftCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.draft = Path(self.tmp.name) / "draft.md"

    def _write(self, body: str) -> Path:
        self.draft.write_text(body, encoding="utf-8")
        return self.draft

    def _png(self, name: str = "shot.png", **kwargs) -> Path:
        """A real PNG on disk. The fixture is shared with test_images on purpose:
        the file route and the inline route must be fed identical bytes."""
        from test_images import make_png  # noqa: PLC0415

        path = Path(self.tmp.name) / name
        path.write_bytes(make_png(**kwargs))
        return path


class TestOutputEncoding(_DraftCase):
    """--print must not crash on the vocabulary the skill promises.

    SKILL.md tells a drafting agent that emoji and arrows "pass through exactly",
    and step 3 recommends --print for showing the user what they will get. Under
    a cp1252 locale those two statements collided: the preview died with a
    UnicodeEncodeError on exactly the characters the contract advertises.

    Worse than the crash is how it reads. A non-zero exit from this tool means
    "your draft was refused" -- so a host agent that hit this was told, in the
    tool's own exit-code vocabulary, that its perfectly valid draft was invalid.
    Encoding failures must not be able to impersonate refusals.
    """

    def test_print_emits_non_cp1252_characters(self) -> None:
        self._write(f"# Heading\n\n> {NON_CP1252} advarsel\n")
        proc = _run(str(self.draft), "--print")
        self.assertEqual(
            proc.returncode,
            0,
            f"--print exited {proc.returncode}; stderr={proc.stderr.decode('utf-8', 'replace')}",
        )
        self.assertIn(NON_CP1252, proc.stdout.decode("utf-8"))

    def test_encoding_failure_is_not_reported_as_a_refusal(self) -> None:
        """The exit code must stay in its own lane: 1 means 'draft refused'."""
        self._write(f"# Heading\n\n{NON_CP1252}\n")
        proc = _run(str(self.draft), "--print")
        self.assertNotIn(b"REFUSED", proc.stderr)
        self.assertNotIn(b"UnicodeEncodeError", proc.stderr)

    def test_cp1252_safe_characters_are_emitted_as_utf8(self) -> None:
        """Danish and the em dash were never part of the bug -- but the fix
        changes the stream they are written to, so pin that it made them UTF-8
        rather than leaving them cp1252. These are what this operator writes.
        """
        self._write(f"# Overskrift\n\n{CP1252_SAFE}\n")
        proc = _run(str(self.draft), "--print")
        self.assertEqual(proc.returncode, 0)
        self.assertIn(CP1252_SAFE, proc.stdout.decode("utf-8"))

    def test_capabilities_survives_the_locale_encoding(self) -> None:
        """A forward guard, and honestly not more than that.

        _capabilities_text() is ASCII today, so this cannot fail today. It exists
        because the text is built from capabilities.json, and a manifest edit that
        adds an arrow to a gotcha string would make it live without anyone
        thinking about encodings.
        """
        proc = _run("--capabilities")
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Teams compose-box formatting", proc.stdout)


class TestRefusalMessagesStayAscii(_DraftCase):
    """Refusal messages name constructs; they never echo draft content.

    That is what makes them structurally unable to crash while reporting a
    problem -- render.py interpolates only regex capture groups (tag names,
    style properties, both constrained to ASCII by their patterns) plus a line
    number. This pins the invariant rather than asserting the absence of a crash,
    because a test for the crash could never fail while the invariant holds.

    If someone later wants a refusal to quote the offending text, this test is
    the conversation: it will go red, and the encoding question has to be
    answered deliberately rather than discovered in a traceback.
    """

    def test_refusal_naming_a_tag_is_ascii_even_when_the_draft_is_not(self) -> None:
        self._write(f"# Heading\n\n<div>{NON_CP1252}</div>\n")
        proc = _run(str(self.draft), "--print")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"REFUSED", proc.stderr)
        self.assertIn(b"<div>", proc.stderr)
        self.assertTrue(
            proc.stderr.decode("utf-8").isascii(),
            f"refusal message carried non-ASCII: {proc.stderr!r}",
        )

    def test_refusal_naming_a_bad_image_name_does_not_echo_the_draft(self) -> None:
        """Added with --image (2026-07-29), because the first cut of it broke this.

        `<img src="name:X">` was the first construct whose refusal was tempted to
        quote the offending string back -- X is short, and quoting it reads as more
        helpful. It also puts arbitrary draft bytes into the agent's stderr, and the
        two existing tests here could not see it: they exercise <div> and <span>, so
        the img path was outside the only guard on the invariant. Locate with
        `where`, name the rule, quote nothing.
        """
        self._write(f'# Heading\n\n<img src="name:{NON_CP1252}" alt="x">\n')
        proc = _run(str(self.draft), "--print")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"does not name an image", proc.stderr)
        self.assertTrue(
            proc.stderr.decode("utf-8").isascii(),
            f"refusal message carried draft content: {proc.stderr!r}",
        )

    def test_refusal_naming_a_style_property_is_ascii(self) -> None:
        self._write(f'# Heading\n\n<span style="b{NON_CP1252}rder: 1px">x</span>\n')
        proc = _run(str(self.draft), "--print")
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(
            proc.stderr.decode("utf-8").isascii(),
            f"refusal message carried non-ASCII: {proc.stderr!r}",
        )


class TestStdinIsDecodedAsUtf8(_DraftCase):
    """The input half of the same bug -- and the dangerous half.

    sys.stdin's decoder is built from the locale too (cp1252 here, with
    errors='surrogateescape'), so a piped UTF-8 draft used to arrive mojibaked.
    Unlike the --print crash this was SILENT: exit 0, corrupted text placed on
    the clipboard, discovered only after it had been pasted into a real
    conversation. That is precisely the outcome "refuse, don't degrade" exists to
    prevent, and the stdin path is advertised by the CLI's own --help.
    """

    def test_piped_utf8_is_not_mojibaked(self) -> None:
        """Guards the stdin decode alone -- and is blind if the stdout bug returns WITH it.

        Verified by reverting each fix separately: this goes red when only the
        stdin fix is removed, but GREEN when both are removed, because the two
        defects are byte-inverse under surrogateescape -- decoding UTF-8 bytes as
        cp1252 in and encoding them back as cp1252 out reproduces the input
        exactly. (Total across all 256 bytes under surrogateescape; NOT total
        under cp1252 strict, which fails on 0x81/0x8d/0x8f/0x90/0x9d. The error
        handler is the cause, not the charset being single-byte.) The corruption
        is real in memory, and reaches the clipboard, which encodes UTF-8 -- but
        it is invisible through this one channel.

        test_stdin_and_file_argument_agree covers that combination, though not by
        the route the name suggests: under a both-reverted build it fails on the
        FILE run's exit code, because that path decodes correctly and then dies
        encoding the result. Keep both.
        """
        body = f"# Heading\n\n> {NON_CP1252} advarsel {CP1252_SAFE}\n"
        proc = _run("--print", stdin=body.encode("utf-8"))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8")
        self.assertIn(NON_CP1252, out)
        self.assertIn(CP1252_SAFE, out)
        self.assertNotIn("â", out)  # the signature of a UTF-8-as-cp1252 misread

    def test_stdin_and_file_argument_agree(self) -> None:
        """The invariant that matters: same draft, same HTML, either way in.

        --print emits the same fragment object the clipboard path wraps, so if
        these two ever disagree, one of them is what gets pasted.

        This is also the only test that survives BOTH fixes being reverted at
        once -- but read the failure before trusting the reason. It fails on the
        file run's exit code, not on a stdout comparison: the file path decodes
        correctly and then cannot encode the result to cp1252, so there is never
        a second output to compare against. Right backstop, non-obvious route.
        """
        body = f"# Heading\n\n> {NON_CP1252} advarsel {CP1252_SAFE}\n\n- punkt\n"
        path = self._write(body)
        from_file = _run(str(path), "--print")
        from_stdin = _run("--print", stdin=body.encode("utf-8"))
        self.assertEqual(from_file.returncode, 0)
        self.assertEqual(from_stdin.returncode, 0)
        self.assertEqual(from_file.stdout, from_stdin.stdout)

    def test_undecodable_stdin_is_refused_not_mangled(self) -> None:
        proc = _run("--print", stdin=INVALID_UTF8)
        self.assertEqual(proc.returncode, 1, proc.stdout.decode("utf-8", "replace"))
        self.assertIn(b"REFUSED", proc.stderr)
        self.assertIn(b"not valid UTF-8", proc.stderr)
        self.assertEqual(proc.stdout, b"", "nothing should be emitted for an unreadable draft")

    def test_undecodable_file_is_refused_with_its_path(self) -> None:
        self.draft.write_bytes(INVALID_UTF8)
        proc = _run(str(self.draft), "--print")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"not valid UTF-8", proc.stderr)
        self.assertIn(self.draft.name.encode(), proc.stderr)

    def test_a_missing_file_is_refused_not_a_traceback(self) -> None:
        proc = _run(str(self.draft.parent / "nope.md"), "--print")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"REFUSED", proc.stderr)
        self.assertNotIn(b"Traceback", proc.stderr)


class TestImageBindingIsAnArgvBoundary(_DraftCase):
    """`--image NAME=PATH` -- the ONE place this tool opens an image file.

    These run as a real subprocess because that is what the property is about. The
    unit tests in test_images.py can only show that the renderer resolves a name
    through a dict; only running the CLI shows that a path reaches the file system
    when and only when it was typed on the command line. The pair of tests that
    matter most are the two at the end: a path written INSIDE the draft must not be
    read, whether it is spelled as a name or as Markdown.
    """

    def test_a_bound_image_is_read_and_embedded(self) -> None:
        png = self._png(secret=b"pre-crop thumbnail")
        self._write('Here it is:\n\n<img src="name:shot" alt="A red block">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={png}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8")
        self.assertIn('<img src="data:image/png;base64,', out)
        self.assertNotIn("name:shot", out, "the name must be resolved, not emitted")
        self.assertNotIn("pre-crop thumbnail", out, "metadata must not survive the file route")
        self.assertIn(b"image 1: image/png 4x3", proc.stderr, "the embed must be echoed")

    def test_the_echo_reports_what_was_published_not_what_was_asked_for(self) -> None:
        """The operator reviews a filename and publishes bytes. Pin the echo."""
        png = self._png(width=12, height=7)
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={png}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        for expected in (b"image/png", b"12x7", b"sha256:"):
            self.assertIn(expected, proc.stderr)

    def test_repeated_flags_bind_several_images(self) -> None:
        a, b = self._png("a.png"), self._png("b.png", width=6, height=2)
        self._write('<img src="name:a" alt="a">\n\n<img src="name:b" alt="b">\n')
        proc = _run(str(self.draft), "--print", "--image", f"a={a}", "--image", f"b={b}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn(b"image 2:", proc.stderr)

    def test_a_name_with_no_binding_is_refused(self) -> None:
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"REFUSED", proc.stderr)
        self.assertIn(b"--image shot=", proc.stderr)
        self.assertEqual(proc.stdout, b"", "nothing may be emitted for a refused draft")

    def test_a_binding_the_draft_never_uses_is_refused(self) -> None:
        png = self._png()
        self._write("Just text, no image.\n")
        proc = _run(str(self.draft), "--print", "--image", f"shot={png}")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"never references it", proc.stderr)
        self.assertEqual(proc.stdout, b"")

    def test_a_malformed_argument_is_refused(self) -> None:
        self._write("Text.\n")
        proc = _run(str(self.draft), "--print", "--image", "C:/work/shot.png")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"expected --image NAME=PATH", proc.stderr)

    def test_a_duplicate_name_is_refused_rather_than_one_winning(self) -> None:
        a, b = self._png("a.png"), self._png("b.png")
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={a}", "--image", f"shot={b}")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"more than once", proc.stderr)

    def test_a_name_failing_the_identifier_rule_is_refused_on_the_argv_side(self) -> None:
        png = self._png()
        self._write("Text.\n")
        proc = _run(str(self.draft), "--print", "--image", f"../shot={png}")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"is not usable", proc.stderr)
        self.assertIn(b"not a path", proc.stderr)

    def test_an_unreadable_file_is_refused_and_names_the_path(self) -> None:
        missing = Path(self.tmp.name) / "nope.png"
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={missing}")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"could not be read", proc.stderr)
        self.assertIn(missing.name.encode(), proc.stderr)
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_a_directory_bound_as_an_image_is_refused_not_a_traceback(self) -> None:
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={self.tmp.name}")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"REFUSED", proc.stderr)
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_a_large_bound_file_is_accepted_and_the_note_fires(self) -> None:
        """Replaces the over-the-cap refusal test, deleted with the cap on 2026-07-30.

        Two properties in one run, because they are the same behaviour change: a
        payload past the old cap now SUCCEEDS on the argv path (which had its own
        size guard, so the removal had to reach it), and going past the largest
        end-to-end verified payload produces a NOTE rather than silence. The note
        is the whole reason the removal is safe to ship -- 'observe issues in the
        real world' needs something capable of producing an observation.
        """
        from teamsfmt.images import VERIFIED_IMAGE_BYTES  # noqa: PLC0415

        big = Path(self.tmp.name) / "big.png"
        big.write_bytes(_incompressible_png(VERIFIED_IMAGE_BYTES + 1))
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={big}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn(b"data:image/png;base64,", proc.stdout)
        self.assertIn(b"NOTE", proc.stderr)
        self.assertNotIn(b"REFUSED", proc.stderr)

    def test_a_normal_image_gets_no_note(self) -> None:
        """The note must stay rare, or it becomes noise nobody reads."""
        small = Path(self.tmp.name) / "small.png"
        small.write_bytes(_incompressible_png(1024))
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={small}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"NOTE", proc.stderr)

    def test_a_bound_file_that_is_not_an_image_is_refused_by_name(self) -> None:
        notpng = Path(self.tmp.name) / "creds.png"
        notpng.write_bytes(b"SECRET=hunter2\n")
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", f"shot={notpng}")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"not a PNG or a JPEG", proc.stderr)
        self.assertIn(b"--image shot", proc.stderr, "the refusal must say which binding failed")
        self.assertNotIn(b"hunter2", proc.stdout + proc.stderr, "contents must not be echoed back")

    def test_a_path_written_in_the_draft_is_not_read_even_if_it_exists(self) -> None:
        """The security property, at the boundary where it is actually decided.

        The file exists, is a valid PNG, and is named in the draft in the most
        plausible way an agent would name it. Nothing on argv bound it, so nothing
        opens it -- the name is not a path, and there is no directory to join it to.
        """
        png = self._png()
        self._write(f'<img src="name:{png.name}" alt="x">\n')
        proc = _run(str(self.draft), "--print")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"a path is not a name", proc.stderr)
        self.assertIn(b"--image", proc.stderr, "the refusal must point at the argv boundary")
        self.assertNotIn(png.name.encode(), proc.stderr, "the draft's string is not echoed back")
        self.assertEqual(proc.stdout, b"")

    def test_markdown_image_syntax_with_a_real_path_is_still_refused(self) -> None:
        """The other spelling of the same idea, and the likelier one."""
        png = self._png()
        self._write(f"![shot]({png})\n")
        proc = _run(str(self.draft), "--print", "--image", f"shot={png}")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"Markdown image syntax", proc.stderr)
        self.assertEqual(proc.stdout, b"")

    def test_a_unc_path_on_argv_is_refused_without_touching_it(self) -> None:
        """A network path must be refused BEFORE anything opens it.

        On Windows the stat() call is itself the SMB handshake that offers NTLM
        credentials, so "try it and handle the error" is not a safeguard -- by then
        the credentials have already been offered to whoever answered.

        The host is in .invalid (RFC 2606), so if this guard ever regresses the test
        fails on DNS in milliseconds instead of hanging on an SMB timeout.
        """
        self._write('<img src="name:shot" alt="x">\n')
        proc = _run(str(self.draft), "--print", "--image", r"shot=\\nonexistent.invalid\share\probe.png")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"network location", proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertNotIn(b"nonexistent.invalid", proc.stderr, "the refusal must not echo the path back")

    def test_unc_detection_covers_the_spellings_that_matter(self) -> None:
        """The discriminator itself, in-process -- the subprocess test above proves
        the CLI is wired to it, this proves it is right.

        Extended-length LOCAL paths are the reason this is not a leading-backslash
        test: \\\\?\\C:\\x is local and must stay usable.
        """
        from md2teams import _is_unc  # noqa: PLC0415

        for spelling in (r"\\host\share\x.png", "//host/share/x.png", r"\\?\UNC\host\share\x.png"):
            with self.subTest(spelling=spelling):
                self.assertTrue(_is_unc(Path(spelling)), f"{spelling} is a network path")

        for spelling in (r"C:\work\x.png", r"\\?\C:\work\x.png", "x.png", r".\rel\x.png"):
            with self.subTest(spelling=spelling):
                self.assertFalse(_is_unc(Path(spelling)), f"{spelling} is local")

    def test_capabilities_documents_the_flag(self) -> None:
        proc = _run("--capabilities")
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"--image shot=", proc.stdout)
        self.assertIn(b'name:shot', proc.stdout)


if __name__ == "__main__":
    unittest.main()
