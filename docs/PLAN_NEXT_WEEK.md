# PLAN — Next Week (Operator Reliability)

## Scope

This plan reflects main branch after merges through **NXT-010**.
Primary objective: keep local-simple operator workflow reliable, demoable, and easy to run on a single laptop.

## Current baseline (already merged)

- NXT-001: Quick-run payload validation hardening
- NXT-002: Process polling backoff + stale indicator
- NXT-003: Artifact viewer large-JSON responsiveness
- NXT-004: Timeline taxonomy normalization
- NXT-005: Scorecard API + Overview widget
- NXT-006: Correlation-id tracing for run controls
- NXT-007: Local-simple E2E smoke lane
- NXT-008: Fixture expansion + typed artifact errors
- NXT-009: Stop/retry race hardening + idempotency
- NXT-010: One-command demo bootstrap (`make demo-bootstrap*`)

## Next-week focus

1. **Operator runbook quality (NXT-011)**
   - Keep README/onboarding/demo docs aligned with actual local-simple behavior.
   - Remove stale statements that contradict current controls.
   - Keep copy-paste commands deterministic and web-demo friendly.

2. **Workflow confidence checks**
   - Continue using `make smoke-local-simple-e2e` for operator path smoke.
   - Keep `make check-docs` in docs-only changes.

3. **Handoff clarity**
   - Keep local-simple (single-user) vs hardened mode (`autodev gui`) boundaries explicit.

## Definition of done (docs/workflow)

- Docs describe what operators can actually do today.
- Local demo setup works with one-command bootstrap lanes.
- Active planning links point to this file and `docs/BACKLOG_NEXT_WEEK.md`.

## Related docs

- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/LOCAL_SIMPLE_MODE.md`
- `docs/DEMO_PLAYBOOK.md`
- `docs/onboarding.md`
