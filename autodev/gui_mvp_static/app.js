const state = {
  runs: [],
  selectedRunId: null,
  selectedRun: null,
  detail: null,
  liveUpdateEnabled: true,
  pollIntervalMs: 8000,
  pollTimer: null,
  useMock: false,
  validationSearch: '',
  validationStatus: 'all',
  validationName: 'all',
  validationSort: 'severity',
  failedFirst: true,
  focusedTaskId: null,
  triageContext: null,
  compareLeftId: null,
  compareRightId: null,
  comparePayload: null,
  compareSource: '',
  compareManualSelection: false,
  compareTrustFocus: '',
  guiContext: null,
  healthSnapshot: null,
  scorecardPayload: null,
  scorecardError: '',
  scorecardLoading: false,
  trustPayload: null,
  trustError: '',
  trustLoading: false,
  lastProcessId: '',
  trendPayload: null,
  artifactViewerPath: '',
  artifactViewerPayload: null,
  artifactViewerError: '',
  artifactViewerLoading: false,
  artifactViewerRequestedBy: '',
  artifactViewerActionStatus: null,
  artifactViewerExpanded: false,
  processes: [],
  selectedProcessId: null,
  selectedProcessDetail: null,
  selectedProcessHistory: [],
  processFilterState: 'all',
  processFilterRunId: '',
  processPage: 1,
  processPageSize: 20,
  processListError: '',
  processLoadRequestSeq: 0,
  processLoadInFlight: false,
  processActionInFlight: false,
  processActionType: '',
  processTransitionSignature: '',
  processStablePollCount: 0,
  processPollBackoffExp: 0,
  processPollMaxBackoffExp: 3,
  processNextPollAtMs: 0,
  processStaleThresholdMs: 45_000,
  runsLoading: false,
  runsError: '',
  detailLoading: false,
  detailError: '',
};

const ARTIFACT_ACTION_TOAST_MS = 3200;
const ARTIFACT_RENDER_PREVIEW_CHARS = 48_000;
const ARTIFACT_JSON_EXPAND_THRESHOLD_CHARS = 120_000;
let artifactViewerActionToastTimer = null;

const PHASE_COLORS = {
  prd_analysis: '#7c3aed',
  architecture: '#4f46e5',
  planning: '#2563eb',
  implementation: '#0284c7',
  final_validation: '#0d9488',
};

const VALIDATION_STATUS_ORDER = ['failed', 'soft_fail', 'unknown', 'skipped_dependency', 'passed'];
const VALIDATION_STATUS_LABEL = {
  failed: 'Failed',
  soft_fail: 'Soft fail',
  skipped_dependency: 'Skipped',
  passed: 'Passed',
  unknown: 'Unknown',
};

const VALIDATION_STATUS_ALIASES = {
  ok: 'passed',
  success: 'passed',
  succeeded: 'passed',
  pass: 'passed',
  skipped: 'skipped_dependency',
  skip: 'skipped_dependency',
  fail: 'failed',
  error: 'failed',
};

const mock = {
  runs: [
    {
      run_id: 'mock-run-001',
      status: 'failed',
      updated_at: new Date().toISOString(),
      project_type: 'python_cli',
      profile: 'minimal',
      model: 'mock-model',
    },
    {
      run_id: 'mock-run-002',
      status: 'ok',
      updated_at: new Date().toISOString(),
      project_type: 'python_cli',
      profile: 'enterprise',
      model: 'mock-model-v2',
    },
  ],
  details: {
    'mock-run-001': {
      run_id: 'mock-run-001',
      status: 'failed',
      summary: {
        project: { type: 'python_cli' },
        totals: { total_task_attempts: 4, hard_failures: 1, soft_failures: 1 },
        profile: { name: 'minimal' },
      },
      phase_timeline: [
        { phase: 'prd_analysis', duration_ms: 4000 },
        { phase: 'architecture', duration_ms: 2500 },
        { phase: 'planning', duration_ms: 3500 },
        { phase: 'implementation', duration_ms: 9000 },
        { phase: 'final_validation', duration_ms: 5000 },
      ],
      tasks: [
        { task_id: 'task-1', status: 'passed', attempts: 1, hard_failures: 0, soft_failures: 0 },
        { task_id: 'task-2', status: 'failed', attempts: 3, hard_failures: 1, soft_failures: 1 },
      ],
      blockers: ['final_validation'],
      validation: {
        validation: [
          { name: 'ruff', ok: false, status: 'failed', returncode: 1, duration_ms: 3200 },
          { name: 'pytest', ok: true, status: 'passed', returncode: 0, duration_ms: 6700 },
        ],
      },
      validation_normalized: {
        summary: { total: 2, passed: 1, failed: 1, soft_fail: 0, skipped: 0, blocking_failed: 1 },
        validator_cards: [
          { name: 'ruff', status: 'failed', ok: false },
          { name: 'pytest', status: 'passed', ok: true },
        ],
      },
      quality_index: { totals: { total_task_attempts: 4, hard_failures: 1, soft_failures: 1 } },
      metadata: { profile: 'minimal', model: 'mock-model' },
    },
    'mock-run-002': {
      run_id: 'mock-run-002',
      status: 'ok',
      summary: {
        project: { type: 'python_cli' },
        totals: { total_task_attempts: 3, hard_failures: 0, soft_failures: 0 },
        profile: { name: 'enterprise' },
      },
      phase_timeline: [
        { phase: 'prd_analysis', duration_ms: 3000 },
        { phase: 'architecture', duration_ms: 2800 },
        { phase: 'planning', duration_ms: 3200 },
        { phase: 'implementation', duration_ms: 7100 },
        { phase: 'final_validation', duration_ms: 4800 },
      ],
      tasks: [
        { task_id: 'task-1', status: 'passed', attempts: 1, hard_failures: 0, soft_failures: 0 },
        { task_id: 'task-2', status: 'passed', attempts: 2, hard_failures: 0, soft_failures: 0 },
      ],
      blockers: [],
      validation: {
        validation: [
          { name: 'ruff', ok: true, status: 'passed', returncode: 0, duration_ms: 2200 },
          { name: 'pytest', ok: true, status: 'passed', returncode: 0, duration_ms: 5900 },
          { name: 'mypy', ok: false, status: 'soft_fail', returncode: 1, duration_ms: 1000 },
        ],
      },
      validation_normalized: {
        summary: { total: 3, passed: 2, failed: 0, soft_fail: 1, skipped: 0, blocking_failed: 0 },
        validator_cards: [
          { name: 'ruff', status: 'passed', ok: true },
          { name: 'pytest', status: 'passed', ok: true },
          { name: 'mypy', status: 'soft_fail', ok: false },
        ],
      },
      quality_index: { totals: { total_task_attempts: 3, hard_failures: 0, soft_failures: 0 } },
      metadata: { profile: 'enterprise', model: 'mock-model-v2' },
    },
  },
  processes: [
    {
      process_id: 'proc-mock-001',
      action: 'start',
      pid: 12345,
      state: 'running',
      retry_of: null,
      retry_root: 'proc-mock-001',
      retry_attempt: 1,
      run_link: { run_id: 'mock-run-001', out: './generated_runs/mock-run-001' },
      started_at: new Date(Date.now() - 45_000).toISOString(),
      returncode: null,
      stop_reason: null,
      transitions: [
        { at: new Date(Date.now() - 45_000).toISOString(), state: 'spawned', detail: { pid: 12345 } },
        { at: new Date(Date.now() - 44_500).toISOString(), state: 'running' },
      ],
    },
    {
      process_id: 'proc-mock-002',
      action: 'retry',
      pid: 12346,
      state: 'exited',
      retry_of: 'proc-mock-001',
      retry_root: 'proc-mock-001',
      retry_attempt: 2,
      run_link: { run_id: 'mock-run-001', out: './generated_runs/mock-run-001' },
      started_at: new Date(Date.now() - 120_000).toISOString(),
      returncode: 0,
      stop_reason: null,
      transitions: [
        { at: new Date(Date.now() - 120_000).toISOString(), state: 'spawned', detail: { pid: 12346 } },
        { at: new Date(Date.now() - 119_500).toISOString(), state: 'running' },
        { at: new Date(Date.now() - 90_000).toISOString(), state: 'exited', detail: { returncode: 0 } },
      ],
    },
  ],
};

const el = (id) => document.getElementById(id);

function formatTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}

function setStatusChip(status) {
  const chip = el('runStatusChip');
  const normalized = (status || 'unknown').toLowerCase();
  chip.className = `chip chip-${normalized}`;
  chip.textContent = normalized.toUpperCase();
}

function setProcessStateChip(status) {
  const chip = el('processStateChip');
  if (!chip) return;
  const normalized = (status || 'unknown').toLowerCase();
  chip.className = `chip chip-${normalized}`;
  chip.textContent = normalized.toUpperCase();
}

function isProcessActive(status) {
  return ['running', 'spawned', 'stopping'].includes(String(status || '').toLowerCase());
}

function normalizeProcessStateFilter(raw) {
  const val = String(raw || '').trim().toLowerCase();
  if (!val || val === 'all') return '';
  return val;
}

function normalizeProcessRunIdFilter(raw) {
  return String(raw || '').trim().toLowerCase();
}

function normalizeProcessPageSize(raw) {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return 20;
  if (parsed <= 10) return 10;
  if (parsed <= 20) return 20;
  return 50;
}

function filterProcesses(rows) {
  const stateFilter = normalizeProcessStateFilter(state.processFilterState);
  const runIdFilter = normalizeProcessRunIdFilter(state.processFilterRunId);
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    const rowState = String(row?.state || '').toLowerCase();
    const rowRunId = String(row?.run_link?.run_id || '').toLowerCase();
    if (stateFilter && rowState !== stateFilter) return false;
    if (runIdFilter && !rowRunId.includes(runIdFilter)) return false;
    return true;
  });
}

function buildProcessPage(filteredRows) {
  const rows = Array.isArray(filteredRows) ? filteredRows : [];
  const pageSize = normalizeProcessPageSize(state.processPageSize);
  state.processPageSize = pageSize;
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize) || 1);
  const currentPage = Math.min(Math.max(Number(state.processPage) || 1, 1), totalPages);
  state.processPage = currentPage;
  const start = (currentPage - 1) * pageSize;
  return {
    rows,
    pageRows: rows.slice(start, start + pageSize),
    total,
    pageSize,
    totalPages,
    currentPage,
  };
}

function renderProcessPagination(meta) {
  const root = el('processPagination');
  const label = el('processPageLabel');
  const firstBtn = el('processPageFirstBtn');
  const prevBtn = el('processPagePrevBtn');
  const nextBtn = el('processPageNextBtn');
  const lastBtn = el('processPageLastBtn');
  if (!root || !label || !firstBtn || !prevBtn || !nextBtn || !lastBtn) return;

  const total = Number(meta?.total || 0);
  const currentPage = Number(meta?.currentPage || 1);
  const totalPages = Number(meta?.totalPages || 1);
  const show = total > 0 || state.processListError;

  root.classList.toggle('hidden', !show);
  label.textContent = `Page ${currentPage} / ${totalPages}`;

  firstBtn.disabled = currentPage <= 1;
  prevBtn.disabled = currentPage <= 1;
  nextBtn.disabled = currentPage >= totalPages;
  lastBtn.disabled = currentPage >= totalPages;
}

function activateTab(tabName) {
  document.querySelectorAll('.tab').forEach((x) => x.classList.remove('is-active'));
  document.querySelectorAll('.tab-content').forEach((x) => x.classList.remove('is-active'));

  const tabBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
  const target = document.getElementById(`tab-${tabName}`);
  if (tabBtn) tabBtn.classList.add('is-active');
  if (target) target.classList.add('is-active');
}

