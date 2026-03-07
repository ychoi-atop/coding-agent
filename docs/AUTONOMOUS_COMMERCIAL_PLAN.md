# Autonomous Commercial App Generation Plan (Execution-Ready v1)

> v1b refresh (2026-03-07): clarified cross-doc linkage and operator entry points from README, onboarding, and autonomous mode guide.

## Executive summary

This document defines how AutoDev moves from an unattended **engineering loop** to a reliable **commercial app delivery system** that can run with minimal human intervention while preserving safety, quality, and auditability.

The target outcome is not "fully no-human forever". The target is:

- unattended operation for standard product increments,
- explicit policy gates for risky changes,
- measurable quality and release criteria,
- fast rollback and recovery when automation fails.

This plan aligns with existing autonomous mode (`autodev autonomous start`) and extends it with four production roles:

1. **Planner**: converts goals into constrained, testable work packages.
2. **Swarm Executor**: parallel task execution under dependency and budget controls.
3. **Quality Gates**: objective pass/fail checks before promotion.
4. **Release Governor**: controlled rollout, SLO enforcement, and rollback authority.

Success is measured by shipping frequency, escaped-defect rate, MTTR, policy violations, and cost per accepted change.

---

## Principles and non-goals

### Principles

1. **Policy-first autonomy**
   - Automation is permitted only inside declared safety, budget, and side-effect boundaries.
   - Any out-of-policy action is blocked or escalated.

2. **Evidence over assertion**
   - Each decision and transition must have machine-readable evidence (tests, scans, perf results, artifacts).
   - "Model says it is done" is never release evidence.

3. **Deterministic promotion gates**
   - Promotion criteria are explicit and versioned.
   - Same input + same config should produce equivalent gate outcomes.

4. **Fast failure, faster recovery**
   - Fail early in low-cost environments.
   - Contain blast radius with canary + rollback automation.

5. **Human-on-exception model**
   - Humans review policy exceptions, legal/compliance anomalies, and high-risk releases.
   - Humans are not required for every routine change.

6. **Cost-aware execution**
   - Token/tool/runtime budgets are first-class constraints.
   - System degrades gracefully (scope reduction, fewer parallel lanes) when budgets tighten.

### Non-goals (v1)

- Replacing product strategy, legal judgment, or security governance.
- Unbounded autonomous actions on external systems (prod data mutation, billing, public comms) without explicit policy allowances.
- Eliminating all human approvals; v1 minimizes approvals only for low-risk paths.
- Solving multi-region, multi-tenant enterprise governance in one release.

---

## Reference architecture

### Component view

```text
Goal Intake
  -> Planner
  -> Swarm Executor
  -> Quality Gate Service
  -> Release Governor
  -> Telemetry + Learning Store
```

### 1) Planner

**Responsibilities**
- Parse intake (PRD/issue/roadmap delta) into bounded tasks with acceptance criteria.
- Produce dependency DAG, risk class, estimated budget, and required validators.
- Emit execution contract:
  - scope
  - out-of-scope
  - test obligations
  - policy requirements

**Key outputs**
- `plan.json` (DAG, owners, deadlines)
- `risk_profile.json` (risk tier + required controls)
- `execution_contract.json`

### 2) Swarm Executor

**Responsibilities**
- Execute independent tasks in parallel lanes with concurrency caps.
- Use retry/backoff policy and lane isolation.
- Preserve traceability from task -> code change -> validation evidence.

**Controls**
- max parallel lanes (policy-defined)
- per-lane time budget
- per-lane token budget
- side-effect permission matrix

### 3) Quality Gate Service

**Responsibilities**
- Run standardized gates for security, reliability, performance, accessibility, and compliance.
- Publish signed gate decisions (`pass`, `conditional-pass`, `fail`).
- Block promotion when blocking criteria fail.

**Evidence sources**
- test suites, static analysis, dependency scanning
- perf baselines and regression checks
- a11y scans + manual exceptions registry
- license/SBOM/policy reports

### 4) Release Governor

