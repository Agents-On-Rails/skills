# OKF-E v0.1 — profile specification

| | |
|---|---|
| **Profile id** | `okf-e/0.1` |
| **Status** | PoC (pilot; P1–P2 shipped) — normative for `kb-lint` / `kb-query` v0.1 |
| **Base spec** | OKF (Open Knowledge Format), `GoogleCloudPlatform/knowledge-catalog` → `okf/SPEC.md` at commit `d44368c15e38e7c92481c5992e4f9b5b421a801d` (pinned 2026-07-07; Apache-2.0) |
| **License** | MIT (this spec + the OKF-E tooling); the OKF base remains Apache-2.0 — compatible |
| **Source of truth** | Transcribed 2026-07-07 from a certified research record (frozen 2026-07-06, not distributed) per its own §6.1 checklist, plus the two P0 spec adjustments. Design is settled — this document transcribes, it does not reopen. |
| **Edition** | Public edition. Sections are numbered as in the full profile, so a citation means the same thing in both: §0–§10 and §14 are here; §11–§13 and §15 cover a kind-set override recipe, design inputs, transcription errata and a promotion mechanic that are development-internal and are omitted. The numbering is deliberately NOT closed up — renumbering would silently break every existing §N citation, and §11 is already taken by the OKF base spec in cross-references below. |
| **Cite as** | `okf-e-profile.md`, by section — `§N`, or `SSN` where a source file must stay cp1252-safe. Normative for `kb-lint`, `kb-query` and `kb-capture`; where an earlier record cites the research REPORT, this document supersedes it. |

## 0. What this is

OKF-E is a namespaced **extension profile** over OKF that adds per-claim epistemic
evidence — claim-kind typing, verification-method provenance, confidence, and temporal
invalidation — enforced at write time by a linter (`kb-lint`) and re-derived at read time
(`kb-query`). It adds **exactly one** top-level frontmatter key (`evidence:`) and one
line-level claim grammar. Everything else is stock OKF.

The design intent (REPORT §3.0): a minimal, measured experiment. The moat is
**enforcement** (evidence fields always present and structurally valid) plus
**filterability** (retrieval can hard-filter on verification status / confidence /
validity). An unevidenced claim is not forbidden — it is **inert**: it never parses as a
claim, so it can never be served as trusted.

## 1. Base conformance (OKF, pinned)

Findings read from the OKF spec at the pinned commit (P0, 2026-07-07 — these freeze L1):

1. **Required frontmatter key per document: `type` ONLY** (SPEC §4.1 + §9 conformance
   rule 2). `title` / `description` / `resource` / `tags` / `timestamp` are
   recommended/optional. *(The research's working assumption that `okf_version` /
   `timestamp` / `resource` might be required was too broad — corrected at P0.)*
2. **`okf_version` is BUNDLE-level, not per-document** (SPEC §11): declared as
   `okf_version: "0.1"` in a frontmatter block in the bundle-root `index.md` — "the only
   place frontmatter is permitted in an `index.md`". OKF-E instances declare it in
   `kb/index.md`. A per-concept `okf_version` key is tolerated (it is an unknown key to
   OKF consumers, see 4.) but the bundle-root declaration is canonical.
3. **Every non-reserved `.md` under the bundle needs frontmatter with a non-empty
   `type:`** (SPEC §9 rules 1–2) — including ledgers: `kb/_ledger/promoted.md` carries
   `type: Ledger`. Reserved names (no frontmatter, except the bundle-root `index.md`
   `okf_version` block): `index.md`, `log.md`.
4. **Tolerance guarantee** (SPEC §9): consumers MUST NOT reject a bundle for missing
   optional fields or **unknown additional frontmatter keys** — the `evidence:` key
   survives every conforming OKF consumer *by spec, not by hope*. This is the d-base
   premise, re-confirmed at the pinned commit.

## 2. File classes under `kb/`

Every `.md` under `kb/` is exactly one of:

| class | discriminator | frontmatter contract | claim grammar applies? |
|---|---|---|---|
| **reserved** | filename `index.md` / `log.md` | none — except bundle-root `kb/index.md`, which carries the `okf_version` block (§1.2) | no — index is the infuse artifact (one line per concept), never a claim store |
| **ledger** | `type: Ledger` (by convention lives under `kb/_ledger/`) | `type:` required; `evidence:` key **not allowed** (a ledger is an append-only trail, not a claim store) | no — body is inert by class |
| **claim file** | everything else | `type:` required (OKF) **and** `evidence.profile` required (L1) | **yes** — every top-level list item is a claim and must parse (L2) |

The claim-file rule closes the accidental-inertness hole: a concept file that *forgot* its
`evidence:` block is a hard L1 error, not a silently-inert file. The ledger class is not
an enforcement carve-out (a known anti-pattern in prior art, d-nocarve): ledgers still get L1,
and `kb-query` only ever serves claims from claim files — nothing servable escapes gating.

## 3. The `evidence:` extension key (the ONE top-level addition)

```yaml
---
okf_version: "0.1"            # canonical home: bundle-root kb/index.md (§1.2)
type: concept                  # OKF's own typing, untouched
evidence:                      # ← the one OKF-E key (§9-tolerated)
  profile: okf-e/0.1           # REQUIRED — lint trigger + grammar version (L1)
  defaults:                    # OPTIONAL — inheritable key is {src} ONLY
    src: "Shelfmark operator handbook, ch. 4"
  review: 2027-01-06           # OPTIONAL — file-level re-verify-by date (staleness push)
---
```

**The `defaults:` ruling (d-defaults, locked):** file-level `defaults:` may carry `src`
only. `v:` (verification method+date) and `conf:` stay strictly per-claim — a file-wide
`v:` or `conf:` default would invert the burden-asymmetry the design rests on, making the
strong grade the *cheap* default for claims nobody individually verified. Architectural
anti-laundering, not editorial. Escalation gate G1: if the pilot's write-friction metric
shows per-claim `v:`/`conf:` is a top-2 cost AND a defaults-trial audit shows no grade
inflation, `v`/`conf` may join the inheritable set in v0.2 (`kb-lint fix --materialize`
makes that zero-migration).

## 4. Claim grammar (normative — L2)

```
claim-item    := "- [" kind "] " claim-text [ SP brace ] NL continuation*
kind          := "fact" | "decision" | "procedure" | "lesson"    (4 for the pilot; gate G6 adds a 5th in one line)
brace         := "{" pair ("," SP pair)* "}"
pair          := key ": " value
key           := "id" | "v" | "conf" | "src" | "date" | "status" | "until"
              | "supersedes" | "superseded-by" | "reason" | "why" | "seen"
              | "confirmed" | "scope" | "promoted-to"
              # closed whitelist — 15 keys (recounted against REPORT §3.2, 2026-07-07;
              # includes `date` per the P1 transcription erratum); unknown key = lint error
value         := bare-tokens | '"' quoted-string '"'    # quote if it contains , or }
v-value       := method [ SP iso-date ]    # fused verification EVENT
                                           # (date omitted only for `unverified`)
method        := "ran-tool" | "read-primary-source" | "model-inferred"
              | "author-asserted" | "unverified"
continuation  := two-space-indented lines (long bodies); the brace may be the LAST
                 indented line
```

Parse rules (verbatim from §3.2 — these keep parsing cheap and deterministic; no LLM in
the parse path):

1. The brace is parsed **only** as the final syntactic element of the item (inline at
   end-of-line, or alone on the last continuation line). A literal `{` anywhere else is
   plain text.
2. Keys are a **closed whitelist** — an unknown key is a lint error (typo defence;
   extension goes through `.kb-lint.yml`, not ad-hoc keys).
3. `v:` fuses method + date into one value because a verification is an **event** — the
   two are always co-present, and the fusion is what makes the PROV lift mechanical (one
   `prov:Activity` with one `generatedAtTime` per v-event).
4. A claim with **no brace** is legal only if the file's `defaults:` + the kind's
   required-field matrix (§6) are jointly satisfied — otherwise L4 rejects.
5. Prose and nested sub-bullets are **inert**: never loaded as claims. Free prose is
   narrative glue — allowed everywhere, carries no claim authority. Only parsed claim
   items enter the retrieval set.

