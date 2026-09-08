---
name: aor-kb-query
description: Search your evidence-graded knowledge base (personal or work) and get back only trustworthy claims, each stamped with its trust label. Thin wrapper over the OKF-E kb_query CLI. USE PROACTIVELY, not only on request — BEFORE designing, debugging, or deciding in a domain past sessions may already have paid for (build and release tooling, environment quirks that keep recurring, dependency pinning, past incidents, and per-repo topics named after the repo), query first and re-derive second; the live topic list is kb/index.md. Also fires on "what do we know about X", prior lessons, past findings.
allowed-tools: Bash, AskUserQuestion
argument-hint: "[personal|work] [what you're looking for] | help"
license: MIT
compatibility: "Windows only. The tools resolve their config root through Win32 known-folder APIs and the junction guard reads a Windows-only stat field, so they do not run on macOS or Linux. Needs Python 3.9 or later on PATH as python, plus the pinned strictyaml in requirements.txt."
---

# /aor-kb-query — search the evidence-graded KB

Thin wrapper over the OKF-E `kb_query` CLI. You (the agent) translate the user's request into
`kb_query` flags, run it, and present the labelled claims. **All filtering + trust + boundary logic
lives in the tool — never re-implement it here, never hand-read the KB files.**

## Constants (preflight — verify before running)

The tool ships inside this plugin, in `tools/` at the **plugin root** — the directory two levels
above this SKILL.md (`../../` from here). Resolve that once and use the resolved path.

```
PY    = python -E -B                      (from PATH)
QUERY = <plugin-root>/tools/kb_query.py
```
`-E` ignores `PYTHON*` environment variables, so a `PYTHONPATH` cannot shadow the pinned
`strictyaml` that parses every file the boundary check compares against. `-B` is a second
layer against `.pyc` in the shipped tree — the tools already set `sys.dont_write_bytecode`
themselves, so the flag is what still covers a run that dies before reaching that line.
Both are part of the command — do not drop them.
If `python` is not on PATH, or `tools/kb_query.py` is not where this says, say so plainly and
stop — do not guess an alternative interpreter or path.

## Operating discipline

- **Invocation acknowledgement:** first line of your response = ``Running as `aor-kb-query` on the <personal|work> KB.``
- **Help:** if the argument is `help`, `--help`, `?`, or **empty**, print the HELP block below verbatim and stop.
- **Boundary echo:** always name the KB you read in your first output line. Read-only; exactly one instance per call; the two never mix.

## How to run

1. **Resolve the instance:** `personal` (default) or `work`, from the user's words. If genuinely ambiguous, ask — don't guess.
2. **Map the request to flags.** Natural language is fine, but be honest about the tool's limits: there is **no full-text search** — you run a filtered query and pick the matching claims from the labelled set the tool prints; `--scope` is an **exact tag**, not a search term.
   - `--kind` fact|decision|procedure|lesson
   - `--min-conf` high|moderate|low|very-low — a floor; drops lower grades (facts/procedures only)
   - `--min-seen N` — recurrence floor, the natural **lesson** filter (lessons carry no confidence)
   - `--tier` T1|T2|T3|T4 · `--scope <tag>` · `--fresh` (hide stale)
   - reveal: `--include-quarantined` (T3/T4) · `--history` / `--as-of <rev>` (retired versions)
3. **Run it** (do NOT pass `--no-log` — a real query is a legitimate M1 serve event):
   ```
   <PY> <QUERY> --instance <personal|work> [flags]
   ```
4. **Present the printed claims, preserving their trust labels.** Never treat a `QUARANTINED`, superseded, or deprecated claim as current truth. Cite any claim you rely on by its id (`file#c-xxxx`). If a result set looks wrong or empty, re-narrow — don't invent claims.

## HELP

```
aor-kb-query — search your evidence-graded KB and get back only trustworthy claims, each with its trust label.

WHAT IT DOES  Reads ONE KB (personal or work), filters to what you ask for, and prints every matching
              claim WITH its trust label — so you never act on a weak, quarantined, or stale claim by
              accident. Only "- [kind] … {…}" list items are claims; surrounding prose is never authority.
WHEN TO USE   at the start of a task ("what do we know about X?") or any time you'd otherwise guess.
              Prefer it over reading raw files — it filters and labels for you.
USAGE         aor-kb-query [personal|work] [what you're looking for]     (instance defaults to personal; read-only)
              filter:  --kind fact|decision|procedure|lesson
                       --min-conf high|moderate|low|very-low   floor; drops lower grades (facts/procedures only)
                       --min-seen N                            recurrence floor — the natural lesson filter
                       --tier T1|T2|T3|T4                      filter by trust tier directly
                       --scope <area>   --fresh                --fresh hides stale claims
              reveal:  --include-quarantined      show T3/T4 (model-inferred, unverified) claims
                       --history / --as-of <rev>   show superseded & deprecated (retired) versions
TRUST TIERS   computed from each claim's verification method (v:), never stored:
                T1 verified    ran-tool · read-primary-source      served by default
                T2 attested    author-asserted                     served by default (weaker)
                T3 quarantined model-inferred                      hidden unless --include-quarantined
                T4 quarantined unverified                          hidden unless --include-quarantined
              Retired by status (separate axis): superseded / deprecated — hidden unless --history / --as-of.
              Never treat a quarantined, superseded, or deprecated claim as current truth.
LABEL FORMAT  [kind | v-method date | conf-or-QUARANTINED | id | status?] claim text (src: …)
EXAMPLES      aor-kb-query work --kind decision "release branch"
              aor-kb-query personal --kind lesson --min-seen 2
              aor-kb-query work --kind fact --min-conf moderate --include-quarantined
BOUNDARY      'personal' and 'work' each read the repo your instances.yml names for that
              instance. Read-only; one instance per call, never mixed.
```
