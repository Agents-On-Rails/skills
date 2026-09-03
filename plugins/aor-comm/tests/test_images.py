"""Image embedding: what is accepted, what is refused, and what is stripped out.

The refusal and stripping tests matter more than the happy path. An image is the
one construct where the draft carries BYTES rather than prose, so the ways it can
go wrong are not "renders oddly" -- they are "publishes something the author did
not intend to publish".
"""

from __future__ import annotations

import base64
import random
import struct
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "aor-format-teams-message"
sys.path.insert(0, str(SKILL))

from teamsfmt.images import (  # noqa: E402
    IMAGE_NAME,
    ImageError,
    sanitize_bytes,
    sanitize_data_uri,
)
from teamsfmt.render import RenderError, render, render_with_images  # noqa: E402
from teamsfmt.validate import ValidationError, validate_html  # noqa: E402


def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    body = ctype + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def make_png(
    width: int = 4, height: int = 3, *, secret: bytes | None = None, noise: bool = False
) -> bytes:
    """A real PNG, optionally carrying a text chunk that must not survive.

    `secret` stands in for the everyday metadata hazard: EXIF, XMP, a pre-crop
    thumbnail, GPS, a device serial, an author name, an original file path.

    `noise` fills the pixels from a SEEDED PRNG instead of flat red. Flat red
    compresses to almost nothing, so a big flat image is a small file -- useless
    for testing that large payloads survive. Seeded, not random, so the emitted
    size is reproducible run to run.
    """
    if noise:
        rng = random.Random(20260730)
        raw = b"".join(b"\x00" + rng.randbytes(3 * width) for _ in range(height))
    else:
        raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    out = b"\x89PNG\r\n\x1a\n"
    out += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    if secret is not None:
        out += _png_chunk(b"tEXt", b"Comment\x00" + secret)
    out += _png_chunk(b"IDAT", zlib.compress(raw))
    out += _png_chunk(b"IEND", b"")
    return out