**Ids** are random hash-style suffixes (`c-7f3a`) — tool-assigned, corpus-unique without a database (after `gastownhall/beads` @ `c88db21`): assigned by `kb-lint fix`, never
hand-typed, uniqueness checked corpus-wide (L8). Format: kind-letter prefix + lowercase
alphanumeric suffix (regex in `.kb-lint.yml`). Every claim thereby pre-owns a URI for the
PROV lift (gate G2).

## 5. Worked examples (a fictional system; every value invented)

The examples below use **Shelfmark**, an invented library-catalogue server. It is not a
stand-in for a real product and no value here was measured anywhere — the point of the
section is the claim *grammar*, and a fictional subject keeps it that way.

**(a) The modal case — a verified fact, one line of ceremony:**

```markdown
- [fact] Shelfmark's search API returns at most 50 records per page regardless of
  `limit`. {id: c-7f3a, v: ran-tool 2026-07-06, conf: high, src: "probe run against a local instance"}
```

**(b) Repetition amortized by the `{src}` default** (`v`/`conf` still affirmed per claim,
by design):

```markdown
---
type: concept
evidence:
  profile: okf-e/0.1
  defaults: {src: "Shelfmark operator handbook, ch. 4"}
---
- [fact] Shelfmark reindexes the whole catalogue on startup, never incrementally. {id: c-2b8e, v: read-primary-source 2026-07-06, conf: high}
```

**(c) A folded hypothesis** — an unproven causal guess is a fact-shaped claim at an honest
grade; the intended-to-test intent rides as inert prose. After a confirming probe the same
claim upgrades by a one-token edit (git blame carries the transition):

```markdown
- [fact] Nightly import failures are caused by the thumbnail worker racing the catalogue
  reindex. {id: c-flk1, v: model-inferred 2026-07-06, conf: low, src: "two failing runs: reindex-lock then import-timeout"}
  - Proposed test: serialize the two jobs for a week; compare failure rate.  (inert)
```

**(d) A decision** (inline ADR-style; large decisions go to a full ADR file + a pointer
claim). `v:` is *forbidden* on decisions — they are ratified, not verified:

```markdown
- [decision] Use a read-only API key per deployment, not one shared key across all of
  them. {id: d-key1, date: 2026-07-06, status: accepted, why: "a shared key cannot be revoked without breaking every deployment at once", src: "options weighed in the design notes"}
```

**(e) A lesson** — recurrence is the evidence; `seen:`/`confirmed:` drive promotion:

```markdown
- [lesson] Always pass an explicit `fields=` — default field sets differ between Shelfmark
  versions and silently break parsers. {id: l-fld1, seen: 3, confirmed: 2026-07-06, scope: project, src: "three separate import failures"}
```

**(f) A never-true claim, deprecated with a reason — the anti-re-learning tombstone**
(`unverified` is admitted *bare*, which is why it is visibly naked):

```markdown
- [fact] Shelfmark exposes a bulk-withdraw REST endpoint. {id: c-ba9x, v: unverified,
  status: deprecated, reason: "checked 2026-07-06: no such endpoint in any version; recurring agent confabulation — do not re-add"}
```

**(g) A lint rejection — the enforcement moment:**

```
$ kb-lint kb/shelfmark/search-api.md
  :14  L5  [fact] c-9q2z: v is 'read-primary-source' but 'src' is missing — verified-grade
           claims REQUIRE a source (evidence asymmetry; add src: or downgrade v: to unverified)
  :17  L3  claim has no [kind] tag — untyped claims cannot enter the store
           (choose: fact  decision  procedure  lesson)
FAIL: 2 gating errors · 1 fixable (run `kb-lint fix`: 1 missing id)
```

**(h) The control arm, manufactured for free** — `kb-lint strip` emits the identical
corpus minus braces (same content, same wording), which is the pilot's markdown+RAG
baseline:

```
$ kb-lint strip kb/  →  - Shelfmark's search API returns at most 50 records per page regardless of `limit`.
```

## 6. Claim kinds & the per-kind field contract (L3 / L4)

Governing principle: **a kind earns its place iff it changes the linter's required-field
set or the reader's trust computation.** Closed enum, lint-enforced; extending it is a
profile version bump (or the one-line G6 override: add the kind to the `.kb-lint.yml` enum and to the linter whitelist, then adopt a per-kind row for it).

