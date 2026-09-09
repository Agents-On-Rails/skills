#!/usr/bin/env python3
"""kb-lint -- OKF-E v0.1 write-time linter (pilot P1, PoC).

Normative spec: okf-e-profile.md. Config: .kb-lint.yml (walk-up from target).
Skeleton lifted from agentskills/agentskills (skills-ref) @ 0c0c567, validator.py:
closed ALLOWED set, per-field validators returning error lists, parse/validate split.

Checks L1-L8; G = gating (hard reject), F = fixable (`kb-lint fix` repairs).
Exit codes are the API: 0 clean - 1 gating errors - 2 usage/config error - 3 a required dependency is not installed.
Subcommands: check (default) / fix / strip / stats.
"""

import argparse
import random
import re
import subprocess
import sys
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
ID_IN_BRACE = re.compile(r"\bid:\s*([^,}\s]+)")
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
    cfg = {
        "dir": path.parent,
        "profile": data["profile"],
        "kb_path": data.get("kb_path", "kb/").strip("/"),
        "kinds": data["enums"]["kind"],
        "methods": data["enums"]["verified_by"],
        "confs": data["enums"]["conf"],
        "claim_keys": data["claim_keys"],
        "defaults_inheritable": data.get("defaults_inheritable", ["src"]),
        "status_enum": data.get("status_enum", []),
        "id_format": re.compile(data["id_format"]),
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
                                "use date: + status: (proposed/accepted)"))
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

def corpus_checks(all_claims, errs, cfg):
    """L7 referential integrity + L8 id uniqueness, corpus-wide."""
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

    for c in all_claims:
        for key, recip in (("supersedes", "superseded-by"),
                           ("superseded-by", "supersedes")):
            tgt = c.fields.get(key)
            if not tgt:
                continue
            if tgt == c.fields.get("id"):
                errs.append(Err(c.file, c.line, "L7", "G",
                                f"{label(c)} self-supersession -- a claim cannot "
                                f"{key.replace('-', ' ')} itself"))
                continue
            other = by_id.get(tgt)
            if other is None:
                errs.append(Err(c.file, c.line, "L7", "G",
                                f"{label(c)} {key} target '{tgt}' not found in corpus "
                                "-- link to an existing claim id (retire, never delete)"))
            elif c.fields.get("id") and other.fields.get(recip) != c.fields["id"]:
                errs.append(Err(other.file, other.line, "L7", "F",
                                f"{label(other)} missing reciprocal {recip}: "
                                f"{c.fields['id']} -- run `kb-lint fix`",
                                fix=("reciprocal", other, recip, c.fields["id"])))
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
        rc, out = run_git(cfg["dir"], "diff", "--cached", "--name-only",
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
        files = [cfg["dir"] / f for f in out.splitlines()
                 if f.startswith(cfg["kb_path"] + "/") and f.endswith(".md")]
        return _dedupe([f for f in files if f.is_file()])
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


def run_checks(cfg, files, overrides=None):
    """Full check pass. `overrides` maps Path -> content: those files are linted from the
    given text instead of disk (in-memory candidates, kb_capture --dry-run); resolved-path
    matching, so caller path spelling never misses."""
    ov = {Path(k).resolve(): v for k, v in overrides.items()} if overrides else {}
    all_errs, all_claims, nfiles = [], [], 0
    for f in files:
        text = ov.get(Path(f).resolve()) if ov else None
        claims, errs, meta = check_file(f, cfg, text=text)
        nfiles += 1
        all_errs.extend(errs)
        if meta["class"] == "claim":
            all_claims.extend([c for c in claims if c.kind_valid])
    corpus_checks(all_claims, all_errs, cfg)
    return all_errs, all_claims, nfiles


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
                f"{n} missing {k}" for k, n in sorted(kinds.items())) + ")"
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


def format_pass(cfg, files):
    """F4 normalizer (P2): move an overlong INLINE brace to its own indented
    line (the grammar's continuation form). Multi-line braces are already in
    continuation form and are left alone. Idempotent: a brace alone on its
    line is never touched again."""
    _, claims, _ = run_checks(cfg, files)
    by_file = {}
    for c in claims:
        if not c.brace_open or c.brace_open[0] != c.brace_close[0]:
            continue
        ln, col = c.brace_open
        by_file.setdefault(c.file, []).append((ln, col))
    changed = 0
    for relpath, items in by_file.items():
        p = cfg["dir"] / relpath
        lines = p.read_text(encoding="utf-8").split("\n")
        edited = False
        for ln, col in sorted(items, key=lambda t: -t[0]):
            raw = lines[ln - 1]
            head = raw[:col].rstrip()
            if len(raw) <= FORMAT_WIDTH or not head.strip():
                continue  # short enough, or brace already alone on its line
            lines[ln - 1] = head
            lines.insert(ln, "  " + raw[col:].rstrip())
            edited = True
            changed += 1
        if edited:
            p.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return changed


