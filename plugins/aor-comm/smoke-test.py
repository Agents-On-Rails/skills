#!/usr/bin/env python3
"""aor-comm smoke test -- does this install actually work on this machine?

Run it once after installing the plugin. It takes a second, writes nothing outside a
temporary directory, and never touches your clipboard.

    python smoke-test.py

WHAT IT IS AND IS NOT

It is the shipped replacement for the development suites, which stay in the source
repository and never travel (K16: a plugin ships a smoke test, never its development
suites). Those four suites are 115 cases about whether the Markdown-to-Teams mapping is
*correct*; this file is five checks about whether the thing you just installed *runs here* --
the interpreter, the entry point, the shipped manifest, the render path, and the refusal
contract. When a paste comes out wrong, the suites are what tells you why. When the tool
will not start at all, this is.

It never exercises the clipboard. Every render below goes through `--print`, which is the
documented route that formats without copying, so running this cannot disturb whatever you
had copied.

The five assertions are the SMOKE-1 to SMOKE-5 markers in this file.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Leave no bytecode behind. This script runs the tool beside it, and a .pyc records the
# absolute path of its source, so without this the smoke test would write your install
# location into __pycache__ inside the delivered skill folder -- which is then carried by
# anything that copies that folder. Set both: the flag for this process, the variable for
# the tool subprocesses below. This is #45's class, and the reason it is guarded here as
# well as in md2teams.py is that either file can be the first one you run.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

ROOT = Path(__file__).resolve().parent
SKILL = ROOT / "skills" / "aor-format-teams-message"
MD2TEAMS = SKILL / "md2teams.py"
MANIFEST = SKILL / "capabilities.json"

MIN_PYTHON = (3, 9)          # declared in SKILL.md's compatibility field

failures = 0


def check(cond, msg):
    global failures
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures += 1


def run(*args, stdin=None):
    """The tool, in a child process, with its exit code intact.

    Measured without a pipeline on purpose: reading a status through `| head` reports the
    LAST command in the pipe, which has cost this project two wrong conclusions about an
    exit code that was in fact correct."""
    return subprocess.run([sys.executable, str(MD2TEAMS), *[str(a) for a in args]],
                          input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


# SMOKE-5 first: everything below reports confusingly on an interpreter that is too old, so
# say so plainly before the failures start rather than after.
check(sys.version_info[:2] >= MIN_PYTHON,
      "SMOKE-5: this interpreter is Python %d.%d or later [running %d.%d from %s]"
      % (MIN_PYTHON + sys.version_info[:2] + (sys.executable,)))

# SMOKE-1 -- the entry point starts. aor-comm is standard-library only, so unlike aor-kb
# there is no dependency to fail on: if this one fails, the install itself is incomplete.
_help = run("--help") if MD2TEAMS.is_file() else None
check(_help is not None and _help.returncode == 0,
      "SMOKE-1: md2teams.py runs --help and exits 0 [%s]"
      % ("missing at %s" % MD2TEAMS if _help is None else "rc=%s" % _help.returncode))

# SMOKE-2 -- the shipped manifest is present and parses. capabilities.json is the single
# source of truth that both SKILL.md and the converter's validator read, so an install that
# dropped it fails here rather than at the first real message. --capabilities interpolates
# the manifest's verified_client string directly, which is why finding it in the output
# proves the file was read rather than merely existing.
_caps = run("--capabilities")
check(MANIFEST.is_file() and _caps.returncode == 0 and "verified" in _caps.stdout.lower(),
      "SMOKE-2: the shipped capabilities.json is present and --capabilities reads it "
      "[manifest=%s rc=%s bytes=%d]"
      % (MANIFEST.is_file(), _caps.returncode, len(_caps.stdout)))

# SMOKE-3 -- the render path works end to end: stdin, parse, render, validate, print. This
# is the only case that exercises the whole chain, and it is deliberately asserted on the
# mapping rather than on an exact line, so a change to block spacing does not fail an
# install check. --print, so the clipboard is never touched.
_r = run("--print", "--tight", stdin="**bold** and *italic*\n")
_html = _r.stdout
check(_r.returncode == 0 and "<strong>bold</strong>" in _html and "<i>italic</i>" in _html,
      "SMOKE-3: a minimal draft renders to the tags Teams keeps, without touching the "
      "clipboard [rc=%s out=%r]" % (_r.returncode, _html.strip()[:80]))

# SMOKE-4 -- the refusal contract, which is this skill's whole safety property. A construct
# Teams silently downgrades must exit non-zero and NAME the construct, because a downgraded
# message is only discovered after it has been pasted into a real conversation. h4 is the
# canonical case: Teams keeps the text and loses the level. Asserted as "refuses and says
# why", not merely "is non-zero" -- an exit code with no explanation would satisfy a weaker
# check while leaving the agent unable to correct itself.
_ref = run("--print", stdin="#### Four\n")
_said = ("h4" in (_ref.stdout + _ref.stderr))
check(_ref.returncode == 1 and _said,
      "SMOKE-4: a draft Teams cannot render is REFUSED with the construct named, and "
      "nothing is copied [rc=%s named_h4=%s]" % (_ref.returncode, _said))

print()
if failures:
    print("RED -- %d of 5 checks failed. This install is not ready to use." % failures)
else:
    print("GREEN -- 5 of 5. The plugin runs on this machine.")
sys.exit(1 if failures else 0)
