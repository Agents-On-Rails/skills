---
name: aor-kb-capture
description: Record something you learned this session as a properly evidence-graded KB claim, routed to the personal or work instance. Routing is fail-closed but it is not a boundary guarantee - it never reads the claim's content and cannot tell work knowledge from personal, it takes its signal from the working directory the command runs in, and its signal lists are empty until you fill them. Thin wrapper over the OKF-E kb_capture CLI.
allowed-tools: Bash, AskUserQuestion
argument-hint: "<personal|work> <kind> \"<claim>\" [how-known] [conf] [source]  |  help  |  (or just describe it in a sentence)"
license: MIT
compatibility: "Windows only. The tools resolve their config root through Win32 known-folder APIs, and the SEC-002 junction guard raises off Windows rather than silently passing, so they do not run on macOS or Linux. Needs Python 3.9 or later on PATH as python, plus the pinned strictyaml in requirements.txt."
---

# /aor-kb-capture — record an evidence-graded KB claim

Thin wrapper over the OKF-E `kb_capture` CLI. **The employer-boundary control lives in the tool, not
here** — it validates the instance against the target repo's live git remote + identity and HALTs
fail-closed on any mismatch. Your job is to gather the right fields (kind-specific), show a dry-run,
get an explicit confirmation, and let the tool do the boundary-checked write. **Never hand-edit KB
files; never guess the instance.**

## Constants (preflight — verify before running)

The tool ships inside this plugin, in `tools/` at the **plugin root** — the directory two levels
above this SKILL.md (`../../` from here). Resolve that once and use the resolved path.

```
PY  = python -E -B                        (from PATH)
CAP = <plugin-root>/tools/kb_capture.py
```
`-E` ignores `PYTHON*` environment variables, so a `PYTHONPATH` cannot shadow the pinned
`strictyaml` that parses every file the boundary check compares against. `-B` is a second
layer against `.pyc` in the shipped tree — the tools already set `sys.dont_write_bytecode`
themselves, so the flag is what still covers a run that dies before reaching that line.
Both are part of the command — do not drop them.
If `python` is not on PATH, or `tools/kb_capture.py` is not where this says, say so plainly and
stop — do not guess an alternative interpreter or path.

## Operating discipline

- **Invocation acknowledgement:** first line = ``Running as `aor-kb-capture` → <personal|work> KB.``
- **Help:** if the argument is `help`, `--help`, `?`, or **empty with no capturable content**, print the HELP block below verbatim and stop.
- **Instance is required and never guessed.** Resolve `work` or `personal` from the user. If the session is mixed or the instance is ambiguous, **HALT and ask** — do not default.
- **Recorder, not verifier.** Record how you actually know something; never inflate a grade.
- **Don't `cd` before invoking** — there is no `--workspace` override; the SEC-002 guard trusts the real shell cwd, so run the tool from the folder you're actually working in.

## SEC-002 workspace binding — how to handle the two new HALTs

The tool ties the **folder you're working in** to a KB. The first capture from a new folder, and every
personal save from a mixed/unassigned folder, needs a one-time or one-tap operator answer. **Relay these
in plain language — the operator sees only "this folder" + "work KB / personal KB", never a path/jargon.**

- **`workspace-choice HALT … not assigned to a KB`** → the folder is unregistered. Ask via `AskUserQuestion`:
  *"Which knowledge base should notes from **`<folder leaf name>`** go to? I only ask once."* →
  **Work KB** / **Personal KB** / **Not now** (Not now = cancel, nothing saved). On an answer, run
  ``<PY> <CAP> register-workspace --choice <work|personal> --operator-confirm`` **from that same folder**,
  then retry from step 1. (`both` is not offered here — reach it only via the guided upgrade below.)
  - If the HALT includes an **advisory work-pattern note** (the folder name matched one of your
    configured `advisory_name_patterns`, e.g. `acme-*`), relay it as a
    soft nudge only — *"(the name looks like it could be work — your call)"* — it decides nothing.
  - For a **gitless work folder** you want structurally protected (so a future personal save from it is
    hard-refused, not just confirm-gated), offer to run
    ``<PY> <CAP> register-work-path --path <dir> --operator-confirm`` — the operator's explicit "this is a
    work directory" declaration.
- **`workspace-policy HALT: work signal … -> personal`** → the folder looks like work: its git
  remote or commit email matched your configured work domains;
  a personal save is refused, hard. Do **not** work around it. Tell the operator plainly and capture the
  note from a personal folder instead.
- **`workspace-policy HALT: personal-only -> work`** (or `work-only -> personal`) → the folder is registered
  for the *other* KB. Offer a **guided upgrade**: *"this folder is set to <KB>; does it feed **both**?"* →
  if yes, ``register-workspace --choice both --operator-confirm``; if no, cancel. (A `-> personal` upgrade is
  auto-refused by the tool for a work-signalled folder — never a quiet widen.)

## Flow (conversational-primary — the user may just describe the claim)

1. **Preflight + boundary probe** (read-only): `<PY> <CAP> route --instance <instance>` → expect `(boundary OK)`.
   If it HALTs, handle per **SEC-002 workspace binding** above (register / upgrade / redirect), or for a
   destination mismatch translate to plain language, stop, and offer the other instance.