def corpus_ids(cfg):
    """Every id in the corpus -- for id MINTING only (A3).

    apply_fixes lints whatever file set the caller passed, and kb_capture passes the
    single file it just wrote, so `taken` held one file's ids and a freshly minted id
    was unique only within that file. A collision minted this way is not caught at
    write time and costs a claim at read time (see the L8 note in corpus_checks).

    A text scan, not a parse: this needs ids, not claims, and it must not surface
    errors from files the caller did not ask about. Fix scope and gate scope stay on
    the touched/staged set -- widening those would be the regression Track 1.5 warns of.
    """
    ids = set()
    for f in collect_files(cfg, []):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue   # unreadable file cannot narrow the avoid-set; minting stays safe
        for line in text.split(chr(10)):
            if is_claim_line(line):
                ids.update(ID_IN_BRACE.findall(line))
    return ids


def apply_fixes(cfg, files, do_format=False, quiet=False):
    """Assign missing ids, then (re-parsed) write missing reciprocal links."""
    # pass 1: ids
    errs, claims, _ = run_checks(cfg, files)
    taken = {c.fields["id"] for c in claims if c.fields.get("id")} | corpus_ids(cfg)
    edits = {}  # relpath -> [(lineno, edit_fn)]
    for e in errs:
        if e.fix and e.fix[0] == "id":
            c = e.fix[1]
            edits.setdefault(c.file, []).append(_id_edit(c, new_id(cfg, taken)))
    _write_edits(cfg, edits)
    # pass 2: reciprocals (ids may be fresh)
    errs, claims, _ = run_checks(cfg, files)
    edits = {}
    for e in errs:
        if e.fix and e.fix[0] == "reciprocal":
            _, c, recip, val = e.fix
            add = f", {recip}: {val}"
            if recip == "superseded-by" and "status" not in c.fields:
                add += ", status: superseded"  # mechanically entailed by the link
            ln, col = c.brace_close
            edits.setdefault(c.file, []).append(
                (ln, lambda line, col=col, add=add: line[:col] + add + line[col:]))
    _write_edits(cfg, edits)
    if do_format:
        n = format_pass(cfg, files)
        if not quiet:
            print(f"format: {n} brace(s) reflowed to the indented-brace form "
                  f"(width {FORMAT_WIDTH})")
    errs, claims, nfiles = run_checks(cfg, files)
    if not quiet:
        print("fix: done; re-check follows")
    return report(errs, claims, nfiles, quiet=quiet)


def _id_edit(c, kid):
    if c.brace_open:
        ln, col = c.brace_open
        return (ln, lambda line: line[:col + 1] + f"id: {kid}, " + line[col + 1:])
    ln = c.text_lines[-1][0]
    return (ln, lambda line: line.rstrip() + f" {{id: {kid}}}")


def _write_edits(cfg, edits):
    for relpath, lst in edits.items():
        p = cfg["dir"] / relpath
        lines = p.read_text(encoding="utf-8").split("\n")
        for ln, fn in sorted(lst, key=lambda t: -t[0]):  # bottom-up keeps lines valid
            lines[ln - 1] = fn(lines[ln - 1])
        p.write_text("\n".join(lines), encoding="utf-8", newline="\n")


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
            dest.write_text(out_text, encoding="utf-8", newline="\n")
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
          "(serve-log)")
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
    index.write_text(fm + "\n# kb index\n\n" + body + "\n", encoding="utf-8", newline="\n")
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
    if args.cmd == "check":
        errs, claims, nfiles = run_checks(cfg, files)
        return report(errs, claims, nfiles, quiet=args.quiet)
    if args.cmd == "fix":
        return apply_fixes(cfg, files, do_format=args.format, quiet=args.quiet)
    if args.cmd == "strip":
        return do_strip(cfg, files, args.out)
    if args.cmd == "stats":
        return do_stats(cfg, files)


if __name__ == "__main__":
    sys.exit(main())
