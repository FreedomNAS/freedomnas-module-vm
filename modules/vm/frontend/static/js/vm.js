'use strict';

let _status = null;
let _poll = null;
let _selected = null;
let _isoChoice = '';
let _rfb = null;
let _socket = null;

document.addEventListener('DOMContentLoaded', () => {
  if (!initShared()) return;
  document.getElementById('form-setup').addEventListener('submit', setupHypervisor);
  document.getElementById('machines-list').addEventListener('click', onListClick);
  document.getElementById('detail-body').addEventListener('click', onTabClick);
  document.getElementById('tab-summary').addEventListener('click', onSummaryClick);
  document.getElementById('btn-new').addEventListener('click', openNew);
  document.getElementById('form-new').addEventListener('submit', createMachine);
  document.getElementById('btn-remove').addEventListener('click', removeMachine);
  document.getElementById('f-iso-file').addEventListener('change', uploadIso);
  document.getElementById('form-iso-url').addEventListener('submit', fetchIso);
  document.getElementById('isos-body').addEventListener('click', onIsoClick);
  document.getElementById('btn-console').addEventListener('click', toggleConsole);
  document.getElementById('btn-cad').addEventListener('click', () => { if (_rfb) _rfb.sendCtrlAltDel(); });
  document.getElementById('btn-fullscreen').addEventListener('click', () => document.getElementById('console-screen').requestFullscreen());
  bindModalClose('modal-new');
  bindModalClose('modal-remove');
  setInterval(() => { if (_status && _status.enabled && !document.hidden) loadStatus(); }, 5000);
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
  const access = document.getElementById('access-line');
  access.hidden = !s.enabled || !s.access_members.length;
  access.textContent = `${s.access_members.join(', ')} can use virsh and virt-manager with their own account, as members of ` +
    `the group ${s.access_group}. Log out and back in first, also over SSH: a session keeps the groups it started with.`;
  const busy = (s.job || {}).state === 'running';
  const form = document.getElementById('form-setup');
  form.hidden = s.enabled || busy;
  const select = document.getElementById('f-pool');
  if (!s.enabled && !select.options.length) {
    select.innerHTML = s.pools.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
  }
  document.getElementById('machines-section').hidden = !s.enabled;
  document.getElementById('isos-section').hidden = !s.enabled;
  renderMachines();
  renderIsos();
}

const STATES = {
  running: ['badge-ok', 'Running'],
  stopped: ['badge-muted', 'Stopped'],
  undefined: ['badge-err', 'Not defined'],
};

function stateBadge(m) {
  if (m.stopping) return '<span class="badge badge-warn">Stopping…</span>';
  const [cls, label] = STATES[m.state] || ['badge-muted', m.state];
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function renderMachines() {
  const machines = _status.machines;
  const choice = document.getElementById('f-install-iso');
  if (choice) _isoChoice = choice.value;
  if (!machines.some((m) => m.name === _selected)) _selected = machines.length ? machines[0].name : null;
  document.getElementById('machines-list').innerHTML = machines.length
    ? machines.map((m) => `<button class="vm-item${m.name === _selected ? ' active' : ''}" data-machine="${esc(m.name)}">
        <span>${esc(m.name)}</span>${stateBadge(m)}</button>`).join('')
    : '<p class="text-muted">No virtual machines yet.</p>';
  renderDetail(machines.find((m) => m.name === _selected));
}

function renderDetail(m) {
  document.getElementById('detail-empty').hidden = !!m;
  document.getElementById('detail-body').hidden = !m;
  if (!m) return;
  document.getElementById('detail-name').textContent = m.name;
  document.getElementById('tab-summary').innerHTML = m.foreign ? foreignSummary(m) : summary(m);
  document.getElementById('tab-hardware').innerHTML = m.foreign
    ? '<p class="text-muted">FreedomNAS did not create this machine, so it does not know its hardware.</p>' : hardware(m);
}

function foreignSummary(m) {
  return `<table style="width:100%;font-size:0.875rem"><tbody>${row('State', stateBadge(m))}</tbody></table>
    <p class="form-hint">This machine was not created here. Manage it with the hypervisor's own tools.</p>`;
}

function summary(m) {
  const running = m.state === 'running';
  const total = m.disks.reduce((sum, d) => sum + d.size_gb, 0);
  const rows = [row('State', stateBadge(m)), row('Processors', esc(m.cpus)), row('Memory', `${esc(m.memory_mb)} MiB`),
    row('Disks', `${esc(m.disks.length)}, ${esc(total)} GiB`),
    row('At boot', m.wanted === 'up' ? 'Starts with the NAS' : 'Stays off')];
  let actions;
  if (m.stopping) {
    actions = '<button class="btn btn-danger btn-sm" data-action="poweroff">Power off</button>';
  } else if (running) {
    actions = '<button class="btn btn-sm" data-action="stop">Shut down</button>' +
      '<button class="btn btn-danger btn-sm" data-action="poweroff">Power off</button>';
  } else {
    const images = _status.isos.map((i) => `<option value="${esc(i.name)}"${i.name === _isoChoice ? ' selected' : ''}>${esc(i.name)}</option>`).join('');
    actions = '<button class="btn btn-primary btn-sm" data-action="start">Start</button>' +
      (images ? `<select class="input" id="f-install-iso" style="max-width:260px;height:32px;padding:2px 8px">${images}</select>
        <button class="btn btn-sm" data-action="install">Start from image</button>` : '') +
      '<button class="btn btn-danger btn-sm" data-action="remove">Remove</button>';
  }
  const hint = m.stopping
    ? '<p class="form-hint">The guest was asked to shut down and is still running. Some systems ignore the request: Power off cuts it, like pulling the plug.</p>'
    : '';
  return `<table style="width:100%;font-size:0.875rem"><tbody>${rows.join('')}</tbody></table>
    ${hint}<div class="vm-actions">${actions}</div>`;
}

function hardware(m) {
  const fixed = [['Firmware', 'UEFI'], ['Video', 'VGA'], ['Input', 'USB tablet'], ['Serial', 'One port'],
    ['Identity', `<span class="font-mono">${esc(m.uuid)}</span>`]];
  const disks = m.disks.map((d, i) => `<tr><td>Disk ${i}</td><td>${esc(d.size_gb)} GiB, virtio</td>
    <td class="font-mono text-sm">${esc(m.dataset)}/disk${i}</td></tr>`).join('');
  const nics = m.nics.map((n, i) => `<tr><td>Network ${i}</td><td>virtio, on ${esc(n.bridge)}</td>
    <td class="font-mono text-sm">${esc(n.mac)}</td></tr>`).join('');
  return `<div class="table-wrap"><table class="table">
      <thead><tr><th>Device</th><th>Settings</th><th>Where</th></tr></thead>
      <tbody>
        <tr><td>Processors</td><td>${esc(m.cpus)}</td><td></td></tr>
        <tr><td>Memory</td><td>${esc(m.memory_mb)} MiB</td><td></td></tr>
        ${disks}${nics}
      </tbody></table></div>
    <table style="width:100%;font-size:0.875rem;margin-top:12px"><tbody>
      ${fixed.map(([label, value]) => row(label, value)).join('')}</tbody></table>
    <p class="form-hint">Every edition gives a machine this same hardware, so it can move with its pool.</p>`;
}

function onListClick(event) {
  const item = event.target.closest('[data-machine]');
  if (!item) return;
  if (item.dataset.machine !== _selected) closeConsole();
  _selected = item.dataset.machine;
  renderMachines();
}

function onTabClick(event) {
  const tab = event.target.closest('.tab-btn');
  if (!tab || tab.disabled) return;
  document.querySelectorAll('#detail-body .tab-btn').forEach((b) => b.classList.toggle('active', b === tab));
  document.querySelectorAll('#detail-body .tab-panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${tab.dataset.tab}`));
}

async function onSummaryClick(event) {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'install') {
    button.disabled = true;
    try {
      await apiJSON('POST', `/api/vm/machines/${encodeURIComponent(_selected)}/install`, { iso: document.getElementById('f-install-iso').value });
      toast('Started from the image, this once. Open the console to go on.', 'ok');
      document.querySelector('#detail-body [data-tab=console]').click();
      setTimeout(() => { if (!_rfb) toggleConsole(); }, 1500);
    } catch (e) {
      toast(`Error: ${e.message}`, 'err');
    }
    loadStatus();
    return;
  }
  if (action === 'remove') {
    document.getElementById('remove-name').textContent = _selected;
    document.getElementById('f-destroy').checked = false;
    openModal('modal-remove');
    return;
  }
  if (action === 'poweroff' && !await confirm(`Power off ${_selected}`,
    'The machine stops at once, like pulling the plug. What the guest has not saved is lost.', 'Power off')) return;
  button.disabled = true;
  try {
    await apiJSON('POST', `/api/vm/machines/${encodeURIComponent(_selected)}/${action}`);
  } catch (e) {
    toast(`Error: ${e.message}`, 'err');
  }
  loadStatus();
}

function openNew() {
  const select = document.getElementById('f-bridge');
  select.innerHTML = '<option value="">No network</option>' +
    _status.bridges.map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join('');
  if (_status.bridges.length) select.value = _status.bridges[0];
  openModal('modal-new');
}

async function createMachine(event) {
  event.preventDefault();
  const name = document.getElementById('f-name').value.trim();
  const bridge = document.getElementById('f-bridge').value;
  const btn = document.getElementById('btn-create');
  setLoading(btn, true, 'Creating…');
  try {
    await apiJSON('POST', '/api/vm/machines', {
      name,
      cpus: Number(document.getElementById('f-cpus').value),
      memory_mb: Number(document.getElementById('f-memory').value),
      disks: [{ size_gb: Number(document.getElementById('f-disk').value) }],
      nics: bridge ? [{ bridge }] : [],
    });
    closeModal('modal-new');
    event.target.reset();
    _selected = name;
    toast(`${name} created.`, 'ok');
  } catch (e) {
    toast(`Error: ${e.message}`, 'err');
  }
  setLoading(btn, false);
  loadStatus();
}

async function removeMachine() {
  const destroy = document.getElementById('f-destroy').checked;
  const btn = document.getElementById('btn-remove');
  setLoading(btn, true, 'Removing…');
  try {
    await apiJSON('DELETE', `/api/vm/machines/${encodeURIComponent(_selected)}?destroy_disks=${destroy}`);
    closeModal('modal-remove');
    toast(destroy ? 'The machine and its disks were removed.' : 'The machine was removed, its disks are kept.', 'ok');
    _selected = null;
  } catch (e) {
    toast(`Error: ${e.message}`, 'err');
  }
  setLoading(btn, false);
  loadStatus();
}

function renderJob(job) {
  const line = document.getElementById(job.kind === 'download' ? 'iso-line' : 'job-line');
  const word = job.kind === 'download' ? 'Fetching the image failed' : 'Set up failed';
  if (job.state === 'running') {
    line.textContent = job.kind === 'download' ? 'Fetching the image…'
      : 'Setting up the hypervisor… (the packages can take some minutes)';
  } else if (job.state === 'failed') {
    line.textContent = `${word}: ${job.error || ''}`;
  } else {
    line.hidden = true;
    return;
  }
  line.hidden = false;
}

function formatSize(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

function renderIsos() {
  document.getElementById('isos-body').innerHTML = _status.isos.length
    ? _status.isos.map((i) => `<tr><td class="font-mono text-sm">${esc(i.name)}</td><td>${formatSize(i.size)}</td>
        <td style="text-align:right"><button class="btn btn-danger btn-sm" data-iso="${esc(i.name)}">Remove</button></td></tr>`).join('')
    : '<tr><td colspan="3" class="text-muted">No install images yet.</td></tr>';
}

function uploadIso(event) {
  const file = event.target.files[0];
  if (!file) return;
  const line = document.getElementById('iso-line');
  line.hidden = false;
  const request = new XMLHttpRequest();
  request.open('POST', `/api/vm/isos?name=${encodeURIComponent(file.name)}`);
  request.setRequestHeader('Authorization', `Basic ${Auth.get()}`);
  request.upload.onprogress = (e) => { line.textContent = `Uploading ${file.name}: ${Math.round(100 * e.loaded / e.total)}%`; };
  request.onload = () => {
    let detail = '';
    try { detail = JSON.parse(request.responseText).detail || ''; } catch (e) { detail = ''; }
    line.textContent = request.status === 200 ? `${file.name} uploaded.` : `Upload failed: ${detail || request.status}`;
    event.target.value = '';
    loadStatus();
  };
  request.onerror = () => { line.textContent = 'Upload failed: the connection dropped.'; event.target.value = ''; };
  request.send(file);
}

async function fetchIso(event) {
  event.preventDefault();
  const field = document.getElementById('f-iso-url');
  try {
    await apiJSON('POST', '/api/vm/isos/download', { url: field.value.trim() });
    field.value = '';
  } catch (e) {
    toast(`Error: ${e.message}`, 'err');
  }
  loadStatus();
  if (!_poll) _poll = setInterval(loadStatus, 3000);
}

async function onIsoClick(event) {
  const button = event.target.closest('[data-iso]');
  if (!button) return;
  const name = button.dataset.iso;
  if (!await confirm(`Remove ${name}`, 'The image file is deleted from the pool.', 'Remove')) return;
  try {
    await apiJSON('DELETE', `/api/vm/isos/${encodeURIComponent(name)}`);
  } catch (e) {
    toast(`Error: ${e.message}`, 'err');
  }
  loadStatus();
}

function consoleButtons(connected) {
  document.getElementById('btn-console').textContent = connected ? 'Disconnect' : 'Connect';
  document.getElementById('btn-cad').hidden = !connected;
  document.getElementById('btn-fullscreen').hidden = !connected;
  document.getElementById('console-screen').hidden = !connected;
}

function closeConsole() {
  if (_rfb) { _rfb.disconnect(); _rfb = null; }
  consoleButtons(false);
}

function toggleConsole() {
  if (_rfb || _socket) { closeConsole(); return; }
  const line = document.getElementById('console-line');
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${location.host}/api/vm/machines/${encodeURIComponent(_selected)}/console`);
  socket.binaryType = 'arraybuffer';
  _socket = socket;
  line.textContent = 'Connecting…';
  socket.onopen = () => socket.send(JSON.stringify({ auth: Auth.get() }));
  socket.onclose = () => { _socket = null; if (!_rfb) line.textContent = line.textContent === 'Connecting…' ? 'The console did not answer.' : line.textContent; };
  socket.onmessage = (event) => {
    let answer = {};
    try { answer = JSON.parse(event.data); } catch (e) { answer = {}; }
    if (!answer.ok) { line.textContent = answer.error || 'The console did not answer.'; return; }
    socket.onmessage = null;
    socket.onclose = null;
    _socket = null;
    consoleButtons(true);
    const rfb = new window.NoVncRFB(document.getElementById('console-screen'), socket);
    rfb.scaleViewport = true;
    rfb.addEventListener('connect', () => { line.textContent = 'Connected.'; rfb.focus(); });
    rfb.addEventListener('disconnect', () => {
      if (_rfb === rfb) { _rfb = null; consoleButtons(false); }
      line.textContent = 'Disconnected.';
    });
    _rfb = rfb;
  };
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