function focusTaskRow(taskId) {
  if (!taskId) return;
  state.focusedTaskId = taskId;
  const row = document.querySelector(`#tasksTable tr[data-task-id="${CSS.escape(taskId)}"]`);
  if (!row) return;
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function renderRuns(runs) {
  const list = el('runsList');
  list.innerHTML = '';

  if (!runs.length) {
    list.innerHTML = '<div class="empty">No runs found in runs root.</div>';
    renderOverviewState();
    return;
  }

  runs.forEach((run) => {
    const item = document.createElement('button');
    item.className = `list-item ${state.selectedRunId === run.run_id ? 'is-active' : ''}`;
    item.innerHTML = `
      <div class="title">${run.run_id}</div>
      <div class="meta">status=${run.status || 'unknown'} | ${run.project_type || 'n/a'}</div>
    `;
    item.addEventListener('click', () => selectRun(run.run_id));
    list.appendChild(item);
  });

  renderOverviewState();
}

function renderTimeline(phases) {
  const timeline = el('phaseTimeline');
  const empty = el('phaseEmpty');
  timeline.innerHTML = '';

  if (!Array.isArray(phases) || phases.length === 0) {
    empty.classList.remove('hidden');
    timeline.classList.add('hidden');
    return;
  }

  empty.classList.add('hidden');
  timeline.classList.remove('hidden');

  const total = phases.reduce((acc, p) => acc + (p.duration_ms || 0), 0) || 1;
  phases.forEach((p) => {
    const pct = Math.max(((p.duration_ms || 0) / total) * 100, 4);
    const seg = document.createElement('div');
    seg.className = 'timeline-seg';
    seg.style.width = `${pct}%`;
    seg.style.background = PHASE_COLORS[p.phase] || '#6b7280';
    seg.title = `${p.phase}: ${p.duration_ms || 0}ms`;
    seg.textContent = p.phase;
    timeline.appendChild(seg);
  });
}

function renderTasks(tasks) {
  const table = el('tasksTable');
  const tbody = table.querySelector('tbody');
  const empty = el('tasksEmpty');
  tbody.innerHTML = '';

  if (!Array.isArray(tasks) || tasks.length === 0) {
    table.classList.add('hidden');
    empty.classList.remove('hidden');
    return;
  }

  table.classList.remove('hidden');
  empty.classList.add('hidden');

  tasks.forEach((t) => {
    const taskId = String(t.task_id || '');
    const tr = document.createElement('tr');
    tr.dataset.taskId = taskId;
    if (taskId && taskId === state.focusedTaskId) {
      tr.classList.add('task-row-focused');
    }
    tr.innerHTML = `
      <td>${escapeHtml(taskId)}</td>
      <td>${escapeHtml(t.status || 'unknown')}</td>
      <td>${t.attempts ?? 0}</td>
      <td>${t.hard_failures ?? 0}</td>
      <td>${t.soft_failures ?? 0}</td>
    `;
    tbody.appendChild(tr);
  });

  if (state.focusedTaskId) {
    const focused = tbody.querySelector(`tr[data-task-id="${CSS.escape(state.focusedTaskId)}"]`);
    if (focused) focused.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function renderBlockers(blockers) {
  const list = el('blockersList');
  const empty = el('blockersEmpty');
  list.innerHTML = '';

  if (!Array.isArray(blockers) || blockers.length === 0) {
    list.classList.add('hidden');
    empty.classList.remove('hidden');
    return;
  }

  list.classList.remove('hidden');
  empty.classList.add('hidden');
  blockers.forEach((b) => {
    const li = document.createElement('li');
    li.textContent = String(b);
    list.appendChild(li);
  });
}

function renderMetaGrid(detail) {
  const metaGrid = el('metaGrid');
  const metaEmpty = el('metaEmpty');
  metaGrid.innerHTML = '';

  const selectedRun = state.runs.find((r) => r.run_id === detail.run_id) || state.selectedRun || {};
  const summary = detail.summary || {};
  const totals = summary.totals || {};
  const project = summary.project || {};
  const profile = summary.profile || {};

  const rows = [
    ['Run ID', detail.run_id || '-'],
    ['Status', detail.status || 'unknown'],
    ['Project Type', project.type || selectedRun.project_type || '-'],
    ['Profile', profile.name || selectedRun.profile || '-'],
    ['Model', detail.model || selectedRun.model || '-'],
    ['Started At', formatTime(detail.started_at)],
    ['Ended At', formatTime(detail.ended_at)],
    ['Updated At', formatTime(detail.updated_at || selectedRun.updated_at)],
    ['Total Task Attempts', totals.total_task_attempts ?? '-'],
    ['Hard Failures', totals.hard_failures ?? '-'],
    ['Soft Failures', totals.soft_failures ?? '-'],
    ['Blockers', Array.isArray(detail.blockers) ? detail.blockers.length : 0],
  ];

  const validRows = rows.filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!validRows.length) {
    metaGrid.classList.add('hidden');
    metaEmpty.classList.remove('hidden');
    return;
  }

  metaGrid.classList.remove('hidden');
  metaEmpty.classList.add('hidden');

  validRows.forEach(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'meta-item';
    item.innerHTML = `<div class="meta-label">${label}</div><div class="meta-value">${value}</div>`;
    metaGrid.appendChild(item);
  });
}

function buildMockScorecardPayload() {
  const detail = mock.details[mock.runs[0]?.run_id] || {};
  const tasks = Array.isArray(detail.tasks) ? detail.tasks : [];
  const passed = tasks.filter((row) => row.status === 'passed').length;
  const total = tasks.length;
  const passRate = total ? Number(((passed / total) * 100).toFixed(1)) : 0;
  const totals = detail.summary?.totals || {};

  const summary = {
    task_pass_rate_percent: passRate,
    task_pass_count: passed,
    task_total: total,
    task_pass_fraction: `${passed}/${total}`,
    final_status: String(detail.status || 'unknown'),
    total_task_attempts: Number(totals.total_task_attempts || 0),
    hard_failures: Number(totals.hard_failures || 0),
    soft_failures: Number(totals.soft_failures || 0),
    repair_passes: 0,
  };

  const cardDefs = [
    ['task_pass_rate_percent', 'Pass Rate'],
    ['task_pass_fraction', 'Tasks'],
    ['total_task_attempts', 'Attempts'],
    ['repair_passes', 'Repairs'],
    ['hard_failures', 'Hard Fails'],
    ['soft_failures', 'Soft Fails'],
  ];

  const cards = cardDefs.map(([key, label]) => {
    const raw = summary[key];
    const value = key === 'task_pass_rate_percent' ? `${raw}%` : String(raw);
    let tone = 'neutral';
    if (key === 'hard_failures') tone = Number(raw) > 0 ? 'danger' : 'ok';
    else if (key === 'soft_failures') tone = Number(raw) > 0 ? 'warning' : 'ok';
    else if (key === 'task_pass_rate_percent') tone = Number(raw) >= 100 ? 'ok' : 'neutral';
    return { key, label, value, tone };
  });

  return {
    empty: false,
    message: '',
    latest: {
      run_id: String(detail.run_id || mock.runs[0]?.run_id || ''),
      status: String(detail.status || 'unknown'),
      profile: String(detail.summary?.profile?.name || ''),
      model: String(detail.model || ''),
      updated_at: new Date().toISOString(),
    },
    summary,
    cards,
    artifact_errors: [],
  };
}

function buildMockTrustPayload() {
  return {
    empty: false,
    message: '',
    latest: {
      run_id: String(mock.details[mock.runs[0]?.run_id]?.run_id || mock.runs[0]?.run_id || ''),
      profile: String(mock.details[mock.runs[0]?.run_id]?.summary?.profile?.name || ''),
      completed_at: new Date().toISOString(),
    },
    summary: {
      status: 'failed',
      trust_status: 'moderate',
      trust_score: 0.58,
      requires_human_review: true,
      latest_quality_status: 'advisory_warning',
      latest_quality_score: 42,
      incident_owner_team: 'Feature Engineering',
      incident_severity: 'high',
      incident_target_sla: '4h',
      event_count: 12,
      llm_call_count: 6,
      experiment_entry_count: 3,
    },
    packet: {
      operator_next: {
        top_actions: [
          {
            code: 'tests.min_pass_rate_not_met',
            title: 'Tests gate failed',
            actions: ['Stabilize failing tests before retry.', 'Rerun targeted tests, then full suite.'],
          },
          {
            code: 'autonomous_guard.repeated_gate_failure_limit_reached',
            title: 'Stop guard triggered',
            actions: ['Pause retries and narrow scope before resuming.'],
          },
        ],
      },
    },
    warnings: [],
  };
}

function buildMockDetailTrust(detail) {
  const runId = String(detail?.run_id || '');
  if (runId === 'mock-run-001') {
    return {
      trust_summary: {
        status: 'failed',
        trust_status: 'moderate',
        trust_score: 0.58,
        requires_human_review: true,
        latest_quality_status: 'advisory_warning',
        latest_quality_score: 42,
        incident_owner_team: 'Feature Engineering',
        incident_severity: 'high',
        incident_target_sla: '4h',
        event_count: 12,
        llm_call_count: 6,
        experiment_entry_count: 3,
      },
      trust_packet: {
        operator_next: {
          top_actions: [
            {
              code: 'tests.min_pass_rate_not_met',
              title: 'Tests gate failed',
              actions: ['Stabilize failing tests before retry.', 'Rerun targeted tests, then full suite.'],
            },
          ],
        },
      },
      trust_message: '',
    };
  }

  return {
    trust_summary: {
      status: 'completed',
      trust_status: 'high',
      trust_score: 0.93,
      requires_human_review: false,
      latest_quality_status: 'passed',
      latest_quality_score: 96,
      incident_owner_team: 'Autonomy On-Call',
      incident_severity: 'medium',
      incident_target_sla: '12h',
      event_count: 8,
      llm_call_count: 4,
      experiment_entry_count: 2,
    },
    trust_packet: {
      operator_next: {
        top_actions: [
          {
            code: 'autonomous.unmapped_or_missing_code',
            title: 'Routine review',
            actions: ['Review final artifacts and approve closure.'],
          },
        ],
      },
    },
    trust_message: '',
  };
}

function renderScorecardWidget() {
  const cardsNode = el('scorecardCards');
  const emptyNode = el('scorecardEmpty');
  const errorNode = el('scorecardError');
  const metaNode = el('scorecardMeta');
  if (!cardsNode || !emptyNode || !errorNode || !metaNode) return;

  cardsNode.innerHTML = '';
  errorNode.classList.add('hidden');
  errorNode.textContent = '';

  if (state.scorecardLoading) {
    cardsNode.classList.add('hidden');
    emptyNode.classList.remove('hidden');
    emptyNode.textContent = 'Loading latest scorecard…';
    metaNode.textContent = '';
    return;
  }

  if (state.scorecardError) {
    cardsNode.classList.add('hidden');
    emptyNode.classList.add('hidden');
    errorNode.classList.remove('hidden');
    errorNode.textContent = state.scorecardError;
    metaNode.textContent = '';
    return;
  }

  const payload = state.scorecardPayload;
  const cards = Array.isArray(payload?.cards) ? payload.cards : [];
  if (!payload || payload.empty || !cards.length) {
    cardsNode.classList.add('hidden');
    emptyNode.classList.remove('hidden');
    emptyNode.textContent = payload?.message || 'No scorecard data available yet.';
    metaNode.textContent = '';
    return;
  }

  cards.forEach((card) => {
    const article = document.createElement('article');
    article.className = `scorecard-card tone-${card?.tone || 'neutral'}`;
    article.innerHTML = `
      <div class="value">${escapeHtml(card?.value ?? '-')}</div>
      <div class="label">${escapeHtml(card?.label || card?.key || '-')}</div>
    `;
    cardsNode.appendChild(article);
  });

  const latest = payload.latest || {};
  const metaParts = [];
  if (latest.run_id) metaParts.push(`run=${latest.run_id}`);
  if (latest.status) metaParts.push(`status=${latest.status}`);
  if (latest.profile) metaParts.push(`profile=${latest.profile}`);
  if (latest.model) metaParts.push(`model=${latest.model}`);
  if (latest.updated_at) metaParts.push(`updated=${formatTime(latest.updated_at)}`);
  metaNode.textContent = metaParts.join(' • ');

  emptyNode.classList.add('hidden');
  cardsNode.classList.remove('hidden');
}

function trustCardTone(key, summary) {
  const status = String(summary?.trust_status || '').toLowerCase();
  if (key === 'trust_status') {
    if (status === 'high') return 'ok';
    if (status === 'moderate') return 'warning';
    return 'danger';
  }
  if (key === 'requires_human_review') {
    return summary?.requires_human_review ? 'warning' : 'ok';
  }
  if (key === 'incident_severity') {
    const severity = String(summary?.incident_severity || '').toLowerCase();
    if (severity === 'high' || severity === 'critical') return 'danger';
    if (severity === 'medium') return 'warning';
    return 'neutral';
  }
  return 'neutral';
}

function renderTrustWidget() {
  const cardsNode = el('trustCards');
  const actionsNode = el('trustActions');
  const emptyNode = el('trustEmpty');
  const errorNode = el('trustError');
  const metaNode = el('trustMeta');
  if (!cardsNode || !actionsNode || !emptyNode || !errorNode || !metaNode) return;

  cardsNode.innerHTML = '';
  actionsNode.innerHTML = '';
  errorNode.classList.add('hidden');
  errorNode.textContent = '';

  const detailMatchesSelected = Boolean(
    state.detail?.run_id
    && state.selectedRunId
    && String(state.detail.run_id) === String(state.selectedRunId),
  );
  const detailTrustSummary = detailMatchesSelected ? (state.detail?.trust_summary || null) : null;
  const detailTrustPacket = detailMatchesSelected ? (state.detail?.trust_packet || null) : null;
  const detailTrustMessage = detailMatchesSelected ? (state.detail?.trust_message || '') : '';
  const hasSelectedTrustContext = Boolean(state.selectedRunId);
  const payload = detailTrustSummary
    ? {
        empty: false,
        latest: {
          run_id: state.detail?.run_id || state.selectedRunId || '',
          completed_at: state.detail?.metadata?.completed_at || '',
        },
        summary: detailTrustSummary,
        packet: detailTrustPacket || {},
      }
    : state.trustPayload;

  if ((state.detailLoading && hasSelectedTrustContext && !detailMatchesSelected) || (state.trustLoading && !detailTrustSummary)) {
    cardsNode.classList.add('hidden');
    actionsNode.classList.add('hidden');
    emptyNode.classList.remove('hidden');
    emptyNode.textContent = hasSelectedTrustContext
      ? 'Loading trust intelligence for selected run…'
      : 'Loading latest trust intelligence…';
    metaNode.textContent = '';
    return;
  }

  if (state.trustError) {
    cardsNode.classList.add('hidden');
    actionsNode.classList.add('hidden');
    emptyNode.classList.add('hidden');
    errorNode.classList.remove('hidden');
    errorNode.textContent = state.trustError;
    metaNode.textContent = '';
    return;
  }

  const summary = payload?.summary || null;
  if (!payload || payload.empty || !summary) {
    cardsNode.classList.add('hidden');
    actionsNode.classList.add('hidden');
    emptyNode.classList.remove('hidden');
    emptyNode.textContent = hasSelectedTrustContext
      ? (detailTrustMessage || 'Trust intelligence is not available for the selected run.')
      : (payload?.message || 'No trust intelligence data available yet.');
    metaNode.textContent = '';
    return;
  }

  const cardDefs = [
    ['trust_status', 'Trust Status', String(summary.trust_status || '-').toUpperCase()],
    ['trust_score', 'Trust Score', summary.trust_score != null ? String(summary.trust_score) : '-'],
    ['requires_human_review', 'Human Review', summary.requires_human_review ? 'Required' : 'Not needed'],
    ['latest_quality_status', 'Quality Signal', String(summary.latest_quality_status || '-')],
    ['incident_severity', 'Incident Severity', String(summary.incident_severity || '-')],
    ['incident_target_sla', 'Target SLA', String(summary.incident_target_sla || '-')],
  ];

  cardDefs.forEach(([key, label, value]) => {
    const article = document.createElement('article');
    article.className = `scorecard-card tone-${trustCardTone(key, summary)}`;
    article.innerHTML = `
      <div class="value">${escapeHtml(value)}</div>
      <div class="label">${escapeHtml(label)}</div>
    `;
    cardsNode.appendChild(article);
  });

  const latest = payload.latest || {};
  const metaParts = [];
  if (latest.run_id) metaParts.push(`run=${latest.run_id}`);
  if (summary.status) metaParts.push(`status=${summary.status}`);
  if (summary.incident_owner_team) metaParts.push(`owner=${summary.incident_owner_team}`);
  if (summary.event_count != null) metaParts.push(`events=${summary.event_count}`);
  if (summary.llm_call_count != null) metaParts.push(`llm_calls=${summary.llm_call_count}`);
  if (latest.completed_at) metaParts.push(`completed=${formatTime(latest.completed_at)}`);
  metaNode.textContent = metaParts.join(' • ');

  const actions = Array.isArray(payload?.packet?.operator_next?.top_actions)
    ? payload.packet.operator_next.top_actions
    : [];
  if (actions.length) {
    actions.slice(0, 3).forEach((item) => {
      const article = document.createElement('article');
      const actionText = Array.isArray(item?.actions) && item.actions.length
        ? item.actions.join('; ')
        : '-';
      article.className = 'trust-action';
      article.innerHTML = `
        <div class="title">${escapeHtml(item?.title || item?.code || 'Operator action')}</div>
        <div class="meta">${escapeHtml(item?.code || '-')}</div>
        <div class="body">${escapeHtml(actionText)}</div>
      `;
      actionsNode.appendChild(article);
    });
    actionsNode.classList.remove('hidden');
  } else {
    actionsNode.classList.add('hidden');
  }

  emptyNode.classList.add('hidden');
  cardsNode.classList.remove('hidden');
}

async function refreshScorecardWidget({ silent = false } = {}) {
  state.scorecardLoading = true;
  if (!silent) {
    state.scorecardError = '';
  }
  renderScorecardWidget();

  try {
    const payload = state.useMock
      ? buildMockScorecardPayload()
      : await fetchJson('/api/scorecard/latest');
    state.scorecardPayload = payload;
    state.scorecardError = '';
  } catch (err) {
    state.scorecardPayload = null;
    state.scorecardError = `Latest scorecard unavailable: ${err.message || 'request failed'}`;
  } finally {
    state.scorecardLoading = false;
    renderScorecardWidget();
  }
}

async function refreshTrustWidget({ silent = false } = {}) {
  state.trustLoading = true;
  if (!silent) {
    state.trustError = '';
  }
  renderTrustWidget();

  try {
    const payload = state.useMock
      ? buildMockTrustPayload()
      : await fetchJson('/api/autonomous/trust/latest');
    state.trustPayload = payload;
    state.trustError = '';
  } catch (err) {
    state.trustPayload = null;
    state.trustError = `Latest trust intelligence unavailable: ${err.message || 'request failed'}`;
  } finally {
    state.trustLoading = false;
    renderTrustWidget();
  }
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function friendlyApiError(payload, fallback = 'Request failed') {
  if (!payload || typeof payload !== 'object') return fallback;
  const err = payload.error;
  if (typeof err === 'string' && err.trim()) return err;
  if (err && typeof err === 'object') {
    const code = String(err.code || '').trim();
    const message = String(err.message || '').trim();
    if (message && code) return `${code}: ${message}`;
    if (message) return message;
    if (code) return code;
  }
  return fallback;
}

class ApiError extends Error {
  constructor(message, payload = null) {
    super(message);
    this.name = 'ApiError';
    this.payload = payload;
  }
}

function setRunControlStatus(message, { error = false } = {}) {
  const node = el('runControlStatus');
  if (!node) return;
  node.textContent = message;
  node.classList.toggle('error-text', Boolean(error));
}

function setProcessStatus(message, { error = false } = {}) {
  const node = el('processStatusLine');
  if (!node) return;
  node.textContent = String(message || '');
  node.classList.toggle('error-text', Boolean(error));
}

function renderTabStateBox(prefix, { show = false, kind = 'empty', message = '', hint = '' } = {}) {
  const box = el(`${prefix}StateBox`);
  const messageNode = el(`${prefix}StateMessage`);
  const hintNode = el(`${prefix}StateHint`);
  if (!box || !messageNode || !hintNode) return;

  box.classList.remove('is-loading', 'is-error', 'is-empty');
  if (!show) {
    box.classList.add('hidden');
    messageNode.textContent = '';
    hintNode.textContent = '';
    return;
  }

  const normalizedKind = ['loading', 'error', 'empty'].includes(kind) ? kind : 'empty';
  box.classList.add(`is-${normalizedKind}`);
  box.classList.remove('hidden');
  messageNode.textContent = String(message || '');
  hintNode.textContent = String(hint || '');
}

function loadLogsHint() {
  return 'Logs hint: check Process transition history, then inspect .autodev/task_final_last_validation.json in Artifact Viewer.';
}

async function openValidationLogsHint({ autoOpen = false } = {}) {
  const defaultPath = '.autodev/task_final_last_validation.json';
  const input = el('artifactPathInput');
  if (input) input.value = defaultPath;
  state.artifactViewerPath = defaultPath;
  activateTab('validation');

  if (autoOpen && state.selectedRunId) {
    await openArtifactInViewer(defaultPath, { source: 'logs-hint', autoFocus: false });
  } else {
    announceArtifactViewerAction('Loaded validation logs hint path. Click Open to inspect artifact.', 'ok');
  }
}

function renderOverviewState() {
  if (state.runsLoading) {
    renderTabStateBox('overview', {
      show: true,
      kind: 'loading',
      message: 'Loading runs and selected run detail…',
      hint: 'If this takes too long, click Refresh runs to retry.',
    });
    return;
  }

  if (state.runsError) {
    renderTabStateBox('overview', {
      show: true,
      kind: 'error',
      message: `Failed to load runs: ${state.runsError}`,
      hint: loadLogsHint(),
    });
    return;
  }

  if (!state.runs.length) {
    renderTabStateBox('overview', {
      show: true,
      kind: 'empty',
      message: 'No runs found yet.',
      hint: 'Start a new run via Quick Run or Start, then refresh this tab.',
    });
    return;
  }

  if (state.detailLoading) {
    renderTabStateBox('overview', {
      show: true,
      kind: 'loading',
      message: `Loading detail for ${state.selectedRunId || 'selected run'}…`,
      hint: 'Use Retry detail if loading stalls.',
    });
    return;
  }

  if (state.detailError) {
    renderTabStateBox('overview', {
      show: true,
      kind: 'error',
      message: `Failed to load run detail: ${state.detailError}`,
      hint: loadLogsHint(),
    });
    return;
  }

  renderTabStateBox('overview', { show: false });
}

function renderProcessTabState() {
  const filtered = filterProcesses(state.processes);

  if (state.processLoadInFlight && !state.processes.length) {
    renderTabStateBox('process', {
      show: true,
      kind: 'loading',
      message: 'Loading process list…',
      hint: 'Use Refresh processes to retry if this appears stuck.',
    });
    return;
  }

  if (state.processListError) {
    renderTabStateBox('process', {
      show: true,
      kind: 'error',
      message: state.processListError,
      hint: loadLogsHint(),
    });
    return;
  }

  if (!state.processes.length) {
    renderTabStateBox('process', {
      show: true,
      kind: 'empty',
      message: 'No tracked processes yet.',
      hint: 'Start/retry a run from Overview, then refresh this panel.',
    });
    return;
  }

  if (!filtered.length) {
    renderTabStateBox('process', {
      show: true,
      kind: 'empty',
      message: 'No processes match current filters.',
      hint: 'Clear filters or refresh processes to recover.',
    });
    return;
  }

  renderTabStateBox('process', { show: false });
}

function parseIsoMs(raw) {
  if (!raw) return 0;
  const ms = Date.parse(String(raw));
  return Number.isFinite(ms) ? ms : 0;
}

function formatElapsedMs(ms) {
  const totalSeconds = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return `${hours}h ${remMinutes}m`;
}

function latestProcessTransitionMs(process, historyOverride = null) {
  if (!process || typeof process !== 'object') return 0;

  const history = Array.isArray(historyOverride)
    ? historyOverride
    : (Array.isArray(process.transitions) ? process.transitions : []);

  let latest = 0;
  history.forEach((entry) => {
    latest = Math.max(latest, parseIsoMs(entry?.at));
  });

  return Math.max(latest, parseIsoMs(process.started_at));
}

function buildProcessTransitionSignature(rows) {
  if (!Array.isArray(rows) || !rows.length) return '';
  return [...rows]
    .sort((a, b) => String(a?.process_id || '').localeCompare(String(b?.process_id || '')))
    .map((row) => {
      const history = Array.isArray(row?.transitions) ? row.transitions : [];
      const latest = latestProcessTransitionMs(row, history);
      return [
        String(row?.process_id || ''),
        String(row?.state || ''),
        String(history.length),
        String(latest),
      ].join(':');
    })
    .join('|');
}

function setProcessStaleIndicator(process, history = null) {
  const node = el('processStaleIndicator');
  if (!node) return;

  if (!process) {
    node.classList.add('hidden');
    node.textContent = '';
    return;
  }

  const ageMs = Math.max(0, Date.now() - latestProcessTransitionMs(process, history));
  const isStale = ageMs >= Number(state.processStaleThresholdMs || 45_000);
  node.classList.toggle('hidden', !isStale);
  node.textContent = isStale ? `STALE • last transition ${formatElapsedMs(ageMs)} ago` : '';
}

function renderProcessPollingHint() {
  const node = el('processPollingHint');
  if (!node || !state.liveUpdateEnabled || state.useMock) {
    if (node) node.textContent = '';
    return;
  }

  const baseSec = Math.max(1, Math.round(Number(state.pollIntervalMs || 8000) / 1000));
  const intervalSec = baseSec * (2 ** Number(state.processPollBackoffExp || 0));
  const stable = Number(state.processStablePollCount || 0);
  node.textContent = stable > 0
    ? `Auto refresh ${intervalSec}s (adaptive x${2 ** Number(state.processPollBackoffExp || 0)} after ${stable} stable poll${stable > 1 ? 's' : ''})`
    : `Auto refresh ${intervalSec}s`;
}

function resetProcessPollingBackoff() {
  state.processTransitionSignature = '';
  state.processStablePollCount = 0;
  state.processPollBackoffExp = 0;
  state.processNextPollAtMs = 0;
  renderProcessPollingHint();
}

function noteProcessPollingSnapshot(rows, { source = 'manual' } = {}) {
  const signature = buildProcessTransitionSignature(rows);
  const hadPrevious = Boolean(state.processTransitionSignature);
  const changed = !hadPrevious || signature !== state.processTransitionSignature;

  state.processTransitionSignature = signature;

  if (changed) {
    state.processStablePollCount = 0;
    state.processPollBackoffExp = 0;
  } else if (source === 'poll') {
    state.processStablePollCount += 1;
    if (state.processStablePollCount >= 2) {
      state.processPollBackoffExp = Math.min(
        Number(state.processPollMaxBackoffExp || 3),
        Number(state.processPollBackoffExp || 0) + 1,
      );
      state.processStablePollCount = 0;
    }
  }

  const baseMs = Math.max(1000, Number(state.pollIntervalMs || 8000));
  const nextMs = baseMs * (2 ** Number(state.processPollBackoffExp || 0));
  state.processNextPollAtMs = Date.now() + nextMs;
  renderProcessPollingHint();
}

function setRunControlHints(hints) {
  const node = el('runControlHints');
  if (!node) return;

  const rows = Array.isArray(hints)
    ? [...new Set(hints.map((v) => String(v || '').trim()).filter(Boolean))].slice(0, 4)
    : [];

  node.innerHTML = '';
  if (!rows.length) {
    node.classList.add('hidden');
    return;
  }

  rows.forEach((hint) => {
    const li = document.createElement('li');
    li.textContent = hint;
    node.appendChild(li);
  });
  node.classList.remove('hidden');
}

function selectedRunValidatorHint() {
  const rows = normalizeValidationRows(state.detail || {});
  const failed = rows
    .filter((row) => ['failed', 'soft_fail'].includes(row.status))
    .map((row) => String(row.name || '').trim())
    .filter(Boolean);
  const unique = [...new Set(failed)].slice(0, 3);
  if (!unique.length) return '';
  return unique.length === 1
    ? `Selected run has a failed validator (${unique[0]}). Check Validation tab before retry/resume.`
    : `Selected run has failed validators (${unique.join(', ')}). Check Validation tab before retry/resume.`;
}

function deriveRunControlHints(action, payload) {
  const err = payload?.error;
  const hints = [];

  const push = (text) => {
    const val = String(text || '').trim();
    if (val && !hints.includes(val)) hints.push(val);
  };

  const apiHints = err?.fix_hints;
  if (Array.isArray(apiHints)) {
    apiHints.forEach((hint) => push(hint));
  }

  const code = String(err?.code || '').toLowerCase();
  const message = String(err?.message || '').toLowerCase();

  if (!hints.length) {
    if (code === 'missing_prd' || code === 'invalid_prd') {
      push('Set PRD to an existing file path (for example: examples/PRD.md).');
    }
    if (code === 'invalid_out' || code === 'missing_out') {
      push('Set Out to a directory path (not a file).');
    }
    if (code === 'forbidden_role') {
      push('Use developer/operator role or local-simple mode for mutating actions.');
    }
    if (code === 'missing_retry_target') {
      push('For Retry, provide process_id or run_id.');
    }
    if (code === 'invalid_payload' && (message.includes('appears finalized') || message.includes('status is terminal'))) {
      push('This run is finalized; use Retry instead of Resume.');
    }
  }

  if ((action === 'retry' || action === 'resume') && !hints.some((h) => h.toLowerCase().includes('validator'))) {
    const validatorHint = selectedRunValidatorHint();
    if (validatorHint) push(validatorHint);
  }

  return hints.slice(0, 4);
}

function updateProcessIdInput(processId) {
  if (!processId) return;
  state.lastProcessId = processId;
  state.selectedProcessId = processId;
  const input = el('controlProcessId');
  if (input && !input.value) {
    input.value = processId;
  }
}

function normalizeLocalPath(raw) {
  const val = String(raw || '').trim();
  if (!val) return '';
  return val;
}

function normalizeRunToken(raw) {
  const value = normalizeLocalPath(raw);
  if (!value) return '';
  return /^[A-Za-z0-9._:/@+-]+$/.test(value) ? value : '';
}

function firstNonEmpty(values) {
  for (const raw of values) {
    const value = normalizeLocalPath(raw);
    if (value) return value;
  }
  return '';
}

function resolveQuickRunPrdPath() {
  return firstNonEmpty([
    el('controlPrd')?.value,
    state.detail?.metadata?.prd,
    state.detail?.quality_index?.request?.prd,
    state.selectedRun?.prd,
    state.guiContext?.defaults?.prd,
  ]);
}

function updateQuickRunHint() {
  const node = el('quickRunHint');
  if (!node) return;

  const defaults = state.guiContext?.defaults || {};
  const profile = normalizeRunToken(defaults.profile) || 'local_simple';
  const out = normalizeLocalPath(el('controlOut')?.value) || normalizeLocalPath(defaults.out) || './generated_runs';
  const prd = resolveQuickRunPrdPath();

  if (!prd) {
    node.textContent = `Quick Run preset: profile=${profile}, out=${out}. Set PRD path to enable one-click run.`;
    node.classList.add('error-text');
    return;
  }

  node.textContent = `Quick Run preset: profile=${profile}, out=${out}, prd=${prd}`;
  node.classList.remove('error-text');
}

function renderHealthBanner(snapshot) {
  const banner = el('localHealthBanner');
  if (!banner) return;

  const gatewayOk = snapshot?.gateway_ok === true;
  const model = normalizeLocalPath(snapshot?.model);
  const contextOk = snapshot?.context_ok === true;
  const mode = normalizeLocalPath(snapshot?.mode) || 'unknown';

  banner.textContent = [
    `Gateway: ${gatewayOk ? 'OK' : 'Unavailable'}`,
    `Model: ${model || 'Unknown'}`,
    `Context: ${contextOk ? 'OK' : 'Unavailable'} (${mode})`,
  ].join(' • ');

  banner.classList.toggle('is-healthy', gatewayOk && contextOk);
  banner.classList.toggle('is-warning', !gatewayOk || !contextOk || !model);
}

async function refreshHealthBanner() {
  let gatewayOk = false;
  if (state.useMock) {
    gatewayOk = true;
  } else {
    try {
      const payload = await fetchJson('/healthz');
      gatewayOk = Boolean(payload?.ok);
    } catch {
      gatewayOk = false;
    }
  }

  const model = firstNonEmpty([
    state.detail?.model,
    state.selectedRun?.model,
    state.runs?.[0]?.model,
  ]);

  const snapshot = {
    gateway_ok: gatewayOk,
    model,
    context_ok: Boolean(state.guiContext),
    mode: state.guiContext?.mode || 'unknown',
  };
  state.healthSnapshot = snapshot;
  renderHealthBanner(snapshot);
}

function buildRunPayload(action) {
  const payload = {
    execute: Boolean(el('controlExecute')?.checked),
    interactive: Boolean(el('controlInteractive')?.checked),
  };

  const prd = normalizeLocalPath(el('controlPrd')?.value);
  const out = normalizeLocalPath(el('controlOut')?.value);
  const profile = normalizeLocalPath(el('controlProfile')?.value);
  const model = normalizeLocalPath(el('controlModel')?.value);
  const config = normalizeLocalPath(el('controlConfig')?.value);

  if (action === 'start' || action === 'resume') {
    if (prd) payload.prd = prd;
    if (out) payload.out = out;
    if (profile) payload.profile = profile;
    if (model) payload.model = model;
    if (config) payload.config = config;
  }

  if (action === 'stop' || action === 'retry') {
    const processId = normalizeLocalPath(el('controlProcessId')?.value) || state.lastProcessId;
    if (processId) payload.process_id = processId;
  }

  if (action === 'stop') {
    const timeoutRaw = Number(el('controlGracefulTimeout')?.value || 2.0);
    payload.graceful_timeout_sec = Number.isFinite(timeoutRaw) ? timeoutRaw : 2.0;
  }

  if (action === 'retry' && state.selectedRunId && !payload.process_id) {
    payload.run_id = state.selectedRunId;
  }

  return payload;
}

async function postJson(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(friendlyApiError(body, `${url} -> ${res.status}`), body);
  }
  return body;
}

function normalizeValidationStatus(status, ok) {
  const raw = String(status || '').trim().toLowerCase();
  if (raw) return VALIDATION_STATUS_ALIASES[raw] || raw;
  if (ok === true) return 'passed';
  if (ok === false) return 'failed';
  return 'unknown';
}

function statusPriority(status) {
  const idx = VALIDATION_STATUS_ORDER.indexOf(status);
  return idx === -1 ? VALIDATION_STATUS_ORDER.indexOf('unknown') : idx;
}

function normalizeValidationRows(detail) {
  const normalized = detail?.validation_normalized?.validator_cards;
  if (Array.isArray(normalized) && normalized.length) {
    return normalized.map((row) => {
      const status = normalizeValidationStatus(row?.status, row?.ok);
      return {
        name: String(row?.name || row?.validator || 'unknown'),
        status,
        ok: row?.ok !== undefined ? Boolean(row.ok) : status === 'passed',
        returncode: row?.returncode,
        duration_ms: row?.duration_ms,
        phase: row?.phase,
        scope: row?.scope || 'final',
        task_id: row?.task_id || '',
        artifact_path: row?.artifact_path || '.autodev/task_final_last_validation.json',
        message: row?.message || row?.error || row?.note || '',
        stdout: row?.stdout || '',
        stderr: row?.stderr || '',
      };
    });
  }

  const source = detail?.validation;
  if (!source || typeof source !== 'object') return [];

  const candidates = [
    source.validation,
    source.validators,
    source.results,
  ];

  let rows = [];
  for (const c of candidates) {
    if (Array.isArray(c)) {
      rows = c;
      break;
    }
  }

  return rows.map((v) => {
    const status = normalizeValidationStatus(v?.status, v?.ok);
    return {
      name: String(v?.name || v?.validator || 'unknown'),
      status,
      ok: v?.ok !== undefined ? Boolean(v.ok) : status === 'passed',
      returncode: v?.returncode,
      duration_ms: v?.duration_ms,
      phase: v?.phase,
      scope: 'final',
      task_id: '',
      artifact_path: '.autodev/task_final_last_validation.json',
      message: v?.message || v?.error || '',
      stdout: v?.stdout || '',
      stderr: v?.stderr || '',
    };
  });
}

function refreshValidationNameFilter(rows) {
  const select = el('validationNameFilter');
  const prev = state.validationName;
  const names = [...new Set(rows.map((r) => r.name).filter(Boolean))].sort();

  select.innerHTML = '<option value="all">All validators</option>';
  names.forEach((name) => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });

  if (prev !== 'all' && names.includes(prev)) {
    select.value = prev;
  } else {
    state.validationName = 'all';
    select.value = 'all';
  }
}

