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

// Theme: look (glass|normal) and mode (dark|light), remembered per user (see theme.py).
// Applies immediately, mirrors into the nv_theme cookie (so the next page paints right even before
// login state is known), then saves to the account. A failed save keeps the look and says so.
function setTheme({ look, mode }) {
  const root = document.documentElement;
  look = look || root.dataset.look;
  mode = mode || root.dataset.mode;
  root.dataset.look = look;
  root.dataset.mode = mode;
  const scheme = document.querySelector('meta[name="color-scheme"]');
  if (scheme) scheme.content = mode;
  document.querySelectorAll('[data-set-look]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.setLook === look)));
  document.querySelectorAll('[data-set-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.setMode === mode)));
  document.cookie = `nv_theme=${look}-${mode}; path=/; max-age=31536000; samesite=lax`;
  window.dispatchEvent(new CustomEvent('themechange', { detail: { look, mode } }));
  return api('/api/account/theme', { look, mode }).catch(() => {
    toast("Couldn't save your theme", 'bad');
  });
}
window.setTheme = setTheme;

document.addEventListener('click', e => {
  const b = e.target.closest('[data-set-look], [data-set-mode]');
  if (!b) return;
  setTheme(b.dataset.setLook ? { look: b.dataset.setLook } : { mode: b.dataset.setMode });
});

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
  let failures = 0;

  const poll = async () => {
    while (active) {
      try {
        const r = await fetch(`/api/jobs/${jobId}`, { headers: { 'Accept': 'application/json' } });
        // Job gone, session expired or no access: stop polling instead of retrying forever
        if ([401, 403, 404].includes(r.status) || r.redirected) {
          localStorage.removeItem(ACTIVE_BACKUP_KEY);
          t.className = 'toast bad';
          t.textContent = r.status === 404 ? `Backup job #${jobId} not found` : 'Session expired - reload the page to see backup progress';
          setTimeout(() => t.remove(), 6000);
          break;
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        failures = 0;
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
        // network blip: retry, but give up after ~1 minute of consecutive failures
        if (++failures >= 40) {
          localStorage.removeItem(ACTIVE_BACKUP_KEY);
          t.className = 'toast bad';
          t.textContent = `Lost contact with backup job #${jobId} - check the Logs page`;
          setTimeout(() => t.remove(), 8000);
          break;
        }
      }
      await new Promise(r => setTimeout(r, 1500));
    }
  };

  t.finished = poll();  // resolves when the job ends (or tracking stops)
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
  if (btn) {
    if (btn.disabled) return;
    btn.disabled = true;
  }
  try {
    const { job } = await api('/api/backup', body);
    // Keep the button disabled until the job finishes, so repeat clicks can't start
    // parallel SSH backups of the same switches
    await trackBackupJob(job, 'starting…').finished;
  } catch (err) {
    toast('Backup failed to start: ' + err.message, 'bad');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runCheck(ids, btn) {
  const body = ids === 'all' ? { all: true } : { ids };
  if (ids !== 'all' && !ids.length) { toast('Select at least one device', 'bad'); return; }
  if (btn) {
    if (btn.disabled) return;
    btn.disabled = true;
  }
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

// Shared by every page. Some page scripts define their own escapeHtml/closeModal too; this file
// loads after them and replaces them, so these must accept every way the pages call them.
function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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
function closeModal(id) {
  // closeModal('rackModal') etc. hides a page's own modal; closeModal() closes the shared one
  if (typeof id === 'string' && id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
    return;
  }
  document.getElementById('modal')?.classList.remove('open');
}
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

// ── Nav ripple (page-to-page transitions are native CSS view transitions) ───────────────────────────────────────────
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
        message: data.step || (data.device ? `Probing switch ${data.done}/${data.total}: ${data.device}…` : trace.message),
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

/* ── ObaidQatan Liquid Glass Popovers & Selects ──────────────────────────── */
let activeLiquidSelect = null;
let activeLiquidCalendar = null;

function closeAllLiquidPopovers() {
  if (activeLiquidSelect) {
    activeLiquidSelect.close();
    activeLiquidSelect = null;
  }
  if (activeLiquidCalendar) {
    activeLiquidCalendar.close();
    activeLiquidCalendar = null;
  }
}

function initLiquidSelects(root = document) {
  const container = root || document;
  const selects = [];
  if (container.nodeType === 1 && container.matches && container.matches('select:not([multiple]):not(.no-liquid)')) {
    selects.push(container);
  } else if (container.querySelectorAll) {
    selects.push(...container.querySelectorAll('select:not([multiple]):not(.no-liquid)'));
  }

  selects.forEach(select => {
    if (select.dataset.lgEnhanced) return;
    select.dataset.lgEnhanced = 'true';

    // Build custom liquid glass wrapper
    const wrapper = document.createElement('div');
    wrapper.className = 'lg-select-wrapper';

    // Adapt wrapper sizing
    const isWFull = select.classList.contains('w-full') ||
      select.style.width === '100%' ||
      select.getAttribute('style')?.includes('width:100%') ||
      select.getAttribute('style')?.includes('width: 100%') ||
      Boolean(select.closest('.form-group, .field, .audit-control-group, .health-filters, .optics-filters, .rack-controls'));

    if (isWFull) {
      wrapper.classList.add('w-full');
    }
    if (select.classList.contains('site-select') || select.classList.contains('mobile-site-select') || select.closest('.site-switcher-pill, .mobile-site-box')) {
      wrapper.classList.add('site-select-wrapper');
    }
    if (select.disabled) {
      wrapper.classList.add('disabled');
    }

    // Position wrapper next to select and visually hide native select
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    select.style.position = 'absolute';
    select.style.opacity = '0';
    select.style.pointerEvents = 'none';
    select.style.width = '0';
    select.style.height = '0';
    select.style.margin = '0';
    select.style.padding = '0';
    select.setAttribute('tabindex', '-1');

    // Trigger button
    const trigger = document.createElement('div');
    trigger.className = 'lg-select-trigger';
    if (select.classList.contains('input-lg')) {
      trigger.classList.add('input-lg');
    } else if (select.classList.contains('input')) {
      trigger.classList.add('input');
    }
    if (select.classList.contains('site-select') || select.classList.contains('mobile-site-select') || select.closest('.site-switcher-pill, .mobile-site-box')) {
      trigger.classList.add('site-select-trigger');
    }
    trigger.setAttribute('tabindex', '0');
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-expanded', 'false');

    const labelSpan = document.createElement('span');
    labelSpan.className = 'lg-select-label-text';
    const initialOpt = select.options[select.selectedIndex] || select.options[0];
    labelSpan.textContent = initialOpt ? initialOpt.textContent : '';

    const chevronSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevronSvg.setAttribute('class', 'lg-select-chevron');
    chevronSvg.setAttribute('viewBox', '0 0 24 24');
    chevronSvg.setAttribute('fill', 'none');
    chevronSvg.setAttribute('stroke', 'currentColor');
    chevronSvg.setAttribute('stroke-width', '2.5');
    chevronSvg.setAttribute('stroke-linecap', 'round');
    chevronSvg.setAttribute('stroke-linejoin', 'round');
    const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    polyline.setAttribute('points', '6 9 12 15 18 9');
    chevronSvg.appendChild(polyline);

    trigger.appendChild(labelSpan);
    trigger.appendChild(chevronSvg);
    wrapper.appendChild(trigger);

    // Floating Popover (appended directly to document.body so it NEVER renders behind headers or tables)
    const popover = document.createElement('div');
    popover.className = 'lg-select-popover';
    popover.style.display = 'none';

    const optionsList = document.createElement('div');
    optionsList.className = 'lg-select-options';

    let searchInput = null;
    let optionElements = [];

    function attachSearchEvents() {
      if (!searchInput) return;
      searchInput.addEventListener('input', () => {
        const query = searchInput.value.toLowerCase().trim();
        optionElements.forEach((el, i) => {
          const opt = select.options[i];
          const isHidden = opt ? (opt.hidden || opt.style.display === 'none') : false;
          const match = el.textContent.toLowerCase().includes(query);
          el.style.display = match && !isHidden ? '' : 'none';
        });
      });
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          closePopover();
          trigger.focus();
        } else if (e.key === 'Enter') {
          const firstVisible = optionElements.find(el => el.style.display !== 'none');
          if (firstVisible) firstVisible.click();
        }
      });
    }

    function rebuildOptions() {
      optionsList.innerHTML = '';
      optionElements = [];

      const needsSearch = select.options.length > 6;
      let searchBox = popover.querySelector('.lg-select-search');
      if (needsSearch && !searchBox) {
        searchBox = document.createElement('div');
        searchBox.className = 'lg-select-search';
        searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.placeholder = 'Search options…';
        searchInput.setAttribute('autocomplete', 'off');
        searchBox.appendChild(searchInput);
        popover.insertBefore(searchBox, optionsList);
        attachSearchEvents();
      } else if (!needsSearch && searchBox) {
        searchBox.remove();
        searchInput = null;
      }

      Array.from(select.options).forEach((opt, idx) => {
        const optEl = document.createElement('div');
        optEl.className = 'lg-select-option' + (opt.selected ? ' selected' : '');
        optEl.textContent = opt.textContent;
        optEl.dataset.value = opt.value;
        optEl.dataset.index = idx;
        if (opt.hidden || opt.style.display === 'none') {
          optEl.style.display = 'none';
        }

        optEl.addEventListener('click', (e) => {
          e.stopPropagation();
          select.value = opt.value;
          labelSpan.textContent = opt.textContent;
          optionElements.forEach(el => el.classList.remove('selected'));
          optEl.classList.add('selected');
          closePopover();
          select.dispatchEvent(new Event('change', { bubbles: true }));
        });

        optionsList.appendChild(optEl);
        optionElements.push(optEl);
      });

      const selOpt = select.options[select.selectedIndex];
      labelSpan.textContent = selOpt ? selOpt.textContent : (select.options[0] ? select.options[0].textContent : '');
    }

    popover.appendChild(optionsList);
    rebuildOptions();

    function positionPopover() {
      const rect = trigger.getBoundingClientRect();
      const popoverWidth = Math.max(rect.width, 180);
      let left = Math.max(12, Math.min(rect.left, window.innerWidth - popoverWidth - 12));
      let top = rect.bottom + 6;
      const spaceBelow = window.innerHeight - rect.bottom;
      const popoverHeight = popover.offsetHeight || 260;
      if (spaceBelow < Math.min(popoverHeight + 10, 260) && rect.top > 260) {
        top = Math.max(12, rect.top - popoverHeight - 6);
      }
      popover.style.top = top + 'px';
      popover.style.left = left + 'px';
      popover.style.minWidth = popoverWidth + 'px';
      popover.style.maxWidth = Math.max(popoverWidth, 480) + 'px';
    }

    function openPopover() {
      if (select.disabled) return;
      closeAllLiquidPopovers();

      if (select.options.length !== optionElements.length) {
        rebuildOptions();
      }

      wrapper.classList.add('open');
      trigger.setAttribute('aria-expanded', 'true');

      document.body.appendChild(popover);
      popover.style.display = 'flex';

      // Sync active selected state & hidden options
      const curOpt = select.options[select.selectedIndex];
      if (curOpt) {
        labelSpan.textContent = curOpt.textContent;
      }
      let visibleCount = 0;
      optionElements.forEach((el, i) => {
        const opt = select.options[i];
        if (!opt) return;
        const isHidden = opt.hidden || opt.style.display === 'none';
        el.style.display = isHidden ? 'none' : '';
        el.classList.toggle('selected', opt.value === select.value || opt.selected);
        if (!isHidden) visibleCount++;
      });

      positionPopover();

      activeLiquidSelect = {
        wrapper,
        trigger,
        popover,
        positionPopover,
        close: closePopover,
      };

      if (searchInput) {
        searchInput.value = '';
        const searchBox = popover.querySelector('.lg-select-search');
        if (searchBox) searchBox.style.display = visibleCount > 6 ? '' : 'none';
        setTimeout(() => searchInput.focus(), 50);
      }
    }

    function closePopover() {
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      popover.style.display = 'none';
      if (popover.parentNode === document.body) {
        document.body.removeChild(popover);
      }
      if (activeLiquidSelect && activeLiquidSelect.wrapper === wrapper) {
        activeLiquidSelect = null;
      }
    }

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      if (wrapper.classList.contains('open')) {
        closePopover();
      } else {
        openPopover();
      }
    });

    trigger.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault();
        openPopover();
      } else if (e.key === 'Escape') {
        closePopover();
      }
    });

    select.addEventListener('change', () => {
      const selOpt = select.options[select.selectedIndex];
      if (selOpt) {
        labelSpan.textContent = selOpt.textContent;
        optionElements.forEach(el => {
          el.classList.toggle('selected', el.dataset.value === selOpt.value);
        });
      }
    });

    // Observe changes to select options and disabled state
    if (window.MutationObserver) {
      const observer = new MutationObserver(() => {
        wrapper.classList.toggle('disabled', select.disabled);
        rebuildOptions();
      });
      observer.observe(select, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['hidden', 'disabled', 'selected', 'class']
      });
    }
  });
}

