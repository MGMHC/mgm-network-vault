/* Working orb for long-running operations (Hardware Health, Optical Health, Port Audit).
 *
 * The scan streams can be held back by a reverse proxy, so the log panel may stay empty
 * until the very end. While an operation runs, this draws a slowly turning dotted globe
 * with a sweeping scan line: large in the middle of the empty log panel, then small in
 * the panel header once real log lines arrive. It never claims progress it does not
 * know; the caption only names the stages every scan goes through, plus elapsed time.
 *
 * Opt in per modal: <div class="op-modal-backdrop" data-orb> ... </div>
 * "Running" = the modal is open and its primary footer button (Done / Close & View
 * Results) is still disabled. Draws nothing while hidden, minimised or finished. When the
 * viewer prefers reduced motion (Windows with animations off reports this) it still turns,
 * at half speed: it is the only sign the scan is alive. Styles live here, not in app.css,
 * so a cached stylesheet can never leave the orb unstyled.
 */
(function () {
  "use strict";

  var CAPTIONS = [
    "Connecting to switches",
    "Running read-only show commands",
    "Reading the replies",
    "Putting the results together"
  ];
  var CAPTION_MS = 2600;
  var CHECK_MS = 250;
  var DOTS_BIG = 260;
  var DOTS_SMALL = 70;
  var SIZE_BIG = 132;
  var SIZE_SMALL = 18;
  var MAX_DPR = 2;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SPEED = reduceMotion ? 0.5 : 1;

  var CSS =
    ".op-terminal{position:relative}" +
    ".op-orb-overlay{position:absolute;left:0;right:0;bottom:0;top:34px;display:flex;flex-direction:column;" +
    "align-items:center;justify-content:center;gap:10px;pointer-events:none;opacity:0;visibility:hidden;" +
    "transition:opacity .35s ease,visibility .35s}" +
    ".op-orb-overlay.on{opacity:1;visibility:visible}" +
    ".op-orb-overlay canvas{display:block}" +
    ".op-orb-caption{font-size:13px;font-weight:600;color:var(--text);min-height:1.3em;text-align:center}" +
    ".op-orb-elapsed{font-size:11px;color:var(--muted)}" +
    ".op-orb-mini{display:none;vertical-align:middle}" +
    ".op-orb-mini.on{display:inline-block}" +
    ".op-orb-mini.on+span{display:none}";

  function addStyles() {
    if (document.getElementById("op-orb-styles")) return;
    var el = document.createElement("style");
    el.id = "op-orb-styles";
    el.textContent = CSS;
    document.head.appendChild(el);
  }

  function sphere(n) {
    // Even spread of points over a sphere (golden-angle spiral).
    var pts = [], golden = Math.PI * (3 - Math.sqrt(5));
    for (var i = 0; i < n; i++) {
      var y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), a = golden * i;
      pts.push([Math.cos(a) * r, y, Math.sin(a) * r]);
    }
    return pts;
  }

  function cssColor(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function Orb(canvas, size, dots) {
    this.canvas = canvas;
    this.size = size;
    this.pts = sphere(dots);
    this.dot = size >= 64 ? 1.7 : 0.9;
    var dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    canvas.style.width = size + "px";
    canvas.style.height = size + "px";
    this.ctx = canvas.getContext("2d");
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  Orb.prototype.draw = function (t, colors) {
    var ctx = this.ctx, s = this.size, c = s / 2, R = s / 2 - this.dot * 2.5;
    var spin = t * 0.45, tilt = 0.38;
    var cs = Math.cos(spin), sn = Math.sin(spin), ct = Math.cos(tilt), st = Math.sin(tilt);
    var sweep = Math.sin(t * 1.25) * 0.95;          // scan line drifting left and right
    ctx.clearRect(0, 0, s, s);
    for (var i = 0; i < this.pts.length; i++) {
      var p = this.pts[i];
      var x = p[0] * cs + p[2] * sn, z = -p[0] * sn + p[2] * cs;   // spin about Y
      var y = p[1] * ct - z * st; z = p[1] * st + z * ct;            // tilt about X
      var depth = (z + 1) / 2;                                       // 0 back .. 1 front
      var hit = depth > 0.45 ? Math.exp(-Math.pow((x - sweep) / 0.13, 2)) : 0;
      ctx.globalAlpha = Math.min(1, 0.1 + 0.75 * Math.pow(depth, 1.6) + hit * 0.7);
      ctx.fillStyle = hit > 0.35 ? colors.accent : colors.dot;
      ctx.beginPath();
      ctx.arc(c + x * R, c + y * R, this.dot * (0.55 + 0.6 * depth + hit * 0.7), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  };

  function fmtElapsed(ms) {
    var sec = Math.floor(ms / 1000);
    return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2);
  }

  function attach(backdrop) {
    var terminal = backdrop.querySelector(".op-terminal");
    var logs = terminal && terminal.querySelector(".op-terminal-logs");
    var header = terminal && terminal.querySelector(".op-terminal-header");
    var doneBtn = backdrop.querySelector(".op-modal-footer .btn.primary");
    if (!terminal || !logs || !header || !doneBtn) return;

    var overlay = document.createElement("div");
    overlay.className = "op-orb-overlay";
    overlay.setAttribute("role", "status");
    overlay.innerHTML = '<canvas aria-hidden="true"></canvas>' +
      '<div class="op-orb-caption"></div><div class="op-orb-elapsed mono"></div>';
    terminal.appendChild(overlay);
    var small = document.createElement("canvas");
    small.className = "op-orb-mini";
    small.setAttribute("aria-hidden", "true");
    header.firstElementChild ? header.firstElementChild.insertBefore(small, header.firstElementChild.firstChild)
                             : header.insertBefore(small, header.firstChild);

    var big = new Orb(overlay.querySelector("canvas"), SIZE_BIG, DOTS_BIG);
    var mini = new Orb(small, SIZE_SMALL, DOTS_SMALL);
    var caption = overlay.querySelector(".op-orb-caption");
    var elapsed = overlay.querySelector(".op-orb-elapsed");
    var colors = { dot: "#9aa4b2", accent: "#4cc2ff" };
    var startedAt = 0, raf = 0, captionIdx = -1;

    function running() {
      return backdrop.style.display !== "none" && doneBtn.disabled;
    }

    function frame() {
      raf = 0;
      if (!running() || terminal.offsetParent === null) return;
      var t = SPEED * (performance.now() - startedAt) / 1000;
      if (logs.children.length === 0) big.draw(t, colors);
      else mini.draw(t, colors);
      raf = requestAnimationFrame(frame);
    }

    function check() {
      var on = running();
      if (on && !startedAt) {
        startedAt = performance.now();
        captionIdx = -1;
        colors = { dot: cssColor("--muted", colors.dot), accent: cssColor("--accent", colors.accent) };
      }
      if (!on) startedAt = 0;
      var empty = logs.children.length === 0;
      if (on && empty) {
        overlay.style.top = header.offsetHeight + "px";
        var ms = performance.now() - startedAt;
        var idx = Math.floor(ms / CAPTION_MS) % CAPTIONS.length;
        if (idx !== captionIdx) { captionIdx = idx; caption.textContent = CAPTIONS[idx] + "…"; }
        elapsed.textContent = fmtElapsed(ms);
      }
      overlay.classList.toggle("on", on && empty);
      small.classList.toggle("on", on && !empty);
      if (on && !raf) raf = requestAnimationFrame(frame);
    }
    setInterval(check, CHECK_MS);
    check();
  }

  function init() {
    var list = document.querySelectorAll(".op-modal-backdrop[data-orb]");
    if (list.length) addStyles();
    for (var i = 0; i < list.length; i++) attach(list[i]);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