**Responsibilities**
- Choose rollout strategy (canary, staged, full) by risk tier.
- Enforce SLO and error-budget checks during rollout.
- Trigger automatic rollback if guardrails are breached.

**Outputs**
- release decision log
- rollback events
- post-release scorecard

---

## Autonomous lifecycle flow

## 1) Intake
- Inputs: PRD/feature request/bug cluster/compliance update.
- Normalize into machine-readable request.
- Assign risk tier and policy envelope.

## 2) Plan
- Build DAG with acceptance criteria per task.
- Annotate mandatory validators and release preconditions.
- Estimate budget/time and expected blast radius.

## 3) Build
- Swarm executes tasks with bounded retries.
- Continuous local validation per task.
- Artifact capture for each lane.

## 4) Verify
- Run full gate suite on integrated candidate.
- Compare against baseline thresholds.
- Emit deterministic gate report.

## 5) Release
- Apply risk-based rollout policy.
- Observe SLO/error-budget gates in real time.
- Auto-rollback on breach.

## 6) Learn
- Persist run telemetry and failure taxonomy labels.
- Update planner heuristics, retry policies, and gate thresholds (via reviewed config changes).
- Feed backlog with recurring failure eliminations.

---

## Measurable quality gates

| Domain | Gate metric | Pass threshold (v1) | Tooling/evidence | Gate type |
|---|---|---:|---|---|
| Security | High/Critical vulns in runtime deps | 0 open (or approved exception ticket) | `pip_audit`, SBOM/license report, exception registry | Blocking |
| Security | SAST findings (High) | 0 untriaged High | `bandit`, `semgrep` + triage artifact | Blocking |
| Reliability | Test pass rate | 100% required suites pass | `pytest`, integration smoke | Blocking |
| Reliability | Flake rate on retry | < 2% over 20 runs | CI run history + retry stats | Warning -> Blocking if sustained 2 weeks |
| Performance | p95 latency regression | <= 10% vs baseline for critical path | perf smoke/benchmark artifact | Blocking for Tier-1 APIs |
| Performance | Error-rate increase under load | <= 0.5% absolute increase | load snapshot + logs | Blocking |
| Accessibility | WCAG critical violations | 0 critical violations | automated a11y scan report + exception list | Blocking for UI surfaces |
| Compliance | License policy violations | 0 disallowed licenses | SBOM + license classifier | Blocking |
| Compliance | Traceability completeness | 100% tasks linked to evidence | run manifest + artifact index | Blocking |

Notes:
- `conditional-pass` is allowed only when an approved exception exists with owner + expiration date.
- Thresholds are initial defaults; changes require config PR + recorded rationale.

---

## Policy model (budget, time, side-effect, risk)

### Policy dimensions

1. **Budget controls**
   - max tokens per run
   - max tool executions per run
   - max cloud spend per run/day

2. **Time controls**
   - max wall-clock per run
   - per-phase timeout (plan/build/verify/release)
   - retry cooldown and global retry cap

3. **Side-effect controls**
   - filesystem write scope allowlist
   - network egress allowlist
   - deployment/publish controls (deny-by-default)
   - production mutation controls (human approval required)

4. **Risk controls**
   - tier mapping (T0 low -> T3 critical)
   - required validators by tier
   - release strategy by tier
   - mandatory human checkpoints by tier

### Example policy profile (v1)

```yaml
run:
  autonomous:
    max_iterations: 3
    time_budget_sec: 3600
    workspace_allowlist:
      - "/repo"
    blocked_paths:
      - "/repo/secrets"
    external_side_effects:
      allow_docker_build: false
      allow_external_side_effects: false

policy:
  risk_tiers:
    t0:
      max_parallel_lanes: 3
      release_strategy: staged
      human_approval_required: false
    t1:
      max_parallel_lanes: 2
      release_strategy: canary
      human_approval_required: false
    t2:
      max_parallel_lanes: 2
      release_strategy: canary
      human_approval_required: true
    t3:
      max_parallel_lanes: 1
      release_strategy: canary
      human_approval_required: true
      security_signoff_required: true
```

