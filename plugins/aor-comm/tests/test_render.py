"""Renderer behaviour: what we emit, and what we refuse.

The refusal tests matter as much as the rendering ones. Refusing by name is the
skill's safety contract -- if a construct Teams strips can slip through silently,
the user only finds out after pasting into a real conversation.
"""

from __future__ import annotations

import re
import sys
import unittest
from html import unescape
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "aor-format-teams-message"
sys.path.insert(0, str(SKILL))

from teamsfmt.render import RenderError, render  # noqa: E402
from teamsfmt.validate import validate_html  # noqa: E402


class TestInline(unittest.TestCase):
    def test_emphasis_maps_to_the_tags_teams_keeps(self) -> None:
        html = render("**b** *i* ~~s~~ ++u++", spacers=False)
        self.assertIn("<strong>b</strong>", html)
        self.assertIn("<i>i</i>", html)
        self.assertIn("<s>s</s>", html)
        self.assertIn("<u>u</u>", html)

    def test_code_span_is_literal(self) -> None:
        """Markdown inside a code span must not be interpreted."""
        html = render("`**not bold**`", spacers=False)
        self.assertIn("<code>**not bold**</code>", html)
        self.assertNotIn("<strong>", html)

    def test_link(self) -> None:
        html = render("[text](https://example.com/a?b=1)", spacers=False)
        self.assertIn('<a href="https://example.com/a?b=1">text</a>', html)

    def test_text_is_escaped(self) -> None:
        html = render("5 < 6 & 7 > 2", spacers=False)
        self.assertIn("5 &lt; 6 &amp; 7 &gt; 2", html)

    def test_danish_and_symbols_pass_through_literally(self) -> None:
        """Entities are never emitted: Teams preserves the literal characters."""
        html = render("Rødgrød — æbler → ØL ✓ 🤖", spacers=False)
        self.assertIn("Rødgrød — æbler → ØL ✓ 🤖", html)
        self.assertNotIn("&mdash;", html)


class TestBlocks(unittest.TestCase):
    def test_headings_h1_h3(self) -> None:
        html = render("# a\n\n## b\n\n### c", spacers=False)
        self.assertEqual(html, "<h1>a</h1><h2>b</h2><h3>c</h3>")

    def test_nested_list_nests_inside_the_parent_item(self) -> None:
        html = render("- one\n- two\n  - deep\n- three", spacers=False)
        self.assertIn("<li>two<ul><li>deep</li></ul></li>", html)

    def test_ordered_list(self) -> None:
        self.assertIn("<ol><li>a</li><li>b</li></ol>", render("1. a\n2. b", spacers=False))

    def test_code_block_keeps_real_newlines_and_spaces(self) -> None:
        """CKEditor converts these to Teams' own <br>/&nbsp; shape on paste.

        Verified live 2026-07-28: the naive form and a hand-built <br>/&nbsp; form
        produced identical sent HTML, so emitting the simple form is correct.
        """
        html = render("```\ndef f(x):\n    return x\n```", spacers=False)
        self.assertIn("<pre><code>def f(x):\n    return x</code></pre>", html)

    def test_table_header_is_a_real_th(self) -> None:
        """<th> verified on the desktop client 2026-07-29.

        Until then the header row was emitted as bold <td>, because <th> was
        unprobed and bold <td> had evidence. Both are now probed and <th> is the
        honest markup. Teams adds its own <thead> wrapper on paste, so we emit
        <tbody> only rather than mimicking the shape it will impose anyway.
        """
        html = render("| A | B |\n|---|---|\n| 1 | 2 |", spacers=False)
        self.assertIn("<th>A</th>", html)
        self.assertNotIn("<td><strong>A</strong></td>", html)
        self.assertIn("<td>1</td>", html)  # body cells stay <td>

    def test_lists_nest_to_depth_three(self) -> None:
        """Depth 3 verified on desktop 2026-07-29; the renderer never capped it.

        This pins the DOCUMENTED depth against the renderer, since the previous
        'depth 2' figure was a limit of what had been probed, not of the code.
        """
        html = render("- a\n  - b\n    - c", spacers=False)
        self.assertEqual(html.count("<ul>"), 3)
        self.assertIn("<li>c</li>", html)

    def test_hr_and_blockquote(self) -> None:
        html = render("> quoted\n\n---", spacers=False)
        self.assertIn("<blockquote>quoted</blockquote>", html)
        self.assertIn("<hr>", html)

    def test_spacers_are_blank_paragraphs(self) -> None:
        self.assertIn("<p>&nbsp;</p>", render("a\n\nb"))
        self.assertNotIn("<p>&nbsp;</p>", render("a\n\nb", spacers=False))


