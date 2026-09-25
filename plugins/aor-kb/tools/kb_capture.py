#!/usr/bin/env python3
"""kb-capture -- OKF-E v0.1 session-end capture + the employer-boundary routing guard
(pilot P3, PoC, Deliverable E).

Normative: okf-e-profile.md §14 (capture routing + the session-end capture pass). Parser, config
discovery, id assignment and the L1-L8 checks are IMPORTED from kb_lint (same reuse
pattern as kb_query) -- capture adds NO new trust machinery, only the routing guard
and the quarantine-grade floor.

Two structural controls (both in kb_boundary; this tool orchestrates them):
  * DESTINATION (d-route): `--instance {work|personal}` is validated against the manifest
    (instances.yml) and the target repo's LIVE git state -- keyword -> {remote_owner,
    identity, root} -> `git remote get-url origin` owner + `git config user.email` + the
    repo's own `.kb-lint.yml instance:`. Answers "is the repo I'm writing genuinely that
    instance?". Instance identity is the git remote, never a self-declared label.
  * WORKSPACE binding (SEC-002): the CALLING workspace (resolved cwd) is bound to a KB
    choice; a personal-direction write from a work-signalled or unassigned folder is
    refused. Asymmetric -- work-direction flows, personal-direction is the guarded one.
Any mismatch HALTS (exit 2), writes nothing. There is no "write both" mode -- that
absence is a structural guard.

Subcommands:
  route               validate --instance (workspace + destination) and print the root
  verify-boundary     the pre-commit backstop: assert THIS repo's declared instance is
                      consistent with its live remote+identity (fail-closed, exit 1 blocks)
  register-workspace  SEC-002: bind the cwd to a KB choice (operator-confirmed)
  register-work-path  SEC-002: declare a gitless dir WORK for the gate-1 veto
  add                 guarded-route -> grade-floor -> append one claim -> kb-lint fix+check
  reconfirm           record a RE-derivation of a claim already held: a lesson takes a
                      seen: bump + confirmed: refresh, a fact or procedure a v: date
                      refresh (never to a weaker tier), a procedure also a seen: bump;
                      a decision is refused. Edits in place; writes no second claim.

Grade floor (§14.2.6 + §14.5 honesty gate): capture is a RECORDER, not a verifier. It
writes quarantine grades (v: unverified / model-inferred) plus author-asserted freely;
the verified grades ran-tool / read-primary-source require --verified-in-session (the
operator affirming the act happened this session). Upgrades otherwise need a later real
v-event. Exit codes: 0 ok (3 = a required dependency is not installed) - 1 in one of two cases the
output names: REFUSED before the append, nothing written (the lint gates; or a file other than the topic
file that the write or its fix would change has uncommitted changes, or git cannot report its state --
also a --dry-run that would be refused, and `reconfirm` the same way), or WROTE
and then the post-append fix pass failed on a named file - 2 usage/routing/boundary HALT, including an
input refused before anything is written: a double quote in a brace value, a line that would not
parse back to the text and fields given, or a --topic that is not a safe file name.
"""

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

# No bytecode, and it MUST be set before the sibling imports below (#44). This module is
# the one that actually produced the observed pollution: it imports three siblings, so a
# single `kb_capture` run writes kb_lint, kb_boundary and kb_query .pyc -- and NOT its own,
# because a module run as __main__ never writes bytecode for itself. That asymmetry is why
# the three-file set was misread as "the query path" when it is the capture path, and why a
# `--help` reproduction of the wrong tool proves nothing. See kb_query.py for the full
# rationale; the short form is that for a maintainer this tree IS the publish source, and a
# .pyc there embeds the absolute path of the machine that wrote it.
sys.dont_write_bytecode = True

# Self-locate BEFORE importing siblings (K4): under a plugin install the tools
# are reached by absolute path from a SKILL.md, so cwd is the caller's, not tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_lint  # noqa: E402
import kb_boundary  # noqa: E402
import kb_query  # noqa: E402  (TIER_OF_METHOD -- the tier ordering reconfirm compares on)
from kb_lint import die  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# The manifest + destination check + SEC-002 workspace binding live in kb_boundary
# (call them as kb_boundary.X() -- no re-export aliases, so this command module never
# becomes a second public path to the boundary API).

# grades kb-capture may NOT mint without an explicit in-session-verification affirmation
VERIFIED_GRADES = {"ran-tool", "read-primary-source"}
# order in which provided fields render inside the brace (id is auto-assigned by fix)
FIELD_ORDER = ["v", "conf", "src", "seen", "confirmed", "status", "date", "until",
               "scope", "why", "reason", "supersedes", "superseded-by"]


# ---------------------------------------------------------------- claim assembly

def _q(v):
    """Quote a brace value that holds a separator (',' or '}') or a ':', so the parser gives it
    back unchanged.

    A value holding a double quote never reaches here from the CLI: OR5 refuses it, and OR7's
    round-trip guard refuses any line that would not parse back to what was given. The arm that
    quoted a value already starting with a quote (D2) was reachable only through such a value,
    so it went with OR5 (the re-review's N17). The grammar cannot represent an odd number of
    quotes at all; those shapes still fail CLOSED with an L2, pinned by D2-2.
    """
    return f'"{v}"' if ("," in v or "}" in v or ":" in v) else v


# A claim line is assembled as ONE string and render_appended writes it verbatim, so a control
# character in ANY authored value plus a `- [kind]` prefix mints a SECOND claim -- with a grade
# no gate inspected, because grade_floor_ok reads args.v while the forgery rides in args.text.
# _q() quotes for comma/colon/brace but never escapes a newline, so every field is a vector.
# Two guards, deliberately independent: this one rejects at the boundary with a usable message,
# and the single-line assertion in cmd_add makes claim #2 unwritable even if this one is bypassed.
# U+0085 (NEL), U+2028 and U+2029 (the line and paragraph separators) are line breaks too. The parser
# splits on "\n" only, so they passed both guards, while str.splitlines() -- refresh_index's claim
# counter -- and any reader that honours them split the claim there (round 3, T12 = the round-2
# check's M6).
CTRL_CHARS = frozenset(range(0x20)) | {0x7F, 0x85, 0x2028, 0x2029}


def reject_control_chars(name, value):
    """SEC-PC-006. Refuse control and line-break characters in an authored claim value -- ONE capture
    appends exactly ONE claim line, and that has to be structural rather than a convention the caller
    is trusted to honour (the caller is an LLM composing text from untrusted transcript
    content)."""
    if value is None:
        return
    bad = sorted({ord(ch) for ch in str(value) if ord(ch) in CTRL_CHARS})
    if bad:
        codes = ", ".join(f"0x{b:02X}" for b in bad)
        die(f"{name}: control or line-break character(s) {codes} are not allowed in an authored "
            f"claim value. One capture writes exactly ONE claim line; an embedded newline would "
            f"append further claims -- carrying grades no gate inspected. Re-run with the value on "
            f"a single line.")