---

## Failure taxonomy and auto-recovery playbooks

| Failure class | Typical signal | Immediate action | Auto-recovery playbook | Escalation trigger |
|---|---|---|---|---|
| Planning failure | invalid/contradictory task DAG | stop build phase | regenerate plan with stricter schema + reduced scope | plan fails twice |
| Tooling failure | validator/tool crash or missing dependency | isolate lane | re-provision tool env, retry once with cooldown | same tool fails in >=2 lanes |
| Quality gate failure | blocking gate fails | block promotion | apply targeted fix loop (max N), rerun affected gates | repeated fail after max fix loops |
| Policy violation | attempted out-of-policy action | hard deny | emit violation artifact, request human decision | any violation in T2/T3 |
| Budget/time exhaustion | token/time cap reached | halt run | produce partial report + carry-forward backlog | two consecutive runs exhausted |
| Release regression | canary SLO breach | rollback | auto-rollback to last good version, freeze rollout | rollback occurs twice in 7 days |
| Observability blind spot | missing key metrics/logs | stop promotion | run telemetry bootstrap check, block release until restored | key telemetry absent >15 min |

### Recovery design rules

- Recovery must be idempotent.
- Every automated recovery emits a structured incident artifact.
- Recovery loops are bounded; unresolved cases escalate with complete context pack.

---

## Release strategy (canary, rollback, SLO gates)

### Strategy by risk tier

- **T0/T1**: staged canary (5% -> 25% -> 100%)
- **T2**: conservative canary (1% -> 5% -> 25% -> 100%) with mandatory approval at 25%
- **T3**: pre-approved maintenance window + manual checkpoint before any traffic increase

### Rollout gates (per stage)

- error rate within SLO budget
- latency p95/p99 within threshold
- no new critical alerts
- no security/compliance blocker opened during rollout

### Rollback policy

Auto-rollback when any condition is true:
- SLO breach sustained for 5 minutes,
- critical incident alert fires,
- data integrity check fails,
- policy violation detected in release pipeline.

Rollback procedure:
1. route traffic to prior stable release,
2. invalidate failing rollout artifact,
3. open auto-generated incident summary,
4. block further promotion until incident is triaged.

---

## 8-week phased roadmap

| Week | Phase | Milestones | Exit criteria |
|---|---|---|---|
| 1 | Baseline alignment | finalize policy schema; define risk tiers; freeze v1 gate metrics | policy + gate specs approved |
| 2 | Planner hardening | planner outputs DAG + risk profile + acceptance criteria consistently | 95% schema-valid plans on benchmark set |
| 3 | Swarm execution control | lane isolation, concurrency caps, retry/backoff integrated | no cross-lane artifact contamination in tests |
| 4 | Gate orchestration | unified gate service + signed decision artifacts | deterministic gate replay passes in CI |
| 5 | Release governor v1 | canary controller + SLO watcher + rollback hooks | rollback drill succeeds in staging |
| 6 | Failure recovery automation | taxonomy tagging + recovery playbooks wired | 80% known failure classes auto-handled |
| 7 | KPI dashboard + ops runbook | dashboard populated from run artifacts; escalation runbook finalized | on-call dry run completed |
| 8 | Pilot and stabilization | limited production pilot on low-risk scope | pilot meets SLO + defect targets for 2 weeks |

---

## KPI dashboard definitions

| KPI | Definition | Source | Target (initial) |
|---|---|---|---:|
| Autonomous success rate | completed autonomous runs / total autonomous runs | autonomous_report + run registry | >= 70% |
| First-pass gate rate | runs passing all blocking gates without fix loop | gate decision artifacts | >= 50% |
| Escaped defect rate | prod defects per autonomous release | incident tracker + release logs | <= 0.3 per release |
| Mean time to recover (MTTR) | incident open -> stable service restored | incident timeline | <= 30 min |
| Change failure rate | releases requiring rollback/hotfix | release governor logs | <= 15% |
| Lead time to production | intake accepted -> production rollout complete | planner + release timestamps | <= 48 h (T0/T1) |
| Policy violation rate | denied out-of-policy attempts / run | policy audit log | <= 1 per 20 runs |
| Cost per accepted change | (model+compute cost) / merged release change | cost telemetry + release count | tracked and reduced month-over-month |

