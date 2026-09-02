# Tiered Test Formats

Use the format that matches the test's purpose:

## Property Test (two-line declaration)

```
PROPERTY [SR-001.c1]: for any valid cart, order.total == sum(item.price * item.qty) + shipping
GENERATOR: cart with 1..1000 items, price in [0.01..99999.99], qty in [1..999]
```

## Unit Test (parameterized table)

```
TC-002..TC-004 [unit, SR-001.c1] — Password complexity validation

| Input                  | Expected         | Error Code          |
|------------------------|------------------|---------------------|
| "short"                | REJECT (< 12)    | PASS_COMPLEXITY_001 |
| "abcdefghijkl"         | REJECT (no upper)| PASS_COMPLEXITY_002 |
| "Abcdefghijk1"         | ACCEPT           | —                   |
```

## Integration / E2E Test (full Given/When/Then)

```
TC-001 [integration, SR-001.c1, SR-001.c2]
Given: authenticated user with valid cart containing 2 items
When: user clicks "Place Order"
Then: order record created with status=confirmed,
      inventory decremented, confirmation email dispatched within 30s
```

## Security Test (negative assertion)

```
REJECT [SR-001.c1]: SQL injection in password field → HTTP 400, no execution, event logged
```

## Parity Test (equivalence assertion — for re-implementations)

```
PARITY [SR-001.c1]: new_system(input) == legacy_system(input) for golden-master dataset
```

## EARS-to-Test Mapping

| EARS Pattern | Test Strategy |
|---|---|
| Ubiquitous | Property/invariant test |
| State-Driven (While) | State-entry + state-exit tests |
| Event-Driven (When) | Trigger/response unit table or GWT |
| Optional Feature (Where) | Feature-gate test pair (with/without) |
| Unwanted Behaviour (If/Then) | Negative + error verification |
| Complex (While+When) | State-constrained trigger GWT |
| EARS-N | Boundary test confirming prohibition holds |
| EARS-E | Correlated event test with timing |
| EARS-T | Latency/timeout assertion |
| EARS-C | Multi-branch conditional GWT |
| EARS-F | Primary-failure + degraded-mode test |
| EARS-P | Parameterized test per sub-ID row |
| NFR Decomposition | Performance/load test at PR level, unit test at SR level |

## Clause-Level Traceability

Every TC tags the specific SR clause(s) it verifies: `[SR-001.c1]`, not just
`[SR-001]`. This catches partial implementations that pass coarse-grained
tests but fail at the clause level.