function sortValidationRows(rows) {
  const sorted = [...rows];

  sorted.sort((a, b) => {
    if (state.validationSort === 'name') {
      return a.name.localeCompare(b.name);
    }

    if (state.validationSort === 'duration') {
      const durDelta = (b.duration_ms || 0) - (a.duration_ms || 0);
      if (durDelta !== 0) return durDelta;
      return a.name.localeCompare(b.name);
    }

    const sevDelta = statusPriority(a.status) - statusPriority(b.status);
    if (sevDelta !== 0) return sevDelta;
    const durDelta = (b.duration_ms || 0) - (a.duration_ms || 0);
    if (durDelta !== 0) return durDelta;
    return a.name.localeCompare(b.name);
  });

  if (state.failedFirst) {
    sorted.sort((a, b) => {
      const aFail = a.status === 'failed' ? 1 : 0;
      const bFail = b.status === 'failed' ? 1 : 0;
      return bFail - aFail;
    });
  }

  return sorted;
}

function renderValidationSummaryChips(rows, filteredRows) {
  const wrap = el('validationSummaryChips');
  wrap.innerHTML = '';

  if (!rows.length) return;

  const counts = rows.reduce((acc, row) => {
    const key = row.status || 'unknown';
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});

  const filteredOut = rows.length - filteredRows.length;
  const chips = [
    { key: 'all', label: `Total ${rows.length}` },
    ...VALIDATION_STATUS_ORDER
      .filter((status) => counts[status])
      .map((status) => ({
        key: status,
        label: `${VALIDATION_STATUS_LABEL[status] || status} ${counts[status]}`,
      })),
  ];

  if (filteredOut > 0) {
    chips.push({ key: 'filtered', label: `Filtered out ${filteredOut}` });
  }

  chips.forEach((chip) => {
    const node = document.createElement('span');
    node.className = `summary-chip ${chip.key}`;
    node.textContent = chip.label;
    wrap.appendChild(node);
  });
}

function clearArtifactViewerState({ preservePath = false } = {}) {
  if (!preservePath) {
    state.artifactViewerPath = '';
  }
  state.artifactViewerPayload = null;
  state.artifactViewerError = '';
  state.artifactViewerLoading = false;
  state.artifactViewerRequestedBy = '';
  state.artifactViewerActionStatus = null;
  state.artifactViewerExpanded = false;
  if (artifactViewerActionToastTimer) {
    clearTimeout(artifactViewerActionToastTimer);
    artifactViewerActionToastTimer = null;
  }
}

function formatArtifactJsonError(error) {
  if (!error || error.kind !== 'artifact_json_error') return '';
  const detail = [`${error.code || 'artifact_json_error'}: ${error.message || 'Unable to parse JSON'}`];
  if (error.line != null && error.column != null) {
    detail.push(`line=${error.line}, column=${error.column}`);
  }
  if (error.position != null) {
    detail.push(`position=${error.position}`);
  }
  return detail.join(' • ');
}

function jsonShapePreview(value, depth = 0) {
  if (value === null) return null;
  if (Array.isArray(value)) {
    if (depth >= 1) return `[Array(${value.length})]`;
    return value.slice(0, 8).map((row) => jsonShapePreview(row, depth + 1));
  }
  if (typeof value === 'object') {
    if (depth >= 1) return '[Object]';
    const entries = Object.entries(value).slice(0, 20);
    return Object.fromEntries(entries.map(([key, row]) => [key, jsonShapePreview(row, depth + 1)]));
  }
  return value;
}

function formatJsonErrorFallback(payload) {
  const raw = String(payload?.raw_content || '');
  const err = payload?.error;
  if (!raw) return '';
  if (!err || err.kind !== 'artifact_json_error') return raw;

  const lines = raw.split('\n');
  const lineNo = Number(err.line || 1);
  const colNo = Number(err.column || 1);
  const start = Math.max(0, lineNo - 2);
  const end = Math.min(lines.length, lineNo + 2);
  const width = String(end).length;

  const excerpt = [];
  for (let idx = start; idx < end; idx += 1) {
    const displayNo = idx + 1;
    excerpt.push(`${String(displayNo).padStart(width, ' ')} | ${lines[idx]}`);
    if (displayNo === lineNo) {
      const caretPad = ' '.repeat(Math.max(0, colNo - 1));
      excerpt.push(`${' '.repeat(width)} | ${caretPad}^`);
    }
  }

  return [
    '# JSON parse fallback',
    `${err.code || 'artifact_json_error'}: ${err.message || 'Unable to parse JSON'}`,
    '',
    ...excerpt,
    '',
    '--- raw payload preview ---',
    raw.slice(0, ARTIFACT_RENDER_PREVIEW_CHARS),
    raw.length > ARTIFACT_RENDER_PREVIEW_CHARS ? '\n… (preview truncated)' : '',
  ].filter(Boolean).join('\n');
}

