#!/usr/bin/env python3
"""aor-kb smoke test -- does this install actually work on this machine?

Run it once after installing the plugin. It takes a few seconds, touches nothing outside a
temporary directory unless you ask it to, and tells you whether the skills' tools can
run here at all.

    python smoke-test.py                 # check an installed plugin
    python smoke-test.py --root DIR      # write the config-root check into DIR instead

WHAT IT IS AND IS NOT

It is the shipped replacement for the development selftests, which stay in the source
repository and never travel: they are the author's verification instrument, they carry
machine-specific fixtures, and a plugin install copies whatever sits in the plugin
directory. So this file is deliberately small and deliberately about YOUR machine -- the
interpreter, the dependency, the config root, the Windows-only guard -- rather than about
whether the knowledge-base logic is correct. That is what the source suites are for.

The seven assertions are specified in this file's own SMOKE-1 to SMOKE-4, SMOKE-6 and SMOKE-7
markers, plus the Python floor (SMOKE-5), which the item-3 commit explicitly deferred to this
file rather than to the dependency manifest. Seven, counted from that list.
"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Leave no bytecode behind. This script runs the tools beside it, and a .pyc records the
# absolute path of its source, so without this the smoke test would write your install
# location into __pycache__ inside the plugin directory -- which is then copied by
# anything that copies that directory. Set both: the flag for this process, the variable
# for the tool subprocesses below.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
CAPTURE = TOOLS / "kb_capture.py"
QUERY = TOOLS / "kb_query.py"

MIN_PYTHON = (3, 9)          # declared in plugin.json's description and both SKILL.md files

failures = 0
ran = 0          # derived, never written by hand: the summary below counts what ACTUALLY ran,
                 # so adding a case cannot leave the total behind. It did once -- six cases
                 # printed under a hardcoded "5 of 5".


def check(cond, msg):
    global failures, ran
    ran += 1
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures += 1


def run(*cmd, **kw):
    return subprocess.run([sys.executable, *[str(c) for c in cmd]],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120, **kw)


def snapshot(root):
    """relative path -> sha256 for every file under root, and '<dir>' for every folder, so a
    new empty folder counts as a change too."""
    return {p.relative_to(root).as_posix():
            (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>")
            for p in sorted(root.rglob("*"))}


ap = argparse.ArgumentParser(description="aor-kb install smoke test")
ap.add_argument("--root", help="write the config-root check here instead of a temporary "
                               "directory. The real config root is never touched either way.")
args = ap.parse_args()


# SMOKE-5 first: everything below reports confusingly on an interpreter that is too old,
# so say so plainly before the failures start rather than after.
check(sys.version_info[:2] >= MIN_PYTHON,
      "SMOKE-5: this interpreter is Python %d.%d or later [running %d.%d from %s]"
      % (MIN_PYTHON + sys.version_info[:2] + (sys.executable,)))

# SMOKE-1 -- both tools start. This is also the dependency check: a missing or broken
# strictyaml exits 3 here with a message naming what to install, not a traceback.
_help = {}
for _name, _tool in (("kb-capture", CAPTURE), ("kb-query", QUERY)):
    _help[_name] = run(_tool, "--help") if _tool.is_file() else None
check(all(r is not None and r.returncode == 0 for r in _help.values()),
      "SMOKE-1: both tools run --help and exit 0 [%s]"
      % ", ".join("%s=%s" % (n, "missing" if r is None else r.returncode)
                  for n, r in _help.items()))
for _name, _r in _help.items():
    if _r is not None and _r.returncode == 3:
        print("       %s exited 3: the pinned dependency is missing. Install it with:" % _name)
        print("       %s -m pip install -r %s" % (sys.executable, ROOT / "requirements.txt"))

# SMOKE-6 -- every skill this plugin ships actually arrived. A packaging step that dropped a
# skill folder produces an install that loads, reports enabled, and is missing a whole entry
# point, with nothing else here noticing: SMOKE-1 exercises the TOOLS, and the tools are shared
# by all three skills. Names rather than a count, so a dropped folder is named in the failure.
_EXPECTED_SKILLS = ("aor-kb-query", "aor-kb-capture", "aor-kb-setup")
_missing_skills = [n for n in _EXPECTED_SKILLS
                   if not (ROOT / "skills" / n / "SKILL.md").is_file()]
check(not _missing_skills,
      "SMOKE-6: all three shipped skills are present -- %s. Three, counted from that list "
      "[missing=%r]" % (", ".join(_EXPECTED_SKILLS), _missing_skills))

with TemporaryDirectory() as _tmp:
    _tmp = Path(_tmp)

    # SMOKE-2 -- init builds a config root from nothing. --root keeps this off the real one:
    # the config root is deliberately not environment-overridable, so a flag is the only way
    # to prove the behaviour without creating the operator's actual state.
    _cfg = Path(args.root).resolve() if args.root else (_tmp / "config-root")
    _init = run(CAPTURE, "init", "--root", _cfg)
    _state = ("instances.yml", "workspaces.jsonl", "work-paths.txt")
    _present = [n for n in _state if (_cfg / n).is_file()]
    check(_init.returncode == 0 and len(_present) == len(_state),
          "SMOKE-2: init creates a config root and its three state files -- %s. Three, "
          "counted from that list; digest.salt is the fourth file the root ends up holding "
          "but it is written lazily on first capture, not by init [rc=%s present=%r]"
          % (", ".join(_state), _init.returncode, _present))

    # SMOKE-3 -- a one-claim knowledge base serves exactly that claim. This is the only
    # assertion that exercises the read path end to end: config, lint profile, parse, filter,
    # trust label.
    _kb = _tmp / "instance" / "kb"
    (_kb / "smoke").mkdir(parents=True)
    # Guarded: a partial install can leave this absent, and reading it unguarded killed
    # the whole script with a traceback -- SMOKE-3 and SMOKE-4 never ran and no summary
    # printed. A smoke test that can die silently is worse than one that fails loudly.
    _profile = ROOT / ".kb-lint.yml"
    if _profile.is_file():
        (_tmp / "instance" / ".kb-lint.yml").write_text(
            _profile.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    else:
        check(False, "SMOKE-3: the shipped lint profile .kb-lint.yml is missing from the "
                     "plugin root, so a knowledge base cannot be read at all")
    (_kb / "index.md").write_text('---\nokf_version: "0.1"\n---\n\n# smoke\n\n'
                                  "- smoke/only.md — the single claim\n",
                                  encoding="utf-8", newline="\n")
    (_kb / "smoke" / "only.md").write_text(
        "---\ntype: concept\nevidence:\n  profile: okf-e/0.1\n  review: 2099-01-01\n---\n\n"
        "# The one claim\n\n"
        "- [fact] This installation can serve a claim. "
        '{id: c-smk1, v: ran-tool 2026-01-01, conf: high, src: "smoke-test.py"}\n',
        encoding="utf-8", newline="\n")
    if _profile.is_file():
        _before = snapshot(_tmp / "instance")
        _q = run(QUERY, _kb, "--quiet")
        _served = [ln for ln in _q.stdout.splitlines() if ln.startswith("[")]
        check(_q.returncode == 0 and len(_served) == 1 and "c-smk1" in _served[0],
              "SMOKE-3: a one-claim knowledge base serves exactly that claim "
              "[rc=%s served=%d %r]" % (_q.returncode, len(_served), _served[:2]))

        # SMOKE-7 -- a query is read-only on disk: the knowledge base holds exactly the same
        # files, byte for byte, after the query as before it. Versions up to 0.1.6 appended
        # every query's command line to a log inside the knowledge base; this is the check
        # that no write of that kind has come back.
        _after = snapshot(_tmp / "instance")
        _changed = sorted(k for k in set(_before) | set(_after)
                          if _before.get(k) != _after.get(k))
        check(_q.returncode == 0 and not _changed,
              "SMOKE-7: a query leaves the knowledge base unchanged [rc=%s changed=%r]"
              % (_q.returncode, _changed))

# SMOKE-4 -- P6's guard. The config root is resolved through Windows APIs that read the
# process token; there is no environment-derived fallback, by design. So the module must
# REFUSE to import off-Windows rather than quietly resolving something else. Checked in a
# subprocess with a patched sys.platform, because this one cannot be tested by being true.
_probe = (
    "import sys\n"
    "sys.platform = 'linux'\n"
    "sys.path.insert(0, r'%s')\n"
    "try:\n"
    "    import kb_boundary\n"
    "except OSError:\n"
    "    print('RAISED')\n"
    "except Exception as exc:\n"
    "    print('WRONG:' + type(exc).__name__)\n"
    "else:\n"
    "    print('NO-RAISE')\n" % TOOLS)
_p6 = run("-c", _probe)
check(_p6.stdout.strip() == "RAISED",
      "SMOKE-4: importing kb_boundary on a non-Windows platform raises rather than "
      "resolving a config root some other way (P6) [got %r]" % (_p6.stdout.strip(),))

print()
if failures:
    print("RED -- %d of %d checks failed. This install is not ready to use." % (failures, ran))
else:
    print("GREEN -- %d of %d. The plugin runs on this machine." % (ran, ran))
sys.exit(1 if failures else 0)
