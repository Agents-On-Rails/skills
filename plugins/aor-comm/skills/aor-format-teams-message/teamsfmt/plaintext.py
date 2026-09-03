"""The CF_UNICODETEXT flavor: a crude de-tagging of the HTML fragment.

Lives in the package because BOTH clipboard writers need it and neither should
import the other. It was previously defined in setclip.py and imported by
md2teams.py -- a sideways dependency between two entry points, which is also
what kept a probe-only tool inside the installed skill folder.

Only a fallback: any target that understands CF_HTML never sees this.
"""

from __future__ import annotations

import re

_TAGS = re.compile(r"<[^>]+>")


def plain_text_fallback(fragment: str) -> str:
    """De-tag the fragment, turning block boundaries into newlines.

    Block boundaries become newlines so the text flavor is not one run-on line.
    """
    text = re.sub(r"<(br|/p|/li|/h[1-6]|/pre|/tr)\s*/?>", "\n", fragment, flags=re.I)
    text = _TAGS.sub("", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(entity, char)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
