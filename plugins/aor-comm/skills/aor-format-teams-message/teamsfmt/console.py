"""Console output encoding, forced to UTF-8.

Every CLI in this skill needs this and needs it identically, so it is defined
once. Two copies of one statement is how SKILL.md and capabilities.json drifted
apart; the same trap applies to code.
"""

from __future__ import annotations

import sys


def force_utf8_output() -> None:
    """Make stdout/stderr UTF-8, whatever the host hands us.

    Outside UTF-8 mode the interpreter builds these streams from the locale --
    cp1252 on this box -- and that applies to a pipe just as much as to an
    interactive console. cp1252 cannot encode the arrows and emoji this skill's
    vocabulary advertises as passing through exactly, so printing them raised
    UnicodeEncodeError.

    In md2teams.py that failure was worse than cosmetic: a non-zero exit from
    that CLI means "your draft was refused", so an encoding crash told a
    drafting agent its valid draft was invalid, in the tool's own exit-code
    vocabulary.

    backslashreplace, not replace: it never raises, and unlike replace it is
    lossless-recoverable, so this cannot quietly destroy a byte in a diagnostic.
    A backstop that discards data is the wrong backstop -- which matters most in
    clipdump.py, whose whole job is reporting bytes faithfully.

    hasattr guards a caller having already replaced the stream with something
    that is not a text wrapper -- notably unittest's -b buffer, an io.StringIO.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
