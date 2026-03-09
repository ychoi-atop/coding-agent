# AUTONOMOUS V5 — Carryover Policy (AV5 → AV6)

Status: ✅ Approved for AV5 closure annotations

## Purpose

Define a deterministic rule for carrying unfinished AV5 backlog tickets into AV6 at wave closure.

## Policy rule

At AV5 closure time:

1. Any AV5 ticket not marked `✅ Merged` **must** be carried over to AV6.
2. The AV6 backlog entry **must** keep traceability to its AV5 source ID.
3. The AV5 closure document **must** include a carryover annotation entry for every deferred ticket.

## Required closure annotation format

Use this canonical annotation line in the AV5 closure carryover section:

```text
[CARRYOVER][AV5->AV6] source=<AV5-ID> target=<AV6-ID> status=deferred reason="<short reason>" owner=<owner>
```

Field requirements:

- `source`: `AV5-###`
- `target`: `AV6-###`
- `status`: fixed value `deferred`
- `reason`: non-empty short reason in double quotes
- `owner`: accountable owner (`@team` or role)

## AV6 backlog ticket naming convention

Use this ticket title pattern for AV6 carryover items:

```text
<AV5-ID> carryover: <original AV5 ticket title>
```

Example:

- `AV6-003 | AV5-014 carryover: AV5 carryover policy definition`

## Sample carryover entry (reference)

| Source | Target | Closure annotation |
|---|---|---|
| `AV5-013` | `AV6-002` | `[CARRYOVER][AV5->AV6] source=AV5-013 target=AV6-002 status=deferred reason="Kickoff smoke index not finalized before AV5 closure window" owner=@autonomous-docs` |

## Validation expectation

- `scripts/check_av5_carryover_policy.py` validates:
  - annotation format contract,
  - sample entry existence,
  - source/target ID pattern compliance.
