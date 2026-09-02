---
name: aor-review-adhoc
description: Spawn an ad-hoc SME on demand for a one-off review perspective. Optionally persist the SME for reuse.
allowed-tools: Read, Write, Grep, Glob, AskUserQuestion
argument-hint: <role description>  [--save <slug>]  [target paths...]
---

# /aor-review-adhoc — Ad-Hoc SME Spawner

Spawns a specialist reviewer on demand. Two modes:

1. **Ephemeral** (default) — runs the reviewer once, returns findings, no file written
2. **Persisted** (`--save <slug>`) — writes `.claude/agents/aor-sme-<slug>.md` so future `/aor-review <slug>` invocations work

## Operating discipline

Apply throughout. State each at startup so the user sees it.

- **Invocation acknowledgement:** the first response of the skill states
  "Running as `aor-review-adhoc` in <ephemeral | persisted (--save)>
  mode." If `--save` is present, also state the target path
  (`.claude/agents/aor-sme-<slug>.md`). Eliminates the "did the skill
  actually run?" ambiguity.
- **Q&A discipline:** ask one clarifying question at a time. Wait for
  the user's answer before asking the next. Do not stack questions.
  A single `AskUserQuestion` call presenting one decision with multiple
  alternatives is *one* question; presenting two unrelated decisions in
  one call is stacking.
- **Failure-disclosure:** if any prescribed step cannot be completed
  (sub-agent invocation failed, file write blocked, role description
  too vague to construct evaluation criteria, partial completion,
  timeout, permission denied), state it in chat. After disclosing, halt
  and ask whether to retry, refine the role, or abort — do not continue
  silently. One retry is permitted for clearly transient failures
  (e.g., network timeout) before mandatory disclosure.

## Step 1: Parse arguments

The argument string has three parts:

```
<role description>  [--save <slug>]  [target1 target2 ...]
```

If no role description was given, ask via AskUserQuestion: "What expertise
should the SME have? (e.g., 'regulatory affairs SME for EU MDR')"

If `--save <slug>` is present, persist mode is active:

- `<slug>` must match `^[a-z][a-z0-9-]*$` (lowercase alphanumeric + hyphens, must start with a letter)
- Examples: `mdr`, `cybersecurity-medical`, `hipaa-privacy`
- If a file at `.claude/agents/aor-sme-<slug>.md` already exists, ask the user before overwriting

If no targets are given, default to the current working directory.

## Step 2: Construct the reviewer system prompt

Build a system prompt with this structure:

```
You are a specialist {ROLE} reviewer with deep expertise in {DOMAIN}.
You evaluate whether {SCOPE STATEMENT}.

## Evaluation Criteria

- {Criterion 1 — derived from authoritative standard or domain practice}
- {Criterion 2}
- {... 6-8 criteria total}

## Changed Files Guidance

When the review prompt includes a "Changed Files" section with diff hunks,
prioritize those files. Focus findings on the changes shown.

## Scope Boundary

Focus exclusively on {ROLE DOMAIN}. Do NOT evaluate areas already covered by
other SMEs (architecture, code, security, etc.) unless directly relevant.

## Output Format

Respond ONLY with a single JSON object matching this schema (no markdown,
no preamble, no explanation outside the JSON):

{insert the JSON Schema block from the bottom of this skill file verbatim here}
```

When you assemble the prompt, **substitute the schema block in** before
sending — do not leave the literal `{insert ...}` placeholder in the final
prompt. The schema must travel with the prompt so the sub-agent's output is
parseable by the aggregation step.

When generating criteria, draw from authoritative standards relevant to the
role. Examples:

- "EU MDR regulatory affairs" → MDR Annex I essential requirements, technical documentation completeness, post-market surveillance, clinical evaluation, GSPR coverage
- "WCAG 2.2 accessibility specialist" → success criteria coverage, ARIA usage, keyboard navigation, focus management, contrast ratios
- "GDPR data protection officer" → lawful basis, data minimisation, retention, DPIA triggers, subject rights handling

If the role is unfamiliar, ask the user for 3-5 specific concerns the SME should focus on, then derive criteria from those.

## Step 3a: Ephemeral mode — invoke the SME

Use the host CLI's sub-agent mechanism (Agent tool in Claude Code; equivalent
in Copilot CLI) to run the reviewer with:

- The constructed system prompt
- The target file paths
- Instructions to return one JSON object matching the schema

Display the JSON findings as a human-readable summary:

```
Ad-hoc Review: {role}
=====================
Reviewer: {role} (ephemeral)
Targets:  {paths}
Verdict:  {pass | warn | fail}

Findings:
  [{severity}] {item} — {description}  ({location})
  ...

Recommendation: {approve | request_changes | block}
```

## Step 3b: Persisted mode — write the agent file

Write to `.claude/agents/aor-sme-<slug>.md` using the standard SME frontmatter:

```yaml
---
name: aor-sme-<slug>
description: {one-line description derived from role}
tools: Read, Grep, Glob
---
```

Then write the constructed system prompt as the file body.

After writing, confirm to the user:

```
Persisted SME: aor-sme-<slug>
Path:          .claude/agents/aor-sme-<slug>.md

Future invocations:
  /aor-review <slug>             — run this SME alone
  /aor-review requirements,<slug>  — combine with others
```

Then ask whether to also run the review now.

## Output JSON Schema (must match canonical SMEs)

```
{
  "reviewerId": "<slug or short role name>",
  "verdict": "pass" | "warn" | "fail",
  "findings": [
    {
      "severity": "critical" | "major" | "minor" | "info",
      "item": "<short identifier>",
      "description": "<what is wrong and why it matters>",
      "location": "<file path:line number or section reference>",
      "relatedArtifacts": ["<artifact-id>"]
    }
  ],
  "summary": { "pass": <count>, "warn": <count>, "fail": <count> },
  "recommendation": "approve" | "request_changes" | "block"
}
```

Verdict rules:

- `fail` if any finding has severity `critical`
- `warn` if any finding has severity `major` and none are `critical`
- `pass` if all findings are `minor` or `info`

Recommendation rules:

- `block` if verdict is `fail`
- `request_changes` if verdict is `warn`
- `approve` if verdict is `pass`