| kind | what it is | distinct linter/trust delta |
|---|---|---|
| `fact` | truth-apt claim about world/system/env | full evidence duty: `v:`, and if `v ≠ unverified` then `src:` + `conf:` |
| `decision` | a commitment / option call — not truth-apt | `v:` **forbidden**; ratification lifecycle (`status:` proposed/accepted) + supersession |
| `procedure` | reusable how-to (small; big ones route to SKILL.md) | `v:` records last-known-working (method+date); staleness matters more than confidence |
| `lesson` | meta-knowledge validated by recurrence | evidence = recurrence: `seen:` + `confirmed:` required; `v:`/`conf:` forbidden |

**Universal fields** (every claim, all kinds): kind tag (**gating**); `id` (**fixable** —
auto-assigned); `status` (only when non-active); `until:` (optional); `supersedes` /
`superseded-by` (iff superseding — reciprocal, missing side **fixable**); `reason` (iff
`status: deprecated`).

**Per-kind matrix (check L4)** — required present, forbidden absent; other whitelisted
keys are tolerated rather than rejected in v0.1 — tightening to closed per-kind key sets would be a profile version bump:

| kind | mandatory | optional | forbidden |
|---|---|---|---|
| `fact` | `v:`; and iff `v ≠ unverified`: `src:` + `conf:` | `until` | `src`/`conf` when `v: unverified` (bare admission is visibly bare) |
| `decision` | `date:`, `status:` | `why`, `src`, `supersedes` | `v:` |
| `procedure` | `v:` (method+date = last-known-working) | `src`, `until`, `conf`, `seen`, `promoted-to` | — |
| `lesson` | `seen:` (int ≥ 1), `confirmed:` (ISO date) | `scope`, `src`, `promoted-to` | `v:`, `conf:` |

*(P3 resolved: `seen:` is admitted **optional** on `procedure` so the §14.6 promotion gate
— `ran-tool` AND `seen ≥ 2` AND cross-project — is expressible. `seen:` stays a positive int, L6.)*

**`status` enum** (one field, kind-conditional values; implicit value is `active`):
`proposed` / `accepted` (decisions — ADR lifecycle) · `superseded` (a truth that aged out,
with `superseded-by:`) · `deprecated` (never-true, with mandatory `reason:` — kept so
others know not to re-add) · `promoted` (a lesson lifted to the always-on layer, with
`promoted-to:`). Retire, never delete: a deleted claim that anything links to breaks L7,
so silent deletion of any *linked* claim is structurally impossible. Physical deletion is
reserved for content that should never have existed (secrets — which also triggers key
rotation).

**`conf:`** is one-axis GRADE-4: `high` / `moderate` / `low` / `very-low`. No second axis,
no `importance` field (retrieval ranking, not provenance).

**Temporal** — two fields plus git: world-time validity = the `v:`-date (when verified
true) + optional `until:` (expiry) + supersession links. System-time = git itself (no
`created` / `author` / `recorded-at` fields — `git log`/`blame` carry them for free);
`--as-of` at small scale is `git show <rev>` + parse.

**Write-cost floor** (Q3 discipline): the modal claim — a verified fact — costs one brace
on one line (`{v: …, conf: …, src: "…"}`, id auto-added); in a `{src}`-default session
file it drops to `{v: …, conf: …}`. Lessons cost two short fields. Nothing else is
mandatory.

## 7. `verified-by` methods → derived trust tiers (L5 / L6 semantics)

Trust is **computed from `v:` (and kind) at query time, never stored** — so trust and its
basis can never disagree.

| `v:` value | meaning | derived tier | default serving (`kb-query`) |
|---|---|---|---|
| `ran-tool` | executed/probed on a live system by the author-agent | **T1 verified** | served, labelled |
| `read-primary-source` | primary doc/spec/code read directly | **T1 verified** | served, labelled |
| `author-asserted` | a human stated it, unverified by tool/source | **T2 attested** | served, labelled |
| `model-inferred` | derived by model reasoning from named inputs (requires `src:` = the inputs) | **T3 quarantined** | excluded from trusted serving; `--include-quarantined`, labelled |
| `unverified` | captured, unchecked, no derivation recorded | **T4 quarantined** | same |