function getArtifactViewerExportText(payload) {
  if (!payload) {
    return { text: '', canExport: false };
  }

  if (typeof payload.raw_content === 'string') {
    return { text: payload.raw_content, canExport: payload.raw_content.length > 0 };
  }

  if (typeof payload.content === 'string') {
    return { text: payload.content, canExport: payload.content.length > 0 };
  }

  if (payload.content !== null && payload.content !== undefined) {
    return { text: JSON.stringify(payload.content, null, 2), canExport: true };
  }

  return { text: '', canExport: false };
}

function getArtifactViewerTextPayload(payload, { expanded = false } = {}) {
  if (!payload) {
    return { text: '', mode: 'empty', canExport: false, previewOnly: false, canExpand: false };
  }

  const exportPayload = getArtifactViewerExportText(payload);
  const isJson = payload.content_type === 'application/json';
  const rawLen = typeof payload.raw_content === 'string' ? payload.raw_content.length : 0;
  const canExpand = rawLen > ARTIFACT_RENDER_PREVIEW_CHARS;

  if (isJson && payload.content !== null && payload.content !== undefined) {
    const shouldRenderSummary = !expanded && rawLen >= ARTIFACT_JSON_EXPAND_THRESHOLD_CHARS;
    if (shouldRenderSummary) {
      const summary = {
        notice: `Large JSON payload (${rawLen.toLocaleString()} chars). Previewing schema to keep UI responsive.`,
        top_level_type: Array.isArray(payload.content) ? 'array' : typeof payload.content,
        top_level_summary: jsonShapePreview(payload.content),
      };
      return {
        text: `${JSON.stringify(summary, null, 2)}\n\nUse “Expand full” to render full JSON payload.`,
        mode: 'json_summary',
        canExport: exportPayload.canExport,
        previewOnly: true,
        canExpand,
      };
    }

    const pretty = JSON.stringify(payload.content, null, 2);
    if (!expanded && pretty.length > ARTIFACT_RENDER_PREVIEW_CHARS) {
      return {
        text: `${pretty.slice(0, ARTIFACT_RENDER_PREVIEW_CHARS)}\n\n… (preview truncated; expand for full view)` ,
        mode: 'json_pretty_preview',
        canExport: exportPayload.canExport,
        previewOnly: true,
        canExpand,
      };
    }

    return {
      text: pretty,
      mode: 'json_pretty',
      canExport: exportPayload.canExport,
      previewOnly: false,
      canExpand,
    };
  }

  if (isJson && typeof payload.raw_content === 'string') {
    const fallback = formatJsonErrorFallback(payload);
    if (!expanded && fallback.length > ARTIFACT_RENDER_PREVIEW_CHARS) {
      return {
        text: `${fallback.slice(0, ARTIFACT_RENDER_PREVIEW_CHARS)}\n\n… (preview truncated; expand for full view)`,
        mode: 'json_raw_preview',
        canExport: exportPayload.canExport,
        previewOnly: true,
        canExpand,
      };
    }
    return {
      text: fallback,
      mode: 'json_raw',
      canExport: exportPayload.canExport,
      previewOnly: false,
      canExpand,
    };
  }

  if (typeof payload.content === 'string') {
    const val = payload.content;
    if (!expanded && val.length > ARTIFACT_RENDER_PREVIEW_CHARS) {
      return {
        text: `${val.slice(0, ARTIFACT_RENDER_PREVIEW_CHARS)}\n\n… (preview truncated; expand for full view)`,
        mode: payload.content_type === 'text/markdown' ? 'markdown_preview' : 'text_preview',
        canExport: exportPayload.canExport,
        previewOnly: true,
        canExpand,
      };
    }
    return {
      text: val,
      mode: payload.content_type === 'text/markdown' ? 'markdown' : 'text',
      canExport: exportPayload.canExport,
      previewOnly: false,
      canExpand,
    };
  }

  if (payload.content !== null && payload.content !== undefined) {
    const pretty = JSON.stringify(payload.content, null, 2);
    return {
      text: pretty,
      mode: 'object_pretty',
      canExport: exportPayload.canExport,
      previewOnly: false,
      canExpand: pretty.length > ARTIFACT_RENDER_PREVIEW_CHARS,
    };
  }

  return { text: '', mode: 'empty', canExport: false, previewOnly: false, canExpand: false };
}

function artifactViewerDownloadName(path, payload) {
  const normalized = String(path || '').trim();
  if (!normalized) {
    return payload?.content_type === 'application/json' ? 'artifact.json' : 'artifact.txt';
  }
  const base = normalized.split('/').pop() || 'artifact';
  if (base.includes('.')) {
    return base;
  }
  if (payload?.content_type === 'application/json') {
    return `${base}.json`;
  }
  return `${base}.txt`;
}

function setArtifactViewerActionStatus(message, kind = 'ok') {
  const node = el('artifactViewerActionStatus');
  if (!node) return;
  if (!message) {
    node.textContent = '';
    node.classList.add('hidden');
    node.classList.remove('ok', 'error');
    return;
  }
  node.textContent = message;
  node.classList.remove('hidden');
  node.classList.remove('ok', 'error');
  node.classList.add(kind === 'error' ? 'error' : 'ok');
}

function announceArtifactViewerAction(message, kind = 'ok') {
  if (artifactViewerActionToastTimer) {
    clearTimeout(artifactViewerActionToastTimer);
    artifactViewerActionToastTimer = null;
  }
  state.artifactViewerActionStatus = { message, kind };
  renderArtifactViewer();
  artifactViewerActionToastTimer = window.setTimeout(() => {
    const current = state.artifactViewerActionStatus;
    if (current?.message === message && current?.kind === kind) {
      state.artifactViewerActionStatus = null;
      renderArtifactViewer();
    }
    artifactViewerActionToastTimer = null;
  }, ARTIFACT_ACTION_TOAST_MS);
}

async function withPreservedFocus(action) {
  const active = document.activeElement;
  try {
    return await action();
  } finally {
    if (active instanceof HTMLElement && document.contains(active) && !active.hasAttribute('disabled')) {
      try {
        active.focus({ preventScroll: true });
      } catch {
        active.focus();
      }
    }
  }
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const ghost = document.createElement('textarea');
  ghost.value = text;
  ghost.setAttribute('readonly', '');
  ghost.style.position = 'fixed';
  ghost.style.opacity = '0';
  document.body.appendChild(ghost);
  ghost.focus();
  ghost.select();
  document.execCommand('copy');
  document.body.removeChild(ghost);
}

function renderArtifactViewer() {
  const panel = el('artifactViewerPanel');
  const empty = el('artifactViewerEmpty');
  const errorNode = el('artifactViewerError');
  const contentNode = el('artifactViewerContent');
  const pathNode = el('artifactViewerPathLabel');
  const metaNode = el('artifactViewerMeta');
  const pathInput = el('artifactPathInput');
  const copyBtn = el('artifactCopyBtn');
  const downloadBtn = el('artifactDownloadBtn');
  const expandBtn = el('artifactExpandBtn');

  if (!panel || !empty || !errorNode || !contentNode || !pathNode || !metaNode) {
    return;
  }

  if (pathInput && pathInput.value !== state.artifactViewerPath) {
    pathInput.value = state.artifactViewerPath;
  }

  pathNode.textContent = state.artifactViewerPath || '-';
  setArtifactViewerActionStatus(state.artifactViewerActionStatus?.message || '', state.artifactViewerActionStatus?.kind || 'ok');

  if (state.artifactViewerLoading) {
    panel.classList.remove('hidden');
    empty.classList.add('hidden');
    errorNode.classList.add('hidden');
    metaNode.textContent = 'Loading artifact…';
    contentNode.textContent = '';
    if (copyBtn) copyBtn.disabled = true;
    if (downloadBtn) downloadBtn.disabled = true;
    if (expandBtn) {
      expandBtn.disabled = true;
      expandBtn.classList.add('hidden');
    }
    return;
  }

  if (state.artifactViewerError) {
    panel.classList.remove('hidden');
    empty.classList.add('hidden');
    errorNode.classList.remove('hidden');
    errorNode.textContent = state.artifactViewerError;
    metaNode.textContent = state.artifactViewerRequestedBy
      ? `requested by ${state.artifactViewerRequestedBy}`
      : 'Artifact read failed';
    contentNode.textContent = '';
    if (copyBtn) copyBtn.disabled = true;
    if (downloadBtn) downloadBtn.disabled = true;
    if (expandBtn) {
      expandBtn.disabled = true;
      expandBtn.classList.add('hidden');
    }
    return;
  }

  const payload = state.artifactViewerPayload;
  if (!payload) {
    panel.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = 'Select a failed validator or enter a .autodev artifact path.';
    errorNode.classList.add('hidden');
    metaNode.textContent = '';
    contentNode.textContent = '';
    if (copyBtn) copyBtn.disabled = true;
    if (downloadBtn) downloadBtn.disabled = true;
    if (expandBtn) {
      expandBtn.disabled = true;
      expandBtn.classList.add('hidden');
    }
    return;
  }

  panel.classList.remove('hidden');
  empty.classList.add('hidden');

  const textPayload = getArtifactViewerTextPayload(payload, {
    expanded: Boolean(state.artifactViewerExpanded),
  });
  const exportPayload = getArtifactViewerExportText(payload);

  const metaParts = [payload.content_type || 'text/plain'];
  if (payload.truncated) {
    metaParts.push('truncated');
  }
  if (payload.error?.code) {
    metaParts.push(`error=${payload.error.code}`);
  }
  if (textPayload.previewOnly) {
    metaParts.push('preview');
  }
  if (textPayload.mode === 'json_raw' || textPayload.mode === 'json_raw_preview') {
    metaParts.push('raw-fallback');
  }
  if (typeof payload.full_size_bytes === 'number') {
    metaParts.push(`bytes=${Number(payload.full_size_bytes).toLocaleString()}`);
  }
  if (state.artifactViewerRequestedBy) {
    metaParts.push(`requested by ${state.artifactViewerRequestedBy}`);
  }
  metaNode.textContent = metaParts.join(' • ');

  const typedError = formatArtifactJsonError(payload.error);
  if (typedError) {
    errorNode.classList.remove('hidden');
    errorNode.textContent = typedError;
  } else {
    errorNode.classList.add('hidden');
    errorNode.textContent = '';
  }

  contentNode.textContent = textPayload.text;
  const canExport = exportPayload.canExport;
  if (copyBtn) copyBtn.disabled = !canExport;
  if (downloadBtn) downloadBtn.disabled = !canExport;

  if (expandBtn) {
    const showExpand = textPayload.canExpand;
    expandBtn.classList.toggle('hidden', !showExpand);
    expandBtn.textContent = state.artifactViewerExpanded ? 'Collapse preview' : 'Expand full';
    expandBtn.disabled = !showExpand;
  }
}

async function fetchJsonWithPayload(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(friendlyApiError(body, `${url} -> ${res.status}`), body);
  }
  return body;
}

async function openArtifactInViewer(path, { source = '', autoFocus = true } = {}) {
  const normalizedPath = String(path || '').trim();
  if (!normalizedPath) {
    clearArtifactViewerState();
    state.artifactViewerError = 'artifact path is required';
    renderArtifactViewer();
    return;
  }

  if (!state.selectedRunId || state.useMock) {
    clearArtifactViewerState({ preservePath: true });
    state.artifactViewerPath = normalizedPath;
    state.artifactViewerError = 'artifact viewer requires a selected real run (mock mode unsupported).';
    state.artifactViewerRequestedBy = source;
    renderArtifactViewer();
    return;
  }

  state.artifactViewerPath = normalizedPath;
  state.artifactViewerPayload = null;
  state.artifactViewerError = '';
  state.artifactViewerLoading = true;
  state.artifactViewerRequestedBy = source;
  state.artifactViewerActionStatus = null;
  state.artifactViewerExpanded = false;
  renderArtifactViewer();

  const query = new URLSearchParams({ path: normalizedPath, max_bytes: '512000' }).toString();
  try {
    const payload = await fetchJsonWithPayload(`/api/runs/${encodeURIComponent(state.selectedRunId)}/artifacts/read?${query}`);
    state.artifactViewerPayload = payload;
    state.artifactViewerError = '';
  } catch (err) {
    state.artifactViewerPayload = null;
    state.artifactViewerError = err?.message || 'artifact read failed';
  } finally {
    state.artifactViewerLoading = false;
    renderArtifactViewer();
    if (autoFocus) {
      activateTab('validation');
    }
  }
}

function renderValidationTriageContext(context, allRows) {
  const panel = el('validationTriagePanel');
  const empty = el('validationTriageEmpty');
  const body = el('validationTriageBody');

  body.innerHTML = '';

  if (!context) {
    panel.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = 'Click a failed validator to open task/artifact context.';
    return;
  }

  panel.classList.remove('hidden');
  empty.classList.add('hidden');

  const heading = document.createElement('div');
  heading.className = 'triage-heading';
  heading.innerHTML = `<strong>${escapeHtml(context.name)}</strong> • ${escapeHtml(VALIDATION_STATUS_LABEL[context.status] || context.status)}`;
  body.appendChild(heading);

  const source = document.createElement('div');
  source.className = 'triage-meta';
  source.textContent = `source=${context.scope || 'final'}${context.task_id ? ` • task=${context.task_id}` : ''}`;
  body.appendChild(source);

  const artifacts = [...new Set([
    context.artifact_path,
    ...allRows
      .filter((row) => row.name === context.name && row.status === 'failed' && row.artifact_path)
      .map((row) => row.artifact_path),
  ].filter(Boolean))];

  const artifactList = document.createElement('ul');
  artifactList.className = 'triage-list';
  artifacts.forEach((path) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'triage-link-btn triage-artifact-btn';
    btn.innerHTML = `<code>${escapeHtml(path)}</code>`;
    btn.addEventListener('click', () => {
      void openArtifactInViewer(path, {
        source: `validator=${context.name}`,
      });
    });
    li.appendChild(btn);
    artifactList.appendChild(li);
  });
  if (artifacts.length) {
    const label = document.createElement('div');
    label.className = 'triage-subtitle';
    label.textContent = 'Related artifacts';
    body.appendChild(label);
    body.appendChild(artifactList);
  }

  const relatedTasks = [...new Set(
    allRows
      .filter((row) => row.name === context.name && row.status === 'failed' && row.task_id)
      .map((row) => row.task_id)
  )];

  if (relatedTasks.length) {
    const label = document.createElement('div');
    label.className = 'triage-subtitle';
    label.textContent = 'Related tasks';
    body.appendChild(label);

    const row = document.createElement('div');
    row.className = 'triage-actions';
    relatedTasks.forEach((taskId) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'triage-link-btn';
      btn.textContent = taskId;
      btn.addEventListener('click', () => {
        state.focusedTaskId = taskId;
        activateTab('overview');
        renderTasks(state.detail?.tasks || []);
        focusTaskRow(taskId);
      });
      row.appendChild(btn);
    });
    body.appendChild(row);
  }

  const filterHint = document.createElement('div');
  filterHint.className = 'triage-meta';
  filterHint.textContent = 'Tip: combine status=Failed + validator filter for tight triage.';
  body.appendChild(filterHint);
}

function renderValidationState({ rows = [], filtered = [] } = {}) {
  if (state.detailLoading) {
    renderTabStateBox('validation', {
      show: true,
      kind: 'loading',
      message: `Loading validation data for ${state.selectedRunId || 'selected run'}…`,
      hint: 'Use Refresh run detail if loading stalls.',
    });
    return;
  }

  if (state.detailError) {
    renderTabStateBox('validation', {
      show: true,
      kind: 'error',
      message: `Failed to load run detail: ${state.detailError}`,
      hint: loadLogsHint(),
    });
    return;
  }

  if (!rows.length) {
    renderTabStateBox('validation', {
      show: true,
      kind: 'empty',
      message: 'Validation artifact not found for selected run.',
      hint: 'Refresh run detail, then open logs hint to inspect .autodev artifacts.',
    });
    return;
  }

  if (!filtered.length) {
    renderTabStateBox('validation', {
      show: true,
      kind: 'empty',
      message: 'No validators match current filter/search.',
      hint: 'Clear/adjust filters to reveal validator results.',
    });
    return;
  }

  renderTabStateBox('validation', { show: false });
}

