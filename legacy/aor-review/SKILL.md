---
name: aor-review
description: Multi-SME review router. Fans out to specialist agents in .claude/agents/aor-sme-*.md and aggregates findings.
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion
argument-hint: general | all | <role> | <role,role>  [target paths...]
---

# /aor-review — Multi-SME Review Router

Two modes:

1. **General review** — checklist-based independent review of provided artifacts
2. **Specialist review** — fan out to one or more SME agents, aggregate JSON findings

## Operating discipline

Apply throughout. State each at startup so the user sees it.

- **Invocation acknowledgement:** the first response of the skill states
  "Running as `aor-review` in <general | specialist> mode. Report will
  be written to `aor-reviews/<timestamp>[-<token>].md` unless you
  override." Eliminates the "did the skill actually run?" ambiguity.
- **Q&A discipline:** ask one clarifying question at a time. Wait for
  the user's answer before asking the next. Do not stack questions.
  A single `AskUserQuestion` call presenting one decision with multiple
  alternatives (e.g., the mode-routing menu in Step 1) is *one*
  question; presenting two unrelated decisions in one call is stacking.
- **Failure-disclosure:** if any prescribed step cannot be completed
  (sub-agent invocation failed, target not found, file write blocked,
  partial completion, timeout, permission denied), state it in chat.
  After disclosing, halt and ask whether to retry, fall back to
  sequential or partial coverage, or abort — do not continue silently.
  One retry is permitted for clearly transient failures (e.g., network
  timeout) before mandatory disclosure.

## Step 1: Parse arguments

The argument string after `/aor-review` has two parts:

```
<mode> [target1 target2 ...]
```

If no targets are given, default to the current working directory (review all
requirements artifacts found there).

### Mode routing

| Mode | Behavior |
|---|---|
| (empty) | Use AskUserQuestion to present interactive menu |
| `general` | Run the General Review section |
| `all` | Fan out to every `.claude/agents/aor-sme-*.md` agent |
| Single role (e.g., `requirements`) | Fan out to one specific SME |
| Comma-separated roles (e.g., `requirements,test`) | Fan out to multiple specific SMEs |

### No argument → Interactive menu

Use AskUserQuestion to present:

```
What type of review?
1. General review — checklist-based
2. Specialist review — all SMEs
3. Single specialist — pick one
4. Multiple specialists — pick a comma-separated list
```

Then ask for target paths if not provided.

### Discovering available roles

Available roles are determined dynamically by listing files matching
`.claude/agents/aor-sme-*.md` (use Glob). The role name is the suffix after
`aor-sme-`.

If the user passes a role that has no matching agent file, produce:

```
Error: No SME agent found for role "{role}".

Available roles (from .claude/agents/aor-sme-*.md):
  {list discovered roles}

Usage:
  /aor-review general                  — checklist review
  /aor-review all                      — every SME
  /aor-review requirements             — one SME
  /aor-review requirements,test        — multiple SMEs
  /aor-review all path/to/SR.md        — restrict to specific files
```

## Step 2 (general mode): Checklist Review

You are an independent reviewer. You verify the provided requirements
artifacts against the framework's quality rules without bias.

### Targets

If targets are paths, read those files. Otherwise (REQ-C scan criterion):

1. Glob `**/*.md` to depth 3 from the current working directory.
2. Select files matching either:
   a. **Filename** containing `requirement`, `specification`, `test`,
      `traceability`, `SR`, `PR`, `UN`, or `TC` (case-insensitive); OR
   b. **Content** containing UN-NNN / PR-NNN / SR-NNN / TC-NNN
      identifiers, OR canonical cross-reference markers (`traces_to::`,
      `derived_from::`, `validates::`, `mitigates::`, `conflicts_with::`).

   A file matching either rule is in scope; the match reason is not
   used for ordering or de-duplication.
3. Skip `node_modules/`, `.git/`, `.worktrees/`, `dist/`, `build/`,
   `out/`, `coverage/`, `.next/`, `target/`, `vendor/`, and the
   bundle's own `aor-reviews/` directory.

The same glob + filter on every run gives reproducible review scopes.

### Checklist

```
[ ] Every UN traces to at least one persona
[ ] Every UN has at least one derived PR
[ ] Every PR traces backward to at least one UN
[ ] Every PR has at least one derived SR
[ ] Every SR traces backward to at least one PR
[ ] Every SR has at least one TC
[ ] Every SR uses a valid EARS pattern with "shall"
[ ] No SR contains multiple independent behaviours
[ ] Every cross-reference resolves to an existing identifier
[ ] All identifiers match the canonical format (UN/PR/SR/TC-NNN[.X])
[ ] Every EARS-N has a companion positive requirement
```

### Output