def reject_unencodable(name, value):
    """Operator ruling OR13, item 1 (2026-09-25): refuse text that cannot be written as UTF-8 -- a
    lone surrogate, half of a character, which a command line can deliver -- before anything is
    written. It passed every other guard, and the write then failed after opening the file for
    writing, which had already emptied it (the round-2 check's Q2-a). The writes now also build their
    bytes before they open a file; this is the message, that is the structure."""
    if value is None:
        return
    try:
        str(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        die(f"{name}: holds U+{ord(str(value)[exc.start]):04X} at position {exc.start}, half of a "
            "surrogate pair, which cannot be written as UTF-8. Re-run with the value re-typed. "
            "Nothing was written.")


def reject_double_quote(name, value):
    """Operator ruling OR5 (2026-09-24): refuse a double quote in any value rendered INTO THE
    BRACE, before anything is written. The grammar cannot represent a quote inside a quoted
    value (see _q), so `--src 'x", supersedes: "<an id>'` rendered as a second KEY inside one
    line -- which lints clean, and on 0.1.8's corpus-wide fix silently retired a claim in another
    file (the build review's V4). SEC-PC-006's guard is about control characters and does not
    cover it. Escaping instead would be a grammar change. The claim text is not a brace value:
    a quote in prose is inert, because the parser finds the brace scanning back from its end."""
    if value is not None and '"' in str(value):
        die(f"{name}: a double quote is not allowed in a brace value -- the brace grammar cannot "
            "hold one inside a quoted value, and one outside a quoted value would start a new "
            "key. Rephrase without it (single quotes are fine). Nothing was written.")


def build_brace(fields):
    pairs = [f"{k}: {_q(v)}" for k, v in fields if v is not None and v != ""]
    return "{" + ", ".join(pairs) + "}" if pairs else ""


def fuse_v_date(v):
    """G4 (Track 0 / T8): a BARE active v: method fuses today's ISO date -- a verification
    is an EVENT, and capture time IS the event time the author is asserting. An explicit
    second token passes through untouched (the author's event record; the linter judges
    it); `unverified` stays bare (L6: there was no verification event to date). An invalid
    bare method also gets the date -- inert, since L6 rejects the method itself first."""
    if not v:
        return v
    parts = v.split()
    if len(parts) == 1 and parts[0] != "unverified":
        return f"{v} {date.today().isoformat()}"
    return v


def grade_floor_ok(kind, v_value, verified_in_session):
    """§14.2.6 + §14.5: capture may not mint verified grades unless the operator
    affirms the verifying act occurred this session."""
    if not v_value:
        return True
    method = v_value.split()[0]
    if method in VERIFIED_GRADES and not verified_in_session:
        die(f"grade floor: capture may not write v: {method} without "
            "--verified-in-session -- the capture pass is a recorder, not a verifier; record how you "
            "actually know (unverified / model-inferred / author-asserted), or affirm the "
            "in-session verification (§14.5 honesty gate)")
    return True


UNSAFE_NAME_CHARS = frozenset('<>:"|?*')
DEVICE_NAME = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", re.IGNORECASE)


def round_trip_problem(cfg, kind, text, fields, line):
    """OR7's round-trip guard (2026-09-25). Parse the EXACT line a capture would append with the
    parser's own function, and say what differs from what was given, or return None.

    One capture must write exactly the claim it was given. Checking the written output instead
    of a list of bad characters closes every break-out at once: a `--text` ending in its own
    `{...}` became the metadata brace when no field rendered one (the re-review's N1 = R2 --
    `v: ran-tool` laundered past the grade floor, a claim in another file retired); an unquoted
    `{` in a field value moved the brace's start and silently dropped the keys before it (R4);
    and whatever shape has not been found yet. The claim's outer whitespace is not content (the
    parser trims it), so the text and values compare trimmed."""
    errs = []
    claims = kb_lint.parse_claim_items([line], 1, "(candidate)", errs, cfg)
    if len(claims) != 1:
        return f"the line parses as {len(claims)} claims"
    c = claims[0]
    want = {k: str(v).strip() for k, v in fields if v is not None and v != ""}
    problems = []
    if c.kind != kind:
        problems.append(f"the kind parses as '{c.kind}'")
    if c.text != text.strip():
        problems.append(f"the text parses as '{c.text}'")
    gained = sorted(set(c.fields) - set(want))
    lost = sorted(set(want) - set(c.fields))
    moved = sorted(k for k in set(want) & set(c.fields) if c.fields[k] != want[k])
    if gained:
        problems.append("keys that were not given: " + ", ".join(gained))
    if lost:
        problems.append("given keys missing: " + ", ".join(lost))
    if moved:
        problems.append("values that parse differently: " + ", ".join(moved))
    return "; ".join(problems) or None


def refuse_round_trip(problem):
    die(f"the claim would not parse back to what was given ({problem}) -- the round-trip guard "
        "(OR7) refuses the write: one capture writes exactly the text and fields it was given. A "
        "--text ending in a {...} block, or a value holding a brace, is read by the parser as "
        "metadata; rephrase it. Nothing was written.")


def topic_path(root: Path, topic: str, reserved, kb_path):
    # SEC-001: --topic MUST stay inside <root>/<kb_path>. An absolute topic (pathlib would
    # discard `root` on join) or a `..` traversal would land the write in ANOTHER repo
    # while the three-way guard still reported "boundary OK" -- a wrong-repo write that
    # defeats d-route. Reject absolute/traversal, then assert containment on the
    # resolved path (belt-and-suspenders; fail-closed, exit 2).
    # A8: a topic must actually name a concept file. An empty or whitespace-only
    # --topic resolved to kb/.md (or kb/"  ".md) and was written with exit 0 -- not a
    # containment escape, but a file rglob then collects and lints forever, holding a
    # claim no reader will look for.
    if not topic.strip():
        die("--topic must name a concept file -- an empty or whitespace-only topic "
            f"resolves to {kb_path}/.md, which no reader will ever find (SEC-001)")
    if os.path.isabs(topic) or ".." in Path(topic).parts:
        die(f"--topic '{topic}' must be a relative slug inside {kb_path}/ (no absolute path, "
            "no '..') -- refusing to escape the instance root (SEC-001)")
    t = topic if topic.endswith(".md") else topic + ".md"
    # OR7 (2026-09-25): --topic must be a safe file name. A '"' crashed the write with an OSError
    # traceback at exit 1 (the re-review's R5), and ':' named an alternate data stream or a
    # drive-relative path. Refused at exit 2, before anything is written.
    for seg in re.split(r"[\\/]", t):
        if set(seg) & UNSAFE_NAME_CHARS or seg != seg.rstrip(" .") or DEVICE_NAME.fullmatch(seg):
            die(f"--topic '{topic}' is not a safe file name: a path segment holds one of "
                "< > : \" | ? *, ends in a dot or a space, or is a reserved device name (OR7). Use a "
                "plain slug such as release-process. Nothing was written.")
    # A1: a reserved file is GENERATED, not authored. check_file returns class
    # 'reserved' and parses NO claims from one, so a claim appended here is never
    # id-assigned, never graded, and refresh_index rebuilds the file from the concept
    # list in the same run -- the capture prints "wrote" then "refreshed" and exits 0
    # having stored nothing. Match the linter's own rule (basename, any depth,
    # kb_lint.check_file) so the two agree about what "reserved" means. OR13, item 2: in any letter
    # case -- on a case-insensitive file system kb/INDEX.md IS kb/index.md, which refresh_index then
    # rebuilt in the same run: "wrote", exit 0, the claim gone (the round-2 check's Q2-b).
    if Path(t).name.lower() in {r.lower() for r in reserved}:
        die(f"--topic '{topic}' targets reserved file '{Path(t).name}' -- reserved "
            f"files are generated by `kb-lint refresh`, so a claim written there is "
            f"discarded rather than stored; capture into a topic file instead "
            f"(reserved: {sorted(reserved)})")
    # Issue 61: the claims directory is the instance's kb_path, as the linter reads it. A
    # literal "kb" sent every capture into a folder a KB with any other kb_path never reads.
    fp = root / kb_path / t
    kb_root = (root / kb_path).resolve()
    # load_config refuses a kb_path that NAMES a folder outside the repository; this is the case
    # it cannot see -- a folder inside the repository that RESOLVES outside it, through a junction.
    if not kb_root.is_relative_to(root.resolve()):
        die(f"kb_path '{kb_path}' resolves to {kb_root}, outside the instance root {root} -- "
            "refused (SEC-001)")
    if not fp.resolve().is_relative_to(kb_root):
        die(f"--topic '{topic}' resolves outside {kb_root} -- refused (SEC-001)")
    return fp


NEW_FILE_FM = "---\ntype: concept\nevidence:\n  profile: {profile}\n---\n"


def render_appended(fp: Path, line, profile):
    """The SINGLE authority for the content a capture append produces. The dry-run lints
    EXACTLY this string and append_claim writes EXACTLY this string -- preview==write is
    structural, not two code paths agreeing by luck (panel #2 arch#2)."""
    if fp.is_file():
        return fp.read_text(encoding="utf-8").rstrip("\n") + "\n" + line + "\n"
    return NEW_FILE_FM.format(profile=profile) + line + "\n"


def append_claim(fp: Path, line, profile):
    # OR13, item 1: the bytes are built BEFORE anything is touched. Path.write_text opened the file
    # for writing -- emptying it -- and only then met the character it could not encode.
    data = render_appended(fp, line, profile).encode("utf-8")
    fp.parent.mkdir(parents=True, exist_ok=True)
    created = not fp.is_file()
    fp.write_bytes(data)
    return created


# ---------------------------------------------------------------- subcommands

def cmd_init(args):
    """P8: create the config root from nothing, non-interactively.

    This is the ONLY subcommand that may run before main()'s load_manifest(), and the ordering
    is the whole point: load_manifest fail-closes on the very file this creates (OPS-008 --
    nothing bootstraps an instance, so the precondition can otherwise only be met by
    hand-authoring a six-way match correctly first time). No guided walk, no prose, no prompts:
    the first-run EXPERIENCE is #21's, not this.

    It never overwrites. An existing instances.yml is the operator's real config, and silently
    replacing it with the placeholder template would drop a boundary control while every
    subsequent message still read normal.
    """
    root = Path(args.root).resolve() if args.root else kb_boundary.config_root()
    template = Path(__file__).resolve().parent.parent / "instances.yml.example"
    if not template.is_file():
        die(f"init: the shipped template {template.name} is missing -- refusing to invent a "
            "manifest, because a guessed one would name repos that are not yours")

    root.mkdir(parents=True, exist_ok=True)
    created, kept = [], []
    for name, body in (("instances.yml", template.read_text(encoding="utf-8")),
                       ("workspaces.jsonl", ""),
                       ("work-paths.txt", "")):
        target = root / name
        if target.is_file():
            kept.append(name)
        else:
            target.write_text(body, encoding="utf-8", newline="\n")
            created.append(name)

    # Validate rather than assert: report success only about what is now on disk.
    missing = [n for n in ("instances.yml", "workspaces.jsonl", "work-paths.txt")
               if not (root / n).is_file()]
    if missing:
        die(f"init: {', '.join(missing)} still absent after init -- refusing to report success")
    text = (root / "instances.yml").read_text(encoding="utf-8")
    if "work:" not in text or "personal:" not in text:
        die("init: the manifest names neither a work nor a personal instance -- refusing to "
            "report success on a file no boundary check could use")
    # #57: this check only ever asked whether the two writable names were PRESENT, so it
    # reported success on a manifest that also carried a third entry -- and init is the one
    # command onboarding tells a new adopter to run. A third entry is legal (it is readable),
    # so this REPORTS rather than refuses: reporting a correct-but-notable state with a
    # non-error status is the point.
    extra = [ln.split(":", 1)[0] for ln in text.splitlines()
             if ln[:1].isalnum() and ":" in ln
             and ln.split(":", 1)[0] not in kb_boundary.WRITABLE_INSTANCES
             and ln.split(":", 1)[0] != "boundary"]

    print(f"config root: {root}")
    if extra:
        print(f"  NOTE: {', '.join(extra)} -- readable, but NOT writable. The boundary policy "
              f"defines exactly two writable directions {list(kb_boundary.WRITABLE_INSTANCES)}; "
              "kb-query can read these, and a capture to one is refused (#57).")
    print(f"  created: {', '.join(created) if created else '(nothing -- already initialised)'}")
    if kept:
        print(f"  kept:    {', '.join(kept)}")
    if "instances.yml" in created:
        print("  NEXT: edit instances.yml. It is the shipped TEMPLATE and names no real repo, "
              "so every write fails closed until you fill it in -- which is the intended state, "
              "not an error.")
    return 0


def cmd_route(args, manifest):
    # route is a WRITE preflight -> guarded. confirm_personal=True: route probes the
    # STRUCTURAL gates (registration + work-signal veto); the per-save human confirm
    # is add's step, not a routing property.
    root, _ = kb_boundary.resolve_instance_guarded(args.instance, manifest,
                                                    confirm_personal=True)
    note = "; a personal save will still confirm" if args.instance == "personal" else ""
    print(f"{args.instance}: {root}  (boundary OK{note})")
    return 0


def cmd_register(args):
    """SEC-002: bind the resolved cwd to a KB choice (work|personal|both). Writes
    the machine-local registry only after --operator-confirm; refuses to register a
    work folder as personal/both (work-signal veto) and refuses a junction cwd."""
    raw = Path.cwd()
    if not args.operator_confirm:
        die("register REFUSED: --operator-confirm required -- registering a folder is an "
            "operator decision; the skill passes this flag only after you answer (SEC-002)")
    if kb_boundary.is_reparse(raw):
        die(f"register REFUSED: {raw} is a junction/symlink -- cd to its real target and "
            "register that (SEC-002 §6)")
    cwd = raw.resolve()
    if args.choice in ("personal", "both"):
        why = kb_boundary.work_signal(cwd)
        if why:
            die(f"register REFUSED: work signal ({why}) -- refusing to register a work "
                f"folder as '{args.choice}' (SEC-002 §6)")
    # read-back BEFORE appending (CODE-001): reject a nesting conflict loudly here rather
    # than letting the next capture die at load with a disconnected overlap HALT.
    clash = kb_boundary.overlapping_registration(cwd, kb_boundary.load_registry())
    if clash:
        die(f"register REFUSED: {cwd} overlaps the registered workspace {clash} -- "
            "workspaces must be flat, non-nested (SEC-002 §6)")
    kb_boundary.append_registry(cwd, args.choice, args.ts or date.today().isoformat())
    print(f"registered {cwd} -> {args.choice}")
    return 0


def cmd_register_workpath(args):
    """SEC-002: declare a directory (and its subtree) WORK for signal purposes, so the
    gate-1 veto fires there even though it is gitless. Uses --path (you can mark a work dir
    without being in it); the SAFE direction only -- it can add a work veto (over-restrict),
    never grant personal -- so a caller-typed --path is acceptable here."""
    if not args.operator_confirm:
        die("register-work-path REFUSED: --operator-confirm required -- declaring a folder "
            "work is an operator decision (SEC-002)")
    raw = Path(args.path)
    if kb_boundary.is_reparse(raw):
        die(f"register-work-path REFUSED: {raw} is a junction/symlink -- pass its real "
            "target (SEC-002)")
    p = raw.resolve()
    if not p.is_dir():
        die(f"register-work-path REFUSED: {p} is not a directory")
    # SEC-D2: refuse a path so broad it contains a KB root -- it would veto personal captures
    # inside the KB itself. List the specific work directory, not an ancestor of a KB.
    for inst, entry in kb_boundary.load_manifest().items():
        root = Path(entry["root"]).resolve()
        if root == p or root.is_relative_to(p):
            die(f"register-work-path REFUSED: {p} contains the '{inst}' KB root {root} -- too "
                "broad; list the specific work directory, not an ancestor of a KB (SEC-002)")
    kb_boundary.append_workpath(p)
    print(f"work-path registered: {p} -- personal saves from here + its subtree are now "
          "structurally refused (gate 1)")
    return 0


def cmd_verify_boundary(args):
    """Pre-commit backstop. Runs from the repo root (hook cwd) or --repo."""
    manifest = kb_boundary.load_manifest()
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()
    inst = kb_boundary.declared_instance(repo)
    if inst is None:
        print(f"kb-capture verify-boundary: BLOCKED -- {repo}/.kb-lint.yml has no "
              "instance: key (required; fail-closed)", file=sys.stderr)
        return 1
    if inst not in manifest:
        print(f"kb-capture verify-boundary: BLOCKED -- declared instance '{inst}' not "
              f"in the manifest {sorted(manifest)}", file=sys.stderr)
        return 1
    kb_boundary._assert_repo_matches(inst, manifest[inst], repo, blocking="hook")
    if not args.quiet:
        print(f"boundary OK: {repo.name} is instance '{inst}'")
    return 0


def preview_lint(cfg, fp, line):
    """Lint `line` appended in memory to `fp`, against the whole corpus, gated on that file.
    Shared by --dry-run and by the write (which appends only on a clean verdict). Returns
    (errs, claims, nfiles, candidate_text); the candidate's own missing id is dropped from
    errs, since the write's fix assigns it."""
    cand = render_appended(fp, line, cfg["profile"])
    # the REPORT set is the one file being written (gated on the touched file, R3); the rest
    # of the corpus is the resolution pool, so a link into another topic file resolves and a
    # pre-existing error elsewhere never refuses this write
    errs, claims, nfiles = kb_lint.run_checks(cfg, [fp], overrides={fp: cand})
    relf = fp.resolve().relative_to(Path(cfg["dir"]).resolve()).as_posix()
    cand_ln = cand.rstrip("\n").count("\n") + 1
    errs = [e for e in errs
            if not (e.fix and e.fix[0] == "id" and e.file == relf and e.line == cand_ln)]
    return errs, claims, nfiles, cand


def cmd_add(args, manifest):
    root, entry = kb_boundary.resolve_instance_guarded(
        args.instance, manifest, confirm_personal=args.confirm_personal)
    grade_floor_ok(args.kind, args.v, args.verified_in_session)
    args.v = fuse_v_date(args.v)  # T8/G4: bare active method -> method + today (floor already ran)

    cfg_path = kb_lint.find_config(root)
    if not cfg_path:
        die(f"no .kb-lint.yml at instance root {root}")
    cfg = kb_lint.load_config(cfg_path)
    if args.kind not in cfg["kinds"]:
        die(f"kind '{args.kind}' not in {cfg['kinds']}")

    fields = [(k, getattr(args, k.replace("-", "_"))) for k in FIELD_ORDER]
    # SEC-PC-006 guard 1: every authored value, at the boundary, before assembly.
    reject_control_chars("--text", args.text)
    reject_control_chars("--topic", args.topic)
    for _k, _v in fields:
        reject_control_chars(f"--{_k}", _v)
    for _k, _v in fields:                # OR5: no quote-break key injection through a brace value
        reject_double_quote(f"--{_k}", _v)
    for _name, _val in (("--text", args.text), ("--topic", args.topic),
                        *((f"--{_k}", _v) for _k, _v in fields)):
        reject_unencodable(_name, _val)  # OR13-1: a lone surrogate, before anything is written
    brace = build_brace(fields)
    fp = topic_path(root, args.topic, cfg["reserved"], cfg["kb_path"])
    line = f"- [{args.kind}] {args.text}" + (f" {brace}" if brace else "")
    # SEC-PC-006 guard 2, structural: whatever the inputs were, what we are about to render must
    # be a single claim line. Independent of guard 1 on purpose -- bad output stays impossible
    # rather than merely improbable. str.splitlines() knows every line break, U+2028 included (T12).
    if line.splitlines() != [line]:
        die("assembled claim line is not a single line -- refusing to write, since one capture "
            "appends exactly one claim (SEC-PC-006)")
    # OR7: the line must parse back to exactly the text and fields given -- before the dry run,
    # the preview and the write, so a refused line is never previewed as GREEN either.
    problem = round_trip_problem(cfg, args.kind, args.text, fields, line)
    if problem:
        refuse_round_trip(problem)

    # T8/G4: lint the candidate appended IN-MEMORY to the REAL target file (inheriting its
    # defaults:{src}), against the REAL corpus (L7 supersession targets + L8 id uniqueness
    # resolve). A stub/throwaway context would false-fail inherited-src facts and false-pass
    # id collisions -- the exact dry-run != write divergence T8 is about. Nothing is written
    # by the preview: no file under kb_path, no index refresh, no HEAD move.
    # SCOPE (issue 73, ruling R3/R4 of 2026-09-24): the preview and the write judge the SAME
    # thing -- the candidate against the whole corpus, gated on this file. The write runs
    # this preview FIRST and appends only on a clean verdict (R4: a refusal refuses, it does
    # not write and then report). The write-time fix that follows the append resolves links
    # corpus-wide too and follows a link into the file its target lives in (kb_lint.apply_fixes),
    # so a supersede across topic files is a normal write, not a sticky error. An earlier
    # comment here recorded the opposite intent ("keep FIX and GATE on the touched set");
    # the 2026-09-23 panel traced that shape to a half-linked corpus and it was ruled out.
    errs, claims, nfiles, cand = preview_lint(cfg, fp, line)
    rel = fp.relative_to(root).as_posix()
    swept = swept_files(cfg, fp, claims)
    if args.dry_run:
        created = "new file" if not fp.is_file() else "append"
        print(f"[dry-run] {args.instance} -> {fp} ({created})")
        print(f"  {line}")
        rc = kb_lint.report(errs, claims, nfiles, quiet=args.quiet)
        if rc == 0 and swept:
            print("[dry-run] the write would be REFUSED, writing nothing: " + swept_account(swept))
            return 1
        if not args.quiet:
            print("[dry-run] candidate lints GREEN in the real corpus (id assigned at write)"
                  if rc == 0 else
                  "[dry-run] candidate FAILS the real lint -- the write would be REFUSED the "
                  "same way, writing nothing")
        return rc
    gating = [e for e in errs if e.cls == "G"]
    if gating:
        kb_lint.report(errs, claims, nfiles, quiet=False)
        relf = fp.resolve().relative_to(Path(cfg["dir"]).resolve()).as_posix()
        cand_ln = cand.rstrip("\n").count("\n") + 1
        print(f"REFUSED -- NOTHING was written to {rel}. "
              + gating_account(gating, claims, relf, cand_ln, rel), file=sys.stderr)
        return 1
    if swept:
        print(f"REFUSED -- NOTHING was written to {rel}. " + swept_account(swept), file=sys.stderr)
        return 1

    created = append_claim(fp, line, cfg["profile"])
    print(f"wrote {'new ' if created else ''}{rel} (instance '{args.instance}', boundary OK)")
    # id assignment + lint on the touched file; the fix follows a link into another file
    outcome = {}
    rc = kb_lint.apply_fixes(cfg, [fp], do_format=True, quiet=args.quiet, outcome=outcome)
    # P4 (a2): keep the infuse artifact (<kb_path>/index.md) current after every capture. Lives in
    # kb_lint so it reuses the linter's reserved-file set + claim predicate (ARCH-004).
    idx = kb_lint.refresh_index(cfg)
    if not args.quiet:
        print(f"refreshed {idx.relative_to(root).as_posix()}")
    if rc:
        # A11 (S3): exit 1 after a write is a different case from a refusal, and says so
        failed = ", ".join(outcome.get("failed") or []) or "a file named above"
        print(f"WROTE {rel} -- the claim is on disk; then the fix pass failed on {failed} (the "
              "lines above say why). Exit 1 here means written, and the named file does not lint: "
              "fix it before you commit.", file=sys.stderr)
    return rc


def followed_files(cfg, fp, claims):
    """{relpath: [target id, ...]} for every file OTHER than `fp` that the fix after this write would
    write into. `claims` are fp's claims as they will stand after the write (the preview's candidate).

    The fix writes into another file only to complete a link: the reciprocal, and the status flip it
    entails, land on the claim at the link's other end (kb_lint.corpus_checks' reciprocal pass, then
    _apply_claim_edits). So the files are those holding the other end of a link from a claim in fp
    whose pair is not complete -- the new claim's --supersedes or --superseded-by, or a claim already
    in fp: a legacy half-link, or an id-less claim whose link the fix completes once its id is
    assigned. A target resolves the way corpus_checks resolves it: an id in fp itself first, then the
    first claim-file claim that holds it. A pair that cannot be completed (a tombstone, a second
    successor) gates in the preview, and that refusal comes first."""
    here = fp.resolve()
    own = {c.fields.get("id") for c in claims if c.fields.get("id")}
    by_id = {}
    for f in kb_lint.corpus_files(cfg):
        if f.resolve() == here:
            continue
        try:
            fclaims, _errs, meta = kb_lint.check_file(f, cfg)
        except SystemExit:
            continue   # outside the config root: it resolves nothing (run_checks' pool does the same)
        if meta["class"] != "claim":
            continue
        for c in fclaims:
            kid = c.fields.get("id")
            if c.kind_valid and kid and cfg["id_format"].match(kid):
                by_id.setdefault(kid, c)
    out = {}
    for c in claims:
        cid = c.fields.get("id")
        for key, recip in (("supersedes", "superseded-by"), ("superseded-by", "supersedes")):
            tgt = c.fields.get(key)
            if not tgt or tgt in own or tgt not in by_id:
                continue
            other = by_id[tgt]
            if cid and other.fields.get(recip) == cid:
                continue   # the pair is complete: the fix writes nothing there
            out.setdefault(other.file, []).append(tgt)
    return out


def swept_files(cfg, fp, claims):
    """Operator ruling OR12 (2026-09-25), which widens OR10: [(relpath, target ids, why)] for every
    file other than the capture's topic file that its write or its fix would change and that differs
    from HEAD -- staged or not, or untracked. The capture refuses before writing anything, and its dry
    run says it would.

    The fix writes a back-link into such a file, and the capture page's step 5 stages it whole, so
    uncommitted edits there -- another session's, or this session's own from earlier in a batch --
    would ride into this commit (the re-review's N4; the round-2 check's Q5 reading 2 and M2). Every
    route counts: --supersedes, --superseded-by, and the fix following a link from a claim already in
    the topic file. A git that cannot answer fails closed.

    Not checked: the topic file itself (OR12: it stays documented -- step 5 has the agent look at
    `git diff HEAD` on it), and the generated kb/index.md, which every capture rebuilds whole from the
    corpus and which holds no authored content (a stated default of round 3: counting it would refuse
    every batch's second capture, a cost OR12 does not name)."""
    out = []
    for relp, ids in sorted(followed_files(cfg, fp, claims).items()):
        rc, st = kb_lint.run_git(cfg["dir"], "--no-optional-locks", "status", "--porcelain",
                                 "--untracked-files=all", "--", relp)
        if rc != 0:
            out.append((relp, ids, f"whose state git could not report (git exited {rc})"))
        elif st.strip():
            out.append((relp, ids, "which has uncommitted changes (staged or not)"))
    return out


def swept_account(swept):
    files = "; ".join(f"{relp} (onto {', '.join(ids)}), {why}" for relp, ids, why in swept)
    return (f"The fix after this write would also write into {files}. Staging that whole would carry "
            "those changes into your commit (OR12). If they are this session's own earlier captures or "
            "reconfirms, commit them first, then continue; if they are not, stop and ask the operator.")


def gating_account(gating, claims, relf, cand_ln, rel):
    """Who gates, for the REFUSED line (A11, stated default S3 of the 2026-09-24 build review):
    the new claim, a claim already in the topic file, or both -- by id and line, with the checks.
    A capture into a file carrying any gating error is refused, so "the claim would not lint"
    was wrong whenever the gating line was about a different claim."""
    ids = {(c.file, c.line): c.fields.get("id") for c in claims}
    new = sorted({e.check for e in gating if e.file == relf and e.line == cand_ln})
    others = {}
    for e in gating:
        if e.file == relf and e.line == cand_ln:
            continue
        who = ids.get((e.file, e.line)) or ("the frontmatter" if e.line == 1
                                            else "the id-less claim")
        others.setdefault(f"{who} at {e.file}:{e.line}", set()).add(e.check)
    parts = ([f"the new claim ({', '.join(new)})"] if new else []) + [
        f"{who} ({', '.join(sorted(checks))})" for who, checks in others.items()]
    text = "Gating: " + "; ".join(parts) + " -- the gating lines above say why."
    if others and not new:
        text += (" That is not the new claim, which has no gating finding of its own: a capture "
                 f"into a file that does not lint is refused until {rel} is fixed.")
    return text


# ------------------------------------------------------------------------- reconfirm
# The T3 re-learn path: record that something the KB ALREADY holds was re-derived, rather
# than writing a second claim at seen: 1 -- which is the very thing the T3 rule forbids and
# the only route available before this existed.
#
# Spec: the reconfirm rulings in the design record.

# Tier ordering for J2's monotonicity gate. It is derived from kb_query.TIER_OF_METHOD and
# NEVER from .kb-lint.yml's `verified_by` list: that enum is a validation WHITELIST which
# happens to put model-inferred ahead of author-asserted, while the tier map makes
# author-asserted T2 and model-inferred T3. A comparator built on enum index is silently
# inverted for exactly that pair, in the one requirement this subcommand exists to enforce
# enforce. Importing the map is the fix; the enum keeps its order and gains a comment.
TIER_RANK = {"T1": 1, "T2": 2, "T3": 3, "T4": 4}


def claim_tier(kind, v_value):
    """The tier a claim sits at. Mirrors kb_query.tier_of: lesson and decision are pinned at a
    constant T2 and never read v: (it is lint-FORBIDDEN on both), so 'method-monotonic' is
    definable only over fact and procedure."""
    if kind in ("decision", "lesson"):
        return "T2"
    method = (v_value or "unverified").split()[0]
    return kb_query.TIER_OF_METHOD.get(method, "T4")


def instance_claims(root):
    """Locate every claim in the instance by id.

    Re-implemented here rather than lifted from the development tooling, which is not part of
    this payload. It is five lines and depends only on kb_lint, so the copy is cheaper than
    the dependency -- but it is a further copy of a shape that exists elsewhere, and nothing
    detects divergence between copies. Noted rather than hidden.

    NOT kb_lint.corpus_ids(): that returns id STRINGS, for id minting. It cannot locate a claim,
    and a Claim is what an in-place edit needs -- .file, .kind, .fields and .brace_open/.brace_close
    as exact (line, raw column) pairs.
    """
    cfg = kb_lint.load_config(kb_lint.find_config(root))
    files = kb_lint.collect_files(cfg, [])
    _errs, claims, _n = kb_lint.run_checks(cfg, files)
    return cfg, {c.fields["id"]: c for c in claims if c.fields.get("id")}


# A brace value is either a quoted run (which may contain commas) or a bare run up to the next
# separator. Anchoring on the separator matters: an unanchored `seen:` also matches a longer
# token, and a claim's TEXT line may itself contain a brace in prose.
_BRACE_VALUE = r'("(?:[^"]*)"|[^,}]*)'


def _field_re(key):
    return re.compile(r'((?:\{|,\s)' + re.escape(key) + r':\s*)' + _BRACE_VALUE)


def apply_to_brace(brace, key, value):
    """PURE: return `brace` with `key` set to `value` -- replacing an existing value, or
    inserting the key at its FIELD_ORDER position when absent. No I/O, so --dry-run previews
    what the write produces by running the write's own code rather than a second copy of it.

    This is the value-REPLACING counterpart of the additive brace annotator used elsewhere,
    which can only ADD a key: used to bump an existing `seen: 1` it appends a SECOND `seen:`,
    and the duplicate-key rule then rejects the result -- loudly, but only after the file has
    been written. The reuse is the obvious move and it is wrong.
    """
    rx = _field_re(key)
    if rx.search(brace):
        return rx.sub(lambda m: m.group(1) + _q(str(value)), brace, count=1)
    idx = FIELD_ORDER.index(key)
    prev = None
    for k in re.findall(r'(?:\{|,\s)([A-Za-z][\w-]*):\s', brace):
        if k not in FIELD_ORDER or FIELD_ORDER.index(k) < idx:
            prev = k
    if prev is None:
        die(f"cannot place '{key}': the brace has no field to anchor the insertion after")
    m = _field_re(prev).search(brace)
    return brace[:m.end()] + f", {key}: {_q(str(value))}" + brace[m.end():]


def claim_lines(cfg, c):
    """The claim's path and lines. The brace sits entirely on the claim's own indented
    continuation line, so every edit is scoped there and the claim TEXT above cannot match."""
    p = Path(cfg["dir"]) / c.file
    return p, p.read_text(encoding="utf-8").split("\n")


def splice_brace(lines, c, brace):
    ln, lo = c.brace_open
    _ln2, hi = c.brace_close
    out = list(lines)
    out[ln - 1] = out[ln - 1][:lo] + brace + out[ln - 1][hi + 1:]
    return out


def cmd_reconfirm(args, manifest):
    # Same boundary + SEC-002 workspace binding as `add`. FIRST, before anything reads the
    # corpus -- this is what "boundary-checked exactly like add" means concretely.
    root, _entry = kb_boundary.resolve_instance_guarded(
        args.instance, manifest, confirm_personal=args.confirm_personal)

    for _name, _val in (("--v", args.v), ("--src", args.src), ("--conf", args.conf)):
        reject_control_chars(_name, _val)
        reject_double_quote(_name, _val)     # OR5: these reach the brace through the same _q
        reject_unencodable(_name, _val)      # OR13-1: a lone surrogate emptied the file here too

    cfg, claims = instance_claims(root)
    c = claims.get(args.claim_id)
    if c is None:
        die(f"{args.claim_id}: no claim with that id in this instance -- reconfirm resolves "
            f"its target by id across the corpus, not by --topic. Check the id -- two live "
            f"ids can differ by a single character, so copy it rather than retyping it.")

    today = date.today().isoformat()

    # J1 -- a decision is REFUSED, loudly. v: is lint-forbidden there so P5's fact/procedure
    # branch is illegal; seen:/confirmed: would lint clean but nothing reads them on a
    # decision, tier_of pins the kind at a constant T2, and a decision is RATIFIED rather than
    # re-observed. A silent no-op here would let an unavailable oracle SATISFY the check
    # instead of failing it, which is the failure mode this refusal exists to avoid.
    if c.kind == "decision":
        die(f"{args.claim_id}: kind 'decision' is refused -- a decision is ratified, not "
            f"re-observed. Nothing reads seen:/confirmed: on a decision and v: is forbidden "
            f"there (L4). To change a decision, supersede it. Nothing was written.")

    recorded_v = c.fields.get("v")
    recorded_tier = claim_tier(c.kind, recorded_v)
    method_now = (args.v or "").split()[0] if args.v else None

    if c.kind == "lesson":
        if args.v:
            die(f"{args.claim_id}: --v is not accepted on a lesson -- L4 forbids v: on that "
                f"kind and tier_of pins it at a constant T2. A lesson records recurrence "
                f"(seen:), not a verification method.")
    elif method_now:
        # J2 -- monotonic on TIER, never on enum position. T1 covers both ran-tool and
        # read-primary-source, so a move between those two is an EQUALITY and is accepted in
        # either direction; it was never a tie to break.
        offered_tier = kb_query.TIER_OF_METHOD.get(method_now, "T4")
        if TIER_RANK[offered_tier] > TIER_RANK[recorded_tier]:
            die(f"{args.claim_id}: tier downgrade refused -- recorded {recorded_tier} "
                f"(v: {recorded_v}), offered {offered_tier} (v: {method_now}). A re-derivation "
                f"may confirm a claim or strengthen it, never weaken what is already on "
                f"record. Omit --v to refresh the date at the recorded method.")
        # The honesty gate, named with the claim id so the message meets the refusal contract.
        # grade_floor_ok below is the shared rule and stays the backstop -- two deliberately
        # independent guards, the same shape as SEC-PC-006's pair in cmd_add.
        if method_now in VERIFIED_GRADES and not args.verified_in_session:
            die(f"{args.claim_id}: v: {method_now} requires --verified-in-session. reconfirm "
                f"IS the 'later real v-event' the grade floor asks for, but the affirmation "
                f"is still the operator's: record it only if the verifying act happened this "
                f"session.")
        grade_floor_ok(c.kind, args.v, args.verified_in_session)

    # ---- the stamp the idempotence guard compares. It must EXIST and be a real date before
    # the guard can decide anything: an oracle that cannot answer must fail the check, never
    # satisfy it.
    upgrade = bool(method_now) and TIER_RANK[
        kb_query.TIER_OF_METHOD.get(method_now, "T4")] < TIER_RANK[recorded_tier]

    if c.kind == "lesson":
        stamp = c.fields.get("confirmed")
    else:
        # J11 -- the last whitespace token of a BARE method is the method NAME, which can never
        # equal a date, so the comparison below would pass silently without deciding anything.
        _parts = (recorded_v or "").split()
        stamp = _parts[-1] if len(_parts) > 1 else None
        if stamp is None:
            die(f"{args.claim_id}: the recorded v: is '{recorded_v}' and carries no date, so "
                f"the same-day guard has no date to compare against and would pass without "
                f"deciding anything. Repair the claim's v: first -- reconfirm refreshes a "
                f"recorded date, it does not invent one.")

    # J10 -- a stamp in the future is an error the linter exists to catch (it rejects future
    # dates). Refuse rather than write: the write would pull the stamp BACKWARDS to today and
    # destroy that evidence before reconfirm's own post-write lint ever saw it. Comparison is
    # ISO string ordering, which is why a malformed date is the linter's job and not this one.
    if stamp and stamp > today:
        die(f"{args.claim_id}: the recorded stamp {stamp} is in the FUTURE (today is {today}). "
            f"Refusing: reconfirming would rewrite it backwards to today and erase an error "
            f"the linter is there to catch. Fix the claim's date first.")

    # J4 -- idempotence. seen: counts SESSIONS in which a lesson re-fired, not events, so a
    # same-day repeat would double-count it. Two ways past it:
    #   * a METHOD UPGRADE on a fact or procedure (J4's own exception) -- a fact has no seen: to
    #     double-count and a stronger verification genuinely happened;
    #   * --again (J9), the operator affirming a separate occasion on the same calendar day.
    # J9 exists because the guard's UNIT is the session while its INSTRUMENT is the calendar
    # day, and a switch puts several sessions in one day by design. Under-counting is the worse
    # direction here: the promotion gate's whole dynamic range is 1 -> 2, so one lost occasion
    # is total signal loss, and a refusal writes nothing and leaves no trace -- whereas an
    # over-count still faces the operator's confirm at the propose-only promotion gate.
    if not upgrade and not args.again and stamp == today:
        die(f"{args.claim_id}: already reconfirmed today ({stamp}) -- refusing a same-day "
            f"same-method repeat. seen: counts sessions a lesson re-fired, not events. Pass "
            f"--again if this really is a separate session on the same calendar day, or "
            f"upgrade the method with --v.")

    # ---- the change, COMPUTED before it is applied, so --dry-run and the write share one
    # code path and cannot drift apart.
    if c.kind == "lesson":
        changes = [("seen", int(c.fields.get("seen", "0")) + 1), ("confirmed", today)]
    else:
        new_v = fuse_v_date(args.v) if args.v else f"{(recorded_v or '').split()[0]} {today}"
        changes = [("v", new_v)]
        if args.src:
            changes.append(("src", args.src))
        if args.conf:
            changes.append(("conf", args.conf))
        # J3 -- a procedure also takes a seen: bump, created at 1 when absent. Without it no
        # procedure can ever reach the seen >= 2 promotion gate that seen: exists on that kind
        # solely to express, and none of the live procedures carry the field. A stated default
        # with veto: reversing it is this branch and its fixture.
        if c.kind == "procedure":
            cur = c.fields.get("seen")
            changes.append(("seen", int(cur) + 1 if cur else 1))

    fp, lines = claim_lines(cfg, c)
    before_brace = lines[c.brace_open[0] - 1][c.brace_open[1]:c.brace_close[1] + 1]
    after_brace = before_brace
    for _k, _v in changes:
        after_brace = apply_to_brace(after_brace, _k, _v)
    candidate = splice_brace(lines, c, after_brace)
    # OR7's round-trip guard, for the brace reconfirm rewrites: the edited claim must parse back to
    # its own text and exactly its old fields with the changes applied. An unquoted '{' in --src
    # moved the brace's start, dropped the id and v: into the prose, and the post-write fix then
    # minted a NEW id for the claim.
    want = {k: str(v).strip() for k, v in c.fields.items()}
    want.update({k: str(v).strip() for k, v in changes})
    cand_claims = kb_lint.check_file(fp, cfg, text="\n".join(candidate))[0]
    parsed = next((p for p in cand_claims if p.line == c.line), None)
    if parsed is None or parsed.text != c.text or parsed.fields != want:
        refuse_round_trip("the edited brace parses as "
                          + ("no claim" if parsed is None else ", ".join(sorted(parsed.fields))))
    # OR12: reconfirm's post-write fix follows every incomplete link from the claims in this file, as
    # add's does, so it refuses on the same files, before anything is written (the round-2 check's M2).
    swept = swept_files(cfg, fp, [p for p in cand_claims if p.kind_valid])
    relfp = fp.relative_to(root).as_posix()

    if args.dry_run:
        print(f"[dry-run] {args.claim_id} ({c.kind}) -> {relfp}")
        print(f"  before  {before_brace}")
        print(f"  after   {after_brace}")
        # Lint the candidate IN MEMORY against the real corpus, the way add's dry-run does: a
        # preview that reports a different verdict from the write is worse than no preview.
        files = kb_lint.collect_files(cfg, [])
        errs, claims, nfiles = kb_lint.run_checks(
            cfg, files, overrides={fp: "\n".join(candidate)})
        rc = kb_lint.report(errs, claims, nfiles, quiet=args.quiet)
        if rc == 0 and swept:
            print("[dry-run] the write would be REFUSED, writing nothing: " + swept_account(swept))
            return 1
        if not args.quiet:
            print("[dry-run] candidate lints GREEN in the real corpus; nothing was written"
                  if rc == 0 else
                  "[dry-run] candidate FAILS the real lint -- the write would be rejected "
                  "the same way")
        return rc
    if swept:
        print(f"REFUSED -- NOTHING was written to {relfp}. " + swept_account(swept), file=sys.stderr)
        return 1

    kb_lint.write_utf8(fp, "\n".join(candidate))   # OR13-1: encoded before the file is opened
    print(f"reconfirmed {args.claim_id} ({c.kind}) in {relfp} (instance '{args.instance}', boundary OK)")

    # Post-write lint gate, deliberately WITHOUT the format pass and WITHOUT refresh_index
    # (DoD 10 asks for both to be decisions, not accidents):
    #   do_format=False -- reconfirm's edit is one continuation line; a whole-file reformat is
    #     not its business, and the KB pre-commit hook runs `fix` without --format too, so the
    #     write and the hook's re-check stay the same shape.
    #   no refresh_index -- reconfirm adds no claim and no topic, so the index cannot have gone
    #     stale. There is a recorded incident where a capture appended a claim, refresh_index
    #     rebuilt the file three lines later, and the tool printed 'wrote', then 'refreshed', and
    #     exited 0 having stored nothing.
    return kb_lint.apply_fixes(cfg, [fp], do_format=False, quiet=args.quiet)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        prog="kb-capture", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p_route = sp.add_parser("route", help="validate --instance, print resolved root")
    p_route.add_argument("--instance", required=True)

    p_vb = sp.add_parser("verify-boundary", help="pre-commit boundary backstop")
    p_vb.add_argument("--repo", help="repo root (default: cwd)")
    p_vb.add_argument("--quiet", action="store_true")

    p_add = sp.add_parser("add", help="append one boundary-checked claim + lint")
    p_add.add_argument("--instance", required=True,
                       help="work|personal -- REQUIRED, no default (§14.2.1)")
    p_add.add_argument("--topic", required=True,
                       help="topic file under the instance's kb_path (slug or path)")
    p_add.add_argument("--kind", required=True)
    p_add.add_argument("--text", required=True, help="claim text (no brace)")
    for k in ("v", "conf", "src", "seen", "confirmed", "status", "date", "until",
              "scope", "why", "reason", "supersedes", "superseded-by"):
        p_add.add_argument(f"--{k}")
    p_add.add_argument("--verified-in-session", action="store_true",
                       dest="verified_in_session",
                       help="affirm ran-tool/read-primary-source was actually done this "
                            "session (§14.5 honesty gate)")
    p_add.add_argument("--confirm-personal", action="store_true",
                       dest="confirm_personal",
                       help="SEC-002: affirm this note really is personal (required for a "
                            "personal save from a 'both'/unattested folder; the skill passes "
                            "it only after the operator says 'yes, personal')")
    p_add.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="preview the exact rendered line AND its real lint verdict "
                            "(candidate linted in-memory against the real corpus; writes "
                            "nothing; exit 1 if the write would be lint-rejected)")
    p_add.add_argument("--quiet", action="store_true")

    p_rc = sp.add_parser("reconfirm",
                         help="re-derived a claim already held? bump it in place")
    p_rc.add_argument("claim_id", help="the id to reconfirm (positional; resolved corpus-wide)")
    p_rc.add_argument("--instance", required=True,
                      help="work|personal -- REQUIRED, no default (§14.2.1)")
    p_rc.add_argument("--v", help="method for THIS re-derivation. Omit to keep the recorded "
                                  "method and refresh only its date. Not accepted on a lesson.")
    p_rc.add_argument("--src", help="origin of this re-derivation (L5, on a method upgrade)")
    p_rc.add_argument("--conf", help="confidence after this re-derivation (L5)")
    p_rc.add_argument("--verified-in-session", action="store_true",
                      dest="verified_in_session",
                      help="affirm ran-tool/read-primary-source was actually done this "
                           "session (§14.5 honesty gate)")
    p_rc.add_argument("--confirm-personal", action="store_true", dest="confirm_personal",
                      help="SEC-002: affirm this really is personal")
    p_rc.add_argument("--again", action="store_true",
                      help="affirm this is a SEPARATE occasion on the same calendar day, "
                           "overriding the same-day guard. seen: counts sessions, and a "
                           "switch puts several sessions in one day.")
    p_rc.add_argument("--dry-run", action="store_true", dest="dry_run",
                      help="preview the exact brace before and after AND its real lint "
                           "verdict (candidate linted in-memory against the real corpus; "
                           "writes nothing; exit 1 if the write would be lint-rejected)")
    p_rc.add_argument("--quiet", action="store_true")

    p_reg = sp.add_parser("register-workspace",
                          help="SEC-002: bind the cwd to a KB choice (work|personal|both)")
    p_reg.add_argument("--choice", required=True, choices=kb_boundary.CHOICES)
    p_reg.add_argument("--operator-confirm", action="store_true",
                       dest="operator_confirm",
                       help="the operator's explicit assent (fail-closed without it)")
    p_reg.add_argument("--ts", help="record timestamp (default: today; tests pin it)")

    p_wp = sp.add_parser("register-work-path",
                         help="SEC-002: declare a (gitless) dir WORK for the gate-1 veto")
    p_wp.add_argument("--path", required=True, help="the work directory (subtree included)")
    p_wp.add_argument("--operator-confirm", action="store_true", dest="operator_confirm",
                      help="the operator's explicit assent (fail-closed without it)")

    p_init = sp.add_parser("init",
                           help="create the config root from nothing (non-interactive; P8)")
    p_init.add_argument("--root", help="write to this directory instead of the resolved config "
                                       "root. Echoed on every run. It changes only where init "
                                       "WRITES -- no boundary check reads it, so it grants "
                                       "nothing; fixtures use it because the config root is "
                                       "deliberately not environment-overridable.")

    args = ap.parse_args()
    # init MUST dispatch before load_manifest() below: it exists to create the file that call
    # fail-closes on (OPS-008). The development test suite pins this ordering.
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "verify-boundary":
        return cmd_verify_boundary(args)
    if args.cmd == "register-workspace":
        return cmd_register(args)
    if args.cmd == "register-work-path":
        return cmd_register_workpath(args)
    manifest = kb_boundary.load_manifest()
    if args.cmd == "route":
        return cmd_route(args, manifest)
    if args.cmd == "add":
        return cmd_add(args, manifest)
    if args.cmd == "reconfirm":
        return cmd_reconfirm(args, manifest)


if __name__ == "__main__":
    sys.exit(main())