function renderValidationPanels(detail) {
  const groupsRoot = el('validationCards');
  const validationEmpty = el('validationEmpty');
  const qualityPanel = el('qualityPanel');
  const qualityEmpty = el('qualityEmpty');

  const rows = normalizeValidationRows(detail || {});
  refreshValidationNameFilter(rows);

  const keyword = state.validationSearch.trim().toLowerCase();
  const filtered = rows.filter((row) => {
    const statusOk = state.validationStatus === 'all' || row.status === state.validationStatus;
    const nameOk = state.validationName === 'all' || row.name === state.validationName;
    const keywordOk =
      !keyword
      || row.name.toLowerCase().includes(keyword)
      || row.status.toLowerCase().includes(keyword)
      || String(row.message || '').toLowerCase().includes(keyword)
      || String(row.stdout || '').toLowerCase().includes(keyword)
      || String(row.stderr || '').toLowerCase().includes(keyword);
    return statusOk && nameOk && keywordOk;
  });

  renderValidationState({ rows, filtered });

  const contextStillExists = state.triageContext
    && rows.some((row) => row.name === state.triageContext.name && row.status === state.triageContext.status);
  if (state.triageContext && !contextStillExists) {
    state.triageContext = null;
  }

  if (!state.triageContext) {
    const firstFailed = rows.find((row) => row.status === 'failed');
    if (firstFailed) {
      state.triageContext = {
        name: firstFailed.name,
        status: firstFailed.status,
        scope: firstFailed.scope,
        task_id: firstFailed.task_id,
        artifact_path: firstFailed.artifact_path,
      };
    }
  }

  renderValidationSummaryChips(rows, filtered);
  renderValidationTriageContext(state.triageContext, rows);

  groupsRoot.innerHTML = '';
  if (!rows.length) {
    groupsRoot.classList.add('hidden');
    validationEmpty.classList.remove('hidden');
    validationEmpty.textContent = 'Validation artifact not found.';
  } else if (!filtered.length) {
    groupsRoot.classList.add('hidden');
    validationEmpty.classList.remove('hidden');
    validationEmpty.textContent = 'No validators match current filter/search.';
  } else {
    groupsRoot.classList.remove('hidden');
    validationEmpty.classList.add('hidden');

    const sorted = sortValidationRows(filtered);
    const grouped = sorted.reduce((acc, row) => {
      const key = row.status || 'unknown';
      if (!acc[key]) acc[key] = [];
      acc[key].push(row);
      return acc;
    }, {});

    const groupOrder = VALIDATION_STATUS_ORDER.filter((status) => grouped[status]);
    Object.keys(grouped)
      .filter((status) => !groupOrder.includes(status))
      .sort()
      .forEach((status) => groupOrder.push(status));

    groupOrder.forEach((status) => {
      const section = document.createElement('section');

      const title = document.createElement('h4');
      title.className = 'validation-group-title';
      title.textContent = `${VALIDATION_STATUS_LABEL[status] || status} (${grouped[status].length})`;
      section.appendChild(title);

      const cards = document.createElement('div');
      cards.className = 'validation-cards';

      grouped[status].forEach((row) => {
        const card = document.createElement('article');
        const isTriageTarget = row.status === 'failed';
        card.className = `validator-card status-${row.status}${isTriageTarget ? ' is-clickable' : ''}`;
        card.innerHTML = `
          <div class="validator-header">
            <strong>${escapeHtml(row.name)}</strong>
            <span class="validator-status ${row.status}">${(VALIDATION_STATUS_LABEL[row.status] || row.status).toUpperCase()}</span>
          </div>
          <div class="validator-meta">
            <span>rc: ${row.returncode ?? '-'}</span>
            <span>duration: ${row.duration_ms ?? '-'}ms</span>
            <span>phase: ${escapeHtml(row.phase || '-')}</span>
            <span>source: ${escapeHtml(row.scope || 'final')}${row.task_id ? `/${escapeHtml(row.task_id)}` : ''}</span>
          </div>
          ${row.message ? `<div class="validator-message">${escapeHtml(row.message)}</div>` : ''}
          <div class="validator-output">
            ${row.stderr ? `<details><summary>stderr</summary><pre>${escapeHtml(row.stderr)}</pre></details>` : ''}
            ${row.stdout ? `<details><summary>stdout</summary><pre>${escapeHtml(row.stdout)}</pre></details>` : ''}
          </div>
        `;

        if (isTriageTarget) {
          card.addEventListener('click', () => {
            state.triageContext = {
              name: row.name,
              status: row.status,
              scope: row.scope,
              task_id: row.task_id,
              artifact_path: row.artifact_path,
            };
            renderValidationTriageContext(state.triageContext, rows);
            if (row.artifact_path) {
              void openArtifactInViewer(row.artifact_path, {
                source: `validator=${row.name}`,
                autoFocus: false,
              });
            }
          });
        }

        cards.appendChild(card);
      });

      section.appendChild(cards);
      groupsRoot.appendChild(section);
    });
  }

  if (detail.quality_index && Object.keys(detail.quality_index).length > 0) {
    qualityPanel.textContent = JSON.stringify(detail.quality_index, null, 2);
    qualityPanel.classList.remove('hidden');
    qualityEmpty.classList.add('hidden');
  } else {
    qualityPanel.classList.add('hidden');
    qualityEmpty.classList.remove('hidden');
  }
}

function initComparisonState({ forceLatest = false } = {}) {
  const ids = state.runs.map((run) => run.run_id).filter(Boolean);
  if (!ids.length) {
    state.compareLeftId = null;
    state.compareRightId = null;
    return;
  }

  const existingValid =
    !forceLatest
    && state.compareLeftId
    && state.compareRightId
    && ids.includes(state.compareLeftId)
    && ids.includes(state.compareRightId)
    && state.compareLeftId !== state.compareRightId;

  if (existingValid) {
    return;
  }

  state.compareLeftId = ids[0] || null;
  state.compareRightId = ids.length >= 2 ? ids[1] : null;
}

function refreshCompareRunOptions() {
  const left = el('compareLeftRun');
  const right = el('compareRightRun');
  if (!left || !right) return;

  left.innerHTML = '';
  right.innerHTML = '';

  if (!state.runs.length) {
    const emptyLeft = document.createElement('option');
    emptyLeft.value = '';
    emptyLeft.textContent = 'No runs';
    const emptyRight = emptyLeft.cloneNode(true);
    left.appendChild(emptyLeft);
    right.appendChild(emptyRight);
    return;
  }

  state.runs.forEach((run) => {
    const label = `${run.run_id} (${run.status || 'unknown'})`;

    const leftOpt = document.createElement('option');
    leftOpt.value = run.run_id;
    leftOpt.textContent = label;
    left.appendChild(leftOpt);

    const rightOpt = document.createElement('option');
    rightOpt.value = run.run_id;
    rightOpt.textContent = label;
    right.appendChild(rightOpt);
  });

  if (state.compareRightId === null) {
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select second run';
    right.insertBefore(placeholder, right.firstChild);
  }

  left.value = state.compareLeftId || state.runs[0].run_id;
  right.value = state.compareRightId || '';
}

function normalizeComparisonTrust(detail) {
  const trust = detail?.trust_summary || {};
  return {
    status: String(trust.trust_status || trust.status || '').trim(),
    score: Number(trust.trust_score ?? 0),
    requires_human_review: typeof trust.requires_human_review === 'boolean' ? trust.requires_human_review : null,
    latest_quality_status: String(trust.latest_quality_status || '').trim(),
    incident_owner_team: String(trust.incident_owner_team || '').trim(),
    incident_severity: String(trust.incident_severity || '').trim(),
  };
}

function formatComparisonTrustScore(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '-';
  return num.toFixed(2);
}

function formatComparisonReviewState(value) {
  if (value === true) return 'Required';
  if (value === false) return 'Not required';
  return '-';
}

function comparisonTrustTone(side) {
  const status = String(side?.trust?.status || '').toLowerCase();
  if (status === 'high') return 'ok';
  if (status === 'moderate') return 'warning';
  if (status === 'low') return 'danger';
  return 'neutral';
}

function toComparisonSummary(detail) {
  const summary = detail?.summary || {};
  const totals = summary.totals || {};
  const metadata = detail?.metadata || {};
  const validationSummary = detail?.validation_normalized?.summary || {};
  const blockers = Array.isArray(detail?.blockers) ? detail.blockers.map((x) => String(x)) : [];

  const validatorCards = Array.isArray(detail?.validation_normalized?.validator_cards)
    ? detail.validation_normalized.validator_cards
    : normalizeValidationRows(detail);

  const outcomes = {};
  validatorCards.forEach((row) => {
    const name = String(row?.name || row?.validator || '').trim();
    if (!name) return;
    outcomes[name] = normalizeValidationStatus(row?.status, row?.ok);
  });

  const normalizedStatus = String(detail?.status || 'unknown').toLowerCase();

  return {
    run_id: detail?.run_id || '',
    status: normalizedStatus,
    project_type: summary?.project?.type || '',
    profile: summary?.profile?.name || metadata?.profile || '',
    model: detail?.model || metadata?.model || '',
    totals: {
      total_task_attempts: Number(totals.total_task_attempts || 0),
      hard_failures: Number(totals.hard_failures || 0),
      soft_failures: Number(totals.soft_failures || 0),
      task_count: Array.isArray(detail?.tasks) ? detail.tasks.length : 0,
      blocker_count: blockers.length,
    },
    validation: {
      total: Number(validationSummary.total || 0),
      passed: Number(validationSummary.passed || 0),
      failed: Number(validationSummary.failed || 0),
      soft_fail: Number(validationSummary.soft_fail || 0),
      skipped: Number(validationSummary.skipped || 0),
      blocking_failed: Number(validationSummary.blocking_failed || 0),
    },
    blockers,
    validator_outcomes: outcomes,
    trust: normalizeComparisonTrust(detail),
    trust_packet: detail?.trust_packet || null,
    trust_message: String(detail?.trust_message || ''),
  };
}

function buildComparisonFromDetails(leftDetail, rightDetail) {
  const left = toComparisonSummary(leftDetail);
  const right = toComparisonSummary(rightDetail);

  const leftOutcomes = left.validator_outcomes || {};
  const rightOutcomes = right.validator_outcomes || {};
  const shared = Object.keys(leftOutcomes).filter((name) => rightOutcomes[name] !== undefined).sort();

  const validationDiffs = shared
    .filter((name) => leftOutcomes[name] !== rightOutcomes[name])
    .map((name) => ({ name, left: leftOutcomes[name], right: rightOutcomes[name] }));

  return {
    left,
    right,
    delta: {
      total_task_attempts: right.totals.total_task_attempts - left.totals.total_task_attempts,
      hard_failures: right.totals.hard_failures - left.totals.hard_failures,
      soft_failures: right.totals.soft_failures - left.totals.soft_failures,
      blocker_count: right.totals.blocker_count - left.totals.blocker_count,
      validation_failed: right.validation.failed - left.validation.failed,
      validation_passed: right.validation.passed - left.validation.passed,
      status_changed: left.status !== right.status,
      trust_score: right.trust.score - left.trust.score,
      trust_status_changed: left.trust.status !== right.trust.status,
      trust_review_changed: left.trust.requires_human_review !== right.trust.requires_human_review,
      trust_quality_status_changed: left.trust.latest_quality_status !== right.trust.latest_quality_status,
      trust_owner_changed: left.trust.incident_owner_team !== right.trust.incident_owner_team,
      trust_severity_changed: left.trust.incident_severity !== right.trust.incident_severity,
      blockers_only_left: left.blockers.filter((b) => !right.blockers.includes(b)),
      blockers_only_right: right.blockers.filter((b) => !left.blockers.includes(b)),
      validation_changed: validationDiffs,
    },
  };
}

async function enrichComparisonPayload(payload) {
  const hasValidatorDiffs = Array.isArray(payload?.delta?.validation_changed);
  const hasBlockerSplits = Array.isArray(payload?.delta?.blockers_only_left)
    && Array.isArray(payload?.delta?.blockers_only_right);
  const hasTrustRows = payload?.left?.trust && payload?.right?.trust;
  const hasTrustDelta = payload?.delta && Object.prototype.hasOwnProperty.call(payload.delta, 'trust_score');
  const hasTrustPackets = payload?.left?.trust_packet && payload?.right?.trust_packet;

  if (hasValidatorDiffs && hasBlockerSplits && hasTrustRows && hasTrustDelta && hasTrustPackets) {
    return payload;
  }

  if (!state.compareLeftId || !state.compareRightId || state.useMock) {
    return payload;
  }

  try {
    const [leftDetail, rightDetail] = await Promise.all([
      fetchJson(`/api/runs/${encodeURIComponent(state.compareLeftId)}`),
      fetchJson(`/api/runs/${encodeURIComponent(state.compareRightId)}`),
    ]);
    const fallback = buildComparisonFromDetails(leftDetail, rightDetail);
    return {
      ...payload,
      left: {
        ...(payload?.left || {}),
        trust: payload?.left?.trust || fallback.left.trust,
        trust_packet: payload?.left?.trust_packet || fallback.left.trust_packet,
        trust_message: payload?.left?.trust_message || fallback.left.trust_message,
      },
      right: {
        ...(payload?.right || {}),
        trust: payload?.right?.trust || fallback.right.trust,
        trust_packet: payload?.right?.trust_packet || fallback.right.trust_packet,
        trust_message: payload?.right?.trust_message || fallback.right.trust_message,
      },
      delta: {
        ...(payload?.delta || {}),
        status_changed: payload?.delta?.status_changed ?? fallback.delta.status_changed,
        blockers_only_left: payload?.delta?.blockers_only_left || fallback.delta.blockers_only_left,
        blockers_only_right: payload?.delta?.blockers_only_right || fallback.delta.blockers_only_right,
        validation_changed: payload?.delta?.validation_changed || fallback.delta.validation_changed,
        trust_score: payload?.delta?.trust_score ?? fallback.delta.trust_score,
        trust_status_changed: payload?.delta?.trust_status_changed ?? fallback.delta.trust_status_changed,
        trust_review_changed: payload?.delta?.trust_review_changed ?? fallback.delta.trust_review_changed,
        trust_quality_status_changed: payload?.delta?.trust_quality_status_changed ?? fallback.delta.trust_quality_status_changed,
        trust_owner_changed: payload?.delta?.trust_owner_changed ?? fallback.delta.trust_owner_changed,
        trust_severity_changed: payload?.delta?.trust_severity_changed ?? fallback.delta.trust_severity_changed,
      },
    };
  } catch {
    return payload;
  }
}

function buildCompareTrustInspectButtons() {
  return [
    '<button type="button" class="compare-diff-action" data-compare-trust-side="left">Inspect baseline</button>',
    '<button type="button" class="compare-diff-action" data-compare-trust-side="right">Inspect candidate</button>',
  ].join('');
}

function renderCompareTrustDrilldown(payload) {
  const panel = el('compareTrustPanel');
  const emptyNode = el('compareTrustEmpty');
  if (!panel || !emptyNode) return;

  panel.innerHTML = '';

  if (!payload?.left || !payload?.right) {
    panel.classList.add('hidden');
    emptyNode.classList.remove('hidden');
    emptyNode.textContent = 'Click a trust difference to inspect trust packets and operator actions for either run.';
    return;
  }

  const focus = state.compareTrustFocus && payload[state.compareTrustFocus] ? state.compareTrustFocus : '';
  const sides = [
    ['left', 'Baseline', payload.left || {}],
    ['right', 'Candidate', payload.right || {}],
  ];

  sides.forEach(([sideKey, sideLabel, side]) => {
    const article = document.createElement('article');
    article.className = `compare-trust-card tone-${comparisonTrustTone(side)} ${focus === sideKey ? 'is-focused' : ''}`.trim();

    const trust = side?.trust || {};
    const packet = side?.trust_packet || {};
    const actions = Array.isArray(packet?.operator_next?.top_actions)
      ? packet.operator_next.top_actions.slice(0, 3)
      : [];
    const message = String(side?.trust_message || '').trim();
    const summaryItems = [
      ['Trust status', trust.status || '-'],
      ['Trust score', formatComparisonTrustScore(trust.score)],
      ['Human review', formatComparisonReviewState(trust.requires_human_review)],
      ['Quality signal', trust.latest_quality_status || '-'],
      ['Owner', trust.incident_owner_team || '-'],
      ['Severity', trust.incident_severity || '-'],
    ];

    article.innerHTML = `
      <div class="compare-trust-header">
        <div>
          <h4>${escapeHtml(sideLabel)} trust</h4>
          <div class="compare-trust-meta">${escapeHtml(side.run_id || '-')}</div>
        </div>
        <button type="button" data-compare-open-run="${escapeHtml(sideKey)}">Open run</button>
      </div>
      <div class="compare-trust-summary">
        ${summaryItems.map(([label, value]) => `
          <div>
            <div class="label">${escapeHtml(label)}</div>
            <div class="value">${escapeHtml(String(value))}</div>
          </div>
        `).join('')}
      </div>
      <div class="compare-trust-controls">
        <button type="button" data-compare-focus="${escapeHtml(sideKey)}">Focus ${escapeHtml(sideLabel.toLowerCase())}</button>
      </div>
    `;

    const actionsNode = document.createElement('div');
    actionsNode.className = 'compare-trust-actions';
    if (actions.length) {
      actions.forEach((item) => {
        const actionText = Array.isArray(item?.actions) && item.actions.length
          ? item.actions.join('; ')
          : '-';
        const actionCard = document.createElement('article');
        actionCard.className = 'trust-action';
        actionCard.innerHTML = `
          <div class="title">${escapeHtml(item?.title || item?.code || 'Operator action')}</div>
          <div class="meta">${escapeHtml(item?.code || '-')}</div>
          <div class="body">${escapeHtml(actionText)}</div>
        `;
        actionsNode.appendChild(actionCard);
      });
    } else {
      const empty = document.createElement('div');
      empty.className = 'compare-trust-empty';
      empty.textContent = message || 'No trust packet actions available for this run.';
      actionsNode.appendChild(empty);
    }
    article.appendChild(actionsNode);
    panel.appendChild(article);
  });

  panel.classList.remove('hidden');
  emptyNode.classList.add('hidden');
}

