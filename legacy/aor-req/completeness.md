# Specification Completeness Checklist

Before declaring requirements authoring done, verify:

## Traceability Completeness

```
[ ] Every UN traces to at least one persona via traces_to::UN->Persona [ID]
[ ] Every UN has at least one derived PR (forward link)
[ ] Every PR traces backward to at least one UN
[ ] Every PR has at least one derived SR (forward link)
[ ] Every SR traces backward to at least one PR
[ ] Every SR has at least one TC (forward link)
[ ] All cross-references resolve to existing identifiers (no dangling refs)
[ ] All identifiers match regex: ^(UN|PR|SR|TC)-\d{3}(\.[A-Z])?$
[ ] All cross-references use canonical syntax: relation_type::SOURCE->TARGET [ID-list]
[ ] Bidirectional persona coverage: every persona referenced is defined; every defined persona is used
```

## EARS Compliance

```
[ ] Every PR uses a recognized EARS pattern (6 base + 6 extension = 12 patterns total — see ears-guide.md)
[ ] Every SR uses a recognized EARS pattern with specific component names
[ ] UNs use narrative format (NOT EARS syntax)
[ ] EARS-N requirements: each has a companion positive requirement
[ ] No banned words in PR/SR body text: "fast", "reliable", "user-friendly", "adequate", "several", "appropriate", "etc."
```

## Requirement Quality (INCOSE C1-C9)

```
[ ] C1 Necessary: every requirement traces to a real stakeholder need
[ ] C2 Appropriate: UNs express intent, PRs express behaviour, SRs express implementation
[ ] C3 Unambiguous: single interpretation, no vague terms without quantification
[ ] C4 Complete: all conditions, constraints, and quality factors stated
[ ] C5 Singular: one capability per requirement (except EARS-C cascade and EARS-E complex)
[ ] C6 Feasible: achievable within known constraints
[ ] C7 Verifiable: testable with objective pass/fail criteria
[ ] C8 Correct: accurately represents the actual need
[ ] C9 Conforming: uses adopted EARS pattern + canonical cross-reference syntax
```

## Functional Coverage

```
[ ] Error conditions: every happy-path SR has at least one If/Then (Unwanted Behaviour) SR
[ ] Cross-field dependencies: fields whose validation depends on other fields have explicit SRs
[ ] Out-of-scope: explicit exclusions listed
[ ] Field specification tables: every editable UI field has a row with validation rule and error msg
[ ] External contracts: all third-party API interactions have failure/timeout SRs
```

## Echo Gate

```
[ ] Every SR clause has a one-line testable assertion (VERIFY: line)
```

If you cannot write a VERIFY line for a clause, the requirement is untestable
and must be rewritten.