2. **Determine the kind** (fact | decision | procedure | lesson) and gather the **kind-specific** fields — ask only for what that kind needs:
   - **fact** → `--v <how-known> [--conf <level> --src "<origin>"]` (conf + src required unless `--v unverified`, which is bare)
   - **procedure** → `--v <how-known>` (a last-known-working method; `--conf`/`--src` optional)
   - **decision** → `--status proposed|accepted --why "<rationale>" --date <today>` (NO `--v`, NO `--conf`)
   - **lesson** → `--seen <count> --confirmed <today>` (NO `--v`, NO `--conf`)
   - always: `--topic <slug>` (the kb/ file it joins; suggest one, e.g. `release-process`)
   - for `<today>`, get the real date (`date +%F` / `Get-Date -Format yyyy-MM-dd`), don't hardcode.
3. **How-known + the honesty gate (facts/procedures).** Map the user's basis to `--v`:
   `author-asserted` (you/a source stated it) · `model-inferred` (you inferred it — name inputs in `--src`) · `unverified` (unsure — bare). If the user claims **`ran-tool`** or **`read-primary-source`**, ASK: "did you actually run that tool / read that source *this session*?" — if yes, add `--verified-in-session`; if no, record `author-asserted` instead and tell them. (The tool enforces this floor regardless.)
4. **Dry-run first, then confirm by naming the KB back.**
   ```
   <PY> <CAP> add --instance <instance> --dry-run --topic <slug> --kind <kind> --text "<claim>" [fields]
   ```
   Show the exact claim line + the resolved **repo path** it will land in. Ask the user to confirm by **naming the instance** ("yes, work" / "yes, personal") — not a bare "ok".
   - **Folded personal confirm (SEC-002):** if `--instance personal` from a **`both`** or unattested folder, that same "yes, personal" IS the safety confirm — phrase it *"…and this folder feeds both KBs, so confirming: this note is personal, not work? It syncs to personal GitHub."* Only on a "yes, personal" do you add `--confirm-personal` at step 5.
5. **Write** (drop `--dry-run`; add `--confirm-personal` only per the folded confirm above). Exit 0 = written + linted clean; exit 1 = written but lint flagged (fix + re-check); exit 2 = routing/boundary/workspace HALT (report it, or handle a `CONFIRM: -> personal` HALT by asking "yes, personal" then re-running with `--confirm-personal`). 
6. **Commit** in the instance repo. There is **no pre-commit backstop in this preview** (see README) —
   the boundary and lint checks already ran at write time, in step 5:
   ```
   git -C <repo-root> add kb/<slug>.md kb/index.md && git -C <repo-root> commit -m "kb: capture <short note>"
   ```
   **Pushing is operator-gated (Tier-2)** — do not push unless the operator says so.

## HELP

```
aor-kb-capture — record something you learned this session as an evidence-graded KB claim.

WHAT IT DOES  Writes ONE claim to the instance you name, tagged with how you know it, then lints it.
              It is a RECORDER, not a verifier: by default a captured claim lands QUARANTINED
              (v: unverified or model-inferred) and is served only with --include-quarantined. You can
              record a trusted (T1) grade ONLY if you actually ran the tool / read the source THIS
              session — the --verified-in-session honesty gate.
WHEN TO USE   at session end — whatever routine closes your session — for durable knowledge
              worth reusing. Not for transient task notes.
USAGE         aor-kb-capture <personal|work> <fact|decision|procedure|lesson> "<claim>" [fields…]
              Instance is REQUIRED (no default). Required fields depend on the KIND:
                fact       how-you-know (v:) + confidence + source
                           (source & confidence required for every grade EXCEPT bare 'unverified')
                procedure  how-you-know (v: = last-known-working); source & confidence optional
                decision   status (proposed|accepted) + why    — NO how-you-know, NO confidence
                lesson     seen (count) + confirmed (date)      — NO how-you-know, NO confidence
HOW-YOU-KNOW  (facts & procedures only) — sets the claim's trust tier:
                ran-tool · read-primary-source  T1 verified    you did it this session (needs --verified-in-session)
                author-asserted                 T2 attested    you/a source stated it, not tool-verified
                model-inferred                  T3 quarantined you inferred it; name the inputs in 'source'
                unverified                      T4 quarantined unsure; written 'bare' (no source/confidence)
CONTROL       I show a dry-run of the exact claim + which repo it lands in; you confirm by naming the
              KB back ('yes, personal'); then I write + lint + commit. Pushing is your call (Tier-2).
EXAMPLES      aor-kb-capture work fact "The release API returns at most 100 items per page" ran-tool high "probe run, see notes" --verified-in-session
              aor-kb-capture personal fact "The DNS filter blocks the staging hostname" unverified
              aor-kb-capture work decision "Release branch gates on a green smoke suite" --status accepted --why "2026-07 retro"
              aor-kb-capture personal lesson "Long-running jobs need a heartbeat log" --seen 2 --confirmed <today>
BOUNDARY      'personal' → the personal repo only; 'work' → the work repo only, each as your
              instances.yml names it. Two structural guards: (1) the
              target repo's git remote owner + user.email must match the instance; (2) SEC-002 — the
              FOLDER you're working in is bound to a KB, and a personal save from a work-signalled or
              unassigned folder is refused (work content can't reach personal GitHub). First capture from
              a new folder asks a one-time "work KB / personal KB?" It asks; it never guesses.
```

**Copilot CLI (no button UI):** run the same tool; where `AskUserQuestion` isn't available, ask the
workspace/confirm question in plain text and **wait for the operator's word** before running
`register-workspace --operator-confirm` or adding `--confirm-personal`. The structural HALT is identical
across tools — neither can write without clearing it.
