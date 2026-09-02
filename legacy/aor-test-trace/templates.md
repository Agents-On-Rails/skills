# Trace Matrix and TC Templates

## TRACEABILITY.md Format

```markdown
# Traceability — {Project Name}

## Coverage Matrix

| UN | PR | SR | SR Clauses | TC | Coverage |
|----|----|----|-----------|----|----------|
| UN-001 | PR-001 | SR-001 | .c1, .c2 | TC-001, TC-002 | ✅ covered |
| UN-001 | PR-001 | SR-002 | .c1 | TC-003 | ✅ covered |
| UN-002 | PR-002 | SR-003 | .c1 | — | ❌ gap |
| UN-002 | PR-003-ADV | SR-004 | — | TC-004 | ✅ covered |

Coverage values:
- ✅ covered — every clause has at least one TC
- ⚠️ partial — some clauses lack TCs
- ❌ gap — no TCs at all

## Gaps

List every SR clause without a TC. Each gap must be resolved or explicitly
deferred with rationale before declaring traceability complete.

| SR Clause | Gap Description | Resolution |
|-----------|-----------------|------------|
| SR-003.c1 | No TC drafted | TBD |

## Validation Plan

| UN | Validator | Scenario | Question |
|----|-----------|----------|----------|
| UN-001 | Developer | {realistic usage scenario} | Does this serve the user need described in UN-001? |
| UN-002 | Product Manager | {realistic usage scenario} | Does this serve the user need described in UN-002? |

The Validation Plan is for human acceptance — distinct from automated TCs.
Each UN must have at least one validation entry.
```

## TEST_CASES.md Format

```markdown
# Test Cases — {Project Name}

## TC-001: {Title}
**Format:** Integration / Given-When-Then

**Given** {precondition}
**When** {action}
**Then** {expected outcome with measurable pass/fail criteria}

validates::TC->SR [SR-001.c1, SR-001.c2]

## TC-002..TC-004: {Title}
**Format:** Unit / parameterized table

| Input | Expected | Error Code |
|-------|----------|------------|
| ... | ... | ... |

validates::TC->SR [SR-002.c1]

## TC-005: {Title}
**Format:** Property

PROPERTY [SR-003.c1]: {invariant}
GENERATOR: {input space}

validates::TC->SR [SR-003.c1]
```

The canonical `validates::TC->SR` link is the sole TC→SR cross-reference —
it serves as both the verification link AND the backward-traceability link
required by the requirements review. Always include clause-level IDs
(`SR-001.c1`, not `SR-001`) so partial implementations cannot pass coarse-grained tests.

The range notation `TC-002..TC-004` is a label for a contiguous range of TCs
sharing one parameterized table; in the trace matrix it expands to the
individual IDs `TC-002`, `TC-003`, `TC-004`.

## Cross-Reference Syntax

All TCs link to the SR they verify:

```
validates::TC->SR [SR-NNN]
```

For TCs that derive from a chain (UN → PR → SR), the validates link is
sufficient — the upstream chain is recoverable from the SR's own
`derived_from::SR->PR` and the PR's `derived_from::PR->UN` links.