`status: superseded|deprecated` ⇒ excluded from current serving (retrievable via
`--history` / `--as-of`).

**The evidence asymmetry (L5):** on facts, T1–T3 grades require `src:` + `conf:`; only
`unverified` is admitted bare — so "verified without a source" is **unwritable**, and
"unverified" is always **visibly naked**. Quarantine-default admission is cheap; trusted
grades are expensive. `kb-lint` never defaults or coerces a missing method (fail-closed —
a fail-open `coerce_source_type` write in `Kromatic-Innovation/athenaeum` @ `d38e4c2` is the
named anti-pattern). Auto-capture
tools may only ever write `v: unverified` or `model-inferred` — structurally incapable of
minting T1/T2. *(Scope note: L5's full force is the `fact` contract; for `procedure`,
`src`/`conf` stay optional per the L4 matrix, which is the semantic source of truth wherever it and L5 differ.)*

**Value validity (L6):** `v:` method ∈ enum + ISO date (present for the 4 active methods,
omitted for `unverified`); event dates (`v:`-date, `date:`, `confirmed:`) must not be in
the future; `until:` is an expiry — strictly later than the `v:`-date, may be future;
`conf:` ∈ GRADE-4; `seen:` positive int.

## 8. The provenance chaining rule (F2 — convention, not machinery)

**`src:` names what YOU read; the chain it attests rides inside the `src:` text.** `v:`
records *your act* (e.g. `read-primary-source` of the named artifact), and deeper trust
composes through that artifact's own tags. Reading a verified *secondary* source earns
`read-primary-source` **of that secondary artifact** — never the grade of the primary
evidence behind it. Without this rule, second-hand verification silently inflates. (The
full chain is exactly what the PROV projector, gate G2, adds if ever needed.)

## 9. Repository layout

```
kb/
  <topic>/<concept>.md      # one concept per file (OKF-native), many claims inside
  index.md                  # bundle root: okf_version block + one line per concept (the infuse artifact)
  _ledger/promoted.md       # promotion trail (type: Ledger)
AGENTS.md / CLAUDE.md       # pointer + the ~10-line consumer contract (§10) — see placement note
.kb-lint.yml                # profile version, enum config, check settings
```

**Placement note (SCOPE decision 11 rules over this generic layout):** in THIS pilot the
personal instance gets NO repo-local agent file — its consumer contract
installs into the operator's global `~/.claude/CLAUDE.md`; the work instance gets
a repo-local `AGENTS.md` (Copilot CLI consumes AGENTS.md only). The repo-local line above
is the generic OKF-E default for other adopters.

Claims are line-scoped list items with corpus-unique ids: concurrent edits to *different*
claims merge cleanly; concurrent edits to the *same* claim conflict — the **correct**
outcome (two agents disagreeing about one claim is an adjudication signal, not a merge
nuisance). Known friction: two sessions both incrementing a lesson's `seen:` conflict on
the number — resolve max+1 manually in v0.1 (`merge=union` driver is gated, G9).

## 10. The consumer contract (~10 lines, rides AGENTS.md / CLAUDE.md)

```markdown
## KB contract (okf-e/0.1)
- Knowledge lives in kb/; only `- [kind] … {…}` items are claims; prose is context, never authority.
- Prefer kb-query (it filters + labels). Reading raw files: trust is derived from v: —
  ran-tool/read-primary-source > author-asserted > model-inferred/unverified (quarantined).
- Never treat a quarantined, superseded, or deprecated claim as current truth.
- Cite claims by id (file#c-xxxx). To correct a claim: supersede or deprecate — never delete.
- New knowledge: write claims through the wrap pass (kb-capture), then kb-lint before commit.
```

Installed at P2 per SCOPE decision 11 (personal → global `~/.claude/CLAUDE.md`; work →
a repo-local `AGENTS.md` — see §9 placement note).

**The serving contract** (part of the protocol, not an implementation detail — transcribed
from REPORT §3.4 at wrap 2026-07-07, dry-run catch): every served claim is
emitted with its labels inline, one line per claim — **the consumer never receives a naked
claim** (the ClashEval mechanism: the measured failure is models following wrong retrieved
content when no trust signal is visible, 60.8%; the measured fix is making it visible,
+13.9%). Label line format:

```
[fact|ran-tool 2026-07-06|high|c-7f3a] Shelfmark's search API returns at most 50 records … (src: probe run)
[fact|unverified|QUARANTINED|c-ba9x|DEPRECATED: never existed] Shelfmark exposes a bulk-withdraw …
```

Fields between pipes: kind · `v:` value (method + date) · conf, or the derived-state word
`QUARANTINED` for T3/T4 · claim id · optional status flag (`DEPRECATED: <reason excerpt>`
/ `SUPERSEDED-BY: <id>`), then the claim text, then `(src: …)` when present. A claim that
fails lint at read time is served — if at all — as quarantined with the failing check
named (choke point 3: trust is computed on every read, never stored).

## 14. Capture routing & the wrap-time capture pass (Deliverable E — P3, normative)

Transcribed from REPORT §3.5 (routing compass), §3.6 (wrap pass), §6.5 (capture hook) + the two P3
boundary decisions ratified 2026-07-07 (SME panel: security + architecture + devops).
Design source note: REPORT §3.5's compass routes by claim **kind** only and predates the two-instance
split; §14.2 adds the **instance** axis it lacked.

- [decision] Capture routes to a boundary-correct instance by an explicit per-invocation target
  (`kb-capture --instance {work|personal}`, no default), NOT a per-claim tag and NOT a content
  classifier; the structural guard is git remote/identity validation at write time (single-target,
  fail-closed) with `kb-lint` kept content-agnostic; a mixed session runs the pass once per instance.
  {id: d-route, date: 2026-07-07, status: accepted, why: "the employer boundary is a session-level property enforced by repo topology; per-claim tagging invents cross-repo writes and pollutes the closed grammar, a classifier is editorial + needs an LLM (barred), and a content boundary is not a decidable linter predicate — only declared-vs-remote topology is", src: "boundary-forks review Q1-1; SME panel security+arch+devops; SCOPE decisions 3,4,5"}

### 14.1 The tool

`tools/kb_capture.py` — the wrap-time harvester. Runs at wrap when a §14.5 trigger fired; otherwise
skips cleanly (a forced pass on an empty session manufactures filler = rot).

### 14.2 Routing — the boundary control (fail-closed, git-remote is the source of truth)

1. **`--instance {work|personal}` is REQUIRED, no default.** Absent/unrecognized → exit 2, write
   nothing. (The ambient default on this box is the personal account active — a "current repo/account"
   default would default to the *catastrophic* direction, so no default is permitted.)
2. **Instance identity is the git remote, not a self-declared label.** `kb-capture` resolves the target
   root from the boundary manifest `instances.yml` (§14.3), then asserts a **three-way match** before
   any write — all must agree or it HALTS (exit 2):
   - `--instance` keyword → expected `remote_owner` + `root` (from the manifest);
   - `git -C <root> remote get-url origin` owner == the `remote_owner` the manifest gives for work or
     personal);
   - `git -C <root> config user.email` == the `identity` the manifest gives for work or for
     personal); and the target repo's `.kb-lint.yml instance:` (§14.4) == `--instance`.
   The remote is ground-truth; the declared `.kb-lint.yml instance:` is a cross-check whose *disagreement*
   with the remote fails closed (drift is detected, never trusted).
3. **Single-target per invocation.** `kb-capture` writes only under `<root>/kb/`. There is no
   "write both" mode — that absence is the structural guard.
4. **Mixed session → run the pass twice**, once per instance; the operator partitions the harvest at
   §14.5 step 1. No single invocation writes both repos.
5. **Fail-safe on ambiguity.** A harvested claim the operator has not assigned to the current run's
   instance is **held, not auto-placed** — capture halts and asks, or drops it to the session handover.
   If a direction must be forced, it is **WORK** (over-restriction = a personal note in a private
   employer repo = recoverable; under-restriction = employer content on personal GitHub = catastrophic).
6. **Grade floor (reuse §7):** `kb-capture` may write only `v: unverified` / `model-inferred`
   (T3/T4) — auto-capture is structurally incapable of minting T1/T2. Upgrades require a later real
   v-event (§3.6 honesty gate). `kb-lint` enforces this already; capture adds no new trust machinery.

