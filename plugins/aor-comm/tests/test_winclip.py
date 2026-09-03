"""CF_HTML correctness tests.

The whole clipboard approach dies silently if the CF_HTML header's byte offsets
are wrong: consumers slice by the declared offsets, so a payload with plausible
HTML and bad offsets reads as empty or garbage and looks exactly like "the target
app stripped my formatting". These tests exist to keep that failure impossible.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "aor-format-teams-message"
sys.path.insert(0, str(SKILL))

from teamsfmt.winclip import (  # noqa: E402
    CF_UNICODETEXT,
    dump_all,
    html_format_id,
    set_clipboard,
    unwrap_cf_html,
    wrap_cf_html,
)


def _header(payload: bytes) -> dict[str, int]:
    text = payload.split(b"<html", 1)[0].decode("ascii")
    out = {}
    for line in text.strip().split("\r\n"):
        key, _, value = line.partition(":")
        if value.isdigit():
            out[key] = int(value)
    return out


class TestCfHtmlOffsets(unittest.TestCase):
    def test_offsets_are_byte_exact_for_ascii(self) -> None:
        fragment = "<b>hello</b>"
        payload = wrap_cf_html(fragment)
        h = _header(payload)
        self.assertEqual(payload[h["StartFragment"] : h["EndFragment"]], fragment.encode("utf-8"))

    def test_offsets_are_byte_exact_for_danish(self) -> None:
        """The test that catches char-offsets-masquerading-as-byte-offsets.

        Every one of these characters is multi-byte in UTF-8, so a wrap that
        counted characters produces offsets that are short by exactly the number
        of non-ASCII characters -- and the fragment slice comes back truncated.
        """
        fragment = "<b>Rødgrød med fløde</b> — søde æbler → ØL"
        payload = wrap_cf_html(fragment)
        h = _header(payload)
        sliced = payload[h["StartFragment"] : h["EndFragment"]]
        self.assertEqual(sliced, fragment.encode("utf-8"))
        self.assertEqual(sliced.decode("utf-8"), fragment)

    def test_start_html_points_at_the_document(self) -> None:
        payload = wrap_cf_html("<p>x</p>")
        h = _header(payload)
        self.assertTrue(payload[h["StartHTML"] :].startswith(b"<html"))

    def test_end_html_is_end_of_document(self) -> None:
        payload = wrap_cf_html("<p>x</p>")
        h = _header(payload)
        self.assertTrue(payload[h["StartHTML"] : h["EndHTML"]].rstrip().endswith(b"</html>"))

    def test_header_length_is_constant_regardless_of_payload_size(self) -> None:
        """Zero-padded fixed-width offsets are what make single-pass computation safe."""
        small = wrap_cf_html("<p>x</p>")
        large = wrap_cf_html("<p>" + "x" * 100_000 + "</p>")
        self.assertEqual(small.index(b"<html"), large.index(b"<html"))

    def test_fragment_markers_are_present_and_unpadded(self) -> None:
        payload = wrap_cf_html("<p>x</p>")
        self.assertIn(b"<!--StartFragment-->", payload)
        self.assertIn(b"<!--EndFragment-->", payload)

    def test_unwrap_round_trips(self) -> None:
        fragment = "<ul><li>æble</li><li>pære</li></ul>"
        parsed = unwrap_cf_html(wrap_cf_html(fragment))
        self.assertEqual(parsed["fragment"], fragment)
        self.assertTrue(parsed["offsets_consistent"])
        self.assertEqual(parsed["header"]["Version"], "0.9")

    def test_unwrap_detects_a_lying_header(self) -> None:
        """A corrupted offset must be reported, not silently tolerated."""
        payload = bytearray(wrap_cf_html("<b>hello</b>"))
        start = payload.index(b"StartFragment:") + len("StartFragment:")
        payload[start : start + 10] = b"0000000999"
        parsed = unwrap_cf_html(bytes(payload))
        self.assertFalse(parsed["offsets_consistent"])


@unittest.skipUnless(
    os.environ.get("TEAMSFMT_LIVE_CLIPBOARD") == "1",
    "writes to the real machine-wide clipboard; set TEAMSFMT_LIVE_CLIPBOARD=1 to run",
)
class TestLiveClipboard(unittest.TestCase):
    """Round-trips through the REAL Windows clipboard -- opt-in, and here is why.

    The clipboard is ambient machine-wide state. This test overwrites whatever the
    operator had copied, and the session-start ritual runs the suite every session,
    so leaving it on by default silently destroys their clipboard as a side effect
    of checking the build. Everything else in the suite is genuinely offline.
    """

    def test_set_and_read_back_both_flavors(self) -> None:
        fragment = "<b>Rødgrød</b> — æøå → ✓"
        plain = "Rødgrød — æøå → ✓"
        set_clipboard(
            {
                html_format_id(): wrap_cf_html(fragment),
                CF_UNICODETEXT: plain.encode("utf-16-le") + b"\x00\x00",
            }
        )

        by_name = {e["name"]: e for e in dump_all()}
        self.assertIn("HTML Format", by_name)
        self.assertIn("CF_UNICODETEXT", by_name)

        parsed = unwrap_cf_html(by_name["HTML Format"]["data"])
        self.assertEqual(parsed["fragment"], fragment)
        self.assertTrue(parsed["offsets_consistent"])

        text = by_name["CF_UNICODETEXT"]["data"].decode("utf-16-le").rstrip("\x00")
        self.assertEqual(text, plain)


if __name__ == "__main__":
    unittest.main(verbosity=2)
