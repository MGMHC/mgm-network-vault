const CSRF = document.querySelector('meta[name="csrf"]')?.content;

async function api(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { data = { error: `HTTP ${r.status}` }; }
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function toast(html, kind = '', ms = 5000) {
  const box = document.getElementById('toast');
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = html;
  box.appendChild(el);
  if (ms) setTimeout(() => el.remove(), ms);
  return el;
}

function selected(scope = document) {
  return [...scope.querySelectorAll('input.sel:checked')].map(i => i.value);
}

document.addEventListener('change', e => {
  if (e.target.matches('input.sel-all')) {
    const table = e.target.closest('table');
    table.querySelectorAll('input.sel').forEach(i => { i.checked = e.target.checked; });
  }
});

document.addEventListener('submit', e => {
  const msg = e.target.dataset.confirm;
  if (msg && !confirm(msg)) e.preventDefault();
});

async function runBackup(ids, btn) {
  const body = ids === 'all' ? { all: true } : { ids };
  if (ids !== 'all' && !ids.length) { toast('Select at least one device', 'bad'); return; }
  if (btn) btn.disabled = true;
  let t;
  try {
    const { job } = await api('/api/backup', body);
    t = toast(`<b>Backup job #${job}</b> <span class="jt">starting…</span><div class="progress"><div></div></div>`, '', 0);
    while (true) {
      await new Promise(r => setTimeout(r, 1500));
      const j = await fetch(`/api/jobs/${job}`).then(r => r.json());
      t.querySelector('.jt').textContent = `${j.done}/${j.total} done · ${j.ok} ok · ${j.failed} failed`;
      t.querySelector('.progress > div').style.width = (j.total ? 100 * j.done / j.total : 100) + '%';
      if (j.status !== 'running') {
        t.className = 'toast ' + (j.failed ? 'bad' : 'ok');
        t.querySelector('.jt').textContent =
          `finished: ${j.ok} ok (${j.unchanged} unchanged), ${j.failed} failed of ${j.total}`;
        setTimeout(() => location.reload(), 1600);
        break;
      }
    }
  } catch (err) {
    if (t) t.remove();
    toast('Backup failed to start: ' + err.message, 'bad');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runCheck(ids, btn) {
  const body = ids === 'all' ? { all: true } : { ids };
  if (btn) btn.disabled = true;
  const t = toast('Checking reachability (ping + SSH)…', '', 0);
  try {
    const res = await api('/api/check', body);
    const vals = Object.values(res);
    const up = vals.filter(v => v.status === 'up').length;
    t.remove();
    toast(`Reachability: ${up}/${vals.length} reachable`, up === vals.length ? 'ok' : 'bad');
    setTimeout(() => location.reload(), 1000);
  } catch (err) {
    t.remove();
    toast('Check failed: ' + err.message, 'bad');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runDetect(ids, btn) {
  if (!ids.length) { toast('Select at least one device', 'bad'); return; }
  if (btn) btn.disabled = true;
  const t = toast(`Detecting model of ${ids.length} switch(es)…`, '', 0);
  try {
    const res = await api('/api/detect', { ids });
    const ok = Object.values(res).filter(Boolean).length;
    t.remove();
    toast(`Model detected on ${ok}/${ids.length} switch(es)` + (ok < ids.length ? ' - see Logs for errors' : ''),
          ok === ids.length ? 'ok' : 'bad');
    setTimeout(() => location.reload(), 1200);
  } catch (err) {
    t.remove();
    toast('Detection failed: ' + err.message, 'bad');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function colorDiff(text) {
  return text.split('\n').map(l => {
    let cls = '';
    if (/^\[edit/.test(l) || /^@@/.test(l)) cls = 'hunk';
    else if (/^\+/.test(l) && !/^\+\+\+/.test(l)) cls = 'add';
    else if (/^-/.test(l) && !/^---/.test(l)) cls = 'del';
    return `<span class="l ${cls}">${escapeHtml(l) || ' '}</span>`;
  }).join('');
}

function openModal(title, html) {
  const m = document.getElementById('modal');
  m.querySelector('h2').textContent = title;
  m.querySelector('.body').innerHTML = html;
  m.classList.add('open');
}
function closeModal() { document.getElementById('modal').classList.remove('open'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

async function runTool(devId, kind, btn, a, b) {
  if (btn) btn.disabled = true;
  openModal('Running…', '<p class="muted">Connecting to the switch over SSH…</p>');
  try {
    const r = await api(`/api/devices/${devId}/tool`, { kind, a, b });
    const diffish = ['rollback', 'unsaved'].includes(kind);
    openModal(r.title, `<pre class="code udiff">${diffish ? colorDiff(r.text) : escapeHtml(r.text)}</pre>`);
  } catch (err) {
    openModal('Error', `<pre class="code">${escapeHtml(err.message)}</pre>`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function compareSelected() {
  const ids = selected();
  if (ids.length !== 2) { toast('Select exactly two backups to compare', 'bad'); return; }
  location.href = `/compare?a=${ids[0]}&b=${ids[1]}`;
}

function zipSelected() {
  const ids = selected();
  if (!ids.length) { toast('Select backups to download', 'bad'); return; }
  location.href = `/backups/zip?ids=${ids.join(',')}`;
}
