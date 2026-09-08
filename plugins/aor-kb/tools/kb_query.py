#!/usr/bin/env python3
"""kb-query -- OKF-E v0.1 read side: filtered, labelled serving (pilot P2, PoC).

Normative: okf-e-profile.md SS7 (v: -> trust tiers) + SS10 (serving contract).
Config: .kb-lint.yml (walk-up, same discovery as kb-lint). Parser and the
L1-L8 checks are IMPORTED from kb_lint and re-run on every file read (choke
point 3: trust is re-derived at read time, never stored).

Default serving policy: tiers T1/T2, current claims only -- excludes
superseded / deprecated / expired-`until:` (lifecycle axis) and T3/T4
(quarantine axis), plus any claim failing lint at read time. Every served
claim carries its labels inline, one line per claim -- the consumer never
receives a naked claim (SS10).

Label line: [kind|basis|conf|id|flag] text (src: ...)
  basis  fact/procedure = the v: event; decision = "<status> <date>";
         lesson = "seen <n>, confirmed <date>"    (P2 implementation choice)
  conf   GRADE-4 value; `-` where absent or kind-forbidden; QUARANTINED for
         T3/T4; QUARANTINED:<check> for lint-failing claims (check named)
  flag   DEPRECATED: <reason excerpt> / SUPERSEDED-BY: <id> / EXPIRED: <until>
  src    explicit src:, else the file's inherited {src} default -- except on
         v: unverified facts/procedures (inheritance never applies to them;
         bare admission stays visibly bare, spec SS6 matrix + SS7)

Opt-in axes are ORTHOGONAL: --include-quarantined (or an explicit --tier
listing T3/T4) widens the tier set; --history admits lifecycle-excluded
claims; a deprecated unverified claim therefore needs BOTH. Lint-failing
claims are reachable only via --include-quarantined. Tier derivation:
fact/procedure from the v: method; decision/lesson = T2 (attested by
ratification / recurrence -- SS7's "computed from v: (and kind)").

Serve-log (metric M1 reads it): append-only JSONL at
<kb_path>/_serve/serve-log.jsonl in the real instance --
{ts, query, served: [{id, label}]}. --no-log skips the append; all tests
use it (M1-contamination guard). stdout carries served lines ONLY; the
summary goes to stderr (--quiet suppresses it).

Exit codes: 0 query ran (zero served is not an error) - 2 usage/config error - 3 a required dependency is not installed.
"""

import argparse
import json
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

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

# No bytecode, and it MUST be set before the sibling imports below (#44). Importing a
# sibling writes tools/__pycache__/<mod>.cpython-NNN.pyc into the tree it was imported
# from -- and for a maintainer that tree IS the publish source, where a .pyc is a content
# leak: it embeds the absolute path of the machine that wrote it. The publish gate fails it
# as `aor-windows-user-path` and CUT-13 fails it on disk, so it cannot ship; the cost is
# that it reddens the tree under whoever opens it next. Observed twice on 2026-09-08,
# ~40 minutes apart, in two different sessions, neither of which wrote it knowingly.
# Structural rather than remembered: the shipped SKILL.md specifies `PY = python` with no
# `-B`, so a documented flag would never have covered the runs that actually created these.
# The flag alone suffices here -- unlike aor-comm's md2teams.py (#46), none of these tools
# spawns a python subprocess (kb_lint shells out to `git` only), so no child interpreter
# needs PYTHONDONTWRITEBYTECODE in the environment.
sys.dont_write_bytecode = True

# Self-locate BEFORE importing siblings (K4): under a plugin install the tools
# are reached by absolute path from a SKILL.md, so cwd is the caller's, not tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_lint  # noqa: E402
from kb_lint import die  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TIERS = ("T1", "T2", "T3", "T4")
TIER_OF_METHOD = {"ran-tool": "T1", "read-primary-source": "T1",
                  "author-asserted": "T2", "model-inferred": "T3",
                  "unverified": "T4"}
EXCERPT_LEN = 60


def tier_of(c):
    if c.kind in ("decision", "lesson"):
        return "T2"
    method = (c.fields.get("v") or "unverified").split()[0]
    return TIER_OF_METHOD.get(method, "T4")


def excerpt(text):
    cut = text.split(";")[0].strip()
    if len(cut) > EXCERPT_LEN:
        cut = cut[:EXCERPT_LEN].rstrip() + "…"
    return cut


