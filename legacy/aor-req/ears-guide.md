# EARS Reference — Twelve Patterns

## Requirement Strength

- **shall** = mandatory (always use in EARS)
- **shall not** = prohibition (EARS-N only)
- should/will/may = NOT for EARS requirements

## Base Patterns (Mavin et al., RE'09)

### 1. Ubiquitous (always active)

```
The <system name> shall <system response>
```

Use for: invariants, baseline security, constants.

### 2. Event-Driven (triggered by event)

```
When <trigger>, the <system name> shall <system response>
```

Use for: user actions, system events, API calls. Most common pattern.

### 3. State-Driven (while in a state)

```
While <precondition(s)>, the <system name> shall <system response>
```

Use for: mode-based behaviour, ongoing conditions.

### 4. Optional Feature (feature gate)

```
Where <feature is included>, the <system name> shall <system response>
```

Use for: feature flags, plan tiers, optional modules.

### 5. Unwanted Behaviour (error handling)

```
If <trigger>, then the <system name> shall <system response>
```

Use for: error conditions, invalid input, security violations.
Prescribes a **mitigation action** in response to an undesirable event.

### 6. Complex Combination (state + event)

```
While <precondition(s)>, when <trigger>, the <system name> shall <system response>
```

## Extension Patterns

### 7. EARS-N — Negative (prohibition)

```
WHEN/WHILE/IF <condition>, the <system name> SHALL NOT <action>
```

Use for: security boundaries, data constraints, prohibited behaviours.
Condition-first syntax — consistent with all other EARS patterns.

**Distinction from UNWANTED:**

- UNWANTED: `IF [bad event] THEN [recovery action]` — prescribes mitigation
- EARS-N: `WHEN [condition], [system] SHALL NOT [action]` — prohibits behaviour
- Discriminator: presence of "SHALL NOT" → EARS-N; "IF [bad] THEN [fix]" → UNWANTED

**Guardrail:** Every EARS-N requirement MUST have at least one companion
positive requirement (UBIQUITOUS, EVENT_DRIVEN, or STATE_DRIVEN) specifying
the expected correct behaviour. Negative requirements are inherently hard to
verify — the companion positive requirement provides the testable specification.

### 8. EARS-E — Complex Event (multi-event trigger)

```
When <event1> AND <event2> within <time window>, the <system name> shall <system response>
```

Use for: correlated events, temporal conjunctions, multi-signal triggers.

### 9. EARS-T — Temporal (time-bounded response)

```
When <event>, the <system name> shall <system response> WITHIN <time constraint>
```

Use for: latency requirements, timeout bounds, SLA constraints.

### 10. EARS-C — Cascade (conditional branching)

```
When <trigger>, then if <condition1> the <system name> shall <action1>,
  if <condition2> the <system name> shall <action2>
```

Use for: multi-branch error handling, conditional response selection, priority chains.

### 11. EARS-F — Fallback (degraded operation)

```
If <primary unavailable>, then the <system name> shall <fallback action>
  with degraded <quality attribute>
```

Use for: graceful degradation, resilience patterns, partial availability.

### 12. EARS-P — Tabular (parametric variations)

```
<Template requirement text referencing parameter table>

| Sub-ID | Parameter1 | Parameter2 |
|--------|-----------|-----------|
| PR-010.A | value1 | value2 |
| PR-010.B | value3 | value4 |
```

Use for: repetitive variations, tier-specific thresholds, configuration matrices.
Sub-IDs use single-letter suffix: `PR-010.A`, `PR-010.B`.

### NFR Decomposition (cross-level pattern)

```
UN (quality goal, narrative) → PR (measurable attribute, EARS) → SR (implementation constraint, EARS)
```

Example:

- UN: "perceived as instant" (quality goal)
- PR: "shall complete within 200ms at p95" (measurable)
- SR: "shall use query timeout of 150ms" (allocated constraint)

## Cross-Reference Syntax

Canonical format: `relation_type::SOURCE->TARGET [ID-list]`

Bounded vocabulary (5 types):

| Type | Direction | Use |
|------|-----------|-----|
| `traces_to` | forward | UN→PR, PR→SR, SR→TC, UN→Persona |
| `derived_from` | backward | PR→UN, SR→PR, TC→SR |
| `validates` | verification | TC→SR |
| `mitigates` | risk control | SR→RISK |
| `conflicts_with` | contradiction | any→any |

Examples:

```markdown
traces_to::PR->SR [SR-001, SR-002]
derived_from::SR->PR [PR-003]
traces_to::UN->Persona [end-user]
mitigates::SR->RISK [RISK-001]
```

## Quality Checklist (INCOSE C1-C9)

Every requirement must satisfy:

| ID | Attribute | Check |
|----|-----------|-------|
| C1 | Necessary | Traces to a real stakeholder need |
| C2 | Appropriate | Detail level matches abstraction level (UN=intent, PR=behaviour, SR=implementation) |
| C3 | Unambiguous | Single interpretation; no banned words: "fast", "reliable", "user-friendly", "adequate", "several", "appropriate", "etc." |
| C4 | Complete | All conditions, constraints, and quality factors stated |
| C5 | Singular | One capability per requirement (no compound "and shall" except EARS-C/E) |
| C6 | Feasible | Achievable within known constraints |
| C7 | Verifiable | Testable with objective pass/fail criteria |
| C8 | Correct | Accurately represents the actual need |
| C9 | Conforming | Uses adopted EARS pattern + canonical cross-reference syntax |

## EARS-to-Test Mapping

| Pattern | Test Strategy |
|---------|--------------|
| Ubiquitous | Property/invariant test |
| Event-Driven | Trigger/response unit or GWT |
| State-Driven | State-entry + state-exit tests |
| Optional Feature | Feature-gate test pair |
| Unwanted Behaviour | Negative + error verification |
| Complex | State-constrained trigger GWT |
| EARS-N | Boundary test confirming prohibition holds |
| EARS-E | Correlated event test with timing |
| EARS-T | Latency/timeout assertion |
| EARS-C | Multi-branch conditional GWT |
| EARS-F | Primary-failure + degraded-mode test |
| EARS-P | Parameterized test per sub-ID row |
| NFR Decomposition | Performance/load test at PR level, unit at SR level |

## Hierarchy

```
UN-NNN (User Need) → PR-NNN (Product Req) → SR-NNN (Software Req) → TC-NNN (Test Case)
```

Identifier regex: `^(UN|PR|SR|TC)-\d{3}(\.[A-Z])?$`

UNs use narrative format (NOT EARS). PRs and SRs use EARS syntax.
TCs use Given/When/Then or procedural format.