class TestWrappedListItems(unittest.TestCase):
    """A list item wrapped across two lines -- the most ordinary thing in a .md file.

    Until 2026-07-29 this DEGRADED SILENTLY, which is the failure class the whole
    project exists to prevent, arriving through the plainest construct there is. The
    continuation line escaped the <ol> into its own <p>, emphasis spanning the wrap
    leaked as literal asterisks, and the next item opened a SECOND <ol> restarting at
    1. Exit 0, straight onto the clipboard, discovered only after pasting.

    A continuation JOINS the item above it and renders as <br> -- the same model
    render.py already applies to a newline inside a paragraph, rather than GFM's
    paragraph-joining semantics.

    The structure and the emphasis are tested SEPARATELY on purpose: a fix that
    joins the lines but still runs the inline passes per line produces correct <li>
    nesting while leaving the literal-asterisk bug live, and one combined test would
    call that fixed.
    """

    # The operator's actual draft, kept verbatim -- this is the reproduction, not an
    # illustration of it.
    OPERATOR_DRAFT = (
        "1. **Does the picture below appear at all**, or do you get a broken or blank box?\n"
        "2. Four coloured bands sit above four striped areas. **Under which colours can you still\n"
        "   see separate stripes, and under which does it look like flat grey?** Red is the finest\n"
        "   pattern, then green, then blue, and orange is the coarsest.\n"
        "3. **Hover your mouse over the picture** — does any tooltip appear?\n"
    )

    def test_the_operators_draft_is_one_list_of_three_items(self) -> None:
        """Property one: structure."""
        html = render(self.OPERATOR_DRAFT, spacers=False)
        self.assertEqual(html.count("<ol>"), 1, f"the list was split: {html}")
        self.assertEqual(html.count("<li>"), 3, f"wrong item count: {html}")
        self.assertNotIn("<p>", html, "a continuation escaped the list into its own paragraph")
        validate_html(html)

    def test_emphasis_spanning_the_wrap_is_rendered_not_leaked(self) -> None:
        """Property two: the inline passes must see the JOINED text, not each line.

        This is the assertion that separates a real fix from a structural one. The
        ** in the operator's draft opens on one line and closes on the next; run the
        inline pass per line and both halves stay literal.
        """
        html = render(self.OPERATOR_DRAFT, spacers=False)
        self.assertNotIn("**", html, f"emphasis leaked as literal asterisks: {html}")
        self.assertIn(
            "<strong>Under which colours can you still<br>see separate stripes",
            html,
            "the <strong> should span the line break, not stop at it",
        )

    def test_bold_opened_on_one_line_and_closed_on_the_next(self) -> None:
        """The same property reduced to its smallest case."""
        html = render("- a **bold phrase\n  spanning the wrap** ends here", spacers=False)
        self.assertNotIn("**", html)
        self.assertIn("<strong>bold phrase<br>spanning the wrap</strong>", html)

    def test_a_continuation_joins_the_item_above_it_with_a_line_break(self) -> None:
        html = render("- first line\n  second line", spacers=False)
        self.assertEqual(html, "<ul><li>first line<br>second line</li></ul>")

    def test_an_unindented_continuation_joins_too(self) -> None:
        """Indentation is not what makes a continuation -- the absence of a marker is.

        A human wrapping a line in an editor may or may not indent the second line,
        and the classification must not depend on which.
        """
        html = render("- first line\nsecond line", spacers=False)
        self.assertEqual(html, "<ul><li>first line<br>second line</li></ul>")

    def test_a_continuation_inside_a_nested_item_stays_in_that_item(self) -> None:
        html = render(
            "- outer\n  - inner starts here\n    and wraps to here\n- second outer",
            spacers=False,
        )
        self.assertIn("<li>inner starts here<br>and wraps to here</li>", html)
        self.assertIn("<li>second outer</li>", html)
        self.assertEqual(html.count("<ul>"), 2)

    def test_a_wrapped_line_that_merely_starts_with_digits_is_not_a_new_item(self) -> None:
        """"1985 was the year" carries digits but no marker, so it is a continuation.

        Asserted as the whole fragment, deliberately. The first version of this test
        checked only that no second <li> and no <ol> appeared -- and it passed
        against the BROKEN renderer, which put the line in a stray <p> instead. That
        satisfies "not a new item" while still being the defect. A test written for
        a bug must fail on that bug; this one did not until it named the join.
        """
        html = render("- The release slipped.\n  1985 was the year it shipped.", spacers=False)
        self.assertEqual(
            html, "<ul><li>The release slipped.<br>1985 was the year it shipped.</li></ul>"
        )

    # --- the guards on the fix, not on the defect -------------------------------
    #
    # These pass BEFORE the fix as well. They exist because "join anything that is
    # not a list marker" is the obvious implementation and it silently swallows
    # nested items and every other block construct written under a list.

    def test_a_nested_item_is_still_nested_not_swallowed_as_a_continuation(self) -> None:
        for marker, tag in (("-", "ul"), ("1.", "ol")):
            with self.subTest(marker=marker):
                html = render(f"{marker} a\n  {marker} b\n    {marker} c", spacers=False)
                self.assertEqual(html.count(f"<{tag}>"), 3, f"depth 3 lost: {html}")
                self.assertIn("<li>c</li>", html)

    def test_a_blank_line_still_ends_the_list(self) -> None:
        html = render("- a\n\nnot in the list", spacers=False)
        self.assertEqual(html, "<ul><li>a</li></ul><p>not in the list</p>")

    def test_another_block_construct_under_a_list_item_still_starts_its_own_block(self) -> None:
        """The fourth case, absent from the three-way split the fix was specified as.

        A heading, rule, quote or fence written directly under a list item -- no
        blank line -- must still be that block. Treating every non-marker line as a
        continuation would swallow all four into the <li>, trading one silent
        degradation for four.
        """
        cases = {
            "- a\n# Heading": "<h1>Heading</h1>",
            "- a\n---": "<hr>",
            "- a\n> quoted": "<blockquote>quoted</blockquote>",
            "- a\n```\ncode\n```": "<pre><code>code</code></pre>",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                html = render(source, spacers=False)
                self.assertIn("<li>a</li>", html)
                self.assertIn(expected, html)

    def test_a_table_directly_under_a_list_item_is_still_a_table(self) -> None:
        """Its own test because a table is the one starter needing TWO lines to see.

        The first cut of the continuation fix swallowed this: every other block
        starter is decidable from one line, a GFM table is not, and a classifier
        that only looks at the current line reads the header row as prose. Verified
        against the pre-fix build -- it emitted a real <table> here -- so this is a
        regression the fix introduced and had to take back out, not a pre-existing gap.
        """
        html = render("- a\n| A | B |\n|---|---|\n| 1 | 2 |", spacers=False)
        self.assertIn("<li>a</li>", html)
        self.assertIn("<th>A</th>", html)
        self.assertNotIn("|", html, "the table markup leaked through as literal text")

    def test_a_table_directly_under_a_paragraph_is_still_a_table(self) -> None:
        """The same defect in the sibling code path -- and this one predates the fix.

        The paragraph accumulator and the list classifier answer the same question
        ("does this line start a block?"), and before 2026-07-29 they were separate
        copies that had already drifted: NEITHER knew about tables, so a table
        written under a paragraph with no blank line was destroyed silently. Checked
        against the committed build to be sure it was not caused by the list change.
        Both call one predicate now, so this cannot drift apart again.
        """
        html = render("some text\n| A | B |\n|---|---|\n| 1 | 2 |", spacers=False)
        self.assertIn("<p>some text</p>", html)
        self.assertIn("<th>A</th>", html)
        self.assertNotIn("|", html)

    def test_the_classifier_and_the_block_loop_agree_on_what_ends_a_list(self) -> None:
        """Tie the two directions together: END here == a new block in render().

        The classification is the part of this fix a future reader has to check, so
        assert it directly rather than only through rendered output -- and assert it
        BOTH ways, because a classifier that ends the list too eagerly and a block
        loop that never opens the block produce sane-looking HTML for the wrong reason.
        """
        from teamsfmt.render import _ListLine, _classify_list_line  # noqa: PLC0415

        starters = {
            "# Heading": "<h1>",
            "---": "<hr>",
            "> quoted": "<blockquote>",
            "```\ncode\n```": "<pre>",
            "| A | B |\n|---|---|": "<table>",
        }
        for source, tag in starters.items():
            first, _, rest = source.partition("\n")
            with self.subTest(starter=first):
                nxt = rest.split("\n")[0] if rest else None
                self.assertIs(_classify_list_line(first, nxt), _ListLine.END)
                html = render(f"- a\n{source}", spacers=False)
                self.assertIn("<li>a</li>", html)
                self.assertIn(tag, html)

    def test_the_classifier_reads_items_continuations_and_ends(self) -> None:
        from teamsfmt.render import _ListLine, _classify_list_line  # noqa: PLC0415

        cases = [
            ("- x", None, _ListLine.ITEM),
            ("  - x", None, _ListLine.ITEM),  # nesting: a marker wins over indent
            ("1. x", None, _ListLine.ITEM),
            ("    1) x", None, _ListLine.ITEM),
            ("", None, _ListLine.END),
            ("   ", None, _ListLine.END),
            ("wrapped prose", None, _ListLine.CONTINUATION),
            ("  indented prose", None, _ListLine.CONTINUATION),
            ("1985 was the year", None, _ListLine.CONTINUATION),
            ("| pipes | no separator below |", "just prose", _ListLine.CONTINUATION),
        ]
        for line, nxt, expected in cases:
            with self.subTest(line=line):
                self.assertIs(_classify_list_line(line, nxt), expected)


class TestIndentedHeadings(unittest.TestCase):
    """"  ## Sub" is a heading, not the literal text "## Sub".

    Until 2026-07-30 _HEADING was the only block pattern with no leading-whitespace
    allowance, so an indented heading silently became prose at exit 0 while "  > x"
    and "  ---" worked. Same silent-degradation class as the wrapped list item, and
    nobody had chosen it -- it read as an oversight in one regex.
    """

    def test_an_indented_heading_is_a_heading(self) -> None:
        for source, expected in (
            ("  ## Sub\n", "<h2>Sub</h2>"),
            ("    ### Deeper\n", "<h3>Deeper</h3>"),
            ("\t# Tabbed\n", "<h1>Tabbed</h1>"),
        ):
            with self.subTest(source=source):
                self.assertIn(expected, render(source, spacers=False))
                self.assertNotIn("#", render(source, spacers=False))

    def test_the_h4_refusal_still_fires_when_indented(self) -> None:
        """The indent must not become a way around a refusal."""
        with self.assertRaises(RenderError) as caught:
            render("  #### Four\n")
        self.assertTrue(any("h4" in p for p in caught.exception.problems))

    def test_a_hash_without_a_space_is_still_text(self) -> None:
        """Why the fix is safe: a heading needs # then whitespace, so a wrapped line
        that happens to begin with a hash keeps joining the item above it."""
        out = render("- issue closed\n  #123 was the culprit\n", spacers=False)
        self.assertIn("<li>issue closed<br>#123 was the culprit</li>", out)
        self.assertNotIn("<h1>", out)

    def test_an_indented_heading_ends_a_list(self) -> None:
        """The visible consequence of the fix, pinned so it is a decision and not a
        surprise: an indented quote and rule already ended a list this way."""
        out = render("- item\n  ## Sub\n", spacers=False)
        self.assertIn("<ul><li>item</li></ul>", out)
        self.assertIn("<h2>Sub</h2>", out)


class TestRefusals(unittest.TestCase):
    def _problems(self, source: str) -> list[str]:
        with self.assertRaises(RenderError) as ctx:
            render(source)
        return ctx.exception.problems

    def test_div_is_refused_by_name(self) -> None:
        problems = self._problems('<div class="x">boxed</div>')
        self.assertTrue(any("<div>" in p for p in problems), problems)

    def test_h4_is_refused(self) -> None:
        self.assertTrue(any("h4" in p for p in self._problems("#### too deep")))

    def test_unsupported_span_style_is_refused(self) -> None:
        problems = self._problems('<span style="border: 1px solid red">x</span>')
        self.assertTrue(any("border" in p for p in problems), problems)

    def test_every_problem_is_reported_not_just_the_first(self) -> None:
        """An agent correcting itself should see the whole list in one pass."""
        problems = self._problems("#### deep\n\n<div>a</div>\n\n<img src='x'>")
        self.assertGreaterEqual(len(problems), 3)

    def test_supported_span_colour_is_allowed(self) -> None:
        html = render('<span style="color: red">x</span>', spacers=False)
        self.assertIn('<span style="color: red">x</span>', html)


class TestLinkUrlIsEscapedExactlyOnce(unittest.TestCase):
    """A pasted link must resolve to the URL the author wrote.

    The inline pass escapes the whole line before the link pattern is applied,
    so by the time a URL is captured its & is already &amp;. Escaping the
    captured URL a second time published &amp;amp; -- which decodes to a
    literal "&amp;" in the query string, i.e. a different URL than the draft
    said. Silent, and invisible until someone clicks the link in a real
    conversation: exactly the class of failure "refuse, don't degrade" exists
    to prevent, arriving through the one construct that carries a machine-read
    value rather than prose.
    """

    def _href(self, markdown: str) -> str:
        html = render(markdown, spacers=False)
        m = re.search(r'<a href="([^"]*)"', html)
        self.assertIsNotNone(m, f"no link emitted from {markdown!r}: {html}")
        return m.group(1)

    def test_query_string_ampersands_survive_as_single_entities(self) -> None:
        href = self._href("[r](https://example.com/r?a=1&b=2&c=3)")
        self.assertNotIn("&amp;amp;", href)
        self.assertEqual(href, "https://example.com/r?a=1&amp;b=2&amp;c=3")

    def test_href_decodes_back_to_the_authored_url(self) -> None:
        """The property that actually matters, stated as a round trip."""
        url = "https://example.com/search?q=a&lang=da&page=2"
        href = self._href(f"[s]({url})")
        self.assertEqual(unescape(href), url)

    def test_double_quote_in_url_cannot_break_out_of_the_attribute(self) -> None:
        """quote=False upstream leaves " alone, so this layer must handle it."""
        href = self._href('[x](https://example.com/a"b)')
        self.assertNotIn('"', href)
        self.assertIn("&quot;", href)

    def test_link_text_is_still_escaped(self) -> None:
        html = render("[a & b](https://example.com/)", spacers=False)
        self.assertIn(">a &amp; b<", html)
        self.assertNotIn("&amp;amp;", html)


class TestHighlightPinsBothColours(unittest.TestCase):
    """==highlight== must emit a foreground colour, not just a background.

    This is a safety property, not a style preference, which is why it is pinned
    rather than left to the manifest's generic "background-color is allowed" check
    -- that check passed happily while only the background was set. Setting a light
    background and letting the client choose the text colour renders white-on-pale-
    yellow under a dark theme: an unreadable bar the sender never sees. No dark
    client has been probed, so the pair is what removes the dependency.
    """

    def test_both_properties_are_emitted(self) -> None:
        html = render("==urgent==")
        self.assertIn("background-color", html)
        self.assertIn("color: #242424", html)

    def test_the_style_comes_from_one_definition(self) -> None:
        """Two copies of one style string is how this lane's docs drifted before."""
        from teamsfmt.render import HIGHLIGHT_STYLE  # noqa: PLC0415

        self.assertIn(HIGHLIGHT_STYLE, render("==urgent=="))


class TestRendererOutputAlwaysValidates(unittest.TestCase):
    """Whatever the renderer emits must satisfy the manifest. Belt and braces."""

    SAMPLES = [
        "# H\n\ntext **b** `c` [l](https://e.com)\n\n- a\n  - b\n\n1. x\n\n> q\n\n---\n\n```\ncode\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |",
        "Rødgrød — æøå → ✓ 🤖",
        '<span style="background-color: yellow">hi</span>',
        # A wrapped item, with emphasis spanning the wrap: the <br> the join emits
        # has to satisfy the manifest like any other tag we produce.
        "1. a **phrase that\n   wraps** here\n2. and a second item",
    ]

    def test_all_samples_validate(self) -> None:
        for sample in self.SAMPLES:
            with self.subTest(sample=sample[:30]):
                validate_html(render(sample))


if __name__ == "__main__":
    unittest.main(verbosity=2)