def lifecycle(f, today):
    st = f.get("status")
    if st in ("superseded", "deprecated"):
        return st
    u = f.get("until")
    if u and kb_lint.ISO_RE.match(u) and u < today:
        return "expired"
    return None


def file_meta(cfg, relpaths):
    """Per claim-file: inherited {src} default + evidence.review date."""
    meta = {}
    for rel in relpaths:
        m = {"src": None, "review": None}
        try:
            text = (cfg["dir"] / rel).read_text(encoding="utf-8")
            fm_text, _, _ = kb_lint.split_frontmatter(text)
            if fm_text is not None:
                ev = dirty_load(fm_text, allow_flow_style=True).data.get(
                    "evidence") or {}
                if isinstance(ev, dict):
                    m["src"] = (ev.get("defaults") or {}).get("src")
                    r = str(ev.get("review") or "")
                    m["review"] = r if kb_lint.ISO_RE.match(r) else None
        except Exception:
            pass  # lint already reported the breakage; meta stays empty
        meta[rel] = m
    return meta


def render(c, tr, taint, fmeta, today):
    """Return (label, full line) for one served claim."""
    f = c.fields
    if c.kind == "decision":
        basis = " ".join(x for x in (f.get("status"), f.get("date")) if x) or "?"
    elif c.kind == "lesson":
        basis = f"seen {f.get('seen', '?')}, confirmed {f.get('confirmed', '?')}"
    else:
        basis = f.get("v", "unverified")
    if taint:
        conf = f"QUARANTINED:{taint}"
    elif tr in ("T3", "T4"):
        conf = "QUARANTINED"
    else:
        conf = f.get("conf", "-")
    parts = [c.kind, basis, conf, f.get("id", "?")]
    lc = lifecycle(f, today)
    if lc == "deprecated":
        parts.append(f"DEPRECATED: {excerpt(f.get('reason', ''))}")
    elif lc == "superseded":
        parts.append(f"SUPERSEDED-BY: {f.get('superseded-by', '?')}")
    elif lc == "expired":
        parts.append(f"EXPIRED: {f['until']}")
    label = "[" + "|".join(parts) + "]"
    src = f.get("src")
    if not src:
        method = (f.get("v") or "unverified").split()[0]
        naked = c.kind in ("fact", "procedure") and method == "unverified"
        if not naked:
            src = fmeta.get("src")
    return label, f"{label} {c.text}" + (f" (src: {src})" if src else "")


def claim_topic(relpath, kb_path):
    """A claim's topic is its concept FILE: the path under kb_path, without '.md'."""
    t = relpath[len(kb_path):] if relpath.startswith(kb_path) else relpath
    return t[:-3] if t.endswith(".md") else t


def topic_names(relpath, kb_path):
    """The names a --topic value may use for this file: full path under kb/, and stem."""
    full = claim_topic(relpath, kb_path)
    return {full, full.rsplit("/", 1)[-1]}


def passes(c, tr, taint, lc, args, tiers, rank, fmeta, today, topics=None):
    # A4: --topic filters HERE, in the serve loop, and never by narrowing the
    # collected path set. taint and the L7/L8 corpus checks are corpus-scoped, so
    # narrowing paths changes which claims come back QUARANTINED -- a subject filter
    # that silently altered trust derivation would be worse than having none.
    if topics is not None and c.file not in topics:
        return False
    if args.kind and c.kind not in args.kind:
        return False
    if taint and not args.include_quarantined:
        return False
    if tr not in tiers:
        return False
    if lc and not args.history:
        return False
    if args.min_conf is not None and c.kind not in ("decision", "lesson"):
        conf = c.fields.get("conf")  # conf-less facts/procedures excluded
        if conf not in rank or rank[conf] > rank[args.min_conf]:
            return False
    if args.min_seen is not None:
        s = c.fields.get("seen", "")
        if not s.isdigit() or int(s) < args.min_seen:
            return False
    if args.scope is not None and c.fields.get("scope") != args.scope:
        return False
    if args.fresh and fmeta.get("review") and fmeta["review"] < today:
        return False
    return True


