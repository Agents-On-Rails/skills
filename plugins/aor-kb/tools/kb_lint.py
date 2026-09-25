#!/usr/bin/env python3
"""kb-lint -- OKF-E v0.1 write-time linter (pilot P1, PoC).

Normative spec: okf-e-profile.md. Config: .kb-lint.yml (walk-up from target).
Skeleton lifted from agentskills/agentskills (skills-ref) @ 0c0c567, validator.py:
closed ALLOWED set, per-field validators returning error lists, parse/validate split.

Checks L1-L8; G = gating (hard reject), F = fixable (`kb-lint fix` repairs).
Exit codes are the API: 0 clean - 1 gating errors (for `fix`, also: a write it refused -- a file whose
working tree differs from its staged copy in hook mode, or a claim whose live brace already holds another
value -- or a gating error it introduced into a file it followed a link into; under `check --changed
--hook`, also any fixable finding still present) - 2 usage/config error - 3 a required dependency is not
installed.
Subcommands: check (default) / fix / strip / stats.
"""

import argparse
import atexit
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

# No bytecode (#44). This module imports no siblings, so as an entry point it writes
# nothing today and this line is a no-op -- it is here because ANY of the three CLIs can be
# the first one you run, and the guard belongs in each of them rather than in whichever one
# happens to import the others. The same reasoning put it in aor-comm's smoke-test.py as
# well as md2teams.py (#46). See kb_query.py for why a .pyc in this tree is a leak.
sys.dont_write_bytecode = True

try:
    from strictyaml import dirty_load
except ImportError as _exc:  # aor-kb dependency guard -- K3
    # ImportError, not only ModuleNotFoundError: a present-but-broken strictyaml -- a
    # truncated install, a renamed symbol, a module shadowing it on sys.path -- raises the
    # plain parent class, and that traceback is exactly what this guard exists to replace.
    # Line 1 always names the DECLARED dependency, the one requirements.txt actually pins.
    # _exc.name can be a transitive module instead (a missing python-dateutil reports
    # "dateutil"), so it is reported separately rather than as the thing to install.
    # The literal text is ASCII, which is all these tools' stderr is guaranteed to carry at
    # import time; the interpolated paths are whatever the filesystem holds.
    _req = Path(__file__).resolve().parent.parent / "requirements.txt"
    _also = "" if _exc.name in (None, "strictyaml") else "  missing module: %s\n" % _exc.name
    sys.stderr.write(
        "aor-kb: cannot import required dependency 'strictyaml' (%s)\n"
        "%s"
        "  interpreter:  %s\n"
        "  declared in:  %s\n"
        "  install with: \"%s\" -m pip install -r \"%s\"\n"
        % (type(_exc).__name__, _also, sys.executable, _req, sys.executable, _req)
    )
    raise SystemExit(3)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PAIR_RE = re.compile(r"^([a-z][a-z-]*):\s+(.+)$", re.DOTALL)
KIND_RE = re.compile(r"^\[([a-z-]+)\]\s*(.*)$", re.DOTALL)
ID_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"
EVIDENCE_KEYS = {"profile", "defaults", "review"}  # closed set of evidence: subkeys


# ---------------------------------------------------------------- config

def find_config(start: Path):
    for d in [start] + list(start.parents):
        cand = d / ".kb-lint.yml"
        if cand.is_file():
            return cand
    return None


def load_config(path: Path):
    try:
        data = dirty_load(path.read_text(encoding="utf-8"), allow_flow_style=True).data
    except Exception as e:  # report type only (standing rule: never echo raw exc)
        die(f"config {path} failed to parse ({type(e).__name__})")
    # #60: the required keys are read through _require, so a config that PARSES but omits one
    # dies NAMING it at exit 2. They were read directly -- outside the try above -- so a
    # hand-made .kb-lint.yml raised a bare KeyError traceback, contradicting this payload's
    # own principle of exiting "naming exactly what is missing, rather than failing on an
    # ImportError traceback", on a path the README now documents. Optional keys keep their
    # .get() defaults and are deliberately NOT required.
    def _require(dotted):
        cur = data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                die(f"config {path} is missing required key '{dotted}' -- copy the shipped "
                    ".kb-lint.yml template to your KB repo root and set instance: on the copy")
            cur = cur[part]
        return cur

    # kb_path decides where a capture WRITES as well as what a query reads, now that every
    # consumer honours it. A parent hop or an absolute path moved captures out of the repository
    # the boundary had just verified, so a value that can name a folder outside the repository is
    # refused here, once, for every consumer. A junction under the repository is invisible to a
    # textual rule; each consumer that resolves the folder refuses that case itself.
    kb_path_raw = str(data.get("kb_path", "kb/"))
    kb_path = kb_path_raw.replace("\\", "/")
    if (kb_path.startswith("/") or Path(kb_path).is_absolute() or Path(kb_path).drive
            or ".." in Path(kb_path).parts or not kb_path.strip("/")):
        die(f"config {path}: kb_path '{kb_path_raw}' must be a relative folder inside the "
            "knowledge base repository -- no absolute path, no drive, no '..'")

    cfg = {
        "dir": path.parent,
        "profile": _require("profile"),
        "kb_path": kb_path.strip("/"),
        "kinds": _require("enums.kind"),
        "methods": _require("enums.verified_by"),
        "confs": _require("enums.conf"),
        "claim_keys": _require("claim_keys"),
        "defaults_inheritable": data.get("defaults_inheritable", ["src"]),
        "status_enum": data.get("status_enum", []),
        "id_format": re.compile(_require("id_format")),
        "id_prefix": data.get("id_assign_prefix", "c-"),
        "reserved": data.get("reserved_files", ["index.md", "log.md"]),
        "ledger_type": data.get("ledger_type", "Ledger"),
    }
    cfg["verified_grades"] = [m for m in cfg["methods"] if m != "unverified"]
    return cfg