/* ── ObaidQatan Liquid Glass Rounded Calendar Datepicker ─────────────────── */
const CAL_MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
];
const CAL_WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function parseISODate(str) {
  if (!str) return null;
  const parts = str.split('-');
  if (parts.length === 3) {
    const y = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10) - 1;
    const d = parseInt(parts[2], 10);
    if (!isNaN(y) && !isNaN(m) && !isNaN(d)) {
      return new Date(y, m, d);
    }
  }
  return null;
}

function formatISODate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function initLiquidDatepickers() {
  const dateInputs = document.querySelectorAll('input[type="date"]');
  dateInputs.forEach(input => {
    if (input.dataset.lgDateEnhanced) return;
    input.dataset.lgDateEnhanced = 'true';

    // Change to type="text" to completely prevent the native square OS picker
    input.type = 'text';
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('placeholder', 'YYYY-MM-DD');
    input.classList.add('lg-date-input');

    // Create wrapper
    const wrapper = document.createElement('div');
    wrapper.className = 'lg-date-wrapper';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    // Create calendar trigger button
    const calBtn = document.createElement('button');
    calBtn.type = 'button';
    calBtn.className = 'lg-date-btn';
    calBtn.setAttribute('title', 'Open Calendar');
    calBtn.setAttribute('tabindex', '-1');
    calBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="3" ry="3"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>`;
    wrapper.appendChild(calBtn);

    // Create floating calendar popover
    const calPopover = document.createElement('div');
    calPopover.className = 'lg-calendar-popover';
    calPopover.style.display = 'none';

    let viewDate = parseISODate(input.value) || new Date();

    function renderCalendar() {
      calPopover.innerHTML = '';

      const year = viewDate.getFullYear();
      const month = viewDate.getMonth();
      const today = new Date();
      const selectedDate = parseISODate(input.value);

      // Header (Prev, Title, Next)
      const header = document.createElement('div');
      header.className = 'lg-cal-header';

      const prevBtn = document.createElement('button');
      prevBtn.type = 'button';
      prevBtn.className = 'lg-cal-nav-btn lg-cal-prev';
      prevBtn.setAttribute('title', 'Previous Month');
      prevBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>`;
      prevBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        viewDate = new Date(year, month - 1, 1);
        renderCalendar();
      });

      const title = document.createElement('span');
      title.className = 'lg-cal-month-year';
      title.textContent = `${CAL_MONTHS[month]} ${year}`;

      const nextBtn = document.createElement('button');
      nextBtn.type = 'button';
      nextBtn.className = 'lg-cal-nav-btn lg-cal-next';
      nextBtn.setAttribute('title', 'Next Month');
      nextBtn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>`;
      nextBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        viewDate = new Date(year, month + 1, 1);
        renderCalendar();
      });

      header.appendChild(prevBtn);
      header.appendChild(title);
      header.appendChild(nextBtn);
      calPopover.appendChild(header);

      // Weekday Row
      const weekdays = document.createElement('div');
      weekdays.className = 'lg-cal-weekdays';
      CAL_WEEKDAYS.forEach(dayName => {
        const s = document.createElement('span');
        s.textContent = dayName;
        weekdays.appendChild(s);
      });
      calPopover.appendChild(weekdays);

      // Days Grid
      const daysGrid = document.createElement('div');
      daysGrid.className = 'lg-cal-days';

      const firstDay = new Date(year, month, 1).getDay();
      const totalDays = new Date(year, month + 1, 0).getDate();

      // Empty lead days
      for (let i = 0; i < firstDay; i++) {
        const emptyCell = document.createElement('div');
        emptyCell.className = 'lg-cal-day empty';
        daysGrid.appendChild(emptyCell);
      }

      // Day buttons
      for (let dayNum = 1; dayNum <= totalDays; dayNum++) {
        const dayBtn = document.createElement('button');
        dayBtn.type = 'button';
        dayBtn.className = 'lg-cal-day';
        dayBtn.textContent = dayNum;

        const cellDate = new Date(year, month, dayNum);

        // Check today
        if (
          cellDate.getDate() === today.getDate() &&
          cellDate.getMonth() === today.getMonth() &&
          cellDate.getFullYear() === today.getFullYear()
        ) {
          dayBtn.classList.add('today');
        }

        // Check selected
        if (
          selectedDate &&
          cellDate.getDate() === selectedDate.getDate() &&
          cellDate.getMonth() === selectedDate.getMonth() &&
          cellDate.getFullYear() === selectedDate.getFullYear()
        ) {
          dayBtn.classList.add('selected');
        }

        dayBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          input.value = formatISODate(cellDate);
          closeCalendar();
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });

        daysGrid.appendChild(dayBtn);
      }

      calPopover.appendChild(daysGrid);

      // Footer
      const footer = document.createElement('div');
      footer.className = 'lg-cal-footer';

      const clearBtn = document.createElement('button');
      clearBtn.type = 'button';
      clearBtn.className = 'lg-cal-foot-btn lg-cal-clear';
      clearBtn.textContent = 'Clear';
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        input.value = '';
        closeCalendar();
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });

      const todayBtn = document.createElement('button');
      todayBtn.type = 'button';
      todayBtn.className = 'lg-cal-foot-btn lg-cal-today';
      todayBtn.textContent = 'Today';
      todayBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        input.value = formatISODate(today);
        closeCalendar();
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });

      footer.appendChild(clearBtn);
      footer.appendChild(todayBtn);
      calPopover.appendChild(footer);
    }

    function positionCalendar() {
      const rect = input.getBoundingClientRect();
      const calWidth = 290;
      let left = rect.left;
      if (left + calWidth > window.innerWidth - 12) {
        left = Math.max(12, window.innerWidth - calWidth - 12);
      }
      let top = rect.bottom + 6;
      if (window.innerHeight - rect.bottom < 320 && rect.top > 320) {
        top = Math.max(12, rect.top - calPopover.offsetHeight - 6);
      }
      calPopover.style.top = top + 'px';
      calPopover.style.left = left + 'px';
    }

    function openCalendar() {
      closeAllLiquidPopovers();

      viewDate = parseISODate(input.value) || new Date();
      renderCalendar();

      document.body.appendChild(calPopover);
      calPopover.style.display = 'flex';
      positionCalendar();

      activeLiquidCalendar = {
        input,
        popover: calPopover,
        positionCalendar,
        close: closeCalendar,
      };
    }

    function closeCalendar() {
      calPopover.style.display = 'none';
      if (calPopover.parentNode === document.body) {
        document.body.removeChild(calPopover);
      }
      if (activeLiquidCalendar && activeLiquidCalendar.input === input) {
        activeLiquidCalendar = null;
      }
    }

    input.addEventListener('click', (e) => {
      e.stopPropagation();
      openCalendar();
    });

    calBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openCalendar();
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeCalendar();
      }
    });
  });
}

// Global outside click handler
document.addEventListener('click', (e) => {
  const isSelect = e.target.closest('.lg-select-wrapper') || e.target.closest('.lg-select-popover');
  const isCalendar = e.target.closest('.lg-date-wrapper') || e.target.closest('.lg-calendar-popover');
  if (!isSelect && !isCalendar) {
    closeAllLiquidPopovers();
  }
});

// Global Escape handler
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeAllLiquidPopovers();
  }
});

// Reposition floating popovers on scroll or window resize - at most once per frame, so a
// fast scroll doesn't queue dozens of layout reads and the popover tracks without lag.
let _popoverRaf = 0;
function _schedulePopoverReposition() {
  if (_popoverRaf || (!activeLiquidSelect && !activeLiquidCalendar)) return;
  _popoverRaf = requestAnimationFrame(() => {
    _popoverRaf = 0;
    if (activeLiquidSelect) activeLiquidSelect.positionPopover();
    if (activeLiquidCalendar) activeLiquidCalendar.positionCalendar();
  });
}
window.addEventListener('scroll', _schedulePopoverReposition, { passive: true });
window.addEventListener('resize', _schedulePopoverReposition, { passive: true });

// Initialize on DOM load
function initLiquidComponents() {
  initLiquidSelects();
  initLiquidDatepickers();

  // Auto-enhance dynamically added selects and datepickers (e.g. modals, dynamically rendered tabs)
  if (!window._lgObserverInstalled && window.MutationObserver && document.body) {
    window._lgObserverInstalled = true;
    const bodyObserver = new MutationObserver((mutations) => {
      let needsSelectInit = false;
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1) {
            if (node.matches?.('select:not([multiple]):not(.no-liquid)') || node.querySelector?.('select:not([multiple]):not(.no-liquid)')) {
              needsSelectInit = true;
              break;
            }
          }
        }
        if (needsSelectInit) break;
      }
      if (needsSelectInit) {
        initLiquidSelects();
      }
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initLiquidComponents);
} else {
  initLiquidComponents();
}