function setCompareTrustFocus(sideKey, { scroll = false } = {}) {
  if (!['left', 'right'].includes(String(sideKey || ''))) return;
  state.compareTrustFocus = sideKey;
  renderCompareTrustDrilldown(state.comparePayload);
  if (scroll) {
    const panel = el('compareTrustPanel');
    if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

async function openCompareRun(sideKey) {
  const runId = state.comparePayload?.[sideKey]?.run_id;
  if (!runId) return;
  await selectRun(runId);
  activateTab('overview');
}

function renderComparison(payload, { source = state.compareSource, error = '' } = {}) {
  const grid = el('compareGrid');
  const diffs = el('compareDiffs');
  const badge = el('compareSourceBadge');
  const errorNode = el('compareError');

  state.comparePayload = payload;
  state.compareSource = source || '';

  if (badge) {
    badge.textContent = state.compareSource
      ? `Source: ${state.compareSource === 'api' ? 'SHW-012 API' : 'Adapter fallback'}`
      : '';
  }

  if (!payload) {
    state.compareTrustFocus = '';
    if (grid) {
      grid.classList.add('hidden');
      grid.innerHTML = '';
    }
    if (diffs) diffs.innerHTML = '<div class="empty">Select two runs to compare.</div>';
    renderCompareTrustDrilldown(null);
    if (errorNode) {
      errorNode.classList.toggle('hidden', !error);
      errorNode.textContent = error;
    }
    return;
  }

  if (errorNode) {
    errorNode.classList.add('hidden');
    errorNode.textContent = '';
  }

  const left = payload.left || {};
  const right = payload.right || {};
  const delta = payload.delta || {};

  const rows = [
    ['Status', left.status || 'unknown', right.status || 'unknown', Boolean(delta.status_changed || (left.status !== right.status))],
    ['Trust status', left.trust?.status || '-', right.trust?.status || '-', Boolean(delta.trust_status_changed || ((left.trust?.status || '') !== (right.trust?.status || '')))],
    ['Trust score', formatComparisonTrustScore(left.trust?.score), formatComparisonTrustScore(right.trust?.score), Math.abs(Number(delta.trust_score ?? 0)) > 0.001],
    ['Human review', formatComparisonReviewState(left.trust?.requires_human_review), formatComparisonReviewState(right.trust?.requires_human_review), Boolean(delta.trust_review_changed || (left.trust?.requires_human_review !== right.trust?.requires_human_review))],
    ['Quality signal', left.trust?.latest_quality_status || '-', right.trust?.latest_quality_status || '-', Boolean(delta.trust_quality_status_changed || ((left.trust?.latest_quality_status || '') !== (right.trust?.latest_quality_status || '')))],
    ['Incident owner', left.trust?.incident_owner_team || '-', right.trust?.incident_owner_team || '-', Boolean(delta.trust_owner_changed || ((left.trust?.incident_owner_team || '') !== (right.trust?.incident_owner_team || '')))],
    ['Incident severity', left.trust?.incident_severity || '-', right.trust?.incident_severity || '-', Boolean(delta.trust_severity_changed || ((left.trust?.incident_severity || '') !== (right.trust?.incident_severity || '')))],
    ['Profile', left.profile || '-', right.profile || '-', (left.profile || '') !== (right.profile || '')],
    ['Model', left.model || '-', right.model || '-', (left.model || '') !== (right.model || '')],
    ['Task attempts', left.totals?.total_task_attempts ?? 0, right.totals?.total_task_attempts ?? 0, (delta.total_task_attempts || 0) !== 0],
    ['Hard failures', left.totals?.hard_failures ?? 0, right.totals?.hard_failures ?? 0, (delta.hard_failures || 0) !== 0],
    ['Soft failures', left.totals?.soft_failures ?? 0, right.totals?.soft_failures ?? 0, (delta.soft_failures || 0) !== 0],
    ['Failed validators', left.validation?.failed ?? 0, right.validation?.failed ?? 0, (delta.validation_failed || 0) !== 0],
    ['Passed validators', left.validation?.passed ?? 0, right.validation?.passed ?? 0, (delta.validation_passed || 0) !== 0],
    ['Blocker count', left.totals?.blocker_count ?? left.blockers?.length ?? 0, right.totals?.blocker_count ?? right.blockers?.length ?? 0, (delta.blocker_count || 0) !== 0],
  ];

  if (!grid) return;

  grid.innerHTML = `
    <article class="compare-col">
      <h4>${escapeHtml(left.run_id || state.compareLeftId || '-')}</h4>
      <div class="muted">Baseline</div>
    </article>
    <article class="compare-col">
      <h4>${escapeHtml(right.run_id || state.compareRightId || '-')}</h4>
      <div class="muted">Candidate</div>
    </article>
  `;

  rows.forEach(([label, lv, rv, changed]) => {
    const row = document.createElement('div');
    row.className = `compare-row ${changed ? 'is-diff' : ''}`;
    row.innerHTML = `
      <div class="compare-label">${escapeHtml(String(label))}</div>
      <div class="compare-left">${escapeHtml(String(lv))}</div>
      <div class="compare-right">${escapeHtml(String(rv))}</div>
    `;
    grid.appendChild(row);
  });

  grid.classList.remove('hidden');

  const blockerLeft = Array.isArray(delta.blockers_only_left)
    ? delta.blockers_only_left
    : (left.blockers || []).filter((b) => !(right.blockers || []).includes(b));
  const blockerRight = Array.isArray(delta.blockers_only_right)
    ? delta.blockers_only_right
    : (right.blockers || []).filter((b) => !(left.blockers || []).includes(b));
  const valChanged = Array.isArray(delta.validation_changed) ? delta.validation_changed : [];

  const diffItems = [];
  if ((left.status || 'unknown') !== (right.status || 'unknown')) {
    diffItems.push(`<li><strong>Status:</strong> ${escapeHtml(left.status || 'unknown')} → ${escapeHtml(right.status || 'unknown')}</li>`);
  }
  if (Boolean(delta.trust_status_changed || ((left.trust?.status || '') !== (right.trust?.status || '')))) {
    diffItems.push(`<li><strong>Trust:</strong> ${escapeHtml(left.trust?.status || 'unknown')} (${escapeHtml(formatComparisonTrustScore(left.trust?.score))}) → ${escapeHtml(right.trust?.status || 'unknown')} (${escapeHtml(formatComparisonTrustScore(right.trust?.score))}) ${buildCompareTrustInspectButtons()}</li>`);
  } else if (Math.abs(Number(delta.trust_score ?? 0)) > 0.001) {
    diffItems.push(`<li><strong>Trust score:</strong> ${escapeHtml(formatComparisonTrustScore(left.trust?.score))} → ${escapeHtml(formatComparisonTrustScore(right.trust?.score))} ${buildCompareTrustInspectButtons()}</li>`);
  }
  if (Boolean(delta.trust_review_changed || (left.trust?.requires_human_review !== right.trust?.requires_human_review))) {
    diffItems.push(`<li><strong>Human review:</strong> ${escapeHtml(formatComparisonReviewState(left.trust?.requires_human_review))} → ${escapeHtml(formatComparisonReviewState(right.trust?.requires_human_review))} ${buildCompareTrustInspectButtons()}</li>`);
  }
  if (Boolean(delta.trust_owner_changed || ((left.trust?.incident_owner_team || '') !== (right.trust?.incident_owner_team || '')))) {
    diffItems.push(`<li><strong>Incident owner:</strong> ${escapeHtml(left.trust?.incident_owner_team || '-')} → ${escapeHtml(right.trust?.incident_owner_team || '-')} ${buildCompareTrustInspectButtons()}</li>`);
  }
  if (blockerLeft.length || blockerRight.length) {
    diffItems.push(`<li><strong>Blockers:</strong> only baseline [${escapeHtml(blockerLeft.join(', ') || 'none')}], only candidate [${escapeHtml(blockerRight.join(', ') || 'none')}]</li>`);
  }
  if (valChanged.length) {
    const top = valChanged.slice(0, 6).map((row) => `${row.name}: ${row.left} → ${row.right}`).join(', ');
    diffItems.push(`<li><strong>Validator outcome changes:</strong> ${escapeHtml(top)}${valChanged.length > 6 ? ` (+${valChanged.length - 6} more)` : ''}</li>`);
  }

  if (!diffs) return;
  if (!diffItems.length) {
    diffs.innerHTML = '<div class="empty">No major differences detected (status/blockers/key validators).</div>';
  } else {
    diffs.innerHTML = `<ul>${diffItems.join('')}</ul>`;
  }
  renderCompareTrustDrilldown(payload);
}

async function refreshComparison({ silent = false } = {}) {
  if (!state.compareLeftId || !state.compareRightId) {
    renderComparison(null, { error: 'Need at least two runs to compare.' });
    return;
  }

  if (state.compareLeftId === state.compareRightId) {
    renderComparison(null, { error: 'Choose two different runs for comparison.' });
    return;
  }

  if (state.useMock) {
    const leftBase = mock.details[state.compareLeftId] || {};
    const rightBase = mock.details[state.compareRightId] || {};
    const leftDetail = { ...leftBase, ...buildMockDetailTrust(leftBase) };
    const rightDetail = { ...rightBase, ...buildMockDetailTrust(rightBase) };
    renderComparison(buildComparisonFromDetails(leftDetail, rightDetail), { source: 'fallback' });
    return;
  }

  try {
    const query = `left=${encodeURIComponent(state.compareLeftId)}&right=${encodeURIComponent(state.compareRightId)}`;
    const payload = await fetchJson(`/api/runs/compare?${query}`);
    const enrichedPayload = await enrichComparisonPayload(payload);
    renderComparison(enrichedPayload, { source: 'api' });
  } catch (err) {
    try {
      const [leftDetail, rightDetail] = await Promise.all([
        fetchJson(`/api/runs/${encodeURIComponent(state.compareLeftId)}`),
        fetchJson(`/api/runs/${encodeURIComponent(state.compareRightId)}`),
      ]);
      renderComparison(buildComparisonFromDetails(leftDetail, rightDetail), { source: 'fallback' });
      if (!silent) {
        el('statusLine').textContent = `Comparison API unavailable; used fallback adapter (${err.message})`;
      }
    } catch (fallbackErr) {
      renderComparison(null, { error: `Comparison failed: ${fallbackErr.message}` });
      if (!silent) {
        el('statusLine').textContent = `Comparison failed: ${fallbackErr.message}`;
      }
    }
  }
}

function processRetrySummary(process) {
  if (!process || typeof process !== 'object') {
    return 'No retry-chain metadata.';
  }

  const root = String(process.retry_root || process.process_id || '').trim();
  const attempt = Number(process.retry_attempt || 1);
  const chain = state.processes.filter((row) => String(row.retry_root || row.process_id || '') === root);
  const maxAttempt = chain.reduce((acc, row) => Math.max(acc, Number(row.retry_attempt || 1)), 1);

  return `chain=${root || '-'} • attempt=${attempt}/${maxAttempt} • processes in chain=${chain.length || 1}`;
}

function syncProcessActionButtons(process) {
  const stopBtn = el('processStopBtn');
  const retryBtn = el('processRetryBtn');
  const actionType = String(state.processActionType || '').toLowerCase();
  const busy = Boolean(state.processActionInFlight);
  const hasProcess = Boolean(process?.process_id);

  if (stopBtn) {
    stopBtn.textContent = busy && actionType === 'stop' ? 'Stopping…' : 'Stop process';
    stopBtn.disabled = busy || !hasProcess || !isProcessActive(process?.state);
  }

  if (retryBtn) {
    retryBtn.textContent = busy && actionType === 'retry' ? 'Retrying…' : 'Retry process';
    retryBtn.disabled = busy || !hasProcess;
  }
}

function renderProcessList(processes) {
  const list = el('processList');
  const empty = el('processListEmpty');
  const error = el('processListError');
  if (!list || !empty || !error) return;

  list.innerHTML = '';
  error.textContent = state.processListError || '';
  error.classList.toggle('hidden', !state.processListError);

  if (state.processListError) {
    empty.classList.add('hidden');
    renderProcessTabState();
    return;
  }

  if (!Array.isArray(processes) || !processes.length) {
    empty.classList.remove('hidden');
    renderProcessTabState();
    return;
  }

  empty.classList.add('hidden');

  processes.forEach((process) => {
    const id = String(process.process_id || '-');
    const runId = String(process.run_link?.run_id || '-');
    const item = document.createElement('button');
    item.className = `list-item process-item ${state.selectedProcessId === id ? 'is-active' : ''}`;
    item.innerHTML = `
      <div class="title">${escapeHtml(id)}</div>
      <div class="meta">state=${escapeHtml(process.state || 'unknown')} • action=${escapeHtml(process.action || '-')}</div>
      <div class="meta">run=${escapeHtml(runId)} • attempt=${Number(process.retry_attempt || 1)}</div>
    `;
    item.addEventListener('click', async () => {
      await selectProcess(id);
      const filtered = filterProcesses(state.processes);
      const pageMeta = buildProcessPage(filtered);
      renderProcessList(pageMeta.pageRows);
      renderProcessPagination(pageMeta);
    });
    list.appendChild(item);
  });

  renderProcessTabState();
}

async function refreshProcessPanel({ syncSelection = true, statusMessage = '' } = {}) {
  const filtered = filterProcesses(state.processes);
  const pageMeta = buildProcessPage(filtered);
  renderProcessList(pageMeta.pageRows);
  renderProcessPagination(pageMeta);

  if (!syncSelection) {
    setProcessStatus(statusMessage || `${pageMeta.total} match(es) • page ${pageMeta.currentPage}/${pageMeta.totalPages}`);
    return pageMeta;
  }

  const allRows = Array.isArray(state.processes) ? state.processes : [];
  let nextStatus = String(statusMessage || '').trim();

  if (!filtered.length) {
    state.selectedProcessId = null;
    state.selectedProcessDetail = null;
    state.selectedProcessHistory = [];
    renderProcessDetail(null, []);
    syncProcessActionButtons(null);
    if (!nextStatus) {
      nextStatus = allRows.length
        ? 'No processes match current filters. Adjust filters and refresh.'
        : 'No tracked processes yet. Start or retry a run to populate this panel.';
    }
    setProcessStatus(nextStatus);
    return pageMeta;
  }

  const selectedId = state.selectedProcessId;
  const hasSelectedInAll = allRows.some((row) => row.process_id === selectedId);
  const hasSelectedInFiltered = filtered.some((row) => row.process_id === selectedId);

  if (!hasSelectedInAll || !selectedId) {
    state.selectedProcessId = pageMeta.pageRows[0]?.process_id || filtered[0]?.process_id || null;
    if (!nextStatus && selectedId) {
      nextStatus = `Selected process ${selectedId} is no longer available. Showing ${state.selectedProcessId || 'latest'} instead.`;
    }
  } else if (!hasSelectedInFiltered && !nextStatus) {
    nextStatus = `Selected process ${selectedId} is hidden by current filters.`;
  }

  if (state.selectedProcessId) {
    const sameDetail = state.selectedProcessDetail?.process_id === state.selectedProcessId;
    if (!sameDetail) {
      await selectProcess(state.selectedProcessId);
      const refreshedMeta = buildProcessPage(filterProcesses(state.processes));
      renderProcessList(refreshedMeta.pageRows);
      renderProcessPagination(refreshedMeta);
    } else {
      renderProcessDetail(state.selectedProcessDetail, state.selectedProcessHistory);
    }
  }

  setProcessStatus(nextStatus || `${pageMeta.total} match(es) • page ${pageMeta.currentPage}/${pageMeta.totalPages}`);

  return pageMeta;
}

function renderProcessDetail(process, history) {
  const card = el('processDetailCard');
  const empty = el('processDetailEmpty');
  const error = el('processDetailError');
  const title = el('processDetailTitle');
  const meta = el('processMeta');
  const summary = el('processRetrySummary');
  const historyNode = el('processHistory');
  const historyEmpty = el('processHistoryEmpty');
  const selectRunBtn = el('processSelectRunBtn');

  if (!card || !empty || !error || !title || !meta || !summary || !historyNode || !historyEmpty) return;

  error.classList.add('hidden');
  error.textContent = '';

  if (!process) {
    card.classList.add('hidden');
    empty.classList.remove('hidden');
    syncProcessActionButtons(null);
    setProcessStaleIndicator(null);
    return;
  }

  card.classList.remove('hidden');
  empty.classList.add('hidden');

  title.textContent = `Process: ${process.process_id || '-'}`;
  setProcessStateChip(process.state || 'unknown');

  const runLink = process.run_link || {};
  const rows = [
    ['Process ID', process.process_id || '-'],
    ['State', process.state || 'unknown'],
    ['Action', process.action || '-'],
    ['PID', process.pid ?? '-'],
    ['Started', formatTime(process.started_at)],
    ['Run ID', runLink.run_id || '-'],
    ['Run Out', runLink.out || '-'],
    ['Return code', process.returncode ?? '-'],
    ['Stop reason', process.stop_reason || '-'],
    ['Retry of', process.retry_of || '-'],
    ['Retry root', process.retry_root || '-'],
    ['Retry attempt', process.retry_attempt ?? '-'],
  ];

  meta.innerHTML = '';
  rows.forEach(([label, value]) => {
    const item = document.createElement('div');
    item.className = 'meta-item';
    item.innerHTML = `<div class="meta-label">${escapeHtml(label)}</div><div class="meta-value">${escapeHtml(String(value))}</div>`;
    meta.appendChild(item);
  });

  summary.textContent = processRetrySummary(process);

  syncProcessActionButtons(process);
  if (selectRunBtn) {
    selectRunBtn.disabled = !runLink.run_id;
  }

  historyNode.innerHTML = '';
  const rowsHistory = Array.isArray(history) ? history : [];
  if (!rowsHistory.length) {
    historyEmpty.classList.remove('hidden');
  } else {
    historyEmpty.classList.add('hidden');
    rowsHistory.forEach((entry) => {
      const row = document.createElement('div');
      row.className = 'timeline-row';
      const detail = entry?.detail && typeof entry.detail === 'object'
        ? Object.entries(entry.detail).map(([k, v]) => `${k}=${v}`).join(', ')
        : '';
      row.innerHTML = `
        <span class="timeline-dot"></span>
        <div>
          <div><strong>${escapeHtml(String(entry.state || 'unknown'))}</strong> • ${escapeHtml(formatTime(entry.at))}</div>
          ${detail ? `<div class="muted">${escapeHtml(detail)}</div>` : ''}
        </div>
      `;
      historyNode.appendChild(row);
    });
  }

  setProcessStaleIndicator(process, rowsHistory);
}

async function selectProcess(processId) {
  state.selectedProcessId = processId || null;
  if (!state.selectedProcessId) {
    state.selectedProcessDetail = null;
    state.selectedProcessHistory = [];
    renderProcessDetail(null, []);
    syncProcessActionButtons(null);
    return;
  }

  try {
    let detail;
    let historyPayload;
    if (state.useMock) {
      detail = state.processes.find((row) => row.process_id === state.selectedProcessId) || null;
      historyPayload = { history: detail?.transitions || [] };
    } else {
      [detail, historyPayload] = await Promise.all([
        fetchJson(`/api/processes/${encodeURIComponent(state.selectedProcessId)}`),
        fetchJson(`/api/processes/${encodeURIComponent(state.selectedProcessId)}/history`),
      ]);
    }

    state.selectedProcessDetail = detail;
    state.selectedProcessHistory = Array.isArray(historyPayload?.history) ? historyPayload.history : [];
    renderProcessDetail(state.selectedProcessDetail, state.selectedProcessHistory);
  } catch (err) {
    state.selectedProcessDetail = null;
    state.selectedProcessHistory = [];
    renderProcessDetail(null, []);
    syncProcessActionButtons(null);

    const msg = String(err?.message || 'unknown error');
    if (msg.includes('-> 404')) {
      const staleId = state.selectedProcessId;
      state.selectedProcessId = null;
      setProcessStatus(`Selected process ${staleId} is no longer available. Refreshing list...`);
      await refreshProcessPanel({ syncSelection: true });
      return;
    }

    const node = el('processDetailError');
    if (node) {
      node.textContent = `Failed to load process detail: ${msg}`;
      node.classList.remove('hidden');
    }
  }
}

async function loadProcesses({ silent = false, source = 'manual' } = {}) {
  if (source === 'poll' && state.processLoadInFlight) {
    return;
  }

  const requestSeq = Number(state.processLoadRequestSeq || 0) + 1;
  state.processLoadRequestSeq = requestSeq;
  state.processLoadInFlight = true;
  renderProcessTabState();

  if (!silent) {
    setProcessStatus('Refreshing process list...');
  }

  try {
    let payload;
    if (state.useMock) {
      payload = { processes: mock.processes, count: mock.processes.length };
    } else {
      const params = new URLSearchParams({ limit: '500' });
      payload = await fetchJson(`/api/processes?${params.toString()}`);
    }

    if (requestSeq !== state.processLoadRequestSeq) {
      return;
    }

    state.processListError = '';
    state.processes = Array.isArray(payload?.processes) ? payload.processes : [];
    noteProcessPollingSnapshot(state.processes, { source });
    await refreshProcessPanel();
  } catch (err) {
    if (requestSeq !== state.processLoadRequestSeq) {
      return;
    }

    state.processes = [];
    state.processListError = `Failed to load processes: ${err.message}`;
    const pageMeta = buildProcessPage([]);
    renderProcessList([]);
    renderProcessPagination(pageMeta);
    renderProcessDetail(null, []);
    syncProcessActionButtons(null);
    state.processNextPollAtMs = Date.now() + Math.max(1000, Number(state.pollIntervalMs || 8000));
    renderProcessPollingHint();
    if (!silent) {
      setProcessStatus(state.processListError, { error: true });
    }
  } finally {
    if (requestSeq === state.processLoadRequestSeq) {
      state.processLoadInFlight = false;
    }
    renderProcessTabState();
  }
}

async function runProcessAction(action) {
  const normalizedAction = String(action || '').toLowerCase();
  if (!['stop', 'retry'].includes(normalizedAction)) {
    return;
  }

  if (state.processActionInFlight) {
    setProcessStatus(`Another process action (${state.processActionType || 'request'}) is already in flight.`);
    return;
  }

  const detail = state.selectedProcessDetail;
  if (!detail?.process_id) {
    setProcessStatus('Select a process before running stop/retry.');
    return;
  }

  state.processActionInFlight = true;
  state.processActionType = normalizedAction;
  syncProcessActionButtons(detail);
  setProcessStatus(`${normalizedAction.toUpperCase()} request in progress for ${detail.process_id}...`);

  try {
    if (normalizedAction === 'stop') {
      const timeoutRaw = Number(el('controlGracefulTimeout')?.value || 2.0);
      const gracefulTimeoutSec = Number.isFinite(timeoutRaw) ? timeoutRaw : 2.0;
      await postJson('/api/runs/stop', {
        process_id: detail.process_id,
        graceful_timeout_sec: gracefulTimeoutSec,
      });
    } else {
      const retryBody = await postJson('/api/runs/retry', {
        process_id: detail.process_id,
        execute: true,
      });
      if (retryBody?.process?.process_id) {
        updateProcessIdInput(retryBody.process.process_id);
        state.selectedProcessId = retryBody.process.process_id;
      }
    }

    resetProcessPollingBackoff();
    await Promise.all([loadRuns(), loadProcesses({ silent: true, source: 'action' })]);
    setRunControlStatus(`${normalizedAction.toUpperCase()} OK • process=${detail.process_id}`);
    setProcessStatus(`${normalizedAction.toUpperCase()} completed for ${detail.process_id}.`);
  } catch (err) {
    const msg = `${normalizedAction.toUpperCase()} failed: ${err.message}`;
    setRunControlStatus(msg, { error: true });
    setProcessStatus(msg, { error: true });
  } finally {
    state.processActionInFlight = false;
    state.processActionType = '';
    syncProcessActionButtons(state.selectedProcessDetail);
  }
}

function initProcessControls() {
  const stateFilter = el('processStateFilter');
  const runIdFilter = el('processRunIdFilter');
  const clearBtn = el('processClearFiltersBtn');
  const refreshBtn = el('processRefreshBtn');
  const stopBtn = el('processStopBtn');
  const retryBtn = el('processRetryBtn');
  const openRunBtn = el('processSelectRunBtn');
  const pageSizeSelect = el('processPageSizeSelect');
  const firstBtn = el('processPageFirstBtn');
  const prevBtn = el('processPagePrevBtn');
  const nextBtn = el('processPageNextBtn');
  const lastBtn = el('processPageLastBtn');

  if (
    !stateFilter || !runIdFilter || !clearBtn || !refreshBtn || !stopBtn || !retryBtn || !openRunBtn
    || !pageSizeSelect || !firstBtn || !prevBtn || !nextBtn || !lastBtn
  ) {
    return;
  }

  stateFilter.value = state.processFilterState;
  runIdFilter.value = state.processFilterRunId;
  pageSizeSelect.value = String(normalizeProcessPageSize(state.processPageSize));

  stateFilter.addEventListener('change', async () => {
    state.processFilterState = stateFilter.value;
    state.processPage = 1;
    await refreshProcessPanel();
  });

  runIdFilter.addEventListener('input', async () => {
    state.processFilterRunId = runIdFilter.value || '';
    state.processPage = 1;
    await refreshProcessPanel();
  });

  clearBtn.addEventListener('click', async () => {
    state.processFilterState = 'all';
    state.processFilterRunId = '';
    state.processPage = 1;
    stateFilter.value = 'all';
    runIdFilter.value = '';
    await refreshProcessPanel({ statusMessage: 'Filters cleared' });
  });

  pageSizeSelect.addEventListener('change', async () => {
    state.processPageSize = normalizeProcessPageSize(pageSizeSelect.value);
    pageSizeSelect.value = String(state.processPageSize);
    state.processPage = 1;
    await refreshProcessPanel();
  });

  firstBtn.addEventListener('click', async () => {
    state.processPage = 1;
    await refreshProcessPanel({ syncSelection: false });
  });
  prevBtn.addEventListener('click', async () => {
    state.processPage = Math.max(1, Number(state.processPage || 1) - 1);
    await refreshProcessPanel({ syncSelection: false });
  });
  nextBtn.addEventListener('click', async () => {
    state.processPage = Number(state.processPage || 1) + 1;
    await refreshProcessPanel({ syncSelection: false });
  });
  lastBtn.addEventListener('click', async () => {
    const total = filterProcesses(state.processes).length;
    const size = normalizeProcessPageSize(state.processPageSize);
    state.processPage = Math.max(1, Math.ceil(total / size));
    await refreshProcessPanel({ syncSelection: false });
  });

  refreshBtn.addEventListener('click', async () => {
    resetProcessPollingBackoff();
    await loadProcesses({ source: 'manual' });
  });

  stopBtn.addEventListener('click', async () => {
    await runProcessAction('stop');
  });

  retryBtn.addEventListener('click', async () => {
    await runProcessAction('retry');
  });

  openRunBtn.addEventListener('click', async () => {
    const runId = state.selectedProcessDetail?.run_link?.run_id;
    if (!runId) return;
    await selectRun(runId);
    activateTab('overview');
  });

  syncProcessActionButtons(state.selectedProcessDetail);
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function clearPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function setupPolling() {
  clearPolling();
  if (!state.liveUpdateEnabled || state.useMock) {
    renderProcessPollingHint();
    return;
  }

  state.pollTimer = setInterval(async () => {
    await refreshCurrentRun({ silent: true });
    if (state.selectedProcessDetail) {
      setProcessStaleIndicator(state.selectedProcessDetail, state.selectedProcessHistory);
    }

    const activeTab = document.querySelector('.tab[data-tab="processes"].is-active');
    const selectedState = state.selectedProcessDetail?.state || '';
    if (!(activeTab || isProcessActive(selectedState))) {
      return;
    }

    const now = Date.now();
    if (now < Number(state.processNextPollAtMs || 0)) {
      return;
    }

    await loadProcesses({ silent: true, source: 'poll' });
  }, state.pollIntervalMs);

  renderProcessPollingHint();
}

async function refreshCurrentRun({ silent = false } = {}) {
  if (!state.selectedRunId) return;
  try {
    const detail = await fetchJson(`/api/runs/${encodeURIComponent(state.selectedRunId)}`);
    renderDetail(detail);
    if (state.selectedRunId === state.compareLeftId || state.selectedRunId === state.compareRightId) {
      await refreshComparison({ silent: true });
    }
    if (!silent) {
      el('statusLine').textContent = `${state.runs.length} run(s) • updated ${new Date().toLocaleTimeString()}`;
    }
  } catch (err) {
    if (!silent) el('statusLine').textContent = `Live update failed: ${err.message}`;
  }
}

async function loadRuns() {
  state.useMock = new URLSearchParams(window.location.search).get('mock') === '1';
  state.runsLoading = true;
  state.runsError = '';
  renderOverviewState();
  await loadGuiContext();

  if (state.useMock) {
    state.runs = mock.runs;
    state.selectedRunId = mock.runs[0].run_id;
    state.selectedRun = mock.runs[0];
    renderRuns(state.runs);
    const mockDetail = mock.details[state.selectedRunId] || { run_id: state.selectedRunId, status: 'unknown' };
    renderDetail({ ...mockDetail, ...buildMockDetailTrust(mockDetail) });
    initComparisonState({ forceLatest: !state.compareManualSelection });
    refreshCompareRunOptions();
    await refreshComparison({ silent: true });
    await refreshTrends({ silent: true });
    await refreshScorecardWidget({ silent: true });
    await refreshTrustWidget({ silent: true });
    await loadProcesses({ silent: true });
    await refreshHealthBanner();
    state.runsLoading = false;
    state.runsError = '';
    renderOverviewState();
    el('statusLine').textContent = 'Mock mode enabled (?mock=1).';
    setupPolling();
    return;
  }

  try {
    const payload = await fetchJson('/api/runs');
    state.runs = payload.runs || [];
    state.selectedRunId = state.selectedRunId || state.runs[0]?.run_id || null;
    state.selectedRun = state.runs.find((r) => r.run_id === state.selectedRunId) || null;
    renderRuns(state.runs);
    el('statusLine').textContent = `${state.runs.length} run(s)`;

    if (state.selectedRunId) {
      await selectRun(state.selectedRunId, { rerenderList: false });
    } else {
      renderDetail({ run_id: '-', status: 'unknown', phase_timeline: [], tasks: [], blockers: [] });
    }

    initComparisonState({ forceLatest: !state.compareManualSelection });
    refreshCompareRunOptions();
    await refreshComparison({ silent: true });
    await refreshTrends({ silent: true });
    await refreshScorecardWidget({ silent: true });
    await refreshTrustWidget({ silent: true });
    await loadProcesses({ silent: true });
    await refreshHealthBanner();
    state.runsLoading = false;
    state.runsError = '';
    renderOverviewState();
    setupPolling();
  } catch (err) {
    el('statusLine').textContent = `Failed to load runs. Try ?mock=1 (${err.message})`;
    state.runs = [];
    state.runsLoading = false;
    state.runsError = String(err?.message || 'request failed');
    renderRuns(state.runs);
    renderOverviewState();
    refreshCompareRunOptions();
    renderComparison(null, { error: 'Unable to load runs for comparison.' });
    renderTrends(null);
    state.scorecardPayload = null;
    state.scorecardError = 'Latest scorecard unavailable: unable to load runs list.';
    state.scorecardLoading = false;
    renderScorecardWidget();
    state.trustPayload = null;
    state.trustError = 'Latest trust intelligence unavailable: unable to load runs list.';
    state.trustLoading = false;
    renderTrustWidget();
    await loadProcesses({ silent: true });
    await refreshHealthBanner();
    setupPolling();
  }
}

function renderDetail(detail) {
  state.detail = detail;
  state.triageContext = null;
  state.focusedTaskId = null;
  clearArtifactViewerState();
  el('runTitle').textContent = `Run Detail: ${detail.run_id || '-'}`;
  setStatusChip(detail.status);
  renderMetaGrid(detail);
  renderTimeline(detail.phase_timeline || []);
  renderTasks(detail.tasks || []);
  renderBlockers(detail.blockers || []);
  renderValidationPanels(detail);
  renderArtifactViewer();
  renderTrustWidget();
  renderOverviewState();
  renderProcessTabState();
  renderHealthBanner({
    ...(state.healthSnapshot || {}),
    model: firstNonEmpty([detail?.model, state.healthSnapshot?.model]),
    context_ok: Boolean(state.guiContext),
    mode: state.guiContext?.mode || 'unknown',
  });
}

async function selectRun(runId, options = { rerenderList: true }) {
  state.selectedRunId = runId;
  state.selectedRun = state.runs.find((run) => run.run_id === runId) || null;
  state.detailLoading = true;
  state.detailError = '';
  renderOverviewState();
  renderValidationPanels(state.detail || {});
  if (state.selectedRun?.run_dir) {
    const outInput = el('controlOut');
    if (outInput) outInput.value = state.selectedRun.run_dir;
    updateQuickRunHint();
  }
  if (options.rerenderList) renderRuns(state.runs);

  try {
    const detail = state.useMock
      ? {
          ...(mock.details[runId] || { run_id: runId, status: 'unknown', phase_timeline: [], tasks: [], blockers: [] }),
          ...buildMockDetailTrust(mock.details[runId] || { run_id: runId, status: 'unknown' }),
        }
      : await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    state.detailLoading = false;
    state.detailError = '';
    renderDetail(detail);
  } catch (err) {
    state.detailLoading = false;
    state.detailError = String(err?.message || 'request failed');
    renderDetail({ run_id: runId, status: 'unknown', phase_timeline: [], tasks: [], blockers: [] });
    el('statusLine').textContent = `Failed to load detail for ${runId}: ${err.message}`;
  }
}

function initTabs() {
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tab = btn.dataset.tab;
      activateTab(tab);
      if (tab === 'processes') {
        await loadProcesses({ silent: true });
      }
      if (tab === 'experiment-log') {
        await loadExperimentLog();
      }
    });
  });
}