```
General Review: {targets}
=========================
Reviewer: aor-review (general mode)

PASS:  {item} — {evidence}
WARN:  {item} — {concern}
FAIL:  {item} — {specific deficiency}

Summary: {N} pass, {N} warn, {N} fail
Recommendation: {approve / request_changes / block}
```

## Step 3 (specialist mode): SME Fan-Out

For each selected role, invoke the SME agent as a **sub-agent**, NOT as
a skill. SME agents at `.claude/agents/aor-sme-*.md` are sub-agent system
prompts spawned by this skill — they are not invocable as skills. Skills
live at `.claude/skills/aor-*/SKILL.md` (invoke directly via the host's
skill mechanism); agents live at `.claude/agents/aor-sme-*.md` (spawned
by this skill via the host's sub-agent mechanism — `Agent` tool in
Claude Code, equivalent in GitHub Copilot CLI).

### Per-SME invocation

For role `<R>`, read `.claude/agents/aor-sme-<R>.md`. The body of the file
(after the YAML frontmatter) is the SME's system prompt.

Invoke the sub-agent with this prompt:

```
{system prompt from the agent file}

You are reviewing the following artifact(s):
{list of target paths}

Project root is the current working directory.

Respond ONLY with a single JSON object matching the schema in your system
prompt. No markdown, no preamble, no explanation outside the JSON.
```

**Parallel-fan-out with fallback** (REQ-D): attempt parallel sub-agent
invocation if the host CLI supports it (e.g., multiple `Agent` tool
calls in one Claude Code response). If the host rejects parallel
invocation or returns a runtime error, fall back to sequential
invocation. Note the actual fan-out mode in the aggregate report
preamble using this fixed schema:

    Fan-out mode: <parallel | sequential>[ — <reason for fallback>]

Examples: `Fan-out mode: parallel`; `Fan-out mode: sequential — host
rejected parallel sub-agent invocation`; `Fan-out mode: sequential —
runtime error in 1 of 5 parallel attempts`.

(Note: a host that silently serializes parallel calls — i.e., accepts
the parallel attempt but completes them sequentially — is not
detectable from the LLM's perspective and is therefore not a fallback
trigger. Treat such hosts as parallel-mode for reporting purposes.)

### Aggregation

Collect every SME's JSON response. Compute the aggregate verdict:

| Aggregate verdict | Rule |
|---|---|
| `fail` | Any SME returned `fail` |
| `warn` | No `fail`, but at least one `warn` |
| `pass` | All SMEs returned `pass` |

### Report

Write a markdown report to `aor-reviews/<filename-safe-timestamp>[-<token>].md`
(relative to current working directory).

**Filename-safe timestamp format:** `YYYY-MM-DDTHH-mm-ss` (ISO 8601 with
colons replaced by hyphens — colons are not legal in Windows filenames).
Example: `aor-reviews/2026-04-28T12-47-40.md`. Do NOT use raw ISO with
colons; the report will fail to write on Windows hosts.

**Correlation token:** if the invocation contains `correlation=<token>`
(e.g., from a CI harness passing `--correlation-token`), embed
`<token>` in the report filename after the timestamp. Token format
must match `^[A-Za-z0-9._-]+$`; if a token contains characters outside
this set, sanitize each invalid character to `_` and note the
sanitized form in the report preamble (e.g., "correlation token
sanitized: `feature/foo:bar` → `feature_foo_bar`"). Example:
`aor-reviews/2026-05-04T08-30-15-ci-pr1234.md`. Token-bearing
filenames let an external harness reliably locate the report it just
produced even when multiple parallel reviews could write to the same
directory.

The report has this structure:

```markdown
# Review: {targets}

**Date:** {ISO timestamp, full form with colons OK inside the file body}
**Aggregate verdict:** {fail | warn | pass}
**SMEs invoked:** {comma-separated list}

## Summary

| Reviewer | Verdict | Findings | Recommendation |
|---|---|---|---|
| requirements | warn | 3 | request_changes |
| test         | pass | 0 | approve |
| ...

## Findings by reviewer

### requirements
{full JSON findings, expanded as markdown}

### test
{...}
```

Display a one-screen summary in chat:

```
Review complete
===============
Targets:    {paths}
Verdict:    {aggregate}
SMEs:       {count} invoked, {count_failed} failing

Top findings:
  - [requirements/critical] SR-012 missing EARS pattern (file:line)
  - [test/major] TC-007 has no validates::TC->SR link
  - ...

Full report: aor-reviews/{filename}.md
```

## Constraints

- Sub-agents must NOT modify any files. They read artifacts only.
- Sub-agents must return one JSON object — discard any non-JSON output.
- If a sub-agent's JSON is malformed, mark its result as `error` in the
  aggregate (do not silently treat as `pass`).
- The aggregate verdict is conservative: a single `fail` blocks; do not
  override with majority voting.