Dashboard requirements:
- daily and weekly views,
- by risk tier and product surface,
- trend + anomaly flags,
- links back to raw evidence artifacts.

---

## Implementation backlog (execution tickets)

| ID | Priority | Owner | Ticket | Definition of Done |
|---|---|---|---|---|
| AC-001 | P0 | Platform | Policy schema v1 (`policy.risk_tiers`, budget/time/side-effects) | schema validated in CI; sample configs and migration notes merged |
| AC-002 | P0 | Orchestration | Planner emits risk profile + acceptance criteria per task | benchmark corpus shows >=95% schema-valid output |
| AC-003 | P0 | Orchestration | Swarm lane manager (concurrency cap, lane isolation, retry policy) | integration test proves isolation + bounded retries |
| AC-004 | P0 | Quality | Unified gate runner + signed decision artifact | gate decisions reproducible from same artifacts |
| AC-005 | P0 | Release Eng | Canary controller with SLO guard + auto-rollback hook | staging canary/rollback drill passes 3 consecutive runs |
| AC-006 | P1 | Security | Exception workflow for conditional-pass with expiry | exception requires owner, expiry, rationale; expired exceptions fail gate |
| AC-007 | P1 | Observability | KPI dashboard pipeline from run artifacts | dashboard updates daily; each KPI traceable to source events |
| AC-008 | P1 | Reliability | Failure taxonomy classifier + playbook dispatcher | >=80% known failures mapped and auto-playbook invoked |
| AC-009 | P1 | DX/Docs | Operator runbook for incident + manual override | runbook validated in tabletop drill |
| AC-010 | P2 | Product Ops | Post-release learning loop into planner heuristics | monthly review PR updates heuristic config with evidence |

Owner mapping is role-based and can be adapted to team structure.

---

## Appendix A: sample autonomous commercial config

```yaml
run:
  autonomous:
    max_iterations: 3
    time_budget_sec: 3600
    workspace_allowlist:
      - "/workspace/project"
    blocked_paths:
      - "/workspace/project/secrets"
    external_side_effects:
      allow_docker_build: false
      allow_external_side_effects: false

quality:
  gates:
    security:
      max_high_vulns: 0
    reliability:
      require_test_pass_rate: 1.0
    performance:
      max_p95_regression_pct: 10
    accessibility:
      max_wcag_critical: 0
    compliance:
      disallowed_licenses: ["GPL-3.0"]

release:
  strategy_by_tier:
    t0: [5, 25, 100]
    t1: [5, 25, 100]
    t2: [1, 5, 25, 100]
    t3: [1, 5, 25, 100]
  rollback:
    slo_breach_minutes: 5
    auto_rollback_enabled: true
```

---

## Appendix B: dry-run checklist (before enabling unattended commercial runs)

1. **Policy readiness**
   - [ ] Risk tiers and side-effect matrix approved
   - [ ] Budget/time caps configured and tested

2. **Quality readiness**
   - [ ] Blocking gate thresholds codified
   - [ ] Exception workflow operational with expiry enforcement

3. **Release readiness**
   - [ ] Canary stages configured by risk tier
   - [ ] Auto-rollback drill passed in staging

4. **Observability readiness**
   - [ ] Required SLO metrics available and alerting wired
   - [ ] Run artifacts indexed and queryable

5. **Operational readiness**
   - [ ] Incident playbook tested with on-call team
   - [ ] Manual override path documented and permissioned

6. **Pilot guardrails**
   - [ ] Pilot scope restricted to low-risk services
   - [ ] Exit criteria defined (quality, stability, cost)

If any checklist item is incomplete, do not enable full unattended commercial rollout.
