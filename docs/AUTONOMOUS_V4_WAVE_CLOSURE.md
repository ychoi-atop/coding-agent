# Autonomous v4 Wave Closure

Status: 🚧 Open (kickoff active; closure pending)

## Scope

Wave closure summary for `AV4-001` ~ `AV4-014`.

## Completed tickets

- TODO: fill when AV4 closes.

## Key outcomes

- TODO

## Remaining risks / gaps

- TODO

## Next-wave prioritized items

1. TODO

## Closeout packet template (AV4-014)

- Template: `docs/templates/AV4_CLOSURE_EVIDENCE_BUNDLE.md.tmpl`
- Render helper:

```bash
python3 scripts/render_av4_closure_evidence.py \
  --output artifacts/av4-closure/AV4_CLOSURE_EVIDENCE_BUNDLE.md \
  --closed-at-kst "2026-03-09 10:00 KST" \
  --docs-gate-evidence "artifacts/docs-check/latest.txt" \
  --tests-gate-evidence "artifacts/tests/av4-focused.log" \
  --status-hook-gate-evidence "artifacts/status-hooks/status-hook-audit.jsonl#L1" \
  --closure-pr "https://github.com/ychoi-atop/coding-agent/pull/<id>" \
  --closure-branch "feat/av4-014-closure-evidence-template" \
  --closure-commit "<commit>" \
  --status-hook-audit-entry "<audit-entry-id>" \
  --owner "autodev"
```

## References

- `docs/AUTONOMOUS_V4_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V4_BACKLOG.md`
- `docs/templates/AV4_CLOSURE_EVIDENCE_BUNDLE.md.tmpl`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
