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

const ACTIVE_BACKUP_KEY = 'active_backup_job';

function trackBackupJob(jobId, initialStatus) {
  localStorage.setItem(ACTIVE_BACKUP_KEY, JSON.stringify({ job: jobId, time: Date.now() }));
  
  // Create or retrieve persistent toast
  const t = toast(
    `<b>Backup job #${jobId}</b> <span class="jt">${initialStatus || 'in progress…'}</span><div class="progress"><div></div></div>`,
    '',
    0
  );

  let active = true;

  const poll = async () => {
    while (active) {
      try {
        const j = await fetch(`/api/jobs/${jobId}`).then(r => r.json());
        if (!j || !j.id) {
          localStorage.removeItem(ACTIVE_BACKUP_KEY);
          t.remove();
          break;
        }

        const jtEl = t.querySelector('.jt');
        const progEl = t.querySelector('.progress > div');

        if (jtEl) {
          jtEl.textContent = `${j.done}/${j.total} done · ${j.ok} ok · ${j.failed} failed`;
        }
        if (progEl) {
          progEl.style.width = (j.total ? (100 * j.done / j.total) : 100) + '%';
        }

        if (j.status !== 'running') {
          active = false;
          localStorage.removeItem(ACTIVE_BACKUP_KEY);
          t.className = 'toast ' + (j.failed ? 'bad' : 'ok');
          if (jtEl) {
            jtEl.textContent = `finished: ${j.ok} ok (${j.unchanged} unchanged), ${j.failed} failed of ${j.total}`;
          }
          setTimeout(() => {
            t.remove();
            // If on backups, devices or dashboard, refresh to show new data
            if (['/devices', '/backups', '/', '/dashboard'].includes(location.pathname)) {
              location.reload();
            }
          }, 3500);
          break;
        }
      } catch (err) {
        // network blip, retry next interval
      }
      await new Promise(r => setTimeout(r, 1500));
    }
  };

  poll();
  return t;
}

// Check for ongoing backup job on page load and maintain status toast
(function initPersistentJobTracker() {
  try {
    const raw = localStorage.getItem(ACTIVE_BACKUP_KEY);
    if (!raw) return;
    const item = JSON.parse(raw);
    // Ignore jobs older than 2 hours in case of stale state
    if (Date.now() - item.time > 2 * 60 * 60 * 1000) {
      localStorage.removeItem(ACTIVE_BACKUP_KEY);
      return;
    }
    trackBackupJob(item.job, 'reconnecting…');
  } catch (e) {
    localStorage.removeItem(ACTIVE_BACKUP_KEY);
  }
})();

async function runBackup(ids, btn) {
  const body = ids === 'all' ? { all: true } : { ids };
  if (ids !== 'all' && !ids.length) { toast('Select at least one device', 'bad'); return; }
  if (btn) btn.disabled = true;
  try {
    const { job } = await api('/api/backup', body);
    trackBackupJob(job, 'starting…');
  } catch (err) {
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

// ── Sidebar collapse ──────────────────────────────────────────────────────
function toggleSidebar() {
  const layout = document.getElementById('layout');
  const collapsed = layout.classList.toggle('sidebar-collapsed');
  localStorage.setItem('sidebar-collapsed', collapsed ? '1' : '0');
}

// Restore sidebar state on load
(function() {
  if (localStorage.getItem('sidebar-collapsed') === '1') {
    const layout = document.getElementById('layout');
    if (layout) layout.classList.add('sidebar-collapsed');
  }
})();

// ── Page transition & nav ripple ──────────────────────────────────────────
(function () {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) return;

  // Ripple on sidebar nav links
  document.querySelectorAll('.nav a').forEach(link => {
    link.addEventListener('click', function (e) {
      // Skip modifier-key clicks (open in new tab etc.)
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const r = document.createElement('span');
      r.className = 'nav-ripple';
      this.appendChild(r);
      r.addEventListener('animationend', () => r.remove());
    });
  });

  // Fade-out main content before navigating to a new page
  function shouldAnimate(href) {
    if (!href) return false;
    try {
      const url = new URL(href, location.href);
      // Same origin only; skip hash-only changes and logout (instant redirect)
      if (url.origin !== location.origin) return false;
      if (url.pathname === location.pathname && url.hash) return false;
      return true;
    } catch { return false; }
  }

  document.addEventListener('click', function (e) {
    // Walk up to find an <a> tag
    const a = e.target.closest('a[href]');
    if (!a) return;
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target === '_blank') return;
    if (!shouldAnimate(a.href)) return;

    const layout = document.getElementById('layout');
    const loginWrap = document.querySelector('.login-wrap');
    const target = layout || loginWrap;
    if (!target) return;

    e.preventDefault();
    const dest = a.href;

    if (layout) {
      layout.classList.add('page-leaving');
    } else {
      // login page — fade out the card
      const card = loginWrap.querySelector('.card');
      if (card) {
        card.style.transition = 'opacity .22s cubic-bezier(0.4, 0, 1, 1), transform .22s cubic-bezier(0.4, 0, 1, 1)';
        card.style.opacity = '0';
        card.style.transform = 'scale(.92) translateY(-10px)';
      }
    }
    setTimeout(() => { location.href = dest; }, 220);
  });
})();
