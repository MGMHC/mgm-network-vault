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

async function runOpticsModal(devId, btn) {
  if (btn) btn.disabled = true;
  openModal('Optical Diagnostics', '<div style="text-align:center;padding:24px"><div class="spinner-pulse" style="margin:0 auto 12px"></div><p class="muted">Reading transceiver optical telemetry from switch over SSH…</p></div>');
  try {
    const res = await fetch(`/api/devices/${devId}/optics`).then(r => r.json());
    if (res.error) throw new Error(res.error);
    const ports = res.ports || [];
    if (!ports.length) {
      openModal(
        `${res.device_name || 'Switch'} · Optical Transceivers`,
        `<div style="text-align:center;padding:24px">
          <p><b>No optical transceivers detected.</b></p>
          <p class="muted small">This switch may have all-copper RJ45 interfaces or digital optical monitoring (DDM) is not supported on the inserted modules.</p>
        </div>`
      );
      return;
    }

    let rows = ports.map(p => {
      let barColor = 'var(--ok)';
      if (p.status === 'warn') barColor = 'var(--warn)';
      if (p.status === 'bad') barColor = 'var(--bad)';
      const rxText = p.rx_dbm !== null ? `${p.rx_dbm.toFixed(2)} dBm` : 'N/A';
      const txText = p.tx_dbm !== null ? `${p.tx_dbm.toFixed(2)} dBm` : 'N/A';
      const tempText = p.temp_c !== null ? `${p.temp_c.toFixed(1)}°C` : '—';
      return `
        <tr>
          <td class="mono font-bold" style="color:var(--accent);">${escapeHtml(p.interface)}</td>
          <td>
            <div style="display:flex;align-items:center;gap:8px;">
              <span class="mono font-bold">${rxText}</span>
              <div class="signal-meter" title="RX Signal: ${p.rx_percent}%">
                <div class="signal-fill" style="width:${p.rx_percent}%;background:${barColor};"></div>
              </div>
            </div>
          </td>
          <td class="mono small">${txText}</td>
          <td class="mono small muted">${tempText}</td>
          <td>
            <span class="badge ${p.status}">${escapeHtml(p.status_label)}</span>
            ${p.alarms && p.alarms.length ? `<div class="badge bad" style="margin-top:2px;">${escapeHtml(p.alarms.join(', '))}</div>` : ''}
          </td>
        </tr>
      `;
    }).join('');

    const html = `
      <div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;">
        <div><b>${escapeHtml(res.device_name)}</b> <span class="mono muted small">(${escapeHtml(res.host)})</span></div>
        <span class="badge blue">${ports.length} Optical Port(s)</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Port</th>
              <th>RX Optical Power</th>
              <th>TX Power</th>
              <th>Temp</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
    openModal(`${res.device_name} · Optical Diagnostics (DOM/DDM)`, html);
  } catch (err) {
    openModal('Optical Diagnostics Failed', `<div class="flash error" style="margin:0">${escapeHtml(err.message)}</div>`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* --------------------------------------------------------------------------- Persistent Global Port Locator Dock & Tracker */
const ACTIVE_LOCATOR_KEY = 'nv_active_locator_trace';

function getActiveLocatorTrace() {
  try {
    const raw = localStorage.getItem(ACTIVE_LOCATOR_KEY);
    if (!raw) return null;
    const item = JSON.parse(raw);
    if (Date.now() - (item.time || 0) > 30 * 60 * 1000) {
      localStorage.removeItem(ACTIVE_LOCATOR_KEY);
      return null;
    }
    return item;
  } catch (e) {
    localStorage.removeItem(ACTIVE_LOCATOR_KEY);
    return null;
  }
}

function setActiveLocatorTrace(data) {
  try {
    const existing = getActiveLocatorTrace() || {};
    const updated = { ...existing, ...data, time: Date.now() };
    localStorage.setItem(ACTIVE_LOCATOR_KEY, JSON.stringify(updated));
    return updated;
  } catch (e) {}
}

function clearActiveLocatorTrace() {
  localStorage.removeItem(ACTIVE_LOCATOR_KEY);
  const dock = document.getElementById('globalLocatorDock');
  if (dock) dock.style.display = 'none';
}

function renderGlobalLocatorDock(trace) {
  const dock = document.getElementById('globalLocatorDock');
  if (!dock || !trace) return;

  const isPortLocatorPage = window.location.pathname.startsWith('/operations/port-locator');
  // If we are on port-locator page, the dock only shows if explicitly minimized
  if (isPortLocatorPage && !trace.minimized) {
    dock.style.display = 'none';
    return;
  }

  const titleEl = document.getElementById('gldTitleText');
  const statusEl = document.getElementById('gldStatusText');
  const percentEl = document.getElementById('gldPercent');
  const barFillEl = document.getElementById('gldBarFill');

  if (titleEl) titleEl.textContent = `Tracing '${trace.query || 'host'}'…`;

  if (trace.status === 'complete') {
    dock.classList.add('success');
    dock.classList.remove('error');
    if (barFillEl) barFillEl.style.width = '100%';
    if (percentEl) {
      percentEl.textContent = '100%';
      percentEl.style.color = 'var(--ok)';
    }
    if (statusEl) {
      if (trace.result && trace.result.best_match) {
        const bm = trace.result.best_match;
        statusEl.textContent = `✅ Found: ${bm.switch_name} (${bm.interface})`;
        statusEl.className = 'small ok font-bold';
      } else {
        statusEl.textContent = 'Trace finished · Click to view';
        statusEl.className = 'small muted';
      }
    }
  } else if (trace.status === 'error' || trace.status === 'cancelled') {
    dock.classList.add('error');
    dock.classList.remove('success');
    if (percentEl) percentEl.style.color = 'var(--danger)';
    if (statusEl) {
      statusEl.textContent = trace.error || 'Trace cancelled';
      statusEl.className = 'small danger';
    }
  } else {
    dock.classList.remove('success', 'error');
    const pct = trace.percent || 0;
    if (barFillEl) barFillEl.style.width = `${pct}%`;
    if (percentEl) {
      percentEl.textContent = `${pct}%`;
      percentEl.style.color = 'var(--accent)';
    }
    if (statusEl) {
      statusEl.textContent = trace.message || `Probing switches (${trace.done || 0}/${trace.total || 0})…`;
      statusEl.className = 'small muted';
    }
  }

  dock.style.display = 'block';
}

function handleGlobalLocatorClick(e) {
  if (e) e.stopPropagation();
  maximizeGlobalLocator(e);
}

function maximizeGlobalLocator(e) {
  if (e) e.stopPropagation();
  const trace = getActiveLocatorTrace();
  if (!trace) return;

  const targetUrl = `/operations/port-locator?run_id=${encodeURIComponent(trace.runId)}&q=${encodeURIComponent(trace.query || '')}`;

  if (window.location.pathname.startsWith('/operations/port-locator')) {
    if (typeof restoreLocatorModal === 'function') {
      restoreLocatorModal();
    } else {
      window.location.href = targetUrl;
    }
  } else {
    window.location.href = targetUrl;
  }
}

async function dismissGlobalLocator(e) {
  if (e) e.stopPropagation();
  const trace = getActiveLocatorTrace();
  if (trace && trace.runId) {
    try {
      fetch(`/api/operations/port-locator/cancel/${trace.runId}`, {
        method: 'POST',
        headers: { 'X-CSRF-Token': CSRF },
      });
    } catch (_) {}
  }
  clearActiveLocatorTrace();
  if (window.location.pathname.startsWith('/operations/port-locator')) {
    if (typeof closeLocatorProgressModal === 'function') {
      closeLocatorProgressModal();
    }
  }
}

let _globalLocatorPollInterval = null;

function pollGlobalLocatorStatus(runId) {
  if (_globalLocatorPollInterval) clearInterval(_globalLocatorPollInterval);

  const poll = async () => {
    const trace = getActiveLocatorTrace();
    if (!trace || trace.runId !== runId) {
      clearInterval(_globalLocatorPollInterval);
      return;
    }

    try {
      const res = await fetch(`/api/operations/port-locator/status/${runId}`);
      if (!res.ok) {
        if (res.status === 404) {
          clearInterval(_globalLocatorPollInterval);
          clearActiveLocatorTrace();
        }
        return;
      }
      const data = await res.json();

      const updated = setActiveLocatorTrace({
        status: data.status,
        percent: data.percent,
        done: data.done,
        total: data.total,
        message: data.device ? `Probing switch ${data.done}/${data.total}: ${data.device}…` : trace.message,
        result: data.result || trace.result,
        error: data.error,
      });

      renderGlobalLocatorDock(updated);

      if (data.status !== 'running') {
        clearInterval(_globalLocatorPollInterval);
        if (data.status === 'complete' && !window.location.pathname.startsWith('/operations/port-locator')) {
          const hit = data.result && data.result.best_match;
          if (hit) {
            toast(`🎯 Port Locator: Host physical port pinpointed on <b>${hit.switch_name}</b> (port ${hit.interface})! Click widget below to view.`, 'ok', 7000);
          } else {
            toast(`Port Locator: Trace completed. Click widget below to view results.`, 'ok', 5000);
          }
        }
      }
    } catch (err) {
      // transient network blip
    }
  };

  poll();
  _globalLocatorPollInterval = setInterval(poll, 1500);
}

(function initGlobalLocatorTracker() {
  try {
    const trace = getActiveLocatorTrace();
    if (!trace || !trace.runId) return;

    const isPortLocatorPage = window.location.pathname.startsWith('/operations/port-locator');

    // If not on port-locator page, show the dock and start polling if running
    if (!isPortLocatorPage) {
      renderGlobalLocatorDock(trace);
      if (trace.status === 'running') {
        pollGlobalLocatorStatus(trace.runId);
      }
    } else {
      // On port locator page: if minimized is true, show the dock
      if (trace.minimized) {
        renderGlobalLocatorDock(trace);
      }
    }
  } catch (e) {}
})();