def die(msg):
    print(f"kb-lint: {msg}", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------- errors

class Err:
    def __init__(self, file, line, check, cls, msg, fix=None):
        self.file, self.line, self.check, self.cls, self.msg, self.fix = (
            file, line, check, cls, msg, fix)


def label(claim):
    kid = claim.fields.get("id", "?")
    k = claim.kind if claim.kind_valid else "?"
    return f"[{k}] {kid}:"


# ---------------------------------------------------------------- parsing

def split_frontmatter(text):
    """Return (fm_text|None, body_lines, body_start_lineno)."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return ("\n".join(lines[1:i]), lines[i + 1:], i + 2)
    return (None, lines, 1)


class Claim:
    """One parsed top-level list item from a claim file."""

    def __init__(self, file, line):
        self.file, self.line = file, line   # first physical line (1-based)
        self.text_lines = []                # [(lineno, text, raw_col_offset)]
        self.kind, self.kind_valid = None, False
        self.text = ""                      # claim text (brace removed)
        self.fields, self.order = {}, []    # explicit brace pairs
        self.brace_open = None              # (lineno, RAW col) of '{'
        self.brace_close = None             # (lineno, RAW col) of final '}'


def scan_brace(text_lines):
    """Join an item's text lines (single-space) into (joined, posmap), where posmap maps each
    flat index -> (lineno, raw column). Brace location is done by find_meta_brace on `joined`;
    this only assembles the text + position map (ARCH-005/CODE-008: the old opens/closes were
    computed here but discarded by the caller)."""
    flat, posmap = [], []
    for lineno, text, off in text_lines:
        for col, ch in enumerate(text):
            flat.append(ch)
            posmap.append((lineno, off + col))
        flat.append(" ")  # line join
        posmap.append((lineno, off + len(text)))
    joined = "".join(flat).rstrip()
    return joined, posmap


def split_pairs(s):
    parts, buf, in_q = [], [], False
    for ch in s:
        if ch == '"':
            in_q = not in_q
        if ch == "," and not in_q:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_claim_items(body_lines, body_start, relpath, errs, cfg):
    """Assemble top-level list items; parse kind + brace. Prose/nested bullets inert."""
    claims, cur = [], None
    for i, raw in enumerate(body_lines):
        lineno = body_start + i
        if raw.startswith("- "):
            if cur:
                claims.append(cur)
            cur = Claim(relpath, lineno)
            cur.text_lines = [(lineno, raw[2:], 2)]
        elif cur and raw.startswith("  ") and raw.strip():
            if raw.lstrip().startswith("- "):
                continue  # nested sub-bullet: inert (spec parse rule 5)
            cur.text_lines.append((lineno, raw.strip(),
                                   len(raw) - len(raw.lstrip())))
        else:
            if cur:
                claims.append(cur)
                cur = None
    if cur:
        claims.append(cur)

    for c in claims:
        lineno, first, off = c.text_lines[0]
        m = KIND_RE.match(first)
        if not m:
            errs.append(Err(relpath, c.line, "L3", "G",
                            "claim has no [kind] tag -- untyped claims cannot enter the "
                            f"store (choose: {'  '.join(cfg['kinds'])})"))
            continue
        c.kind = m.group(1)
        consumed = len(first) - len(m.group(2))
        c.text_lines[0] = (lineno, m.group(2), off + consumed)
        if c.kind not in cfg["kinds"]:
            errs.append(Err(relpath, c.line, "L3", "G",
                            f"kind '{c.kind}' not in the closed enum "
                            f"(choose: {'  '.join(cfg['kinds'])}; extension goes through "
                            ".kb-lint.yml, gate G6)"))
        else:
            c.kind_valid = True
        _extract_brace(c, relpath, errs, cfg)
    return claims


def find_meta_brace(joined):
    """The trailing metadata brace {...}, located by a quote-aware BACKWARD scan from the
    final '}'. Prose before it is inert -- including an ODD number of `"` or a stray `{`/`}`
    in the claim text, which the old forward whole-line scan mis-handled (the prose quote
    flipped the in-string state and swallowed the real brace; handover §5). Within-brace
    quotes are balanced, so toggling `in_q` while walking left correctly skips a `}`/`{`
    sitting inside a quoted value. Returns (open_idx, close_idx) or None (no/unbalanced brace)."""
    if not joined.endswith("}"):
        return None
    depth, in_q = 0, False
    for i in range(len(joined) - 1, -1, -1):
        ch = joined[i]
        if ch == '"':
            in_q = not in_q
        elif not in_q:
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    return i, len(joined) - 1
    return None  # unbalanced -> the trailing '}' is plain text (rule 1)


def _brace_blind_match(joined):
    """Backward brace match IGNORING quotes -- structural pairing only. Lets _extract_brace tell
    an unterminated quote INSIDE an intended metadata brace (a real '{...}' pair that the
    quote-aware find_meta_brace rejects) apart from genuine no-brace prose (no structural '{'
    at all). Returns (open_idx, close_idx) or None."""
    if not joined.endswith("}"):
        return None
    depth = 0
    for i in range(len(joined) - 1, -1, -1):
        ch = joined[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                return i, len(joined) - 1
    return None


def _extract_brace(c, relpath, errs, cfg):
    joined, posmap = scan_brace(c.text_lines)
    meta = find_meta_brace(joined)
    if meta is None:
        # find_meta_brace failed but a STRUCTURAL '{...}' pair exists with an odd (unterminated)
        # quote inside -> the quote-aware scan flipped in_q and skipped the real '{' (CODE-002/
        # TEST-004). Emit the targeted L2 instead of a vague downstream matrix error; still fails
        # closed. Genuine no-brace prose (no structural '{', e.g. 'she said } done}') has no blind
        # match and falls through unchanged. PARTIAL cover by design: a few deeper-malformed
        # braces also fall through (a '}' inside the FIRST quoted value defeats the blind match;
        # nested quotes can leave an EVEN count) -- those get the generic L4 matrix error instead
        # of this targeted message, but every such line still fails CLOSED (fuzz-verified, panel
        # 2026-07-10: 0 usable fields in any fallthrough).
        blind = _brace_blind_match(joined)
        if blind is not None and joined[blind[0]:blind[1] + 1].count('"') % 2:
            errs.append(Err(relpath, c.line, "L2", "G",
                            "unterminated quoted value in the metadata brace -- every '\"' must "
                            "be closed (quote a value that contains , or })"))
            c.text = joined[:blind[0]].strip()
            return
        c.text = joined  # no brace: legal iff defaults+matrix satisfied (rule 4)
        return
    open_seq, end_seq = meta
    c.brace_open = posmap[open_seq]
    c.brace_close = posmap[end_seq]
    c.text = joined[:open_seq].strip()
    body = joined[open_seq + 1:end_seq]
    for pair in split_pairs(body):
        m = PAIR_RE.match(pair)
        if not m:
            errs.append(Err(relpath, c.line, "L2", "G",
                            f"malformed pair '{pair}' -- grammar is 'key: value' "
                            "(quote the value if it contains , or })"))
            continue
        k, v = m.group(1), m.group(2).strip()
        if k not in cfg["claim_keys"]:
            errs.append(Err(relpath, c.line, "L2", "G",
                            f"unknown brace key '{k}' -- the whitelist is closed (typo "
                            "defence); extension goes through .kb-lint.yml, not ad-hoc keys"))
            continue
        if v.startswith('"'):
            if len(v) < 2 or not v.endswith('"'):
                errs.append(Err(relpath, c.line, "L2", "G",
                                f"unterminated quoted value for '{k}'"))
                continue
            v = v[1:-1]
        if k in c.fields:
            errs.append(Err(relpath, c.line, "L2", "G", f"duplicate key '{k}'"))
            continue
        c.fields[k] = v
        c.order.append(k)


# ---------------------------------------------------------------- per-file checks

def classify(fm, cfg):
    if fm and fm.get("type") == cfg["ledger_type"]:
        return "ledger"
    return "claim"


def check_file(path: Path, cfg, text=None):
    """Parse + L1..L6 for one file. Returns (claims, errs, meta). `text` overrides the
    on-disk content (kb_capture --dry-run lints a candidate appended in-memory to the
    real file -- same checks, no write; the file need not exist on disk)."""
    try:
        relpath = path.relative_to(cfg["dir"]).as_posix()
    except ValueError:
        die(f"{path} is outside the config root {cfg['dir']} -- kb-lint expects "
            ".kb-lint.yml at the instance root (next to kb/)")
    errs, claims = [], []
    if text is None:
        text = path.read_text(encoding="utf-8")
    fm_text, body_lines, body_start = split_frontmatter(text)
    fm = None
    if fm_text is not None:
        try:
            fm = dirty_load(fm_text, allow_flow_style=True).data
        except Exception as e:
            errs.append(Err(relpath, 1, "L1", "G",
                            f"frontmatter YAML failed to parse ({type(e).__name__})"))
            return claims, errs, {"class": "claim"}
    name = path.name
    kb_root = cfg["dir"] / cfg["kb_path"]

    # reserved files (OKF SS3.1): index.md / log.md
    if name in cfg["reserved"]:
        if name == "index.md" and path.parent == kb_root:
            if not fm or not str(fm.get("okf_version") or "").strip():
                errs.append(Err(relpath, 1, "L1", "G",
                                "bundle-root index.md must declare okf_version in a "
                                "frontmatter block (OKF SPEC SS11; canonical home of "
                                "the pin)"))
        elif fm is not None:
            errs.append(Err(relpath, 1, "L1", "G",
                            f"reserved file '{name}' must not carry frontmatter "
                            "(OKF reserved name; only the bundle-root index.md "
                            "okf_version block is permitted)"))
        return claims, errs, {"class": "reserved"}

    if fm is None:
        errs.append(Err(relpath, 1, "L1", "G",
                        "missing frontmatter -- every non-reserved .md under kb/ needs "
                        "a frontmatter block with type: (OKF SS9) and, for claim files, "
                        "evidence.profile"))
        return claims, errs, {"class": "claim"}
    cls = classify(fm, cfg)
    if not str(fm.get("type") or "").strip():
        errs.append(Err(relpath, 1, "L1", "G",
                        "frontmatter needs a non-empty type: (OKF SS9 conformance rule 2)"))

    if cls == "ledger":
        if "evidence" in fm:
            errs.append(Err(relpath, 1, "L1", "G",
                            f"type: {cfg['ledger_type']} files are append-only trails, "
                            "not claim stores -- remove the evidence: block (spec SS2)"))
        return claims, errs, {"class": cls}

    # claim file: L1 evidence shape
    ev = fm.get("evidence")
    if not isinstance(ev, dict) or not ev.get("profile"):
        errs.append(Err(relpath, 1, "L1", "G",
                        "claim files must declare evidence.profile -- add the evidence: "
                        f"block (or type: {cfg['ledger_type']} if this is a trail file); "
                        "a file without it would be silently inert (spec SS2)"))
        return claims, errs, {"class": cls}
    if ev["profile"] != cfg["profile"]:
        errs.append(Err(relpath, 1, "L1", "G",
                        f"unknown profile '{ev['profile']}' -- this linter implements "
                        f"{cfg['profile']}"))
    for k in ev:
        if k not in EVIDENCE_KEYS:
            errs.append(Err(relpath, 1, "L1", "G",
                            f"unknown evidence: subkey '{k}' (allowed: "
                            f"{sorted(EVIDENCE_KEYS)})"))
    defaults = ev.get("defaults") or {}
    for k in defaults:
        if k not in cfg["defaults_inheritable"]:
            errs.append(Err(relpath, 1, "L1", "G",
                            f"defaults: may carry {cfg['defaults_inheritable']} only "
                            f"(d-defaults) -- '{k}' is a per-claim affirmation; move it "
                            "onto each claim"))
    if "review" in ev and not ISO_RE.match(str(ev["review"])):
        errs.append(Err(relpath, 1, "L1", "G",
                        "evidence.review must be an ISO date (re-verify-by)"))

    claims = parse_claim_items(body_lines, body_start, relpath, errs, cfg)
    default_src = "src" in defaults
    for c in claims:
        if c.kind_valid:
            check_claim(c, default_src, errs, cfg)
    return claims, errs, {"class": cls}


def check_claim(c, default_src, errs, cfg):
    """L4 per-kind matrix + L5 evidence asymmetry + L6 value validity."""
    f = c.fields
    where = (c.file, c.line)
    method, vdate = None, None

    # L6: v: parses as method [iso-date]
    if "v" in f:
        parts = f["v"].split()
        method = parts[0]
        if method not in cfg["methods"]:
            errs.append(Err(*where, "L6", "G",
                            f"{label(c)} v method '{method}' not in {cfg['methods']}"))
            method = None
        elif method == "unverified":
            if len(parts) > 1:
                errs.append(Err(*where, "L6", "G",
                                f"{label(c)} v: unverified carries no date -- there was "
                                "no verification event to date"))
        else:
            if len(parts) != 2 or not ISO_RE.match(parts[1]):
                errs.append(Err(*where, "L6", "G",
                                f"{label(c)} v: '{f['v']}' -- active methods fuse "
                                "method + ISO date (a verification is an EVENT): "
                                "v: ran-tool 2026-07-07"))
            else:
                vdate = parts[1]

    # L4: per-kind required/forbidden (the matrix is the semantic source of truth)
    if c.kind == "fact":
        if "v" not in f:
            errs.append(Err(*where, "L4", "G",
                            f"{label(c)} facts carry v: (how you know + when) -- record "
                            "the verification event or admit v: unverified"))
    elif c.kind == "decision":
        for req in ("date", "status"):
            if req not in f:
                errs.append(Err(*where, "L4", "G",
                                f"{label(c)} decisions are ratified, not verified -- "
                                "use date: + status: (proposed/accepted; superseded once "
                                "replaced)"))
                break
        if "v" in f:
            errs.append(Err(*where, "L4", "G",
                            f"{label(c)} v: is forbidden on decisions -- decisions are "
                            "ratified, not verified; use date: + status:"))
    elif c.kind == "procedure":
        if "v" not in f:
            errs.append(Err(*where, "L4", "G",
                            f"{label(c)} procedures carry v: = last-known-working "
                            "(method + date)"))
    elif c.kind == "lesson":
        for req in ("seen", "confirmed"):
            if req not in f:
                errs.append(Err(*where, "L4", "G",
                                f"{label(c)} lessons are validated by recurrence -- "
                                "evidence is seen: (count) + confirmed: (date), "
                                f"and '{req}' is missing"))
        for k in ("v", "conf"):
            if k in f:
                errs.append(Err(*where, "L4", "G",
                                f"{label(c)} {k}: is forbidden on lessons -- recurrence "
                                "(seen:/confirmed:) is the evidence, not verification"))

    # L5: evidence asymmetry (fact-scoped full force -- spec SS7; matrix wins)
    if c.kind == "fact" and method:
        if method in cfg["verified_grades"]:
            if "src" not in f and not default_src:
                errs.append(Err(*where, "L5", "G",
                                f"{label(c)} v is '{method}' but 'src' is missing -- "
                                "verified-grade claims REQUIRE a source (evidence "
                                "asymmetry; add src: or downgrade v: to unverified)"))
            if "conf" not in f:
                errs.append(Err(*where, "L5", "G",
                                f"{label(c)} v is '{method}' but 'conf' is missing -- "
                                "verified-grade claims REQUIRE a confidence (GRADE-4: "
                                f"{'/'.join(cfg['confs'])})"))
        else:  # unverified must be visibly naked (explicit keys only)
            for k in ("src", "conf"):
                if k in f:
                    errs.append(Err(*where, "L5", "G",
                                    f"{label(c)} v: unverified must be BARE -- either "
                                    f"verify (upgrade v:) or drop {k}: (bare admission "
                                    "is visibly bare)"))
    # universal: deprecated needs its tombstone reason (any kind)
    if f.get("status") == "deprecated" and "reason" not in f:
        errs.append(Err(*where, "L5", "G",
                        f"{label(c)} status: deprecated requires reason: -- the "
                        "tombstone must say why, so nobody re-adds it"))
    if "reason" in f and f.get("status") != "deprecated":
        errs.append(Err(*where, "L4", "G",
                        f"{label(c)} reason: rides only with status: deprecated -- "
                        "for decision rationale use why:"))

    # L6: remaining value validity
    today = date.today().isoformat()
    for k in ("date", "confirmed"):
        if k in f:
            if not ISO_RE.match(f[k]):
                errs.append(Err(*where, "L6", "G",
                                f"{label(c)} {k}: must be an ISO date"))
            elif f[k] > today:
                errs.append(Err(*where, "L6", "G",
                                f"{label(c)} {k}: {f[k]} is in the future -- event "
                                "dates record what happened"))
    if vdate and vdate > today:
        errs.append(Err(*where, "L6", "G",
                        f"{label(c)} v: date {vdate} is in the future"))
    if "until" in f:
        if not ISO_RE.match(f["until"]):
            errs.append(Err(*where, "L6", "G",
                            f"{label(c)} until: must be an ISO date (expiry)"))
        elif vdate and f["until"] <= vdate:
            errs.append(Err(*where, "L6", "G",
                            f"{label(c)} until: must be strictly later than the "
                            f"v: date ({vdate})"))
    if "conf" in f and f["conf"] not in cfg["confs"]:
        errs.append(Err(*where, "L6", "G",
                        f"{label(c)} conf '{f['conf']}' not in GRADE-4 {cfg['confs']}"))
    if "seen" in f and (not f["seen"].isdigit() or int(f["seen"]) < 1):
        errs.append(Err(*where, "L6", "G",
                        f"{label(c)} seen: must be a positive integer"))
    if "status" in f:
        st = f["status"]
        if st not in cfg["status_enum"]:
            errs.append(Err(*where, "L6", "G",
                            f"{label(c)} status '{st}' not in {cfg['status_enum']} "
                            "(implicit value is active -- omit the key)"))
        elif st in ("proposed", "accepted") and c.kind != "decision":
            errs.append(Err(*where, "L6", "G",
                            f"{label(c)} status: {st} is the decision ratification "
                            "lifecycle -- not valid on a " + c.kind))
        elif st == "promoted" and c.kind != "lesson":
            errs.append(Err(*where, "L6", "G",
                            f"{label(c)} status: promoted marks a lesson lifted to the "
                            "always-on layer -- not valid on a " + c.kind))
        if st == "superseded" and "superseded-by" not in f:
            errs.append(Err(*where, "L6", "G",
                            f"{label(c)} status: superseded requires superseded-by: "
                            "(who replaced it)"))


# ---------------------------------------------------------------- corpus checks

def chain_head(claim, by_id):
    """Follow superseded-by: links from `claim` to the claim nothing has replaced yet."""
    node, seen = claim, set()
    while node.fields.get("superseded-by") and node.fields.get("id") not in seen:
        seen.add(node.fields.get("id"))
        nxt = by_id.get(node.fields["superseded-by"])
        if nxt is None:
            break
        node = nxt
    return node


def corpus_checks(all_claims, errs, cfg, pool=None):
    """L7 referential integrity + L8 id uniqueness, corpus-wide.

    `all_claims` is the REPORT set: the claims of the files the caller named or staged --
    every error is about one of them. `pool` is the RESOLUTION set: every other claim in
    the corpus, which links may point at and ids may collide with, but which is never
    reported here (issue 73, ruling R3 of 2026-09-24: resolve corpus-wide in every check;
    keep the gate on the touched/staged set). A link whose target lives in another file is
    therefore a link, not a dangling id; a link to an id that exists nowhere still gates.

    Supersession (issue 70, rulings R1/R2; issue 74): a claim that carries a back-link but
    reads any status other than superseded is a FIXABLE finding -- the flip is entailed by
    the link and `fix` writes it, unless that back-link is itself refused, dangling or
    self-referential (then nothing is flipped). The link shapes below are GATING on the
    LINKING claim, raised here so the fixer never writes onto their target:
      - superseding a deprecated claim, whether or not the pair is complete (a tombstone has
        no successor); likewise a deprecated claim that carries superseded-by: itself, since
        completing that pair would write exactly the refused link;
      - superseding a claim another claim already supersedes: with a back-link the message
        names the chain head; with none, every claim linking to that target gates (S1), and
        so does the mirror, two claims naming one successor that links back to neither
        (a single-valued key cannot hold a fork). The second link is looked for in the pool
        as well as the report set, so a fork is seen whichever file its other end lives in.
    """
    by_id = {}
    flagged = set()   # ids whose FIRST claim has already been reported (A2)
    for c in all_claims:
        kid = c.fields.get("id")
        if not kid:
            errs.append(Err(c.file, c.line, "L8", "F",
                            f"[{c.kind}] claim has no id -- run `kb-lint fix` to assign "
                            "one (beads-style, never hand-typed)", fix=("id", c)))
            continue
        if not cfg["id_format"].match(kid):
            errs.append(Err(c.file, c.line, "L8", "G",
                            f"id '{kid}' does not match the id format "
                            f"({cfg['id_format'].pattern})"))
        if kid in by_id:
            o = by_id[kid]
            # A2: flag BOTH claims, not only the later one. Only the second was
            # reported, so kb_query tainted only the second and served the FIRST with a
            # clean label -- which of two claims sharing an id reached the consumer was
            # decided by sorted(rglob) file order, silently, at read time. Erroring on
            # both makes an ambiguous id serve NOTHING rather than something arbitrary.
            if kid not in flagged:
                errs.append(Err(o.file, o.line, "L8", "G",
                                f"id '{kid}' is also used at {c.file}:{c.line} -- ids "
                                "must be corpus-unique; run `kb-lint fix` to assign "
                                "fresh ids"))
                flagged.add(kid)
            errs.append(Err(c.file, c.line, "L8", "G",
                            f"id '{kid}' already used at {o.file}:{o.line} -- ids must "
                            "be corpus-unique; run `kb-lint fix` to assign fresh ids"))
        else:
            by_id[kid] = c
    for p in pool or ():
        kid = p.fields.get("id")
        if not kid or not cfg["id_format"].match(kid):
            continue
        if kid in by_id:
            c = by_id[kid]
            if c in all_claims:   # a NAMED claim collides with one in a file not named
                errs.append(Err(c.file, c.line, "L8", "G",
                                f"id '{kid}' already used at {p.file}:{p.line} -- ids must "
                                "be corpus-unique; run `kb-lint fix` to assign fresh ids"))
            continue              # first-wins among pool claims; their own check reports them
        by_id[kid] = p

    # Who links to each target, over the report set AND the pool: a fork onto a target that
    # carries no back-link is visible only here, and its second link may live in a file the
    # caller did not name (A3, S1 of the 2026-09-24 build review).
    linkers = {}
    for c in list(all_claims) + list(pool or ()):
        for key in ("supersedes", "superseded-by"):
            if c.fields.get(key):
                linkers.setdefault((key, c.fields[key]), []).append(c)

    for c in all_claims:
        cid = c.fields.get("id")
        sb_refused = False    # the claim's own superseded-by: is refused, dangling or self (A4)
        for key, recip in (("supersedes", "superseded-by"),
                           ("superseded-by", "supersedes")):
            tgt = c.fields.get(key)
            if not tgt:
                continue
            refuse = key == "superseded-by"
            if tgt == cid:
                errs.append(Err(c.file, c.line, "L7", "G",
                                f"{label(c)} self-supersession -- a claim cannot "
                                f"{key.replace('-', ' ')} itself"))
                sb_refused |= refuse
                continue
            other = by_id.get(tgt)
            if other is None:
                errs.append(Err(c.file, c.line, "L7", "G",
                                f"{label(c)} {key} target '{tgt}' not found in corpus "
                                "-- link to an existing claim id (retire, never delete)"))
                sb_refused |= refuse
                continue
            existing = other.fields.get(recip)
            # The refusals come BEFORE the id guard: a candidate at the write path has no id
            # yet (it is assigned after the append), and must still be refused here. The two
            # tombstone refusals come before the pair-complete check too, so a complete legacy
            # pair onto a deprecated claim is refused like a new link (A10).
            if key == "supersedes" and other.fields.get("status") == "deprecated":
                errs.append(Err(c.file, c.line, "L7", "G",
                                f"{label(c)} supersedes '{tgt}', which is deprecated "
                                "(never-true) -- a tombstone has no successor: write the "
                                "corrected claim WITHOUT a link, and leave the tombstone"))
                continue
            if key == "superseded-by" and c.fields.get("status") == "deprecated":
                errs.append(Err(c.file, c.line, "L7", "G",
                                f"{label(c)} is deprecated but carries superseded-by: {tgt} -- a "
                                "tombstone has no successor: remove superseded-by: here (and any "
                                f"supersedes: {cid or 'link'} on {tgt}); the tombstone stays"))
                sb_refused = True
                continue
            if cid and existing == cid:
                continue          # the pair is complete
            if existing:          # the target already links to a DIFFERENT claim
                if key == "supersedes":
                    head = chain_head(other, by_id).fields.get("id", existing)
                    errs.append(Err(c.file, c.line, "L7", "G",
                                    f"{label(c)} supersedes '{tgt}', which is already "
                                    f"superseded by {existing} -- a claim has one successor: "
                                    f"supersede the chain head {head} instead"))
                else:
                    errs.append(Err(c.file, c.line, "L7", "G",
                                    f"{label(c)} superseded-by '{tgt}', but {tgt} supersedes "
                                    f"{existing} -- a claim supersedes one claim: correct "
                                    "the link on one side"))
                sb_refused |= refuse
                continue
            others = [o for o in linkers.get((key, tgt), ()) if o is not c]
            if others:            # a fork onto a target that links back to none of them (S1)
                names = ", ".join(o.fields.get("id") or f"the id-less claim at {o.file}:{o.line}"
                                  for o in others)
                if key == "supersedes":
                    errs.append(Err(c.file, c.line, "L7", "G",
                                    f"{label(c)} supersedes '{tgt}', and so does {names} -- a "
                                    f"claim has one successor and {tgt} links back to none of "
                                    "them: keep one link, and make the other supersede the "
                                    "claim that replaced it"))
                else:
                    errs.append(Err(c.file, c.line, "L7", "G",
                                    f"{label(c)} superseded-by '{tgt}', and so is {names} -- a "
                                    f"claim supersedes one claim and {tgt} links back to none of "
                                    "them: correct the link on one side"))
                sb_refused |= refuse
                continue
            if not cid:
                continue          # its own id is assigned first; the reciprocal follows
            errs.append(Err(other.file, other.line, "L7", "F",
                            f"{label(other)} missing reciprocal {recip}: {cid} -- run "
                            "`kb-lint fix`", fix=("reciprocal", other, recip, cid)))
        sb = c.fields.get("superseded-by")
        st = c.fields.get("status")
        if sb and st not in ("superseded", "deprecated") and not sb_refused:
            errs.append(Err(c.file, c.line, "L7", "F",
                            f"{label(c)} carries superseded-by: {sb} but reads status: "
                            f"{st or 'active'} -- superseded is entailed by the link; run "
                            "`kb-lint fix`", fix=("status", c)))
        tgt = c.fields.get("promoted-to")
        if tgt and cfg["id_format"].match(tgt) and tgt not in by_id:
            errs.append(Err(c.file, c.line, "L7", "G",
                            f"{label(c)} promoted-to target '{tgt}' not found in corpus"))
        # (non-id promoted-to targets are always-on-layer paths; existence is not
        #  gated in v0.1 -- promotion mechanics land at P3)

    # supersession cycles (self-loops already caught above)
    edges = {c.fields["id"]: c.fields["supersedes"] for c in all_claims
             if c.fields.get("id") and c.fields.get("supersedes")}
    for start in edges:
        seen, node = set(), start
        while node in edges and node not in seen:
            seen.add(node)
            node = edges[node]
        if node in seen:
            c = by_id[start]
            errs.append(Err(c.file, c.line, "L7", "G",
                            f"{label(c)} supersession cycle detected through "
                            f"{sorted(seen)}"))
            break


# ---------------------------------------------------------------- targets & report

def collect_files(cfg, paths, changed=False, hook=False):
    if changed:
        rc, out = run_git(cfg["dir"], "diff", "--cached", "--name-only", "-z",
                          "--diff-filter=ACMR")
        if rc != 0:
            # REPORT, do not halt (#50). An empty list here means "lint nothing", which
            # a hook reads as "clean" -- so a git outage silently turns the check green.
            # Saying so is the whole fix: halting would block a commit on any transient
            # git failure, and friction on a safety control breeds workarounds.
            #
            # #51 splits that ruling rather than overturning it. The friction argument is
            # about a HUMAN who gets blocked and routes around the gate; in hook context
            # there is no human waiting to be told, only a commit to stop, and the failure
            # is silent-green in the irreversible direction. So the hook refuses and the
            # interactive path is left exactly as #50 ruled it.
            if hook:
                die(f"REFUSING -- `git diff --cached` exited {rc} in {cfg['dir']}. In hook "
                    "context a staged-file list that could not be read is a FAILURE, not an "
                    "empty list: linting zero files would report this commit clean without "
                    "having looked at it. Re-run once git is healthy, or bypass deliberately "
                    "with `git commit --no-verify`.")
            print(f"kb-lint: WARNING -- `git diff --cached` exited {rc} in {cfg['dir']}; "
                  "the staged-file list is empty because git could not be read, NOT "
                  "because nothing is staged. Anything below covers zero files.",
                  file=sys.stderr)
        files = [cfg["dir"] / f for f in out.split("\0")
                 if f.startswith(cfg["kb_path"] + "/") and f.endswith(".md")]
        # In hook context a staged file is linted from the index whether or not the working
        # tree still holds it -- the commit carries it either way (A7 of the 2026-09-24 build
        # review). Interactively, a staged file deleted from disk is skipped, as before.
        return _dedupe(files if hook else [f for f in files if f.is_file()])
    if not paths:
        paths = [cfg["dir"] / cfg["kb_path"]]
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.md")))
        elif p.is_file():
            files.append(p)
        else:
            die(f"no such path: {p}")
    return _dedupe(files)


def _dedupe(files):
    """One entry per FILE, not per path argument (D1).

    Callers pass whatever the user typed, and `kb-lint check kb/ kb/a.md` or
    `kb-query kb/ kb/a.md` names a.md twice. run_checks then parses it twice, so every
    claim in it appears twice in the corpus and L8 reports each id as colliding WITH
    ITSELF -- the message names the same file and the same line on both sides.

    On the write path that is a false gate. On the READ path it is worse and silent, and
    the two argument shapes do NOT behave the same way -- do not conflate them. A directory
    plus a file inside it taints only that file's claims and still serves everything else,
    which looks healthy: measured 416 served of 437. The SAME directory named twice taints
    the whole corpus and serves NOTHING at exit 0. Both read to a consumer like "the KB
    holds nothing on this", so they re-derive what they already knew, but only the second
    is obvious. Observed, not reasoned.

    Keyed on resolve() so the same file spelled two ways collapses; the first spelling
    is the one returned, because callers report paths back the way they were given.
    """
    out, seen = [], set()
    for f in files:
        try:
            key = f.resolve()
        except OSError:
            key = f            # unresolvable: fall back to the literal path
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def run_git(cwd, *args):
    """(returncode, stdout). The exit code is PART OF THE ANSWER (#50).

    Returning stdout alone made "git succeeded and said nothing" and "git failed and so
    could say nothing" the same value, so every caller that tested only the text fell
    open on a git outage. `rev-parse --is-inside-work-tree` exits 128 with empty stdout
    for a directory that is not a repo AND for a repo whose .git points nowhere --
    kb_boundary.git_signal_status() is where those two are told apart.

    Deliberately does NOT assert returncode == 0: non-zero is a NORMAL state for several
    of these queries (`remote get-url origin` on a repo with no origin, `config
    user.email` with no identity set), so the judgement belongs in each caller, not here.
    An absent git binary still RAISES out of subprocess -- loud rather than fail-open,
    and left that way on purpose.
    """
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8")
    return r.returncode, r.stdout


def corpus_files(cfg):
    """Every .md under kb_path, from disk -- or none when that directory does not exist yet. A
    first capture into a new instance creates it, so a missing corpus is an EMPTY one, never a
    halt (A6 of the 2026-09-24 build review: resolving corpus-wide had made collect_files'
    "no such path" fire before the append that creates the directory)."""
    return collect_files(cfg, []) if (cfg["dir"] / cfg["kb_path"]).exists() else []


def default_pool(cfg, files):
    """The resolution pool a check uses unless told otherwise: every corpus file the caller
    did not name, read from disk (ruling R3: resolve corpus-wide in every check)."""
    named = {Path(f).resolve() for f in files}
    return [(f, None) for f in corpus_files(cfg) if f.resolve() not in named]


def gate_unfixed(errs):
    """Operator ruling OR8 (2026-09-25): in hook context, a finding that is still FIXABLE when
    `check --changed --hook` runs is a fix that could not land, so it gates. Without this, a hook
    that ignores the fixer's exit (the operator's own, and any adopter's that runs the fix and
    stages with `git add -u`) printed the fixer's REFUSED line and then committed a half-link, or
    id-less claims (the re-review's N3 = R1). R2's "fixable" is amended for hook context only.

    Applied to EVERY fixable finding -- a missing back-link, a missing id, a stale status -- not
    only the back-link the ruling names: the rationale is the same for each (a fix that could not
    land), and R1's case 3 committed two id-less claims by the same mechanism. That width is
    pending the operator's ratification (build review, OR8's note)."""
    for e in errs:
        if e.cls == "F":
            e.cls = "G"
            e.msg += (" -- still fixable at commit time, so the fix could not land: the commit is "
                      "refused (OR8). Run `kb-lint fix`, stage what it names, and commit again")


class DiskView:
    """The corpus as the working tree holds it: every read from disk, every file writable.
    What `check`, `fix` and kb_capture use outside hook context."""

    def overrides_for(self, files):
        return None

    def pool_for(self, files):
        return None       # run_checks' default_pool: the rest of the corpus, from disk

    def writable(self, relpath):
        return True


class IndexView:
    """The corpus as the commit will carry it (ruling R3: in hook context the corpus is the
    index -- HEAD plus staged changes), for `check --changed --hook` and `fix --changed --hook`.

    Every corpus file in the index is materialised once with `git checkout-index` into a
    scratch tree removed at exit. A file whose working-tree copy git reports as differing from
    its staged copy (`git diff --name-only`, which applies git's own line-ending filters, so a
    tool's LF write is not mistaken for a change) is read from that staged copy; any other
    indexed file is read from disk, which then holds the same content. Staged files included
    (A7 of the 2026-09-24 build review): they were read from the working tree, so a commit was
    judged on content it does not carry. A file that is not in the index is not in this corpus.

    The write rule (operator ruling OR4): the fixer may write a file only when its working-tree
    copy matches its staged copy -- the only case in which an edit computed from what the
    commit carries lands on the same content. A file with unstaged edits, a file deleted from
    the working tree, and a file with no staged copy at all (not in the index) are never
    written; the fix run then fails naming the file. That last case cannot arise from the
    corpus itself -- every link resolves into the index -- so it is the fail-closed answer to a
    state nothing should produce."""

    def __init__(self, cfg, staged):
        tmp = Path(tempfile.mkdtemp(prefix="kb-lint-index-"))
        atexit.register(shutil.rmtree, tmp, True)
        rc, _ = run_git(cfg["dir"], "checkout-index", "-a", "--prefix=" + tmp.as_posix() + "/")
        if rc != 0:
            die(f"REFUSING -- `git checkout-index` exited {rc} in {cfg['dir']}: the hook cannot "
                "resolve links against the index. Re-run once git is healthy.")
        rc, out = run_git(cfg["dir"], "--no-optional-locks", "diff", "--name-only", "-z",
                          "--no-renames", "--", cfg["kb_path"] + "/")
        if rc != 0:
            die(f"REFUSING -- `git diff` exited {rc} in {cfg['dir']}: the hook cannot tell which "
                "files carry unstaged edits, so it cannot judge the commit. Re-run once git is "
                "healthy.")
        self.cfg = cfg
        self.dirty = {f for f in out.split("\0") if f}
        kb_tmp = tmp / cfg["kb_path"]
        self.indexed = {f.relative_to(tmp).as_posix(): f.read_text(encoding="utf-8")
                        for f in (sorted(kb_tmp.rglob("*.md")) if kb_tmp.is_dir() else [])}
        self._verdict = {}

    def _rel(self, f):
        return Path(f).resolve().relative_to(Path(self.cfg["dir"]).resolve()).as_posix()

    def _text(self, rel):
        return self.indexed[rel] if rel in self.dirty else None

    def overrides_for(self, files):
        out = {}
        for f in files:
            rel = self._rel(f)
            if rel in self.indexed and self._text(rel) is not None:
                out[Path(f)] = self._text(rel)
        return out

    def pool_for(self, files):
        named = {self._rel(f) for f in files}
        return [(self.cfg["dir"] / rel, self._text(rel))
                for rel in sorted(self.indexed) if rel not in named]

    def writable(self, relpath):
        """OR4's rule, plus the re-review's N10 (row T3 of round 2): the dirty set is a snapshot
        taken at start-up, and another live session can edit a clean file inside the hook's run,
        so before a file's FIRST write its disk text must still equal its indexed text. Both are
        read with newline translation, so a CRLF checkout under core.autocrlf compares equal.
        The verdict is kept, so this run's own later writes to the file are not refused."""
        if relpath not in self._verdict:
            p = self.cfg["dir"] / relpath
            disk = p.read_text(encoding="utf-8") if p.is_file() else None
            self._verdict[relpath] = (relpath in self.indexed and relpath not in self.dirty
                                      and disk == self.indexed[relpath])
        return self._verdict[relpath]


def run_checks(cfg, files, overrides=None, resolve=None):
    """Full check pass. `overrides` maps Path -> content: those files are linted from the
    given text instead of disk (in-memory candidates, kb_capture --dry-run); resolved-path
    matching, so caller path spelling never misses. `resolve` is the resolution pool as
    [(path, text-or-None)]; None means the rest of the corpus from disk (default_pool), and
    an empty list means nothing beyond `files`."""
    ov = {Path(k).resolve(): v for k, v in overrides.items()} if overrides else {}
    all_errs, all_claims, nfiles = [], [], 0
    for f in files:
        text = ov.get(Path(f).resolve()) if ov else None
        claims, errs, meta = check_file(f, cfg, text=text)
        nfiles += 1
        all_errs.extend(errs)
        if meta["class"] == "claim":
            all_claims.extend([c for c in claims if c.kind_valid])
    pool = []
    for p, text in (default_pool(cfg, files) if resolve is None else resolve):
        if text is None and ov:
            text = ov.get(Path(p).resolve())
        try:
            claims, _errs, meta = check_file(p, cfg, text=text)
        except SystemExit:
            continue   # a pool file outside the config root resolves nothing; not ours to report
        if meta["class"] == "claim":
            pool.extend([c for c in claims if c.kind_valid])
    corpus_checks(all_claims, all_errs, cfg, pool=pool)
    return all_errs, all_claims, nfiles


# What each fixable kind is called in the summary. A stale status is present and wrong, not
# missing (C19 of the 2026-09-24 build review).
FIX_NAMES = {"id": "missing id", "reciprocal": "missing reciprocal", "status": "stale status"}


def report(errs, claims, nfiles, quiet=False):
    g = sum(1 for e in errs if e.cls == "G")
    fx = [e for e in errs if e.cls == "F"]
    if quiet:  # Q8: silent under --quiet so the hook's fix pass never double-prints
        return 1 if g else 0
    by_file = {}
    for e in errs:
        by_file.setdefault(e.file, []).append(e)
    for f in sorted(by_file):
        print(f)
        for e in sorted(by_file[f], key=lambda e: e.line):
            print(f"  :{e.line}  {e.check}  {e.msg}")
    if g or fx:
        detail = ""
        if fx:
            kinds = {}
            for e in fx:
                kinds[e.fix[0]] = kinds.get(e.fix[0], 0) + 1
            detail = " (run `kb-lint fix`: " + ", ".join(
                f"{n} {FIX_NAMES.get(k, 'missing ' + k)}" for k, n in sorted(kinds.items())) + ")"
        print(f"FAIL: {g} gating errors - {len(fx)} fixable{detail}")
    elif not quiet:
        print(f"OK: {nfiles} files - {len(claims)} claims - 0 errors")
    return 1 if g else 0


# ---------------------------------------------------------------- fix

def new_id(cfg, taken):
    while True:
        kid = cfg["id_prefix"] + "".join(random.choices(ID_CHARS, k=4))
        if kid not in taken:
            taken.add(kid)
            return kid


FORMAT_WIDTH = 100


def format_pass(cfg, files, run=None, view=None):
    """F4 normalizer (P2): move an overlong INLINE brace to its own indented
    line (the grammar's continuation form). Multi-line braces are already in
    continuation form and are left alone. Idempotent: a brace alone on its
    line is never touched again. Writes go through the run, so `wrote:` names
    them too (A8), and a file the view does not let it write is refused (OR4).
    Returns the number of braces reflowed in files written."""
    run = run or FixRun(cfg)
    ov = (view.overrides_for(files) if view else None) or {}
    _, claims, _ = run_checks(cfg, files, overrides=ov, resolve=[])  # the named files' claims suffice
    ov = {Path(k).resolve(): v for k, v in ov.items()}
    by_file = {}
    for c in claims:
        if not c.brace_open or c.brace_open[0] != c.brace_close[0]:
            continue
        ln, col = c.brace_open
        by_file.setdefault(c.file, []).append((ln, col))
    changed = 0
    for relpath, items in by_file.items():
        p = cfg["dir"] / relpath
        text = ov.get(p.resolve())
        lines = (p.read_text(encoding="utf-8") if text is None else text).split("\n")
        n = 0
        for ln, col in sorted(items, key=lambda t: -t[0]):
            raw = lines[ln - 1]
            head = raw[:col].rstrip()
            if len(raw) <= FORMAT_WIDTH or not head.strip():
                continue  # short enough, or brace already alone on its line
            lines[ln - 1] = head
            lines.insert(ln, "  " + raw[col:].rstrip())
            n += 1
        if n and run.write(relpath, "\n".join(lines)):
            changed += n
    return changed


def corpus_ids(cfg):
    """Every id in the corpus -- for id MINTING only (A3).

    apply_fixes lints whatever file set the caller passed, and kb_capture passes the
    single file it just wrote, so `taken` held one file's ids and a freshly minted id
    was unique only within that file. A collision minted this way is not caught at
    write time and costs a claim at read time (see the L8 note in corpus_checks).

    Read through the parser (operator ruling OR13, item 3): the text scan this replaces read
    ids only from `- [kind]` lines, so it missed every id in the continuation form -- a brace
    on its own indented line, which is how every live claim is written (817 of 817, the round-2
    check's Q7-a) -- and the avoid-set was empty on a real corpus. The parser finds a brace in
    every form it accepts, which is exactly the set L8 compares. Its errors are discarded: this
    must not surface findings from files the caller did not ask about.
    """
    ids = set()
    for f in corpus_files(cfg):
        try:
            claims, _errs, _meta = check_file(f, cfg)
        except (OSError, UnicodeDecodeError, SystemExit):
            continue   # an unreadable file cannot narrow the avoid-set; minting stays safe
        ids.update(c.fields["id"] for c in claims if c.fields.get("id"))
    return ids


def write_utf8(path, text):
    """Write `text` as UTF-8 with its newlines untranslated, ENCODING FIRST (operator ruling OR13,
    item 1). Path.write_text opens the file for writing -- emptying it -- before the encoder runs, so
    text that cannot be encoded (a lone surrogate) left an empty file behind a traceback. Here the
    bytes exist before the file is opened, so a failure leaves the file as it was."""
    data = text.encode("utf-8")
    Path(path).write_bytes(data)


class FixRun:
    """What one fix run wrote, and whether it may write. Every write goes through write(),
    so `wrote` holds exactly the files whose bytes this run changed -- the format pass's too
    (A8 of the 2026-09-24 build review) -- `pre` each written file's text from before the run
    (A5), `blocked` the files the run had to write but the view does not allow (OR4: in hook
    mode, a file whose working tree differs from its staged copy, or that has none), and
    `refused` the claims the editor would not touch because their live brace already carries
    the key with another value (A1)."""

    def __init__(self, cfg, writable=None):
        self.cfg = cfg
        self.writable = writable or (lambda relpath: True)
        self.wrote, self.pre, self.blocked, self.refused = set(), {}, set(), []

    def may_write(self, relpath):
        """Asked BEFORE a file with pending edits is even read: a blocked file fails the run
        whether or not its working-tree copy happens to hold the edit already -- a silent no-op
        there would commit a half-link under R8 (K6)."""
        if self.writable(relpath):
            return True
        self.blocked.add(relpath)
        return False

    def write(self, relpath, text):
        if not self.may_write(relpath):
            return False
        p = self.cfg["dir"] / relpath
        old = p.read_text(encoding="utf-8") if p.is_file() else None
        if old == text:
            return False
        self.pre.setdefault(relpath, old)
        write_utf8(p, text)
        self.wrote.add(relpath)
        return True

    def refuse(self, relpath, claim, why):
        self.refused.append(relpath)
        print(f"kb-lint: REFUSED -- {relpath}:{claim.line} {claim.fields.get('id')}: {why}. "
              "Nothing was written onto this claim.", file=sys.stderr)


def apply_fixes(cfg, files, do_format=False, quiet=False, view=None, announce=False,
                outcome=None):
    """Assign missing ids, then (re-parsed) write missing reciprocal links and the status
    flips they entail -- into whichever file the linked claim lives in (R3: the fix follows
    the link). Prints `wrote: <relpath>` for every file it changed, unless quiet; always
    under `announce` (the hook passes it, so it can stage exactly those files -- R8).

    `view` is where the corpus is read from and what may be written: DiskView by default,
    IndexView under `--changed --hook` (A7, OR4). Exit 1 when the named files still gate, when
    a file the fix had to write was not writable, when the editor refused a claim, or when
    the run introduced a gating error into a file it followed a link into (A5). `outcome`, if
    given, receives {"failed": [relpath, ...]} naming every file behind a failure."""
    view = view or DiskView()
    ov, pool = view.overrides_for(files), view.pool_for(files)
    run = FixRun(cfg, view.writable)
    # pass 1: ids
    errs, claims, _ = run_checks(cfg, files, overrides=ov, resolve=pool)
    taken = {c.fields["id"] for c in claims if c.fields.get("id")} | corpus_ids(cfg)
    edits = {}  # relpath -> [(lineno, edit_fn)]
    for e in errs:
        if e.fix and e.fix[0] == "id":
            c = e.fix[1]
            edits.setdefault(c.file, []).append(_id_edit(c, new_id(cfg, taken)))
    _write_edits(cfg, edits, run)
    wrote = run.wrote
    # pass 2: reciprocals and status flips (ids may be fresh); claim-level, re-parsed at
    # write time so an edit into a file outside `files` lands on that file's live lines
    errs, claims, _ = run_checks(cfg, files, overrides=ov, resolve=pool)
    claim_edits = {}  # relpath -> {claim id -> {"append": [pairs], "status": value|None}}
    for e in errs:
        if not e.fix:
            continue
        if e.fix[0] == "reciprocal":
            _, c, recip, val = e.fix
            slot = claim_edits.setdefault(c.file, {}).setdefault(c.fields["id"], {"append": [], "status": None})
            slot["append"].append(f"{recip}: {val}")
            if recip == "superseded-by":
                slot["status"] = "superseded"  # mechanically entailed by the link (R1)
        elif e.fix[0] == "status":
            c = e.fix[1]
            slot = claim_edits.setdefault(c.file, {}).setdefault(c.fields["id"], {"append": [], "status": None})
            slot["status"] = "superseded"
    _apply_claim_edits(cfg, claim_edits, run)
    if do_format:
        n = format_pass(cfg, files, run, view)
        if not quiet:
            print(f"format: {n} brace(s) reflowed to the indented-brace form "
                  f"(width {FORMAT_WIDTH})")
    if not quiet or announce:
        for rel in sorted(wrote):
            print(f"wrote: {rel}")
    for rel in sorted(run.blocked):
        print(f"kb-lint: REFUSED -- the fix must write {rel}, but its working-tree copy differs from "
              "its staged copy (or it has no staged copy), so nothing was written to it. Stage or "
              "stash its changes, then commit again.", file=sys.stderr)
    introduced = _introduced(cfg, run, files, view)
    for rel, found in introduced:
        print(f"kb-lint: this fix run introduced a gating error into {rel}, a file it followed a "
              "link into -- the run fails; fix what is named, then run `kb-lint fix` again:",
              file=sys.stderr)
        for e in found:
            print(f"  {rel}:{e.line}  {e.check}  {e.msg}", file=sys.stderr)
    errs, claims, nfiles = run_checks(cfg, files, overrides=ov, resolve=pool)
    if not quiet:
        print("fix: done; re-check follows")
    rc = report(errs, claims, nfiles, quiet=quiet)
    if outcome is not None:
        gating = {e.file for e in errs if e.cls == "G"}
        outcome["failed"] = sorted(gating | run.blocked | set(run.refused)
                                   | {rel for rel, _ in introduced})
    return 1 if (run.refused or run.blocked or introduced) else rc


def _introduced(cfg, run, files, view):
    """[(relpath, [Err])] for every file this run wrote that the caller did not name -- a file
    it followed a link into -- where THIS run introduced a gating finding (A5, stated default S2
    of the 2026-09-24 build review). The file is checked twice, as it stood before the run and
    as it stands now, each against the rest of the corpus at the same moment, and the gating
    findings are compared as (check, message) multisets, so a finding that only moved lines is
    not new. An error that was already there stays unsurfaced: R3 keeps the gate on the named
    files, and that is not reopened."""
    named = {Path(f).resolve() for f in files}
    before_ov = {cfg["dir"] / rel: text for rel, text in run.pre.items() if text is not None}
    out = []
    for rel in sorted(run.wrote):
        p = cfg["dir"] / rel
        if p.resolve() in named:
            continue
        pool = view.pool_for([p])
        before, _, _ = run_checks(cfg, [p], overrides=before_ov, resolve=pool)
        after, _, _ = run_checks(cfg, [p], resolve=pool)
        seen = Counter((e.check, e.msg) for e in before if e.cls == "G" and e.file == rel)
        found = []
        for e in after:
            if e.cls != "G" or e.file != rel:
                continue
            if seen[(e.check, e.msg)]:
                seen[(e.check, e.msg)] -= 1
            else:
                found.append(e)
        if found:
            out.append((rel, found))
    return out


def _brace_field_span(c, key):
    """Where `key`'s value sits in the claim's metadata brace, as (lineno, lo, hi) raw columns
    on ONE physical line, or None when the key is absent or its value spans a line break.

    The edit is brace-scoped and key-anchored (A2 of the 2026-09-24 build review; ruling R2
    named kb_capture.apply_to_brace's shape): the brace is the one the parser itself found
    (find_meta_brace over the joined item text), and the key is matched only at the START of a
    pair, walking the pairs quote-aware the way split_pairs does. `status:` in the claim's
    prose, or inside a quoted value such as `why: "the status: quo"`, can never match -- the
    line-wide regex this replaces rewrote both, and could delete the brace's `{id: ...`."""
    joined, posmap = scan_brace(c.text_lines)
    meta = find_meta_brace(joined)
    if meta is None:
        return None
    lo_seq, hi_seq = meta
    start, in_q = lo_seq + 1, False
    for i in range(lo_seq + 1, hi_seq + 1):
        ch = joined[i]
        if i < hi_seq and ch == '"':
            in_q = not in_q
        if i == hi_seq or (ch == "," and not in_q):
            seg = joined[start:i]
            m = re.match(r"\s*" + re.escape(key) + r":\s*", seg)
            if m:
                vs, ve = start + m.end(), start + len(seg.rstrip())
                if ve <= vs:
                    return None
                (l1, c1), (l2, c2) = posmap[vs], posmap[ve - 1]
                return (l1, c1, c2 + 1) if l1 == l2 else None
            start = i + 1
    return None


def _edit_claim(run, relpath, c, lines, ed):
    """One claim's appends and status flip, judged against its LIVE brace (A1): a value the
    brace already holds is a no-op, never a second key; another value for a key it holds is
    refused, and then nothing is written onto the claim. A status is REPLACED in place through
    _brace_field_span (A2), or appended when the claim has none."""
    add, pending = [], {}
    for pair in ed["append"]:
        key, val = pair.split(": ", 1)
        have = c.fields.get(key, pending.get(key))
        if have == val:
            continue
        if have is not None:
            run.refuse(relpath, c, f"it already carries {key}: {have}, and the fix would add "
                                   f"{key}: {val} beside it -- a single-valued key cannot hold both")
            return
        pending[key] = val
        add.append(pair)
    status, have = ed["status"], c.fields.get("status")
    if status and have == "deprecated":
        run.refuse(relpath, c, f"it is deprecated, and the fix would set status: {status} -- "
                               "a tombstone is never flipped")
        return
    span = None
    if status and have not in (None, status):
        span = _brace_field_span(c, "status")
        if span is None:
            run.refuse(relpath, c, "its status: value could not be located on one line of its "
                                   "brace, so it cannot be replaced safely")
            return
    elif status and have is None:
        add.append(f"status: {status}")
    if add:   # at the closing brace, which lies after every value on its line
        ln, col = c.brace_close
        lines[ln - 1] = lines[ln - 1][:col] + ", " + ", ".join(add) + lines[ln - 1][col:]
    if span:
        ln, lo, hi = span
        lines[ln - 1] = lines[ln - 1][:lo] + status + lines[ln - 1][hi:]


def _apply_claim_edits(cfg, claim_edits, run=None):
    """Apply per-claim edits by re-parsing each file from disk and locating the claim by id,
    so the coordinates are the file's live ones whether or not it was in the checked set.
    Appends land at the closing brace; a status is REPLACED in place (never appended
    twice -- the additive form is how a duplicate key gets written). Returns the relpaths
    written."""
    run = run or FixRun(cfg)
    for relpath, per_claim in claim_edits.items():
        p = cfg["dir"] / relpath
        if not run.may_write(relpath):
            continue
        claims, _errs, _meta = check_file(p, cfg)
        lines = p.read_text(encoding="utf-8").split("\n")
        for c in sorted(claims, key=lambda c: -(c.brace_close[0] if c.brace_close else c.line)):
            kid = c.fields.get("id")
            if kid in per_claim and c.brace_close:
                _edit_claim(run, relpath, c, lines, per_claim[kid])
        run.write(relpath, "\n".join(lines))
    return sorted(run.wrote)


def _id_edit(c, kid):
    if c.brace_open:
        ln, col = c.brace_open
        return (ln, lambda line: line[:col + 1] + f"id: {kid}, " + line[col + 1:])
    ln = c.text_lines[-1][0]
    return (ln, lambda line: line.rstrip() + f" {{id: {kid}}}")


def _write_edits(cfg, edits, run=None):
    """Line-level edits (id assignment). Returns the relpaths written."""
    run = run or FixRun(cfg)
    for relpath, lst in edits.items():
        p = cfg["dir"] / relpath
        if not run.may_write(relpath):
            continue
        lines = p.read_text(encoding="utf-8").split("\n")
        for ln, fn in sorted(lst, key=lambda t: -t[0]):  # bottom-up keeps lines valid
            lines[ln - 1] = fn(lines[ln - 1])
        run.write(relpath, "\n".join(lines))
    return sorted(run.wrote)


# ---------------------------------------------------------------- strip

def do_strip(cfg, files, outdir):
    """Emit the control-arm corpus: same content, evidence structure removed."""
    for f in files:
        rel = f.relative_to(cfg["dir"] / cfg["kb_path"])
        text = f.read_text(encoding="utf-8")
        fm_text, body_lines, body_start = split_frontmatter(text)
        cls, fm_out = "claim", []
        if fm_text is not None:
            try:
                fm = dirty_load(fm_text, allow_flow_style=True).data
                cls = classify(fm, cfg)
            except Exception:
                fm = None
            skip = False
            for line in fm_text.split("\n"):
                if not line.startswith(" "):
                    skip = line.split(":")[0].strip() == "evidence"
                if not skip:
                    fm_out.append(line)
        if f.name in cfg["reserved"] or cls != "claim":
            out_text = text
        else:
            claims = parse_claim_items(body_lines, body_start, "", [], cfg)
            spans = {c.line - body_start: c for c in claims}
            out, i = [], 0
            while i < len(body_lines):
                c = spans.get(i)
                if c is None:
                    out.append(body_lines[i])
                    i += 1
                    continue
                last = c.text_lines[-1][0] - body_start
                if not c.kind_valid:
                    out.extend(body_lines[i:last + 1])  # passthrough, don't invent
                else:
                    out.append("- " + c.text)  # kind tag + brace gone; wrap collapsed
                    for j in range(i + 1, last + 1):
                        if body_lines[j].lstrip().startswith("- "):
                            out.append(body_lines[j])  # keep inert nested bullets
                i = last + 1
            head = ["---", *fm_out, "---"] if fm_out else []
            out_text = "\n".join(head + out)
        if outdir:
            dest = Path(outdir) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_utf8(dest, out_text)
        else:
            print(out_text)
    if outdir:
        print(f"strip: control arm written to {outdir}")
    return 0


# ---------------------------------------------------------------- stats

def do_stats(cfg, files):
    errs, claims, nfiles = run_checks(cfg, files)
    kinds, methods, confs = {}, {}, {}
    nfields, inherit = 0, 0
    for c in claims:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
        nfields += len(c.fields)
        if c.kind == "fact" and "v" in c.fields:
            m = c.fields["v"].split()[0]
            methods[m] = methods.get(m, 0) + 1
            if "src" not in c.fields and m != "unverified":
                inherit += 1
        if "conf" in c.fields:
            confs[c.fields["conf"]] = confs.get(c.fields["conf"], 0) + 1
    print(f"files: {nfiles}  claims: {len(claims)}  "
          f"gating-errors: {sum(1 for e in errs if e.cls == 'G')}")
    print(f"per kind: {kinds}")
    print(f"fact v: methods: {methods}  ({inherit} facts lean on the {{src}} default)")
    print(f"conf distribution: {confs}")
    if claims:
        print(f"fields/claim: {nfields / len(claims):.1f}")
    q = sum(n for m, n in methods.items() if m in ("model-inferred", "unverified"))
    print(f"quarantine share (T3/T4 facts): {q}")
    print("n/a until P2+: first-commit failure rate (hook log), never-served claims "
          "(no serving record is kept)")
    return 0


# ---------------------------------------------------------------- index refresh (a2)

def is_claim_line(line):
    """The linter's real claim predicate: a top-level '- [kind] ...' bullet whose tag matches
    KIND_RE. parse_claim_items detects items by the '- ' prefix ALONE and validates the kind
    separately (L3); this predicate folds both into one test -- identical outcomes on every
    lint-green file, which is where index counting matters (ARCH-004: don't re-implement the
    authority in kb_capture). '- [ ] todo' is excluded (a
    space is not [a-z-]+); '- [x] done' DOES match (kind 'x'), exactly as the parser treats it --
    an invalid kind is then rejected by the L3 kind-enum check, so a '- [x]' line can never reach a
    committed (lint-green) concept file where the count matters. Nested/indented bullets don't
    match (no '- ' at column 0)."""
    return line.startswith("- ") and bool(KIND_RE.match(line[2:]))


def refresh_index(cfg):
    """Regenerate kb/index.md (the infuse artifact, spec §9) so the concept list stays current
    after a capture. Preserves the existing okf_version frontmatter; one line per concept file
    with its real claim count (is_claim_line, not a raw '- [' prefix that over-counts task items).
    Reserved files (cfg['reserved'], by BASENAME -- so a nested foo/index.md is reserved too, per
    check_file) and any '_'-prefixed path segment are excluded. index.md is itself reserved (body
    not claim-parsed), so the plain list lines are safe. Idempotent. Lives in kb_lint (not
    kb_capture) so it reuses cfg['reserved'] + the parser predicate rather than duplicating them
    (ARCH-004/CODE-003/CODE-004)."""
    kb_root = cfg["dir"] / cfg["kb_path"]
    index = kb_root / "index.md"
    fm = '---\nokf_version: "0.1"\n---\n'
    if index.is_file():
        txt = index.read_text(encoding="utf-8")
        if txt.startswith("---"):
            close = txt.find("\n---", 3)
            if close != -1:
                fm = txt[:close + 4].rstrip("\n") + "\n"
            else:
                # CODE-005: unclosed frontmatter -- WARN, do not silently substitute the default
                # (that would discard the operator's okf_version block). Keep the default only
                # after saying so, so a malformed fence is a visible fix-me, not silent data loss.
                print(f"kb-lint: WARNING {index} has an unclosed frontmatter fence; keeping the "
                      "default okf_version -- fix the closing '---'", file=sys.stderr)
    reserved = set(cfg["reserved"])
    concepts = []
    for f in sorted(kb_root.rglob("*.md")):
        rel = f.relative_to(kb_root).as_posix()
        if f.name in reserved or any(p.startswith("_") for p in rel.split("/")):
            continue
        n = sum(1 for ln in f.read_text(encoding="utf-8").splitlines() if is_claim_line(ln))
        concepts.append((rel[:-3], n))
    body = ("\n".join(f"- {slug} ({n} claim{'' if n == 1 else 's'})" for slug, n in concepts)
            if concepts else "_(empty -- capture lands concepts here)_")
    write_utf8(index, fm + "\n# kb index\n\n" + body + "\n")
    return index


# ---------------------------------------------------------------- main

def main():
    sub = {"check", "fix", "strip", "stats"}
    argv = sys.argv[1:]
    if not argv or argv[0] not in sub:
        argv = ["check"] + argv  # check is the default subcommand

    ap = argparse.ArgumentParser(prog="kb-lint", description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("paths", nargs="*", help="files/dirs (default: kb_path)")
    common.add_argument("--config", help="explicit .kb-lint.yml (default: walk-up)")
    common.add_argument("--changed", action="store_true",
                        help="staged kb/**.md only")
    common.add_argument("--hook", action="store_true",
                        help="caller is a git hook: an unreadable staged-file list is a "
                             "failure, not an empty list (#51)")
    common.add_argument("--quiet", action="store_true")
    sp.add_parser("check", parents=[common])
    p_fix = sp.add_parser("fix", parents=[common])
    p_fix.add_argument("--format", action="store_true",
                       help="normalize brace formatting (P2)")
    p_fix.add_argument("--materialize", action="store_true",
                       help="expand defaults in place (RESERVED for gate G1)")
    p_strip = sp.add_parser("strip", parents=[common])
    p_strip.add_argument("--out", help="write control-arm tree here (default: stdout)")
    sp.add_parser("stats", parents=[common])
    args = ap.parse_args(argv)

    start = Path(args.paths[0]).resolve() if args.paths else Path.cwd()
    if start.is_file():
        start = start.parent
    cfg_path = Path(args.config) if args.config else find_config(start)
    if not cfg_path or not cfg_path.is_file():
        die("no .kb-lint.yml found (walk-up from target; or pass --config)")
    cfg = load_config(cfg_path)

    if args.cmd == "fix" and args.materialize:
        die("--materialize is reserved for gate G1 (v0.2): defaults expansion is "
            "admitted only after the write-friction + no-laundering audit")

    files = collect_files(cfg, [Path(p).resolve() for p in args.paths], args.changed,
                          hook=args.hook)
    if not files:
        if not args.quiet:
            print("OK: nothing to lint")
        return 0
    # where the corpus is read from: disk -- or, in hook context, the INDEX, staged files
    # included, so a commit is judged on what it will contain (R3, A7), and the fix writes
    # only files whose working tree equals their staged copy (OR4)
    view = IndexView(cfg, files) if (args.changed and args.hook) else DiskView()
    if args.cmd == "check":
        errs, claims, nfiles = run_checks(cfg, files, overrides=view.overrides_for(files),
                                          resolve=view.pool_for(files))
        if isinstance(view, IndexView):
            gate_unfixed(errs)
        return report(errs, claims, nfiles, quiet=args.quiet)
    if args.cmd == "fix":
        return apply_fixes(cfg, files, do_format=args.format, quiet=args.quiet,
                           view=view, announce=args.hook)
    if args.cmd == "strip":
        return do_strip(cfg, files, args.out)
    if args.cmd == "stats":
        return do_stats(cfg, files)


if __name__ == "__main__":
    sys.exit(main())
