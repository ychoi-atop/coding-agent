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
  guiContext: null,
  lastProcessId: '',
  trendPayload: null,
};

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
  const rows = normalizeValidationRows(state.selectedDetail || {});
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
  const profile = normalizeLocalPath(defaults.profile) || 'local_simple';
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
    li.innerHTML = `<code>${escapeHtml(path)}</code>`;
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

function renderValidationPanels(detail) {
  const groupsRoot = el('validationCards');
  const validationEmpty = el('validationEmpty');
  const qualityPanel = el('qualityPanel');
  const qualityEmpty = el('qualityEmpty');

  const rows = normalizeValidationRows(detail);
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

function initComparisonState() {
  const ids = state.runs.map((run) => run.run_id).filter(Boolean);
  if (!ids.length) {
    state.compareLeftId = null;
    state.compareRightId = null;
    return;
  }

  if (!state.compareLeftId || !ids.includes(state.compareLeftId)) {
    state.compareLeftId = ids[0];
  }

  if (!state.compareRightId || !ids.includes(state.compareRightId)) {
    state.compareRightId = ids.find((id) => id !== state.compareLeftId) || ids[0];
  }
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

  left.value = state.compareLeftId || state.runs[0].run_id;
  right.value = state.compareRightId || state.runs[0].run_id;
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

  if (hasValidatorDiffs && hasBlockerSplits) {
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
      delta: {
        ...(payload?.delta || {}),
        status_changed: payload?.delta?.status_changed ?? fallback.delta.status_changed,
        blockers_only_left: payload?.delta?.blockers_only_left || fallback.delta.blockers_only_left,
        blockers_only_right: payload?.delta?.blockers_only_right || fallback.delta.blockers_only_right,
        validation_changed: payload?.delta?.validation_changed || fallback.delta.validation_changed,
      },
    };
  } catch {
    return payload;
  }
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
    if (grid) {
      grid.classList.add('hidden');
      grid.innerHTML = '';
    }
    if (diffs) diffs.innerHTML = '<div class="empty">Select two runs to compare.</div>';
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
}

async function refreshComparison({ silent = false } = {}) {
  if (!state.compareLeftId || !state.compareRightId) {
    renderComparison(null);
    return;
  }

  if (state.compareLeftId === state.compareRightId) {
    renderComparison(null, { error: 'Choose two different runs for comparison.' });
    return;
  }

  if (state.useMock) {
    const leftDetail = mock.details[state.compareLeftId] || {};
    const rightDetail = mock.details[state.compareRightId] || {};
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
  if (!state.liveUpdateEnabled || state.useMock) return;

  state.pollTimer = setInterval(async () => {
    await refreshCurrentRun({ silent: true });
  }, state.pollIntervalMs);
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
  await loadGuiContext();

  if (state.useMock) {
    state.runs = mock.runs;
    state.selectedRunId = mock.runs[0].run_id;
    state.selectedRun = mock.runs[0];
    renderRuns(state.runs);
    renderDetail(mock.details[state.selectedRunId] || { run_id: state.selectedRunId, status: 'unknown' });
    initComparisonState();
    refreshCompareRunOptions();
    await refreshComparison({ silent: true });
    await refreshTrends({ silent: true });
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

    initComparisonState();
    refreshCompareRunOptions();
    await refreshComparison({ silent: true });
    await refreshTrends({ silent: true });
    setupPolling();
  } catch (err) {
    el('statusLine').textContent = `Failed to load runs. Try ?mock=1 (${err.message})`;
    state.runs = [];
    renderRuns(state.runs);
    refreshCompareRunOptions();
    renderComparison(null, { error: 'Unable to load runs for comparison.' });
    renderTrends(null);
    setupPolling();
  }
}

function renderDetail(detail) {
  state.detail = detail;
  state.triageContext = null;
  state.focusedTaskId = null;
  el('runTitle').textContent = `Run Detail: ${detail.run_id || '-'}`;
  setStatusChip(detail.status);
  renderMetaGrid(detail);
  renderTimeline(detail.phase_timeline || []);
  renderTasks(detail.tasks || []);
  renderBlockers(detail.blockers || []);
  renderValidationPanels(detail);
}

async function selectRun(runId, options = { rerenderList: true }) {
  state.selectedRunId = runId;
  state.selectedRun = state.runs.find((run) => run.run_id === runId) || null;
  if (state.selectedRun?.run_dir) {
    const outInput = el('controlOut');
    if (outInput) outInput.value = state.selectedRun.run_dir;
    updateQuickRunHint();
  }
  if (options.rerenderList) renderRuns(state.runs);

  try {
    const detail = state.useMock
      ? (mock.details[runId] || { run_id: runId, status: 'unknown', phase_timeline: [], tasks: [], blockers: [] })
      : await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    renderDetail(detail);
  } catch (err) {
    renderDetail({ run_id: runId, status: 'unknown', phase_timeline: [], tasks: [], blockers: [] });
    el('statusLine').textContent = `Failed to load detail for ${runId}: ${err.message}`;
  }
}

function initTabs() {
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      activateTab(btn.dataset.tab);
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
    profileInput.value = defaults.profile || 'enterprise';
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

    await loadRuns();
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

  const quickProfile = normalizeLocalPath(defaults.profile) || 'local_simple';
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

  if (!left || !right || !swap || !refresh) return;

  left.addEventListener('change', async () => {
    state.compareLeftId = left.value;
    await refreshComparison();
  });

  right.addEventListener('change', async () => {
    state.compareRightId = right.value;
    await refreshComparison();
  });

  swap.addEventListener('click', async () => {
    const prevLeft = state.compareLeftId;
    state.compareLeftId = state.compareRightId;
    state.compareRightId = prevLeft;
    refreshCompareRunOptions();
    await refreshComparison();
  });

  refresh.addEventListener('click', async () => {
    await refreshComparison();
  });
}

function initLiveUpdateControls() {
  const toggle = el('liveUpdateToggle');
  const interval = el('pollInterval');

  toggle.checked = state.liveUpdateEnabled;
  interval.value = String(state.pollIntervalMs);

  toggle.addEventListener('change', () => {
    state.liveUpdateEnabled = toggle.checked;
    setupPolling();
    el('statusLine').textContent = state.liveUpdateEnabled
      ? `Live update enabled (${state.pollIntervalMs / 1000}s)`
      : 'Live update paused';
  });

  interval.addEventListener('change', () => {
    const ms = Number(interval.value) || 8000;
    state.pollIntervalMs = ms;
    setupPolling();
    if (state.liveUpdateEnabled) {
      el('statusLine').textContent = `Live update interval: ${ms / 1000}s`;
    }
  });
}

initTabs();
initValidationControls();
initRunControls();
initCompareControls();
initTrendControls();
initLiveUpdateControls();
loadRuns();