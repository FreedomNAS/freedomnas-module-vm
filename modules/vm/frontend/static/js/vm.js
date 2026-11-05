'use strict';

let _status = null;
let _poll = null;

document.addEventListener('DOMContentLoaded', () => {
  if (!initShared()) return;
  document.getElementById('form-setup').addEventListener('submit', setupHypervisor);
  loadStatus();
});

async function loadStatus() {
  try {
    _status = await apiJSON('GET', '/api/vm/status');
  } catch (e) {
    document.getElementById('vm-body').innerHTML =
      `<tr><td colspan="2" class="text-muted">Could not read the hypervisor: ${esc(e.message)}</td></tr>`;
    return;
  }
  render();
  const busy = (_status.job || {}).state === 'running';
  if (busy && !_poll) _poll = setInterval(loadStatus, 3000);
  if (!busy && _poll) { clearInterval(_poll); _poll = null; }
}

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function row(label, value) {
  return `<tr><td class="text-muted" style="padding:5px 0;width:140px">${label}</td><td>${value}</td></tr>`;
}

function render() {
  const s = _status;
  let state;
  if (!s.enabled) state = `<span class="badge ${s.installed ? 'badge-warn' : 'badge-muted'}">${s.installed ? 'Installed, not set up' : 'Not installed'}</span>`;
  else if (s.running) state = '<span class="badge badge-ok">Running</span>';
  else state = '<span class="badge badge-err">Not answering</span>';
  const rows = [row('State', state), row('Backend', esc(s.backend))];
  if (s.pool) rows.push(row('Pool', `<span class="font-mono">${esc(s.pool)}</span>`));
  if (s.accelerated === false) {
    rows.push(row('Processor', '<span class="badge badge-warn">No hardware virtualization</span> Turn on VT-x or AMD-V in the firmware settings.'));
  }
  document.getElementById('vm-body').innerHTML = rows.join('');
  renderJob(s.job || {});
  const busy = (s.job || {}).state === 'running';
  const form = document.getElementById('form-setup');
  form.hidden = s.enabled || busy;
  const select = document.getElementById('f-pool');
  if (!s.enabled && !select.options.length) {
    select.innerHTML = s.pools.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
  }
  document.getElementById('machines-section').hidden = !s.enabled;
  document.getElementById('machines-body').innerHTML = s.machines.length
    ? s.machines.map((m) => `<tr><td>${esc(m.name)}</td><td>${esc(m.state)}</td><td>${esc(m.cpus ?? '')}</td><td>${m.memory_mb ? `${esc(m.memory_mb)} MiB` : ''}</td></tr>`).join('')
    : '<tr><td colspan="4" class="text-muted">No virtual machines yet.</td></tr>';
}

function renderJob(job) {
  const line = document.getElementById('job-line');
  if (job.state === 'running') {
    line.textContent = 'Setting up the hypervisor… (the packages can take some minutes)';
  } else if (job.state === 'failed') {
    line.textContent = `Set up failed: ${job.error || ''}`;
  } else {
    line.hidden = true;
    return;
  }
  line.hidden = false;
}

async function setupHypervisor(event) {
  event.preventDefault();
  const btn = document.getElementById('btn-setup');
  setLoading(btn, true, 'Setting up…');
  try {
    await apiJSON('POST', '/api/vm/setup', { pool: document.getElementById('f-pool').value });
  } catch (e) {
    toast(`Error: ${e.message}`, 'err');
  }
  setLoading(btn, false);
  loadStatus();
}