function initValidationControls() {
  const search = el('validationSearch');
  const status = el('validationStatusFilter');
  const name = el('validationNameFilter');
  const sort = el('validationSort');
  const failedFirst = el('failedFirstToggle');

  sort.value = state.validationSort;
  failedFirst.checked = state.failedFirst;

  search.addEventListener('input', () => {
    state.validationSearch = search.value || '';
    if (state.detail) renderValidationPanels(state.detail);
  });

  status.addEventListener('change', () => {
    state.validationStatus = status.value;
    if (state.detail) renderValidationPanels(state.detail);
  });

  name.addEventListener('change', () => {
    state.validationName = name.value;
    if (state.detail) renderValidationPanels(state.detail);
  });

  sort.addEventListener('change', () => {
    state.validationSort = sort.value;
    if (state.detail) renderValidationPanels(state.detail);
  });

  failedFirst.addEventListener('change', () => {
    state.failedFirst = failedFirst.checked;
    if (state.detail) renderValidationPanels(state.detail);
  });
}

function initTabRecoveryActions() {
  const overviewRefreshBtn = el('overviewRefreshBtn');
  const overviewRetryBtn = el('overviewRetryDetailBtn');
  const overviewLogsBtn = el('overviewLogsHintBtn');
  const validationRefreshBtn = el('validationRefreshBtn');
  const validationLogsBtn = el('validationLogsHintBtn');
  const processRefreshBtn = el('processStateRefreshBtn');
  const processClearBtn = el('processStateClearBtn');
  const processLogsBtn = el('processStateLogsHintBtn');

  overviewRefreshBtn?.addEventListener('click', async () => {
    await loadRuns();
  });

  overviewRetryBtn?.addEventListener('click', async () => {
    if (state.selectedRunId) {
      await selectRun(state.selectedRunId, { rerenderList: false });
    } else {
      await loadRuns();
    }
  });

  overviewLogsBtn?.addEventListener('click', async () => {
    await openValidationLogsHint();
  });

  validationRefreshBtn?.addEventListener('click', async () => {
    if (state.selectedRunId) {
      await selectRun(state.selectedRunId, { rerenderList: false });
    } else {
      await loadRuns();
    }
  });

  validationLogsBtn?.addEventListener('click', async () => {
    await openValidationLogsHint({ autoOpen: true });
  });

  processRefreshBtn?.addEventListener('click', async () => {
    resetProcessPollingBackoff();
    await loadProcesses({ source: 'manual' });
  });

  processClearBtn?.addEventListener('click', async () => {
    state.processFilterState = 'all';
    state.processFilterRunId = '';
    state.processPage = 1;
    const stateFilter = el('processStateFilter');
    const runFilter = el('processRunIdFilter');
    if (stateFilter) stateFilter.value = 'all';
    if (runFilter) runFilter.value = '';
    await refreshProcessPanel({ statusMessage: 'Filters cleared' });
  });

  processLogsBtn?.addEventListener('click', async () => {
    setProcessStatus(loadLogsHint());
    await openValidationLogsHint();
  });
}