def data_uri(payload: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


class TestMetadataIsStripped(unittest.TestCase):
    """The likely hazard, not the exotic one.

    Crop a screenshot to remove a colleague's name and many editors keep the
    PRE-CROP image in an EXIF thumbnail. Scanning for known-bad would be a losing
    game; re-emitting only the chunks an image needs makes carrying it structurally
    impossible.
    """

    def test_png_text_chunk_does_not_survive(self) -> None:
        secret = b"pre-crop thumbnail and the name we cropped out"
        src, info = sanitize_data_uri(data_uri(make_png(secret=secret)), "line 1")
        emitted = base64.b64decode(src.split(",", 1)[1])
        self.assertNotIn(secret, emitted)
        self.assertNotIn(b"tEXt", emitted)
        self.assertGreater(info.stripped_bytes, 0)

    def test_the_image_still_decodes_after_stripping(self) -> None:
        """Stripping must not corrupt the picture -- dimensions prove structure survived."""
        _src, info = sanitize_data_uri(data_uri(make_png(12, 7, secret=b"x")), "line 1")
        self.assertEqual((info.width, info.height), (12, 7))
        self.assertEqual(info.mime, "image/png")

    def test_a_clean_png_is_not_reported_as_stripped(self) -> None:
        _src, info = sanitize_data_uri(data_uri(make_png()), "line 1")
        self.assertEqual(info.stripped_bytes, 0)


class TestTypeIsTakenFromTheBytes(unittest.TestCase):
    """A declared MIME type is an assertion by the least-trusted input."""

    def test_declared_type_that_contradicts_the_bytes_is_refused(self) -> None:
        with self.assertRaises(ImageError) as cm:
            sanitize_data_uri(data_uri(make_png(), mime="image/jpeg"), "line 1")
        self.assertIn("declares image/jpeg", str(cm.exception))

    def test_non_image_bytes_are_refused_however_they_are_labelled(self) -> None:
        with self.assertRaises(ImageError) as cm:
            sanitize_data_uri(data_uri(b"SECRET=hunter2\n", mime="image/png"), "line 1")
        self.assertIn("not a PNG or a JPEG", str(cm.exception))

    def test_undecodable_base64_is_refused(self) -> None:
        with self.assertRaises(ImageError):
            sanitize_data_uri("data:image/png;base64,not-valid-base64!!", "line 1")


class TestSizeIsNotCapped(unittest.TestCase):
    """The 2 MB policy cap was removed on 2026-07-30 (operator decision).

    These replace three refusal tests. They exist because removing a limit is only
    verifiable by ABSENCE otherwise -- "no exception was raised" is equally true of a
    working change and of a test pointed at the wrong thing. So assert the positive:
    a large image goes all the way through and comes out intact.
    """

    @staticmethod
    def _incompressible_png(min_bytes: int) -> bytes:
        """A PNG whose IDAT genuinely exceeds min_bytes.

        NOT make_png() + zeros. That was the old fixture, and it worked only because
        the cap was checked before parsing: the trailing zeros are read as unknown
        zero-length chunks and DROPPED by the stripper, so post-removal such a payload
        collapses back under any threshold and would pin the stripper rather than size
        acceptance. Random pixels defeat zlib, so the size survives the round trip.
        """
        side = 512
        while True:
            png = make_png(side, side, noise=True)
            if len(png) > min_bytes:
                return png
            side *= 2

    def test_an_image_far_over_the_old_cap_is_accepted(self) -> None:
        big = self._incompressible_png(2 * 1024 * 1024)
        clean_src, info = sanitize_data_uri(data_uri(big), "line 1")
        self.assertGreater(info.original_bytes, 2 * 1024 * 1024, "fixture is not over the old cap")
        self.assertGreater(info.emitted_bytes, 2 * 1024 * 1024, "the payload must SURVIVE, not be stripped away")
        self.assertEqual(info.mime, "image/png")
        self.assertTrue(clean_src.startswith("data:image/png;base64,"))

    def test_bound_bytes_far_over_the_old_cap_are_accepted_too(self) -> None:
        """The argv path is the one the removal had to reach -- it had its own guard."""
        big = self._incompressible_png(2 * 1024 * 1024)
        html_out = render('<img src="name:x" alt="a">', images={"x": big})
        self.assertIn("data:image/png;base64,", html_out)


class TestSourceForms(unittest.TestCase):
    def test_external_url_names_the_real_consequence(self) -> None:
        """An http src does not lose the image -- it blocks the whole message."""
        with self.assertRaises(RenderError) as cm:
            render('<img src="https://example.com/logo.png" alt="x">')
        problems = " ".join(cm.exception.problems)
        self.assertIn("ENTIRE paste", problems)

    def test_markdown_image_syntax_is_refused_by_name(self) -> None:
        """It used to render as a literal '!' plus a dead hyperlink, exit 0."""
        with self.assertRaises(RenderError) as cm:
            render("![Architecture diagram](C:/work/diagram.png)")
        problems = " ".join(cm.exception.problems)
        self.assertIn("Markdown image syntax", problems)
        self.assertIn("data:image/png;base64", problems)  # tells it what to do instead

    def test_a_path_written_in_the_draft_is_never_read(self) -> None:
        """The security property, asserted where someone would have to edit it.

        No path syntax exists in the draft grammar, so traversal and UNC paths are
        unexpressible rather than blocked. The wording moved on 2026-07-29 when
        --image gave the CONVERTER a file-reading path -- but only for a path on
        argv, so the draft-side property is unchanged and this test still guards it.
        If a future change starts reading files named in a draft, this message stops
        being true and this test should be the thing that makes someone think about it.
        """
        with self.assertRaises(RenderError) as cm:
            render("![x](../../.aws/credentials)")
        problems = " ".join(cm.exception.problems)
        self.assertIn("path written in the draft is never read", problems)
        self.assertNotIn("../../.aws/credentials", problems, "the path must not be echoed back")


class TestAcceptedImageRoundTrip(unittest.TestCase):
    def test_data_uri_image_renders_validates_and_is_reported(self) -> None:
        md = f'Here it is:\n\n<img src="{data_uri(make_png(9, 5, secret=b"drop me"))}" alt="A red block">'
        html, images = render_with_images(md, spacers=False)

        validate_html(html)  # must satisfy the manifest, not just the renderer
        self.assertIn('<img src="data:image/png;base64,', html)
        self.assertIn('alt="A red block"', html)
        self.assertNotIn("drop me", html)

        self.assertEqual(len(images), 1)
        info = images[0]
        self.assertEqual((info.width, info.height), (9, 5))
        summary = info.summary()
        for expected in ("image/png", "9x5", "sha256:", "stripped"):
            self.assertIn(expected, summary)

    def test_alt_text_cannot_break_out_of_the_attribute(self) -> None:
        md = f'<img src="{data_uri(make_png())}" alt=\'evil" onload="x\'>'
        html, _ = render_with_images(md, spacers=False)

        # validate_html is the real oracle here: it permits only src and alt on
        # <img>, so if the quote had escaped the attribute and produced a genuine
        # onload attribute, this call would raise.
        validate_html(html)
        self.assertIn("&quot;", html, "the embedded quote should be entity-escaped")
        self.assertNotIn('onload="', html, "no real onload attribute may be formed")

    def test_an_image_inside_a_code_span_stays_literal(self) -> None:
        """Documentation about images must not become an image."""
        html = render('`<img src="data:image/png;base64,AAAA">`', spacers=False)
        self.assertIn("&lt;img", html)


class TestSanitizeBytesIsTheSharedFloor(unittest.TestCase):
    """Both routes in -- inline base64 and a file bound on argv -- land here.

    That is the point of the split: a file the operator bound on the command line
    gets byte-for-byte the same treatment as base64 an agent pasted into the draft.
    If the two ever diverge, one of them is the weaker path and nobody would know
    which.
    """

    def test_bound_bytes_and_an_inline_data_uri_produce_identical_output(self) -> None:
        raw = make_png(9, 5, secret=b"pre-crop thumbnail")
        from_bytes, info_bytes = sanitize_bytes(raw, "line 1")
        from_uri, info_uri = sanitize_data_uri(data_uri(raw), "line 1")
        self.assertEqual(from_bytes, from_uri)
        self.assertEqual(info_bytes, info_uri)

    def test_empty_bytes_are_refused(self) -> None:
        with self.assertRaises(ImageError) as cm:
            sanitize_bytes(b"", "line 1")
        self.assertIn("empty", str(cm.exception))

    def test_non_image_bytes_are_refused(self) -> None:
        with self.assertRaises(ImageError) as cm:
            sanitize_bytes(b"SECRET=hunter2\n", "line 1")
        self.assertIn("not a PNG or a JPEG", str(cm.exception))

    def test_it_still_takes_no_path_of_any_kind(self) -> None:
        """The signature IS the security property, so assert the signature.

        sanitize_bytes exists because md2teams.py now opens files. The one thing
        that must never follow it into this module is a path parameter -- the day
        one appears, this module can be made to read a file and the whole 'the
        draft cannot select a file' argument becomes editorial rather than
        structural.
        """
        import inspect  # noqa: PLC0415

        params = inspect.signature(sanitize_bytes).parameters
        self.assertEqual(list(params), ["data", "where"])
        # A string, not the type: this module uses `from __future__ import annotations`.
        self.assertEqual(params["data"].annotation, "bytes")


class TestNamedBindingsResolveWithoutAPath(unittest.TestCase):
    """`<img src="name:X">` looks X up in a dict the CALLER built from argv.

    A name is never joined onto a directory, so traversal and UNC are not attacks
    this code defends against -- they are strings that cannot be names. The tests
    below assert the two halves of the contract stay tied together: an unresolvable
    name is refused, and so is a binding the draft never used.
    """

    def test_a_bound_name_renders_and_is_reported(self) -> None:
        html, images = render_with_images(
            '<img src="name:chart" alt="Build status">',
            spacers=False,
            images={"chart": make_png(9, 5, secret=b"drop me")},
        )
        validate_html(html)
        self.assertIn('<img src="data:image/png;base64,', html)
        self.assertIn('alt="Build status"', html)
        self.assertNotIn("name:chart", html, "the name must be resolved away, not emitted")
        self.assertNotIn("drop me", html)
        self.assertEqual(len(images), 1)
        self.assertEqual((images[0].width, images[0].height), (9, 5))

    def test_a_name_with_no_binding_is_refused_and_says_what_to_add(self) -> None:
        with self.assertRaises(RenderError) as cm:
            render('<img src="name:chart" alt="x">', images={"other": make_png()})
        problems = " ".join(cm.exception.problems)
        self.assertIn("--image chart=", problems)
        self.assertIn("other", problems, "the refusal should list the names that ARE bound")

    def test_a_name_with_no_bindings_at_all_is_refused(self) -> None:
        with self.assertRaises(RenderError) as cm:
            render('<img src="name:chart" alt="x">')
        self.assertIn("nobody bound", " ".join(cm.exception.problems))

    def test_a_binding_the_draft_never_references_is_refused(self) -> None:
        """A silently missing image is the exact failure this tool exists to prevent.

        The operator typed --image chart=..., and without this the message goes to
        the clipboard at exit 0 with no chart in it.
        """
        with self.assertRaises(RenderError) as cm:
            render("Just text.", images={"chart": make_png()})
        problems = " ".join(cm.exception.problems)
        self.assertIn("--image chart=... was bound but the draft never references it", problems)

    def test_every_unused_binding_is_named_not_just_the_first(self) -> None:
        with self.assertRaises(RenderError) as cm:
            render("Just text.", images={"a": make_png(), "b": make_png()})
        problems = " ".join(cm.exception.problems)
        self.assertIn("--image a=", problems)
        self.assertIn("--image b=", problems)

    def test_one_binding_may_be_referenced_twice(self) -> None:
        html, images = render_with_images(
            '<img src="name:x" alt="a">\n\n<img src="name:x" alt="b">',
            spacers=False,
            images={"x": make_png()},
        )
        validate_html(html)
        self.assertEqual(len(images), 2, "both references should embed, and neither is 'unused'")

    def test_a_path_shaped_name_is_refused_because_it_is_not_a_name(self) -> None:
        for src in ("name:../../.aws/credentials", "name:C:/work/x.png", r"name:\\host\share\x.png"):
            with self.subTest(src=src), self.assertRaises(RenderError) as cm:
                render(f'<img src="{src}" alt="x">', images={"ok": make_png()})
            problems = " ".join(cm.exception.problems)
            self.assertIn("does not name an image", problems)
            self.assertNotIn(
                src[len("name:") :], problems, "the refusal must not echo the draft's string back"
            )

    def test_an_empty_name_is_refused(self) -> None:
        with self.assertRaises(RenderError) as cm:
            render('<img src="name:" alt="x">')
        self.assertIn("does not name an image", " ".join(cm.exception.problems))

    def test_the_name_grammar_admits_no_path_character(self) -> None:
        """Pin the grammar itself, not only what the renderer does with it."""
        for ok in ("shot", "SHOT-2", "a_b", "x" * 64):
            self.assertTrue(IMAGE_NAME.match(ok), ok)
        for bad in ("", "x" * 65, "a.png", "a/b", "a\\b", "C:x", "a b", "..", "a\nb"):
            self.assertFalse(IMAGE_NAME.match(bad), bad)

    def test_bound_bytes_get_the_same_refusals_as_inline_ones(self) -> None:
        for label, payload, expected in (
            ("not an image", b"SECRET=hunter2\n", "not a PNG or a JPEG"),
            ("empty", b"", "empty"),
        ):
            with self.subTest(case=label), self.assertRaises(RenderError) as cm:
                render('<img src="name:x" alt="a">', images={"x": payload})
            problems = " ".join(cm.exception.problems)
            self.assertIn(expected, problems)
            self.assertIn("--image x", problems, "the refusal must say WHICH binding failed")

    def test_a_name_inside_a_code_span_is_not_a_reference(self) -> None:
        """Documentation about an image must not silently satisfy a binding.

        `<img src="name:x">` in backticks is prose. Treating it as a use would
        leave a bound image unpublished while the unused-binding guard stayed
        quiet -- the guard would have been disarmed by a mention of itself.
        """
        with self.assertRaises(RenderError) as cm:
            render('`<img src="name:x" alt="a">`', images={"x": make_png()})
        self.assertIn("never references it", " ".join(cm.exception.problems))

    def test_data_uris_still_work_with_bindings_present(self) -> None:
        html, images = render_with_images(
            f'<img src="{data_uri(make_png())}" alt="a">\n\n<img src="name:x" alt="b">',
            spacers=False,
            images={"x": make_png(3, 2)},
        )
        validate_html(html)
        self.assertEqual(len(images), 2)
        self.assertEqual((images[1].width, images[1].height), (3, 2))

    def test_a_src_that_is_neither_form_names_both_routes(self) -> None:
        with self.assertRaises(RenderError) as cm:
            render('<img src="shot.png" alt="x">')
        problems = " ".join(cm.exception.problems)
        self.assertIn("data: URI or a name: reference", problems)
        self.assertIn("--image", problems)


class TestValidatorIsDefenceInDepth(unittest.TestCase):
    """The renderer refuses bad sources; the validator must too, independently."""

    def test_validator_rejects_an_external_src_even_if_the_renderer_is_bypassed(self) -> None:
        with self.assertRaises(ValidationError) as cm:
            validate_html('<p><img src="https://example.com/x.png" alt=""></p>')
        self.assertIn("data:image/", " ".join(cm.exception.problems))

    def test_validator_rejects_unexpected_attributes_on_img(self) -> None:
        with self.assertRaises(ValidationError):
            validate_html('<p><img src="data:image/png;base64,AAAA" onerror="x"></p>')


if __name__ == "__main__":
    unittest.main(verbosity=2)
