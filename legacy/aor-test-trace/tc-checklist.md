# TC Drafting Checklist

Before finalizing test cases for any SR, verify:

```
[ ] Every If/Then EARS requirement has a matching TC (negative test)
[ ] Every Ubiquitous SR has at least one property/invariant test
[ ] Every State-Driven SR has both state-entry and state-exit TCs
[ ] Every input field has at least one error-condition TC
[ ] Boundary values tested (min, min-1, max, max+1)
[ ] Every state transition has an invalid-transition TC
[ ] Every SR clause (.c1, .c2, .c3...) has at least one TC assertion
[ ] Division operations: division-by-zero property check present
[ ] Concurrent/async SRs: idempotency or safe-concurrency TC
[ ] Property tests cover all computation logic
[ ] Every risk control has a verifying TC (when a risk register exists)
[ ] Contract tests for all inter-service boundaries (when applicable)
```

## ISTQB Quality Attributes (T1-T10)

Each TC must satisfy:

| ID | Attribute | Check |
|----|-----------|-------|
| T1 | Correct | Accurately verifies the specified condition |
| T2 | Complete | Includes preconditions, steps, expected outcomes |
| T3 | Feasible | Executable within available resources and environment |
| T4 | Necessary | Addresses a real requirement, risk, or design concern |
| T5 | Traceable | Explicit bidirectional link to SR being verified |
| T6 | Consistent | Uniform language, structure, and naming |
| T7 | Precise | Single unambiguous interpretation |
| T8 | Atomic | Tests a single concept — failure identifies one root cause |
| T9 | Observable | Produces clear, measurable pass/fail evidence |
| T10 | Independent | Success/failure depends only on system behaviour, not other TCs |
