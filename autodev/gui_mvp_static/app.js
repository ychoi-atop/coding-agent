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
};

const PHASE_COLORS = {
  prd_analysis: '#7c3aed',
  architecture: '#4f46e5',
  planning: '#2563eb',
  implementation: '#0284c7',
  final_validation: '#0d9488',
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
  ],
  detail: {
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
    quality_index: { totals: { total_task_attempts: 4, hard_failures: 1, soft_failures: 1 } },
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
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${t.task_id || ''}</td>
      <td>${t.status || 'unknown'}</td>
      <td>${t.attempts ?? 0}</td>
      <td>${t.hard_failures ?? 0}</td>
      <td>${t.soft_failures ?? 0}</td>
    `;
    tbody.appendChild(tr);
  });
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

function normalizeValidationRows(detail) {
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
    const status = String(v?.status || (v?.ok ? 'passed' : 'unknown')).toLowerCase();
    return {
      name: String(v?.name || v?.validator || 'unknown'),
      status,
      ok: Boolean(v?.ok),
      returncode: v?.returncode,
      duration_ms: v?.duration_ms,
      phase: v?.phase,
      message: v?.message || v?.error || '',
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

function renderValidationPanels(detail) {
  const cards = el('validationCards');
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
      || String(row.message || '').toLowerCase().includes(keyword);
    return statusOk && nameOk && keywordOk;
  });

  cards.innerHTML = '';
  if (!rows.length) {
    cards.classList.add('hidden');
    validationEmpty.classList.remove('hidden');
    validationEmpty.textContent = 'Validation artifact not found.';
  } else if (!filtered.length) {
    cards.classList.add('hidden');
    validationEmpty.classList.remove('hidden');
    validationEmpty.textContent = 'No validators match current filter/search.';
  } else {
    cards.classList.remove('hidden');
    validationEmpty.classList.add('hidden');
    filtered.forEach((row) => {
      const card = document.createElement('article');
      card.className = `validator-card status-${row.status}`;
      card.innerHTML = `
        <div class="validator-header">
          <strong>${row.name}</strong>
          <span class="validator-status ${row.status}">${row.status.toUpperCase()}</span>
        </div>
        <div class="validator-meta">
          <span>rc: ${row.returncode ?? '-'}</span>
          <span>duration: ${row.duration_ms ?? '-'}ms</span>
          <span>phase: ${row.phase || '-'}</span>
        </div>
        ${row.message ? `<div class="validator-message">${row.message}</div>` : ''}
      `;
      cards.appendChild(card);
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
    if (!silent) {
      el('statusLine').textContent = `${state.runs.length} run(s) • updated ${new Date().toLocaleTimeString()}`;
    }
  } catch (err) {
    if (!silent) el('statusLine').textContent = `Live update failed: ${err.message}`;
  }
}

async function loadRuns() {
  state.useMock = new URLSearchParams(window.location.search).get('mock') === '1';
  if (state.useMock) {
    state.runs = mock.runs;
    state.selectedRunId = mock.runs[0].run_id;
    state.selectedRun = mock.runs[0];
    renderRuns(state.runs);
    renderDetail(mock.detail);
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
    setupPolling();
  } catch (err) {
    el('statusLine').textContent = `Failed to load runs. Try ?mock=1 (${err.message})`;
    state.runs = [];
    renderRuns(state.runs);
    setupPolling();
  }
}

function renderDetail(detail) {
  state.detail = detail;
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
  if (options.rerenderList) renderRuns(state.runs);

  try {
    const detail = state.useMock && runId === mock.detail.run_id
      ? mock.detail
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
      document.querySelectorAll('.tab').forEach((x) => x.classList.remove('is-active'));
      document.querySelectorAll('.tab-content').forEach((x) => x.classList.remove('is-active'));
      btn.classList.add('is-active');
      const target = document.getElementById(`tab-${btn.dataset.tab}`);
      if (target) target.classList.add('is-active');
    });
  });
}

function initValidationControls() {
  const search = el('validationSearch');
  const status = el('validationStatusFilter');
  const name = el('validationNameFilter');

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
initLiveUpdateControls();
loadRuns();