function initArtifactViewerControls() {
  const pathInput = el('artifactPathInput');
  const openBtn = el('artifactOpenBtn');
  const reloadBtn = el('artifactReloadBtn');
  const clearBtn = el('artifactClearBtn');
  const copyBtn = el('artifactCopyBtn');
  const downloadBtn = el('artifactDownloadBtn');
  const expandBtn = el('artifactExpandBtn');

  if (!pathInput || !openBtn || !reloadBtn || !clearBtn || !copyBtn || !downloadBtn || !expandBtn) return;

  pathInput.addEventListener('input', () => {
    state.artifactViewerPath = pathInput.value;
  });

  openBtn.addEventListener('click', async () => {
    await openArtifactInViewer(pathInput.value, { source: 'manual' });
  });

  pathInput.addEventListener('keydown', async (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    await openArtifactInViewer(pathInput.value, { source: 'manual' });
  });

  reloadBtn.addEventListener('click', async () => {
    if (!state.artifactViewerPath) return;
    await openArtifactInViewer(state.artifactViewerPath, {
      source: state.artifactViewerRequestedBy || 'reload',
      autoFocus: false,
    });
  });

  clearBtn.addEventListener('click', () => {
    clearArtifactViewerState();
    renderArtifactViewer();
  });

  copyBtn.addEventListener('click', async () => {
    const payload = state.artifactViewerPayload;
    const exportPayload = getArtifactViewerExportText(payload);
    if (!exportPayload.canExport) return;
    await withPreservedFocus(async () => {
      try {
        await copyTextToClipboard(exportPayload.text);
        announceArtifactViewerAction('Copied raw artifact payload to clipboard.', 'ok');
      } catch {
        announceArtifactViewerAction('Copy failed. Browser clipboard access was denied.', 'error');
      }
    });
  });

  downloadBtn.addEventListener('click', async () => {
    const payload = state.artifactViewerPayload;
    const exportPayload = getArtifactViewerExportText(payload);
    if (!exportPayload.canExport) return;

    await withPreservedFocus(async () => {
      const blobType = payload?.content_type === 'application/json' ? 'application/json;charset=utf-8' : 'text/plain;charset=utf-8';
      const blob = new Blob([exportPayload.text], { type: blobType });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = artifactViewerDownloadName(state.artifactViewerPath, payload);
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
      announceArtifactViewerAction(`Downloaded raw payload as ${link.download}.`, 'ok');
    });
  });

  expandBtn.addEventListener('click', () => {
    state.artifactViewerExpanded = !state.artifactViewerExpanded;
    renderArtifactViewer();
  });

  renderArtifactViewer();
}

async function loadGuiContext() {
  if (state.useMock) {
    state.guiContext = {
      mode: 'local_simple',
      local_simple_mode: true,
      defaults: {
        profile: 'local_simple',
        out: './generated_runs',
        config: './config.yaml',
        prd: './examples/PRD.md',
      },
    };
  } else {
    try {
      state.guiContext = await fetchJson('/api/gui/context');
    } catch {
      state.guiContext = null;
    }
  }

  const modeBadge = el('runModeBadge');
  const profileInput = el('controlProfile');
  const outInput = el('controlOut');
  const configInput = el('controlConfig');
  const prdInput = el('controlPrd');

  const defaults = state.guiContext?.defaults || {};
  if (profileInput && !profileInput.value) {
    profileInput.value = normalizeRunToken(defaults.profile) || 'enterprise';
  }
  if (outInput && !outInput.value) {
    outInput.value = defaults.out || './generated_runs';
  }
  if (configInput && !configInput.value && defaults.config) {
    configInput.value = defaults.config;
  }
  if (prdInput && !prdInput.value && defaults.prd) {
    prdInput.value = defaults.prd;
  }

  if (modeBadge) {
    const local = Boolean(state.guiContext?.local_simple_mode);
    modeBadge.textContent = local
      ? 'Mode: Local Simple (single-user localhost defaults)'
      : 'Mode: Hardened (explicit auth/policy expected)';
    modeBadge.classList.toggle('is-local', local);
  }

  updateQuickRunHint();
  await refreshHealthBanner();
}

async function runControlAction(action, payloadOverride = null) {
  const payload = payloadOverride || buildRunPayload(action);
  try {
    const body = await postJson(`/api/runs/${action}`, payload);
    const processId = body?.process?.process_id;
    if (processId) {
      updateProcessIdInput(processId);
    }

    const summary = [];
    if (body?.spawned === true) summary.push('spawned');
    if (body?.stopped === true) summary.push('stopped');
    if (body?.retry_of) summary.push(`retry_of=${body.retry_of}`);
    if (body?.run_link?.run_id) summary.push(`run_id=${body.run_link.run_id}`);
    if (processId) summary.push(`process=${processId}`);
    setRunControlStatus(`${action.toUpperCase()} OK${summary.length ? ` • ${summary.join(' • ')}` : ''}`);
    setRunControlHints([]);

    resetProcessPollingBackoff();
    await Promise.all([loadRuns(), loadProcesses({ silent: true, source: 'action' })]);
    if (state.selectedRunId) {
      await refreshCurrentRun({ silent: true });
    }
  } catch (err) {
    setRunControlStatus(`${action.toUpperCase()} failed: ${err.message}`, { error: true });
    const hints = deriveRunControlHints(action, err?.payload || null);
    setRunControlHints(hints);
  }
}

async function runQuickPreset() {
  const defaults = state.guiContext?.defaults || {};
  const quickPrd = resolveQuickRunPrdPath();
  if (!quickPrd) {
    setRunControlStatus('QUICK RUN failed: PRD path is empty. Set PRD file path first.', { error: true });
    updateQuickRunHint();
    return;
  }

  const profileInput = el('controlProfile');
  const outInput = el('controlOut');
  const configInput = el('controlConfig');
  const prdInput = el('controlPrd');

  const quickProfile = normalizeRunToken(defaults.profile) || 'local_simple';
  const quickOut = normalizeLocalPath(outInput?.value) || normalizeLocalPath(defaults.out) || './generated_runs';
  const quickConfig = normalizeLocalPath(configInput?.value) || normalizeLocalPath(defaults.config);

  if (profileInput) profileInput.value = quickProfile;
  if (outInput) outInput.value = quickOut;
  if (prdInput) prdInput.value = quickPrd;

  updateQuickRunHint();

  const payload = buildRunPayload('start');
  payload.execute = true;
  payload.profile = quickProfile;
  payload.out = quickOut;
  payload.prd = quickPrd;
  if (quickConfig) {
    payload.config = quickConfig;
  } else {
    delete payload.config;
  }

  await runControlAction('start', payload);
}

function initRunControls() {
  const quickBtn = el('runQuickBtn');
  const startBtn = el('runStartBtn');
  const resumeBtn = el('runResumeBtn');
  const retryBtn = el('runRetryBtn');
  const stopBtn = el('runStopBtn');

  if (!quickBtn || !startBtn || !resumeBtn || !retryBtn || !stopBtn) return;

  quickBtn.addEventListener('click', async () => {
    await runQuickPreset();
  });

  startBtn.addEventListener('click', async () => {
    await runControlAction('start');
  });

  resumeBtn.addEventListener('click', async () => {
    await runControlAction('resume');
  });

  retryBtn.addEventListener('click', async () => {
    await runControlAction('retry');
  });

  stopBtn.addEventListener('click', async () => {
    await runControlAction('stop');
  });

  ['controlPrd', 'controlOut', 'controlProfile'].forEach((id) => {
    const input = el(id);
    if (!input) return;
    input.addEventListener('input', () => {
      updateQuickRunHint();
    });
  });
}

function renderTrends(payload) {
  state.trendPayload = payload;
  const summary = el('trendSummary');
  const panel = el('trendPanel');
  if (!summary || !panel) return;

  summary.classList.remove('error-text');

  if (!payload) {
    summary.textContent = 'No trend data yet.';
    panel.classList.add('hidden');
    panel.textContent = '';
    return;
  }

  const counters = payload.counters || {};
  summary.textContent = [
    `window=${payload.window?.applied || '-'}`,
    `included=${counters.runs_included ?? 0}`,
    `skipped=${counters.runs_skipped_missing_or_invalid_artifacts ?? 0}`,
    `failed_validators=${payload.aggregates?.validators?.totals?.failed ?? 0}`,
    `blockers=${payload.aggregates?.blockers?.total ?? 0}`,
  ].join(' • ');

  panel.classList.remove('hidden');
  panel.textContent = JSON.stringify(payload, null, 2);
}

async function refreshTrends({ silent = false } = {}) {
  if (state.useMock) {
    renderTrends({
      window: { requested: 20, applied: 20 },
      counters: { runs_included: 2, runs_skipped_missing_or_invalid_artifacts: 0 },
      aggregates: { validators: { totals: { failed: 1 } }, blockers: { total: 1 } },
    });
    return;
  }

  const windowVal = Number(el('trendWindow')?.value || 20);
  const boundedWindow = Math.max(1, Math.min(200, Number.isFinite(windowVal) ? windowVal : 20));
  const allowPartial = Boolean(el('trendPartialToggle')?.checked);

  try {
    const payload = await fetchJson(`/api/runs/trends?window=${boundedWindow}&partial=${allowPartial ? 'true' : 'false'}`);
    renderTrends(payload);
  } catch (err) {
    if (!silent) {
      renderTrends(null);
      el('trendSummary').textContent = `Trend query failed: ${err.message}`;
      el('trendSummary').classList.add('error-text');
    }
  }
}

function initTrendControls() {
  const refreshBtn = el('trendRefreshBtn');
  const partial = el('trendPartialToggle');
  const windowInput = el('trendWindow');

  if (!refreshBtn || !partial || !windowInput) return;

  refreshBtn.addEventListener('click', async () => {
    await refreshTrends();
  });

  partial.addEventListener('change', async () => {
    await refreshTrends({ silent: true });
  });

  windowInput.addEventListener('change', async () => {
    await refreshTrends({ silent: true });
  });
}

function initCompareControls() {
  const left = el('compareLeftRun');
  const right = el('compareRightRun');
  const swap = el('compareSwapBtn');
  const refresh = el('compareRefreshBtn');
  const diffs = el('compareDiffs');
  const trustPanel = el('compareTrustPanel');

  if (!left || !right || !swap || !refresh) return;

  left.addEventListener('change', async () => {
    state.compareManualSelection = true;
    state.compareLeftId = left.value;
    await refreshComparison();
  });

  right.addEventListener('change', async () => {
    state.compareManualSelection = true;
    state.compareRightId = right.value;
    await refreshComparison();
  });

  swap.addEventListener('click', async () => {
    state.compareManualSelection = true;
    const prevLeft = state.compareLeftId;
    state.compareLeftId = state.compareRightId;
    state.compareRightId = prevLeft;
    refreshCompareRunOptions();
    await refreshComparison();
  });

  refresh.addEventListener('click', async () => {
    await refreshComparison();
  });

  if (diffs) {
    diffs.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target.closest('[data-compare-trust-side]') : null;
      const side = target?.getAttribute('data-compare-trust-side') || '';
      if (!side) return;
      setCompareTrustFocus(side, { scroll: true });
    });
  }

  if (trustPanel) {
    trustPanel.addEventListener('click', async (event) => {
      const target = event.target instanceof Element ? event.target.closest('button') : null;
      if (!target) return;
      const focusSide = target.getAttribute('data-compare-focus') || '';
      if (focusSide) {
        setCompareTrustFocus(focusSide);
        return;
      }
      const openSide = target.getAttribute('data-compare-open-run') || '';
      if (openSide) {
        await openCompareRun(openSide);
      }
    });
  }
}

function initLiveUpdateControls() {
  const toggle = el('liveUpdateToggle');
  const interval = el('pollInterval');

  toggle.checked = state.liveUpdateEnabled;
  interval.value = String(state.pollIntervalMs);

  toggle.addEventListener('change', () => {
    state.liveUpdateEnabled = toggle.checked;
    if (!state.liveUpdateEnabled) {
      resetProcessPollingBackoff();
    }
    setupPolling();
    el('statusLine').textContent = state.liveUpdateEnabled
      ? `Live update enabled (${state.pollIntervalMs / 1000}s)`
      : 'Live update paused';
  });

  interval.addEventListener('change', () => {
    const ms = Number(interval.value) || 8000;
    state.pollIntervalMs = ms;
    resetProcessPollingBackoff();
    setupPolling();
    if (state.liveUpdateEnabled) {
      el('statusLine').textContent = `Live update interval: ${ms / 1000}s`;
    }
  });
}

// ---------------------------------------------------------------------------
// Experiment Log tab
// ---------------------------------------------------------------------------
async function loadExperimentLog(taskIdFilter) {
  const statusLine = el('expLogStatusLine');
  statusLine.textContent = 'Loading…';
  try {
    const params = new URLSearchParams();
    if (taskIdFilter) params.set('task_id', taskIdFilter);
    const res = await fetch(`/api/experiment-log?${params}`);
    const data = await res.json();
    renderExperimentLog(data);
    statusLine.textContent = `${data.entries?.length || 0} entries`;
  } catch (e) {
    statusLine.textContent = `Error: ${e.message}`;
  }
}

function renderExperimentLog(data) {
  const body = el('expLogBody');
  const empty = el('expLogEmpty');
  const tableWrap = el('expLogTableWrap');
  const summaryNode = el('expLogSummary');
  const filterSelect = el('expLogTaskFilter');

  const entries = data.entries || [];
  const summary = data.summary || {};

  // Populate task filter dropdown
  const tasks = Object.keys(summary.tasks || {});
  const currentFilter = filterSelect.value;
  filterSelect.innerHTML = '<option value="">All tasks</option>';
  tasks.forEach((tid) => {
    const opt = document.createElement('option');
    opt.value = tid;
    opt.textContent = tid;
    filterSelect.appendChild(opt);
  });
  filterSelect.value = currentFilter;

  // Render summary
  summaryNode.innerHTML = '';
  if (summary.entry_count > 0) {
    const pairs = [
      ['Total entries', summary.entry_count],
      ['Tasks', tasks.length],
    ];
    tasks.forEach((tid) => {
      const t = summary.tasks[tid];
      pairs.push([`${tid} best`, t.best_score?.toFixed(1) || '-']);
      pairs.push([`${tid} decisions`, `A:${t.decisions?.accepted || 0} R:${t.decisions?.reverted || 0} N:${t.decisions?.neutral || 0}`]);
    });
    pairs.forEach(([label, value]) => {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = String(value);
      summaryNode.appendChild(dt);
      summaryNode.appendChild(dd);
    });
  }

  if (entries.length === 0) {
    tableWrap.classList.add('hidden');
    empty.classList.remove('hidden');
    return;
  }
  tableWrap.classList.remove('hidden');
  empty.classList.add('hidden');

  body.innerHTML = '';
  entries.forEach((e) => {
    const tr = document.createElement('tr');
    const decision = e.decision?.decision || '';
    tr.className = decision === 'accepted' ? 'exp-accepted' : decision === 'reverted' ? 'exp-reverted' : 'exp-neutral';

    const cells = [
      e.task_id || '',
      e.iteration ?? '',
      e.attempt ?? '',
      e.composite_score?.toFixed(1) || '-',
      decision,
      e.decision?.reason_code || '',
      (e.decision?.score_delta != null ? (e.decision.score_delta > 0 ? '+' : '') + e.decision.score_delta.toFixed(1) : '-'),
      (e.decision?.hard_blockers || []).join(', ') || '-',
      e.wall_clock_ms != null ? (e.wall_clock_ms / 1000).toFixed(1) + 's' : '-',
    ];
    cells.forEach((text) => {
      const td = document.createElement('td');
      td.textContent = text;
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

function initExperimentLogControls() {
  el('expLogRefreshBtn').addEventListener('click', () => {
    loadExperimentLog(el('expLogTaskFilter').value || undefined);
  });
  el('expLogTaskFilter').addEventListener('change', () => {
    loadExperimentLog(el('expLogTaskFilter').value || undefined);
  });
}

initTabs();
initValidationControls();
initTabRecoveryActions();
initArtifactViewerControls();
initRunControls();
initProcessControls();
initCompareControls();
initTrendControls();
initLiveUpdateControls();
initExperimentLogControls();
renderScorecardWidget();
loadRuns();
