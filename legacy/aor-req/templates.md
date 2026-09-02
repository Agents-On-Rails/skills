# Authoring Templates

## SPECIFICATION.md Structure

```markdown
# Specification — {Project Name}

## User Needs (UN)

### UN-001: {Title}
**Problem:** {2-3 sentence problem statement referencing a specific persona}
**Need:** {Narrative statement of what the persona needs and why — NOT EARS syntax}
**Priority:** Critical | High | Medium | Low
traces_to::UN->Persona [{persona-id}]

### UN-002: {Title}
**Problem:** {Problem statement}
**Need:** {Narrative need statement}
**Priority:** Critical | High | Medium | Low
traces_to::UN->Persona [{persona-id}]

## Product Requirements (PR)

### PR-001: {Title}
{EARS pattern requirement — "The system shall..." or "When X, the system shall..."}
derived_from::PR->UN [UN-001]
traces_to::PR->SR [SR-001, SR-002]

### PR-002: {Title} (EARS-N: Negative)
When {condition}, the {system} shall not {prohibited action}.
derived_from::PR->UN [UN-001]
traces_to::PR->SR [SR-003]

### PR-003 [ADV]: {Error condition}
If {error condition}, then the {system} shall {recovery action}.
derived_from::PR->UN [UN-001]
traces_to::PR->SR [SR-004]

(The `[ADV]` tag in the heading marks this PR as derived from Adversarial
Error Decomposition. The identifier itself stays canonical: `PR-003`.)

### PR-010: {Title} (EARS-P: Tabular)
When {context}, the system shall {action} per the following table:

| Sub-ID | Parameter1 | Parameter2 |
|--------|-----------|-----------|
| PR-010.A | value1 | value2 |
| PR-010.B | value3 | value4 |

derived_from::PR->UN [UN-003]
traces_to::PR->SR [SR-010, SR-011]

## Software Requirements (SR)

### SR-001: {Title}
{EARS pattern with specific system/component names}
Clauses: .c1 {action 1}, .c2 {action 2}, .c3 {action 3}
derived_from::SR->PR [PR-001]
traces_to::SR->TC [TC-001]

VERIFY: [SR-001.c1] — {testable assertion restating the clause}
VERIFY: [SR-001.c2] — {testable assertion restating the clause}

### SR-004: {Title}
If {error condition}, then the {component} shall {specific recovery action}.
derived_from::SR->PR [PR-003]
traces_to::SR->TC [TC-004]
mitigates::SR->RISK [RISK-001]

VERIFY: [SR-004] — {testable assertion}

## Cross-Cutting Requirements
{Global NFRs: performance, security, availability, accessibility}
{Each as a PR or SR with full traceability links}

## Out of Scope
- {Explicit exclusion 1}
- {Explicit exclusion 2}

## Open Questions
- {Unresolved question 1}
```

## Transformation Pipeline

When converting informal input to structured requirements:

1. **Classify level:** Is this intent (UN), system behaviour (PR), implementation
   constraint (SR), or verification (TC)?
   - "As a [persona], I need..." → UN
   - "The system shall..." → PR
   - Technical constraints, component-specific → SR
   - "Verify that..." / Given/When/Then → TC
2. **Apply EARS syntax:** Select the appropriate EARS pattern for PRs and SRs.
   UNs use narrative format. TCs use Given/When/Then.
3. **Check quality attributes:** Verify C1-C9 (see `ears-guide.md` quality checklist).
4. **Add traceability links:** Every PR links backward to UN and forward to SRs.
   Every SR links backward to PR and forward to TCs.
   Every UN links to a persona.

## Identifier Format

Canonical regex: `^(UN|PR|SR|TC)-\d{3}(\.[A-Z])?$`

- Base IDs: `UN-001`, `PR-001`, `SR-001`, `TC-001`
- Sub-IDs (EARS-P tabular): `PR-010.A`, `PR-010.B`
- Clause notation (consolidation): `SR-001.c1`, `SR-001.c2`
- Sequential numbering within each level, zero-padded to 3 digits

## Cross-Reference Syntax

All cross-references use the canonical format:

```
relation_type::SOURCE->TARGET [ID-list]
```

Five relation types only:

- `traces_to` — forward (UN→PR, PR→SR, SR→TC, UN→Persona)
- `derived_from` — backward (PR→UN, SR→PR, TC→SR)
- `validates` — verification (TC→SR)
- `mitigates` — risk control (SR→RISK)
- `conflicts_with` — contradiction (any→any)

Cross-references are literal text lines in the markdown body — visible in any
markdown viewer and machine-parseable by any post-hoc validator.