### 14.3 The boundary manifest (`instances.yml`, tools repo)

Single source of truth for the keyword→repo→remote mapping; version-controlled + auditable:

```yaml
work:     { root: "C:/path/to/your-work-kb-repo",     remote_owner: your-work-github-owner,     identity: "you@employer.example" }
personal: { root: "C:/path/to/your-personal-kb-repo", remote_owner: your-personal-github-owner, identity: "you@personal.example" }
```

### 14.4 Repo self-identity + the hook backstop

Each instance's `.kb-lint.yml` gains **`instance: work|personal`** (REQUIRED — missing → hard fail,
never defaulted, same discipline as the L5 no-coerce rule). The pre-commit hook gains ONE precondition
before `kb-lint`: assert `git remote get-url origin` owner matches the repo's `.kb-lint.yml instance:`
expected owner; mismatch → BLOCK (fail-closed, `d-nocarve`). This is a **topology-consistency** check,
NOT a content L-check — `kb-lint`'s L1–L8 stay instance-agnostic (a "work vs personal" content
predicate is undecidable without an LLM; a tag-matches-repo L-check would be a tautology that buys
false confidence — panel-rejected). The backstop catches manual/other-tool edits and config drift that
bypass `kb-capture`; `kb-capture`'s §14.2 write-time guard is primary.

**Irreducible residual (stated honestly):** no gate verifies that a claim's *asserted* instance matches
its *true* provenance — a work fact honestly mislabeled personal, or a genuinely dual-use claim, passes
every structural gate. Same shape as "the linter cannot check that `ran-tool` is *true*." The structural
controls make misrouting require an explicit wrong choice (never a silent default); the residual is
reduced only by operator care on ambiguous claims (§14.2.5) + periodic boundary spot-audit (G7 analogue).

### 14.5 The 6-step pass (§3.6, instance-scoped)

harvest one-line claim drafts (operator partitions by instance for a mixed session) → **route** via the
§3.5 kind→destination compass, now within the chosen instance → **verification duty** (record how you
know; if you cannot, the claim enters quarantined — §14.2.6) → **scoped dedup** (grep only the topics
touched; `seen:` bump or supersession on collision, never a parallel truth) → **`kb-lint --changed` +
commit + `kb/index.md` refresh** → **promotion sweep** (propose-only: a `lesson` at `seen ≥ 2`, or a `ran-tool`-verified cross-project `procedure` at `seen ≥ 2`, is PROPOSED for promotion, and the operator confirms every lift). Budget ~10 min when triggered (metric M5).

### 14.6 Triggers (a lookup, not a vibe)

Run the pass when ANY fired, else skip: **T1 first-contact** (touched a system/API with no `kb/` entry)
· **T2 surprise** (a claim contradicted by observation → supersede/deprecate + usually a lesson) ·
**T3 re-learn** (re-derived something the KB already holds → run `kb-capture reconfirm <id>`,
never a new entry: a **lesson** takes a `seen:` bump + `confirmed:` refresh; a **fact** or
**procedure** takes a `v:` date refresh, refused if this session's method was weaker than the
recorded one, and a **procedure** also takes a `seen:` bump; a **decision** is ratified rather
than re-observed, and is refused) · **T4 rework/incident** · **T5 operator ask**.

### 14.7 Workspace-choice binding (SEC-002 — P4, normative)

§14.2 validates the **destination** repo; it does not look at the **calling workspace**, so an agent
working an employer task could honestly type `--instance personal` and land work content on personal GitHub.
SEC-002 closes that with a second, independent structural control. Design source:
a 5-SME workspace-binding design review, 2 rounds, operator-ratified 2026-07-08.