def snapshot(cfg, cfg_path, asof, tmp):
    """Materialize kb/ at the last commit <= asof; current config rides along.

    ls-tree names are repo-root-relative; the instance may live in a subdir
    (the test instances do), so the instance prefix is stripped on the way out.
    """
    # #50: this path already HALTED on each empty result, so it was never fail-open --
    # but it blamed every failure on the same cause. The exit code lets each die() name
    # what actually happened, and a git outage no longer masquerades as "not under git"
    # or "no such commit". Behaviour is unchanged; only the diagnosis improves.
    rc, out = kb_lint.run_git(cfg["dir"], "rev-parse", "--show-toplevel")
    top = out.strip()
    if rc != 0 or not top:
        die(f"--as-of needs the instance under git (git exited {rc} in {cfg['dir']})")
    rc, out = kb_lint.run_git(top, "rev-list", "-1", "--before", asof, "HEAD")
    rev = out.strip()
    if rc != 0:
        die(f"--as-of {asof}: git rev-list exited {rc} in {top} -- the history could not "
            "be read, which is NOT the same as there being no commit before that date")
    if not rev:
        die(f"--as-of {asof}: no commit at or before that date")
    root = Path(tmp)
    prefix = cfg["dir"].resolve().relative_to(Path(top).resolve()).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    rc, out = kb_lint.run_git(top, "ls-tree", "-r", "--name-only", rev, "--",
                              prefix + cfg["kb_path"] + "/")
    if rc != 0:
        die(f"--as-of {asof}: git ls-tree exited {rc} at {rev[:12]} -- the tree could not "
            "be listed, which is NOT the same as it being absent")
    names = out.splitlines()
    if not names:
        die(f"--as-of {asof}: no {cfg['kb_path']}/ tree at {rev[:12]}")
    for n in names:
        if n.endswith(".md"):
            dest = root / n[len(prefix):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            rc, blob = kb_lint.run_git(top, "show", f"{rev}:{n}")
            if rc != 0:
                # Writing an empty file here would serve a SILENTLY EMPTY corpus at
                # exit 0, which reads to a consumer as "the KB holds nothing on this".
                die(f"--as-of {asof}: git show exited {rc} for {n} at {rev[:12]} -- "
                    "refusing to snapshot a file whose contents could not be read")
            dest.write_text(blob, encoding="utf-8", newline="\n")
    shutil.copy(cfg_path, root / ".kb-lint.yml")
    return kb_lint.load_config(root / ".kb-lint.yml")


def log_serve(instance_cfg, argv, served):
    p = (instance_cfg["dir"] / instance_cfg["kb_path"] / "_serve"
         / "serve-log.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
             "query": " ".join(argv) or "(default)",
             "served": [{"id": kid, "label": label} for kid, label in served]}
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def csv_arg(value, allowed, what):
    vals = [v.strip() for v in value.split(",") if v.strip()]
    for v in vals:
        if v not in allowed:
            die(f"unknown {what} '{v}' (allowed: {', '.join(allowed)})")
    return vals


def main():
    ap = argparse.ArgumentParser(
        prog="kb-query", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="files/dirs (default: kb_path)")
    # A5: no `choices=` here. The manifest is the authority on what an instance
    # keyword is, and it is operator-owned and machine-local -- a hardcoded tuple
    # duplicated that authority, so an instance the operator had added was refused with
    # argparse's "invalid choice", which never mentions the manifest, and
    # kb_boundary.resolve_instance's own error (which lists the manifest instances) was
    # unreachable. Validation belongs to the resolver, which already fails closed with
    # no default permitted.
    ap.add_argument("--instance",
                    help="resolve the KB root from instances.yml (boundary-checked) "
                         "instead of a positional path -- avoids cwd-based instance "
                         "selection (SEC-003); valid keywords come from the manifest")
    ap.add_argument("--config", help="explicit .kb-lint.yml (default: walk-up)")
    ap.add_argument("--kind", help="comma list: fact,decision,procedure,lesson")
    ap.add_argument("--tier",
                    help="comma list T1..T4 -- REPLACES the default T1,T2 set")
    ap.add_argument("--min-conf", dest="min_conf", metavar="GRADE",
                    help="confidence floor; conf-less facts/procedures are "
                         "excluded while active (conservative serving)")
    ap.add_argument("--min-seen", dest="min_seen", type=int, metavar="N",
                    help="recurrence floor; claims without seen: are excluded")
    ap.add_argument("--topic", help="comma-separated concept files to serve "
                                    "(e.g. build-tooling,dependency-pinning); a subject axis "
                                    "that does not narrow the linted corpus")
    ap.add_argument("--scope", help="exact match on scope:")
    ap.add_argument("--fresh", action="store_true",
                    help="also exclude claims in files whose evidence.review "
                         "date has passed")
    ap.add_argument("--as-of", dest="as_of", metavar="DATE",
                    help="serve the whole corpus at the last commit <= DATE")
    ap.add_argument("--include-quarantined", action="store_true",
                    dest="include_quarantined",
                    help="widen tiers to T3/T4 + lint-failing claims")
    ap.add_argument("--history", action="store_true",
                    help="admit superseded/deprecated/expired claims")
    ap.add_argument("--no-log", action="store_true", dest="no_log",
                    help="skip the serve-log append (tests use this)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the stderr summary")
    args = ap.parse_args()

    # SEC-003: when an instance keyword is given, resolve the KB root from the manifest
    # (boundary-checked, reusing kb_capture's fail-closed resolver) rather than letting
    # the cwd/path walk-up pick an instance -- so a query from a work dir can never
    # silently serve the wrong instance.
    if args.instance:
        if args.paths:
            die("--instance and positional paths are mutually exclusive")
        import kb_boundary
        root, _ = kb_boundary.resolve_instance(args.instance,
                                               kb_boundary.load_manifest())
        args.paths = [str(root / "kb")]

    start = Path(args.paths[0]).resolve() if args.paths else Path.cwd()
    if start.is_file():
        start = start.parent
    cfg_path = Path(args.config) if args.config else kb_lint.find_config(start)
    if not cfg_path or not cfg_path.is_file():
        die("no .kb-lint.yml found (walk-up from target; or pass --config)")
    cfg = kb_lint.load_config(cfg_path)

    if args.kind:
        args.kind = csv_arg(args.kind, cfg["kinds"], "kind")
    if args.tier:
        tiers = set(csv_arg(args.tier.upper(), TIERS, "tier"))
    else:
        tiers = {"T1", "T2"} | ({"T3", "T4"} if args.include_quarantined
                                else set())
    if args.min_conf is not None and args.min_conf not in cfg["confs"]:
        die(f"--min-conf '{args.min_conf}' not in GRADE-4 {cfg['confs']}")
    rank = {v: i for i, v in enumerate(cfg["confs"])}  # index 0 = highest

    instance_cfg = cfg  # the serve-log always lands in the REAL instance
    tmp = None
    try:
        if args.as_of:
            if args.paths:
                die("--as-of serves the whole corpus at a rev; positional "
                    "paths are not supported with it")
            tmp = tempfile.mkdtemp(prefix="kb-asof-")
            cfg = snapshot(cfg, cfg_path, args.as_of, tmp)
        files = kb_lint.collect_files(
            cfg, [Path(p).resolve() for p in args.paths])
        errs, claims, nfiles = kb_lint.run_checks(cfg, files)

        taint_line, taint_file = {}, {}
        for e in errs:
            if e.check == "L1":
                taint_file.setdefault(e.file, e.check)
            else:
                taint_line.setdefault((e.file, e.line), e.check)

        topics = None
        if args.topic:
            wanted = {t.strip() for t in args.topic.split(",") if t.strip()}
            kbp = str(cfg["kb_path"]).replace(chr(92), "/").strip("/") + "/"
            topics, matched = set(), set()
            for f in {c.file for c in claims}:
                names = topic_names(f, kbp)
                hit = names & wanted
                if hit:
                    topics.add(f)
                    matched |= hit
            missing = sorted(wanted - matched)
            if missing:
                # --scope fails silently when nothing matches, which reads exactly like
                # "the KB holds nothing on this" -- a recorded trap: the caller cannot
                # tell an absent topic from an empty one.
                # Name the misses instead; an empty serve stays exit 0, not a usage error.
                print("kb-query: no claims for topic(s): " + ", ".join(missing),
                      file=sys.stderr)
        today = date.today().isoformat()
        fmeta = file_meta(cfg, {c.file for c in claims})
        served = []
        for c in claims:
            tr = tier_of(c)
            tnt = taint_line.get((c.file, c.line)) or taint_file.get(c.file)
            lc = lifecycle(c.fields, today)
            if not passes(c, tr, tnt, lc, args, tiers, rank,
                          fmeta[c.file], today, topics):
                continue
            label, line = render(c, tr, tnt, fmeta[c.file], today)
            print(line)
            served.append((c.fields.get("id", "?"), label))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    if not args.no_log:
        log_serve(instance_cfg, sys.argv[1:], served)
    if not args.quiet:
        print(f"kb-query: served {len(served)} of {len(claims)} claims "
              f"({nfiles} files)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