- [decision] A second structural control (SEC-002) binds the CALLING workspace (resolved `Path.cwd()`,
  per-invocation — no `--workspace` flag, no session-start, no env origin) to a required instance and
  VETOES a mismatched write; it is asymmetric (the work direction flows, the personal direction is
  guarded), additive to §14.2 (veto-only, never selects an instance), and re-derives the dangerous
  direction LIVE at capture rather than trusting stored state.
  {id: d-workspace, date: 2026-07-08, status: accepted, why: "the hazard is asymmetric — work→personal is the irreversible employer-boundary breach, personal→work is an accepted nuisance (SEC-005); cwd is the only signal a stateless CLI can observe non-spoofably (session-start/env are the caller-typed value already rejected, and bias toward the hazard since sessions start in a personal dir); real work dirs are gitless so a stored/registration-time decision is forgeable — the guard must re-derive live", src: "workspace-binding design review; SME panel security+arch+ux+devops+test, 2 rounds; operator forks ratified 2026-07-08"}

**Signal detection (live, per invocation).** WORK signal (any one): the cwd is under an entry of the
**explicit work-path list** (machine-local, git-ignored `work-paths.txt`, `KB_WORKPATHS` override) —
this fires even on a **gitless** dir, so gitless work directories get the
structural veto once listed; OR `user.email` ends with one of `boundary.work_domains`; OR the git
remote host matches one; OR the github.com owner ends with one of `boundary.work_owner_suffixes`.
PERSONAL signal: the manifest's personal `identity` or `remote_owner`. UNKNOWN = a git repo matching neither, or a gitless dir NOT on the work-path
list → no structural signal; the personal direction there is gated by the operator's choice + a per-save
human confirm. The work-path list is populated by `kb-capture register-work-path --path <dir>
--operator-confirm` (the SAFE direction — it can only ADD a work veto, never grant personal — so a
caller-typed `--path` is acceptable). Notes: (a) folder-NAME **patterns** (`boundary.advisory_name_patterns`) are **advisory only**
— surfaced as a hint at the forced-choice prompt, they never enforce or auto-classify, because a work-folder name
convention can legitimately occur in personal context too; (b) an employer git remote matching no configured domain or owner suffix (e.g. Azure
DevOps `dev.azure.com`, a GitHub-Enterprise host) is caught only by the **email** leg;
(c) `git` walks UP, so a **gitless subdirectory** of a git repo inherits that repo's signal — expected
under the cwd model.

**Permission model** (`enforce_workspace_policy`, runs before §14.2 for the personal direction so the
SEC-002 reason wins over a destination misconfig):
- `--instance work` (safe): permitted from a `work`/`both` folder; a `personal` folder HALTs (guided
  upgrade to `both`); unregistered HALTs (forced choice).
- `--instance personal` (dangerous), two gates: **(1) live work-signal veto** — any work signal at cwd
  HALTs regardless of the registry (the honest-mistake killer, structural for git-bearing work folders);
  **(2) choice + human confirm** — unregistered HALTs (forced choice); a `work` folder HALTs; a `personal`
  folder with a positive personal signal permits silently; a `both` folder or an unattested `personal`
  folder requires `--confirm-personal` (the operator's "yes, personal", folded into the existing name-back
  confirm — zero extra taps).

**Registry.** Machine-local, git-ignored, never pushed (`workspaces.jsonl`; overridable by
`KB_WORKSPACES`) — workspace paths are machine-specific and keeping them local keeps employer dir-names
off personal GitHub. Append-only JSON lines, last-record-wins, `.is_dir()`-validated, and **flat**: any
cross-choice nesting fails closed at load. Registration (`register-workspace --choice … --operator-confirm`)
binds the resolved cwd only, refuses a junction cwd (reparse-tag detection), and refuses to register a
work-signalled folder as `personal`/`both`.

**Residual (ISO 14971, extends §14.4).** A gitless work folder **on the explicit work-path list** is now
structurally vetoed (gate 1) and cannot be mis-registered personal. The residual is a gitless work folder
**not yet listed** (and not otherwise work-signalled): there the personal-direction guard is the
operator's choice + the per-save confirm + halt-on-ambiguous + the advisory name hint.
And an agent sharing the operator's full filesystem/git trust domain and willing to forge git state cannot
be perfectly contained by a local tool. SEC-002 makes every honest mistake structurally impossible for
git-bearing work folders and raises the adversarial bar; the human-confirm gates (`--confirm-personal`,
`--operator-confirm`) are friction, not structural guarantees — the structural legs are the live
work-signal veto and the §14.2 destination check.
