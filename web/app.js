/* SUMIPDF front-end controller.
 *
 * The pure state/geometry helpers at the top are DOM-free and exported for
 * tests (tests/ui_state.test.cjs). All DOM wiring lives in boot(), which runs
 * only in a real browser — requiring this file under node leaves the DOM
 * untouched. Backend contract (frozen):
 *   GET  /assets/web/<file>        static web assets
 *   GET  /assets/icon.png          app icon
 *   POST /api/dialog/open  {}          -> {path} | 501
 *   POST /api/dialog/save  {suggested} -> {path} | 501   (desktop only)
 *   POST /api/upload?filename=X  (PDF bytes) -> open shape + uploaded:true
 *   POST /api/save {path|null}    -> {saved,bytes,stat}  (server switches doc)
 * plus the existing edit/ocr/pages/export endpoints.
 */
'use strict';

/* ================= pure helpers (unit-tested) ================= */

function clampPage(pno, pages) {
  pages = Math.max(0, pages | 0);
  if (pages <= 0) return 0;
  pno = pno | 0;
  return Math.min(Math.max(0, pno), pages - 1);
}

function clampDpi(dpi) {
  const n = Number(dpi);
  if (!isFinite(n) || n <= 0) return 120;
  return Math.max(30, Math.min(400, Math.round(n)));
}

function computeScale(o) {
  const vw = +o.viewW, vh = +o.viewH, pw = +o.pageW, ph = +o.pageH;
  if (o.fit === 'custom') return (+o.zoom > 0) ? +o.zoom : 1;
  if (!(vw > 0 && vh > 0 && pw > 0 && ph > 0)) return 1;
  if (o.fit === 'width') return vw / pw;
  return Math.min(vw / pw, vh / ph); // 'page'
}

function fmtZoom(scale) {
  const n = +scale;
  if (!isFinite(n) || n <= 0) return '100%';
  return Math.round(n * 100) + '%';
}

function viewRectToPdf(o) {
  const scale = +o.scale;
  if (!(scale > 0)) return { ok: false, rect: null };
  const r = o.rect;
  const x0 = Math.min(r[0], r[2]) / scale;
  const x1 = Math.max(r[0], r[2]) / scale;
  const vy0 = Math.min(r[1], r[3]) / scale;
  const vy1 = Math.max(r[1], r[3]) / scale;
  const H = +o.pageH || 0;
  // view y grows downward, PDF y grows upward: flip.
  return { ok: true, rect: [x0, H - vy1, x1, H - vy0] };
}

function pdfRectToView(o) {
  const scale = +o.scale;
  if (!(scale > 0)) return { ok: false, rect: null };
  const r = o.rect;
  const x0 = Math.min(r[0], r[2]) * scale;
  const x1 = Math.max(r[0], r[2]) * scale;
  const H = +o.pageH || 0;
  // inverse flip: view_y = (pageH - pdf_y) * scale, y-down and normalized.
  return { ok: true, rect: [x0, (H - Math.max(r[1], r[3])) * scale, x1, (H - Math.min(r[1], r[3])) * scale] };
}

function rectTooSmall(r, min) {
  if (!r) return true;
  return Math.abs(r[2] - r[0]) < min || Math.abs(r[3] - r[1]) < min;
}

function basename(p) {
  if (!p) return '';
  const s = String(p).replace(/[\\/]+$/, '');
  const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
  return i >= 0 ? s.slice(i + 1) : s;
}

function sanitizeFilename(name) {
  let s = basename(String(name == null ? '' : name)).trim();
  if (!s) s = 'output.pdf';
  if (!/\.pdf$/i.test(s)) s += '.pdf';
  return s;
}

function pick(o, a, b) { if (b && (b in o)) return o[b]; return o[a]; }

function allowedKeys(cfg) {
  cfg = cfg || {};
  return {
    print_: !!pick(cfg, 'print', 'print_'),
    copy: !!pick(cfg, 'copy'),
    modify: !!pick(cfg, 'modify'),
    annotate: !!pick(cfg, 'annotate'),
    forms: !!pick(cfg, 'forms'),
    assemble: !!pick(cfg, 'assemble'),
    print_high: !!pick(cfg, 'print_high'),
  };
}

function canDeletePage(pages) { return Math.max(0, pages | 0) > 1; }

function formatApiError(err) {
  if (!err) return '不明なエラー';
  if (err.detail) return String(err.detail);
  if (err.message) return String(err.message);
  if (err.status) return 'HTTP ' + err.status;
  return String(err);
}

function nextDpiForScale(scale, dpr) {
  return clampDpi((+scale || 0) * 72 * (+dpr || 1));
}

function ocrLayerSummary(j) {
  j = j || {};
  const w = Array.isArray(j.words) ? j.words.length : j.words;
  return '検索用テキストを追加 (' + (w || 0) + '語, lang=' + (j.lang || 'auto') + ')。保存で確定';
}

function replaceSummary(j) {
  j = j || {};
  return (j.replaced || 0) + '件置換（対象外 ' + (j.dropped || 0) + '）';
}

function redactFill(kind) {
  if (kind === 'black') return { redact: [0, 0, 0], image: [0, 0, 0], matchBg: false };
  if (kind === 'background') return { redact: [1, 1, 1], image: [255, 255, 255], matchBg: true };
  return { redact: [1, 1, 1], image: [255, 255, 255], matchBg: false }; // white
}

// Every API call of one redact run is built here from ONE pinned page number,
// so a page switch mid-pipeline can never send the calls to different pages.
// text_image goes as ONE /api/redact request with img:true — the server applies
// text then image on the same work bytes or nothing at all (atomic, Task 6).
function redactCallSpecs(pno, rect, target, fill) {
  if (target === 'image') {
    return [{ kind: 'image', path: '/api/img-redact', body: { page: pno, rects: [rect], fill: fill.image } }];
  }
  const body = { page: pno, rects: [rect], match_bg: fill.matchBg, fill: fill.redact };
  if (target !== 'text') { body.img = true; body.img_fill = fill.image; }
  return [{ kind: target === 'text' ? 'redact' : 'both', path: '/api/redact', body }];
}

// Task 6 guard: redaction coordinates are unsafe not only after in-session
// rotation (state.rot) but also on pages with native /Rotate or a CropBox whose
// origin is not (0,0) — the visible origin differs from edit.py's assumptions.
function pageGuardBlock(pno, rotMap, pagesMeta) {
  if (((rotMap && rotMap[pno]) || 0) % 360 !== 0) return true;
  const m = (pagesMeta || [])[pno];
  if (!m) return false;
  if ((+m.rot || 0) % 360 !== 0) return true;
  return Math.abs(+m.crop_x0 || 0) > 1e-6 || Math.abs(+m.crop_y0 || 0) > 1e-6;
}

// Task 6: keep state.rot page numbers aligned after structural page changes
// (delete shifts later pages down, insert pushes them up).
function remapRotAfterDelete(rotMap, deletedPno) {
  const out = {};
  Object.keys(rotMap || {}).forEach((k) => {
    const i = +k;
    if (i === deletedPno) return;
    out[i > deletedPno ? i - 1 : i] = rotMap[k];
  });
  return out;
}
function remapRotAfterInsert(rotMap, at, count) {
  const out = {};
  Object.keys(rotMap || {}).forEach((k) => {
    const i = +k;
    out[(at != null && at >= 0 && i >= at) ? i + count : i] = rotMap[k];
  });
  return out;
}

// confirmDiscard's 3-way guard buttons: 中止 / 破棄して<what> / 保存してから<what>.
// Pure so the labels and order are unit-testable (ui_state.test.cjs).
function discardChoices(what) {
  return [
    { id: 'abort', label: '中止' },
    { id: 'discard', label: '破棄して' + what },
    { id: 'save', label: '保存してから' + what, primary: true },
  ];
}

// Does the follow-up action proceed after the user picked `choice`?
// 'save' proceeds only when the save actually succeeded; 中止/Escape never proceeds.
function discardProceeds(choice, saveOk) {
  if (choice === 'save') return !!saveOk;
  return choice === 'discard';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    clampPage, clampDpi, computeScale, fmtZoom, viewRectToPdf, pdfRectToView, rectTooSmall,
    basename, sanitizeFilename, allowedKeys, canDeletePage, formatApiError,
    nextDpiForScale, ocrLayerSummary, replaceSummary, redactFill, redactCallSpecs,
    pageGuardBlock, remapRotAfterDelete, remapRotAfterInsert,
    discardChoices, discardProceeds,
  };
}

/* ================= browser controller ================= */

function boot() {
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  const state = {
    path: null, name: '', pages: 0, pno: 0, dirty: false,
    dpi: 120, fit: 'page', zoom: 1,
    busy: false, stat: null, lastStat: null, dirtyNotified: false,
    size: [0, 0], fonts: [],
    sel: null,                       // [x0,y0,x1,y1] in PDF pt (y-up), page-local — zoom-invariant
    rot: {},                         // page -> applied rotation (UI-tracked)
    pages_meta: [],                  // per-page native {rot,crop_x0,crop_y0} (guard, Task 6)
    view: { bmpW: 0, bmpH: 0, ptW: 0, ptH: 0, renderDpi: 120, token: 0 },
    lastSelPage: null,
    desktop: null,                   // null=unknown, true/false once probed
  };

  const el = {
    cv: $('#cv'), overlay: $('#overlay'), pagehost: $('#pagehost'), wrap: $('#canvaswrap'),
    docName: $('#docName'), dirtyDot: $('#dirtyDot'),
    pageLabel: $('#pageLabel'), zoomLabel: $('#zoomLabel'),
    thumbList: $('#thumbList'), fontlist: $('#fontlist'), empty: $('#emptyHint'),
    msg: $('#statusMsg'), hint: $('#statusHint'), busyDot: $('#busyDot'),
    pagesPanel: $('#pagesPanel'), settingsPanel: $('#settingsPanel'),
  };
  const ctx = el.cv.getContext('2d');
  const octx = el.overlay.getContext('2d');
  const DPR = Math.max(1, Math.min(3, window.devicePixelRatio || 1));

  /* ---------- messages & busy ---------- */
  function setMsg(text, kind) {
    el.msg.textContent = text || '';
    el.msg.className = kind || '';
  }
  function setBusy(on) {
    state.busy = !!on;
    el.busyDot.classList.toggle('on', state.busy);
    $$('[data-busy]').forEach((b) => { b.disabled = state.busy || b.dataset.disabledNoDoc === '1'; });
    applyNoDocState();
  }
  function applyNoDocState() {
    const has = state.pages > 0 && !state.busy;
    $$('[data-needs-doc]').forEach((b) => { b.disabled = !has; });
  }

  /* ---------- request helper (single body read) ---------- */
  async function api(path, opts) {
    opts = opts || {};
    const init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    if (opts.raw !== undefined) { init.body = opts.raw; }
    const r = await fetch(path, init);
    const ct = r.headers.get('content-type') || '';
    let data = null, text = '';
    if (ct.indexOf('application/json') >= 0) { try { data = await r.json(); } catch (e) { data = null; } }
    else { try { text = await r.text(); } catch (e) { text = ''; } }
    if (!r.ok) {
      const err = new Error((data && data.detail) || text || ('HTTP ' + r.status));
      err.status = r.status; err.data = data; err.detail = data && data.detail;
      throw err;
    }
    return data !== null ? data : text;
  }
  async function run(label, fn) {
    if (state.busy) return null;
    setBusy(true);
    try {
      const out = await fn();
      return out;
    } catch (e) {
      setMsg('✗ ' + label + ': ' + formatApiError(e), 'err');
      return null;
    } finally {
      setBusy(false);
    }
  }

  /* ---------- document lifecycle ---------- */
  function applyOpen(j, opts) {
    opts = opts || {};
    state.path = j.path || null;
    state.name = basename(state.path) || (opts.uploaded ? 'アップロード.pdf' : '');
    state.pages = j.pages | 0;
    state.pno = 0;
    state.dirty = false;
    state.dirtyNotified = false;
    state.stat = j.stat || null;
    state.lastStat = j.stat || null;
    state.size = j.size || [0, 0];
    state.fonts = j.fonts || [];
    state.rot = {};
    state.pages_meta = Array.isArray(j.pages_meta) ? j.pages_meta : [];
    clearSelection(false);
    renderFonts();
    el.docName.textContent = state.name || '無題';
    el.docName.classList.toggle('has-doc', !!state.name);
    el.docName.title = state.path || '';
    updateDirty();
    buildThumbs();
    el.empty.hidden = true;
    render();
    setMsg('開きました: ' + (state.name || ''), 'ok');
  }

  async function openPath(path, password) {
    const j = await run('開く', () => api('/api/open', { method: 'POST', body: { path: path, password: password || null } }));
    if (j) applyOpen(j);
    return j;
  }

  function looksLikeDesktopMiss(e) { return e && (e.status === 501 || e.status === 404 || e.status === 405); }

  async function openFlow() {
    if (state.busy) return;
    if (state.dirty && !await confirmDiscard('別の文書を開く')) return;
    if (state.desktop === false) return openFallback();
    // probe/try native dialog;
    setBusy(true);
    let j = null;
    try {
      j = await api('/api/dialog/open', { method: 'POST', body: {} });
      state.desktop = true;
    } catch (e) {
      if (looksLikeDesktopMiss(e)) { state.desktop = false; setBusy(false); return openFallback(); }
      setBusy(false); setMsg('✗ 開く: ' + formatApiError(e), 'err'); return;
    }
    setBusy(false);
    if (!j || !j.path) { setMsg('開くのをキャンセルしました'); return; }
    await openPath(j.path);
  }

  function openFallback() {
    const m = $('#openModal');
    m.hidden = false;
    $('#openPath').value = '';
    setTimeout(() => $('#openPath').focus(), 0);
  }

  async function doFallbackOpen() {
    const path = $('#openPath').value.trim();
    if (!path) { setMsg('パスを入力してください', 'warn'); return; }
    $('#openModal').hidden = true;
    await openPath(path);
  }

  async function doUploadFile(file) {
    if (!file) return;
    const j = await run('アップロード', () => api('/api/upload?filename=' + encodeURIComponent(file.name),
      { method: 'POST', raw: file }));
    if (j) applyOpen(j, { uploaded: true });
  }

  async function saveFlow() {
    if (!state.pages || state.busy) return false;
    const j = await run('保存', () => api('/api/save', { method: 'POST', body: { path: null } }));
    if (!j) return false;
    if (j.cancelled) { setMsg('保存をキャンセルしました'); return false; } // upload doc: dialog declined
    afterSave(j); setMsg('保存しました: ' + basename(j.saved), 'ok');
    return true;
  }

  async function saveAsFlow() {
    if (!state.pages || state.busy) return;
    const suggested = sanitizeFilename(state.name || 'output.pdf');
    if (state.desktop === false) return saveAsFallback(suggested);
    setBusy(true);
    let pick = null;
    try {
      pick = await api('/api/dialog/save', { method: 'POST', body: { suggested: suggested } });
      state.desktop = true;
    } catch (e) {
      if (looksLikeDesktopMiss(e)) { state.desktop = false; setBusy(false); return saveAsFallback(suggested); }
      setBusy(false); setMsg('✗ 別名保存: ' + formatApiError(e), 'err'); return;
    }
    setBusy(false);
    if (!pick || !pick.path) { setMsg('別名保存をキャンセルしました'); return; } // cancel: doc kept, dirty kept
    const j = await run('別名保存', () => api('/api/save', { method: 'POST', body: { path: pick.path } }));
    if (j) { afterSave(j); setMsg('別名保存しました: ' + basename(j.saved), 'ok'); }
  }

  function saveAsFallback(suggested) {
    const name = window.prompt('保存先ファイル名（ブラウザ表示はパス選択不可）', suggested);
    if (!name) { setMsg('別名保存をキャンセルしました'); return; }
    const target = sanitizeFilename(name);
    run('別名保存', () => api('/api/save', { method: 'POST', body: { path: target } }))
      .then((j) => { if (j) { afterSave(j); setMsg('別名保存しました: ' + basename(j.saved), 'ok'); } });
  }

  function afterSave(j) {
    // server switched the live document: adopt the new path / stat / dirty
    if (j.saved) { state.path = j.saved; state.name = basename(j.saved); }
    if (j.stat) { state.stat = j.stat; state.lastStat = j.stat; }
    state.dirty = false;
    state.dirtyNotified = false;
    el.docName.textContent = state.name || '無題';
    el.docName.title = state.path || '';
    el.docName.classList.toggle('has-doc', !!state.name);
    updateDirty();
  }

  /* ---------- DOM confirm dialog (native confirm() is unreliable in WebView2) ---------- */
  let confirmResolve = null;
  function confirmOpen() { return !$('#confirmModal').hidden; }
  function settleConfirm(id) {
    $('#confirmModal').hidden = true;
    const r = confirmResolve;
    confirmResolve = null;
    if (r) r(id);
  }
  function askConfirm(opts) {
    if (confirmResolve) settleConfirm(null);        // never stack dialogs
    $('#confirmTitle').textContent = opts.title || '確認';
    const text = $('#confirmText');
    text.textContent = opts.text || '';
    text.hidden = !opts.text;
    const wrap = $('#confirmActions');
    wrap.innerHTML = '';
    (opts.buttons || []).forEach((b) => {
      const btn = document.createElement('button');
      btn.textContent = b.label;
      if (b.primary) btn.className = 'primary';
      btn.addEventListener('click', () => settleConfirm(b.id));
      wrap.appendChild(btn);
    });
    $('#confirmModal').hidden = false;
    const first = wrap.querySelector('.primary') || wrap.querySelector('button');
    if (first) first.focus();                       // Enter/Space hit the primary
    return new Promise((resolve) => { confirmResolve = resolve; });
  }
  // Generic OK/cancel confirm — drop-in replacement for the native confirm() call sites.
  async function uiConfirm(text, opts) {
    opts = opts || {};
    const id = await askConfirm({
      title: opts.title || '確認',
      text: text,
      buttons: [
        { id: 'cancel', label: opts.cancelLabel || 'キャンセル' },
        { id: 'ok', label: opts.okLabel || 'OK', primary: true },
      ],
    });
    return id === 'ok';
  }

  async function confirmDiscard(what) {
    if (!state.dirty) return true;
    const choice = await askConfirm({
      title: '未保存の変更',
      text: '保存せず続行すると現在の変更は失われます。',
      buttons: discardChoices(what),
    });
    // save first; abort the follow-up when the save failed or was cancelled
    return discardProceeds(choice, choice === 'save' ? await saveFlow() : undefined);
  }

  function updateDirty() {
    el.dirtyDot.classList.toggle('on', state.dirty);
    el.dirtyDot.title = state.dirty ? '未保存の変更' : '';
  }

  /* ---------- rendering ---------- */
  function clearSelection(redraw) {
    state.sel = null;
    state.lastSelPage = null;
    if (redraw !== false) drawOverlay();
    updateSelHint();
  }

  function pagePointSize() {
    const v = state.view;
    if (v.ptW > 0 && v.ptH > 0) return [v.ptW, v.ptH];
    return state.size;
  }

  function currentScale() {
    const [ptW, ptH] = pagePointSize();
    const availW = Math.max(50, el.wrap.clientWidth - 28);
    const availH = Math.max(50, el.wrap.clientHeight - 28);
    return computeScale({ fit: state.fit, viewW: availW, viewH: availH, pageW: ptW, pageH: ptH, zoom: state.zoom });
  }

  function applyLayout() {
    if (!state.view.bmpW) return;
    const scale = currentScale();
    const [ptW, ptH] = pagePointSize();
    const cssW = Math.max(1, Math.round(ptW * scale));
    const cssH = Math.max(1, Math.round(ptH * scale));
    el.cv.style.width = cssW + 'px';
    el.cv.style.height = cssH + 'px';
    el.pagehost.style.width = cssW + 'px';
    el.pagehost.style.height = cssH + 'px';
    el.overlay.width = cssW; el.overlay.height = cssH;
    el.overlay.style.width = cssW + 'px'; el.overlay.style.height = cssH + 'px';
    el.zoomLabel.textContent = fmtZoom(scale);
    drawOverlay();
  }

  async function render() {
    if (!state.pages) { el.empty.hidden = false; return; }
    state.pno = clampPage(state.pno, state.pages);
    const token = ++state.view.token;
    const approxScale = currentScale();
    const dpi = nextDpiForScale(approxScale, DPR);
    state.dpi = dpi;
    let blob;
    try {
      const r = await fetch('/api/page/' + state.pno + '?dpi=' + dpi);
      if (!r.ok) { setMsg('✗ ページ描画: HTTP ' + r.status, 'err'); return; }
      blob = await r.blob();
    } catch (e) { setMsg('✗ ページ描画: ' + formatApiError(e), 'err'); return; }
    if (token !== state.view.token) return; // a newer render superseded this one
    let bmp;
    try { bmp = await createImageBitmap(blob); } catch (e) { setMsg('✗ 画像読込失敗', 'err'); return; }
    if (token !== state.view.token) return;
    el.cv.width = bmp.width; el.cv.height = bmp.height;
    ctx.drawImage(bmp, 0, 0);
    state.view.bmpW = bmp.width; state.view.bmpH = bmp.height;
    state.view.ptW = bmp.width * 72 / dpi;
    state.view.ptH = bmp.height * 72 / dpi;
    state.view.renderDpi = dpi;
    el.pageLabel.textContent = (state.pno + 1) + ' / ' + state.pages;
    markThumb(state.pno);
    if (state.lastSelPage !== state.pno) clearSelection(false); // page switch clears selection
    applyLayout();
  }

  function nav(d) {
    if (!state.pages || state.busy) return; // busy: a running edit pipeline owns the page
    const next = clampPage(state.pno + d, state.pages);
    if (next === state.pno) return;
    state.pno = next;
    clearSelection(false);
    render();
  }

  function goPage(p) {
    if (!state.pages || state.busy) return; // covers thumbnail clicks too
    const next = clampPage(p, state.pages);
    if (next === state.pno) return;
    state.pno = next;
    clearSelection(false);
    render();
  }

  function updateFitButtons() {
    const cur = currentScale();
    const is100 = state.fit === 'custom' && Math.abs(state.zoom - 1) < 1e-3;
    const set = (id, on) => { const b = $(id); if (b) b.classList.toggle('active', !!on); };
    set('#btnFitPage', state.fit === 'page');
    set('#btnFitWidth', state.fit === 'width');
    set('#btnFit100', is100);
    void cur;
  }
  function setFit(mode) {
    state.fit = mode;
    updateFitButtons();
    applyLayout();
  }
  function setZoom(nz) {
    state.fit = 'custom';
    state.zoom = Math.max(0.1, Math.min(8, nz));
    el.zoomLabel.textContent = fmtZoom(state.zoom);
    updateFitButtons();
    applyLayout();
  }
  function stepZoom(dir) {
    setZoom(currentScale() * (dir > 0 ? 1.25 : 0.8));
  }

  /* ---------- selection overlay ---------- */
  function pointerToLocal(e) {
    const rect = el.overlay.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  }
  function drawOverlay() {
    octx.clearRect(0, 0, el.overlay.width, el.overlay.height);
    if (!state.sel) return;
    const conv = pdfRectToView({ rect: state.sel, scale: currentScale(), pageH: pagePointSize()[1] });
    if (!conv.ok) return;
    const [x0, y0, x1, y1] = conv.rect;
    octx.save();
    octx.strokeStyle = '#c0342b';
    octx.fillStyle = 'rgba(192,52,43,.14)';
    octx.lineWidth = 1.5;
    octx.setLineDash([5, 4]);
    const rx = Math.min(x0, x1), ry = Math.min(y0, y1);
    const rw = Math.abs(x1 - x0), rh = Math.abs(y1 - y0);
    octx.fillRect(rx, ry, rw, rh);
    octx.strokeRect(rx, ry, rw, rh);
    octx.restore();
  }
  let dragging = null;
  function setSelFromView(vr) {
    // store the selection in PDF pt immediately, so zoom/fit/resize can never
    // detach the frame from the characters it was drawn over
    const conv = viewRectToPdf({ rect: vr, scale: currentScale(), pageH: pagePointSize()[1] });
    if (conv.ok) state.sel = conv.rect;
  }
  function onPointerDown(e) {
    if (!state.pages || e.button !== 0) return;
    try { el.overlay.setPointerCapture(e.pointerId); } catch (err) { /* synthetic/already-released pointer */ }
    const [x, y] = pointerToLocal(e);
    dragging = { id: e.pointerId, start: [x, y], view: [x, y] };
    setSelFromView([x, y, x, y]);
    drawOverlay();
  }
  function onPointerMove(e) {
    if (!dragging || e.pointerId !== dragging.id) return;
    const [x, y] = pointerToLocal(e);
    dragging.view = [x, y];
    setSelFromView([dragging.start[0], dragging.start[1], x, y]);
    drawOverlay();                 // NO /api/page refetch on mousemove
    updateSelHint();
  }
  function onPointerUp(e) {
    if (!dragging || e.pointerId !== dragging.id) return;
    try { el.overlay.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    const viewRect = [dragging.start[0], dragging.start[1], dragging.view[0], dragging.view[1]];
    dragging = null;
    if (rectTooSmall(viewRect, 4)) { state.sel = null; drawOverlay(); } // min 4 CSS px, as dragged
    state.lastSelPage = state.pno;
    updateSelHint();
  }

  function selPt() {
    if (!state.sel) return null;
    if (rectTooSmall(state.sel, 2)) return null; // min 2 pt on the page
    return [state.sel[0], state.sel[1], state.sel[2], state.sel[3]];
  }
  function updateSelHint() {
    const el2 = $('#selStatus');
    if (!el2) return;
    const r = state.sel;
    if (r && !rectTooSmall(r, 2)) {
      // state.sel is already in PDF pt — no scale involved, so the hint and
      // the frame stay glued to the same glyphs at any zoom
      el2.textContent = '選択中 (' + Math.round(Math.abs(r[2] - r[0])) + '×' + Math.round(Math.abs(r[3] - r[1])) + ' pt)';
      el2.className = 'selhint has';
    } else {
      el2.textContent = '未選択 — PDF上をドラッグしてください';
      el2.className = 'selhint';
    }
    applyNoDocState();
  }

  /* ---------- thumbnails ---------- */
  let thumbObserver = null;
  function buildThumbs() {
    el.thumbList.innerHTML = '';
    if (thumbObserver) thumbObserver.disconnect();
    thumbObserver = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) {
          const img = en.target.querySelector('img');
          if (img && !img.src) img.src = img.dataset.src;
          thumbObserver.unobserve(en.target);
        }
      });
    }, { root: el.pagesPanel, rootMargin: '120px' });
    for (let i = 0; i < state.pages; i++) {
      const b = document.createElement('button');
      b.className = 'thumb';
      b.type = 'button';
      b.dataset.page = String(i);
      const img = document.createElement('img');
      img.dataset.src = '/api/page/' + i + '?dpi=28';
      img.alt = (i + 1) + ' ページ';
      const num = document.createElement('span');
      num.className = 'num'; num.textContent = String(i + 1);
      b.appendChild(img); b.appendChild(num);
      b.addEventListener('click', () => goPage(i));
      el.thumbList.appendChild(b);
      thumbObserver.observe(b);
    }
    markThumb(state.pno);
  }
  function markThumb(p) {
    $$('.thumb', el.thumbList).forEach((b) => b.classList.toggle('active', +b.dataset.page === p));
    const active = $('.thumb.active', el.thumbList);
    if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
  }

  function renderFonts() {
    if (!el.fontlist) return;
    const list = state.fonts || [];
    el.fontlist.textContent = list.length
      ? list.map((f) => '- ' + f.name + (f.embedded ? '' : ' (未埋込)')).join('\n')
      : 'なし';
  }

  /* ---------- redaction ---------- */
  async function execRedact() {
    const pno = state.pno; // pin the page ONCE: every call below must hit the same page
    const rect = selPt();
    if (!rect) { setMsg('先にPDF上をドラッグして範囲を選択してください', 'warn'); return; }
    if (pageGuardBlock(pno, state.rot, state.pages_meta)) {
      setMsg('回転/CropBox設定があるページは誤消去防止のため中止', 'warn');
      return;
    }
    const target = $('#redactTarget').value;
    const fill = redactFill($('#redactFill').value);
    const calls = redactCallSpecs(pno, rect, target, fill); // text_image = 1 request (img:true)
    await run('墨消し', async () => {
      const notes = [];
      for (const c of calls) {
        const j = await api(c.path, { method: 'POST', body: c.body });
        if (c.kind === 'both') {
          const a = j.applied || {};
          notes.push('文字 削除' + (a.dropped || 0) + '・塗り' + (a.filled || 0)
            + ' / 画像 ' + ((a.img_edited && a.img_edited.length) || 0) + '枚');
        } else if (c.kind === 'redact') {
          notes.push('文字 ' + (j.applied ? ('削除' + j.applied.dropped + '・塗り' + j.applied.filled) : 'ok'));
        } else {
          notes.push('画像 ' + (j.edited ? j.edited.length : 0) + '枚');
        }
        state.dirty = true;
      }
      updateDirty();
      setMsg('墨消し完了: ' + notes.join(' / '), 'ok');
      clearSelection(false);
      render();
      return true;
    });
  }

  /* ---------- find & bulk redact (検索して墨消し) ---------- */
  let findHits = [];
  async function onFindRedact(e) {
    const apply = e && e.currentTarget && e.currentTarget.id === 'btnFindRedact'; // read sync: currentTarget is gone after await
    if (apply) {
      const rects = $$('input[type=checkbox]:checked', $('#findList'))
        .map((c) => findHits[+c.dataset.idx]).filter(Boolean)
        .map((m) => ({ page: m.page, x0: m.x0, y0: m.y0, x1: m.x1, y1: m.y1 }));
      if (!rects.length) { setMsg('チェックした候補がありません', 'warn'); return; }
      await run('一括墨消し', async () => {
        const j = await api('/api/redact-bulk', { method: 'POST', body: { rects: rects } });
        const a = j.applied || {};
        state.dirty = true; updateDirty();
        setMsg('一括墨消し完了: ' + rects.length + '候補（文字削除 ' + (a.dropped || 0) + '・塗り ' + (a.filled || 0) + '）', 'ok');
        findHits = []; $('#findList').innerHTML = ''; $('#findOut').textContent = '';
        render();
        return true;
      });
      return;
    }
    const q = $('#findQ').value.trim();
    if (!q) { setMsg('検索語を入力してください', 'warn'); return; }
    await run('検索', async () => {
      const j = await api('/api/find', { method: 'POST', body: { q: q } });
      findHits = j.matches || [];
      const list = $('#findList');
      list.innerHTML = '';
      findHits.forEach((m, i) => {
        const lab = document.createElement('label');
        lab.className = 'check';
        const cb = document.createElement('input');
        cb.type = 'checkbox'; cb.checked = true; cb.dataset.idx = String(i);
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(
          'p' + (m.page + 1) + ' 「' + (m.context || '') + '」 (' + Math.round(m.x0) + ', ' + Math.round(m.y1) + ') pt'));
        list.appendChild(lab);
      });
      $('#findOut').textContent = findHits.length
        ? findHits.length + '件見つかりました。消さない候補はチェックを外してください'
        : '見つかりませんでした';
      setMsg('検索: ' + findHits.length + '件', findHits.length ? 'ok' : 'warn');
      return true;
    });
  }

  /* ---------- text replace ---------- */
  async function execReplace() {
    const find = $('#replaceFind').value;
    const to = $('#replaceTo').value;
    if (!find) { setMsg('検索文字列を入力してください', 'warn'); return; }
    await run('文字置換', async () => {
      const j = await api('/api/replace', { method: 'POST', body: { page: state.pno, find: find, replace: to } });
      state.dirty = true; updateDirty();
      $('#replaceOut').textContent = replaceSummary(j);
      setMsg('文字置換: ' + replaceSummary(j), 'ok');
      render();
      return true;
    });
  }

  /* ---------- OCR ---------- */
  async function execOcr() {
    const lang = $('#ocrLang').value;
    await run('OCR', async () => {
      // lang is a QUERY param for /api/ocr
      const j = await api('/api/ocr/' + state.pno + '?lang=' + encodeURIComponent(lang), { method: 'POST' });
      $('#ocrOut').textContent = (j.text || '(空)');
      setMsg('OCR完了 engine=' + j.engine + ' lang=' + j.lang + ' words=' + ((j.words && j.words.length) || 0), 'ok');
      return true;
    });
  }
  async function execOcrLayer() {
    const lang = $('#ocrLang').value;
    await run('OCRレイヤー', async () => {
      const j = await api('/api/ocr-layer', { method: 'POST', body: { page: state.pno, lang: lang } });
      state.dirty = true; updateDirty();
      $('#ocrOut').textContent = j.text_head || '';
      setMsg(ocrLayerSummary(j), 'ok');
      render();
      return true;
    });
  }

  /* ---------- page operations ---------- */
  async function doRotate(deg) {
    const pno = state.pno; // pin before await: rot recording must match the rotated page
    await run('回転', async () => {
      await api('/api/pages/rotate', { method: 'POST', body: { page: pno, deg: deg } });
      state.rot[pno] = ((state.rot[pno] || 0) + deg) % 360;
      state.dirty = true; updateDirty();
      state.view.ptW = state.view.ptH = 0; // size may change
      setMsg('p' + (pno + 1) + ' を ' + deg + '° 回転しました', 'ok');
      render();
      return true;
    });
  }
  async function doDeletePage() {
    if (!state.pages) return;
    if (!canDeletePage(state.pages)) { setMsg('最後の1ページは削除できません', 'warn'); return; }
    const pno = state.pno; // pin: remap of rot/pages must match the deleted index
    if (!await uiConfirm('現在のページ p' + (pno + 1) + ' を削除しますか？', { okLabel: '削除する' })) return;
    await run('ページ削除', async () => {
      const j = await api('/api/pages/delete', { method: 'POST', body: { pnos: [pno] } });
      state.pages = j.pages | 0;            // FIX: adopt new page count
      state.rot = remapRotAfterDelete(state.rot, pno);          // Task 6: 番号ずれ対策
      state.pages_meta = Array.isArray(j.pages_meta) ? j.pages_meta : state.pages_meta;
      state.pno = clampPage(state.pno, state.pages);
      state.dirty = true; updateDirty();
      state.size = [0, 0]; state.view.ptW = state.view.ptH = 0;
      clearSelection(false);
      buildThumbs();
      setMsg('削除しました（全 ' + state.pages + ' ページ）', 'ok');
      render();
      return true;
    });
  }
  async function doInsertPdf() {
    const p = $('#insertPath').value.trim();
    if (!p) { setMsg('追加するPDFのパスを入力してください', 'warn'); return; }
    const at = null;                       // UI always appends at the end
    await run('ページ追加', async () => {
      const j = await api('/api/insert', { method: 'POST', body: { path: p, at: at } });
      const added = Math.max(0, (j.pages | 0) - state.pages);
      state.pages = j.pages | 0;            // FIX: adopt new page count
      state.rot = remapRotAfterInsert(state.rot, at, added);    // Task 6: 番号ずれ対策
      state.pages_meta = Array.isArray(j.pages_meta) ? j.pages_meta : state.pages_meta;
      state.dirty = true; updateDirty();
      state.size = [0, 0]; state.view.ptW = state.view.ptH = 0;
      state.pno = clampPage(state.pno, state.pages);
      buildThumbs();
      setMsg('PDFを追加しました（全 ' + state.pages + ' ページ）', 'ok');
      render();
      return true;
    });
  }
  async function doExtract() {
    await run('ページ抽出', async () => {
      const j = await api('/api/pages/extract', { method: 'POST', body: { pnos: [state.pno] } });
      setMsg('抽出: ' + basename(j.out) + ' (' + j.pages + 'ページ)', 'ok');
      return true;
    });
  }
  async function doSplit() {
    await run('分割保存', async () => {
      const j = await api('/api/split', { method: 'POST', body: {} });
      setMsg('分割保存: ' + j.files.length + ' ファイル', 'ok');
      return true;
    });
  }

  /* ---------- export ---------- */
  async function doCompress() {
    await run('圧縮', async () => {
      const j = await api('/api/compress', { method: 'POST', body: {} });
      setMsg('圧縮: ' + j.before + '→' + j.after + ' バイト (' + Math.round(j.ratio * 100) + '%) → ' + basename(j.out), 'ok');
      return true;
    });
  }
  async function doWatermark() {
    const t = $('#wmText').value.trim();
    if (!t) { setMsg('透かし文字を入力してください', 'warn'); return; }
    await run('透かし', async () => {
      await api('/api/watermark', { method: 'POST', body: { page: state.pno, text: t } });
      state.dirty = true; updateDirty();
      setMsg('透かしを追加しました（保存で確定）', 'ok');
      render();
      return true;
    });
  }
  async function doPageNumbers() {
    await run('ページ番号', async () => {
      const j = await api('/api/page-numbers', { method: 'POST', body: {} });
      setMsg('ページ番号を付与: ' + basename(j.out), 'ok');
      return true;
    });
  }
  async function doMetaGet() {
    await run('文書情報', async () => {
      const j = await api('/api/metadata');
      $('#metaOut').textContent = JSON.stringify(j, null, 2);
      setMsg('文書情報を取得しました', 'ok');
      return true;
    });
  }
  async function doEncrypt() {
    const pw = $('#encUser').value;
    const pw2 = $('#encUser2').value;
    if (!pw) { setMsg('パスワードを入力してください', 'warn'); return; }
    if (pw !== pw2) { setMsg('パスワードが一致しません', 'err'); return; }
    const allow = allowedKeys({
      print: $('#encPrint').checked, copy: $('#encCopy').checked,
      modify: $('#encModify').checked, annotate: $('#encAnnotate').checked,
      forms: $('#encForms').checked, assemble: $('#encAssemble').checked,
      print_high: $('#encPrintHigh').checked,
    });
    await run('暗号化', async () => {
      const j = await api('/api/encrypt', { method: 'POST', body: { user_pw: pw, owner_pw: '', algorithm: 'AES-256', allow: allow } });
      setMsg('暗号化保存: ' + basename(j.out), 'ok');
      return true;
    });
  }
  async function doDecrypt() {
    const pw = $('#decPw').value;
    await run('解除', async () => {
      const j = await api('/api/decrypt', { method: 'POST', body: { password: pw } });
      setMsg('復号保存: ' + basename(j.out), 'ok');
      return true;
    });
  }
  async function doSanitize() {
    if (!await uiConfirm('JavaScript・添付ファイル・文書メタデータ・注釈・リンクを除去した複製を作成しますか？\n\n※本文中の文字として書かれた個人情報は消えません。',
      { title: '文書情報の削除', okLabel: '複製を作成' })) return;
    await run('文書情報の削除', async () => {
      const j = await api('/api/sanitize', { method: 'POST', body: {} });
      setMsg('文書情報の削除: ' + basename(j.out) + ' (js:' + j.js + ' files:' + j.files + ' meta:' + j.meta + ')', 'ok');
      return true;
    });
  }

  /* ---------- undo / redo ---------- */
  async function doUndo(redo) {
    await run(redo ? 'やり直し' : '元に戻す', async () => {
      const j = await api(redo ? '/api/redo' : '/api/undo', { method: 'POST', body: { page: state.pno } });
      state.pages = j.pages | 0;
      state.pno = clampPage(j.page | 0, state.pages);
      state.rot = {};                   // server restored the bytes; UI rotations are gone
      state.pages_meta = Array.isArray(j.pages_meta) ? j.pages_meta : state.pages_meta;
      state.dirty = !!j.dirty; updateDirty();
      state.size = [0, 0]; state.view.ptW = state.view.ptH = 0;
      clearSelection(false);
      buildThumbs();
      setMsg(redo ? 'やり直しました（全 ' + state.pages + ' ページ）' : '元に戻しました（全 ' + state.pages + ' ページ）', 'ok');
      render();
      return true;
    });
  }

  /* ---------- AI summarize (Task 10): one handler + registrations ---------- */
  let aiConfigLoaded = false;
  let aiLastResult = '';
  async function aiAction(e) {
    const id = e.currentTarget.id;
    if (!aiConfigLoaded) {           // prefill once from the saved (masked) config
      aiConfigLoaded = true;
      try {
        const j = await api('/api/ai/config');
        if (!$('#aiBaseURL').value) $('#aiBaseURL').value = j.base_url || '';
        if (!$('#aiModel').value) $('#aiModel').value = j.model || '';
        $('#aiKey').placeholder = j.has_key ? ('保存済み（末尾 ' + j.api_key + '）') : '未設定';
      } catch (err) { /* config endpoint unavailable: keep the form as-is */ }
    }
    if (id === 'btnAISave') {
      await run('AI設定保存', async () => {
        const j = await api('/api/ai/config', { method: 'PUT', body: {
          base_url: $('#aiBaseURL').value.trim(), api_key: $('#aiKey').value,
          model: $('#aiModel').value.trim(), timeout: 60,
        } });
        setMsg('AI設定を保存しました（key ' + (j.api_key || '未設定') + '）', 'ok');
        return true;
      });
      return;
    }
    if (id === 'btnAITest') {
      await run('接続テスト', async () => {
        const j = await api('/api/ai/summarize', { method: 'POST', body: {
          scope: 'all', text: 'ping', prompt: '接続テストです。「OK」とだけ返してください。',
        } });
        setMsg('接続OK: ' + String(j.summary || '').slice(0, 40), 'ok');
        return true;
      });
      return;
    }
    if (id === 'btnAIRun') {
      const pno = state.pno;           // pin the page before any await (same as redact)
      const checked = $$('input[name="aiScope"]').filter((r) => r.checked)[0];
      const body = { scope: (checked && checked.value) || 'all',
                     prompt: $('#aiPrompt').value.trim() || null, page: pno };
      if (body.scope === 'range') body.range = $('#aiRange').value.trim();
      if (body.scope === 'selection') {
        const rect = selPt();
        if (!rect) { setMsg('先にPDF上をドラッグして範囲を選択してください', 'warn'); return; }
        body.rects = [rect];
      }
      await run('AI要約', async () => {
        const j = await api('/api/ai/summarize', { method: 'POST', body: body });
        aiLastResult = j.summary || '';
        $('#aiOut').textContent = aiLastResult;
        setMsg('AI要約完了（' + j.chunks + 'チャンク）', 'ok');
        return true;
      });
      return;
    }
    if (id === 'btnAICopy') {
      if (!aiLastResult) { setMsg('コピーする結果がありません', 'warn'); return; }
      try { await navigator.clipboard.writeText(aiLastResult); setMsg('結果をコピーしました', 'ok'); }
      catch (err) {
        const ta = document.createElement('textarea');
        ta.value = aiLastResult; document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); setMsg('結果をコピーしました', 'ok'); }
        catch (e2) { setMsg('✗ AI要約: コピーに失敗しました', 'err'); }
        ta.remove();
      }
    }
  }
  ['btnAISave', 'btnAITest', 'btnAIRun', 'btnAICopy'].forEach((bid) => {
    const b = document.getElementById(bid);
    if (b) b.addEventListener('click', aiAction);
  });

  /* ---------- external update poll (live reload) ---------- */
  async function checkExternalUpdate() {
    if (!state.pages || !$('#autoreload') || !$('#autoreload').checked) return;
    if (state.busy) return;
    let fi;
    try { fi = await api('/api/fileinfo'); } catch (e) { return; }
    state.dirty = !!fi.dirty; updateDirty();
    if (fi.disk && fi.disk.missing) { setMsg('⚠ ファイルが移動/削除されました', 'warn'); return; }
    if (state.lastStat && fi.disk && fi.disk.mtime === state.lastStat.mtime && fi.disk.size === state.lastStat.size) {
      state.dirtyNotified = false; return;
    }
    if (fi.dirty) {
      if (!state.dirtyNotified) { setMsg('⚠ 他アプリがPDFを更新。未保存の編集があります → 保存 または 開き直し', 'warn'); state.dirtyNotified = true; }
      return;
    }
    let j;
    try { j = await api('/api/reload', { method: 'POST', body: { page: state.pno } }); } catch (e) { return; }
    state.pages = j.pages | 0;
    state.pno = clampPage(j.page, state.pages);
    state.rot = {};                     // fresh from disk: no in-memory rotation survives
    state.pages_meta = Array.isArray(j.pages_meta) ? j.pages_meta : [];  // Task 6: ガード用に再取得
    state.lastStat = j.stat; state.stat = j.stat;
    state.dirty = false;
    buildThumbs();
    setMsg('↻ 他アプリによる更新を検出、再読み込みしました', 'warn');
    render();                        // NOTE: render() does NOT clear the message
  }

  /* ---------- keyboard ---------- */
  function onKey(e) {
    if (confirmOpen()) {                    // modal dialog swallows every shortcut
      if (e.key === 'Escape') { e.preventDefault(); settleConfirm(null); }
      return;
    }
    const mod = e.ctrlKey || e.metaKey;
    const k = (e.key || '').toLowerCase();
    if (mod && k === 'o') { e.preventDefault(); openFlow(); return; }
    if (mod && k === 's') { e.preventDefault(); if (e.shiftKey) saveAsFlow(); else saveFlow(); return; }
    const tag = (e.target && e.target.tagName || '').toLowerCase();
    const inField = tag === 'input' || tag === 'select' || tag === 'textarea';
    if (inField) return;
    if (e.key === 'PageDown') { e.preventDefault(); nav(1); }
    else if (e.key === 'PageUp') { e.preventDefault(); nav(-1); }
    else if (e.key === 'Escape') { clearSelection(); }
    else if (mod && k === 'z') { e.preventDefault(); doUndo(false); }       // in-field: native text undo wins (checked above)
    else if (mod && k === 'y') { e.preventDefault(); doUndo(true); }
  }

  /* ---------- wiring ---------- */
  function bind() {
    $('#btnOpen').addEventListener('click', openFlow);
    $('#btnSave').addEventListener('click', saveFlow);
    $('#btnSaveAs').addEventListener('click', saveAsFlow);
    $('#btnUndo').addEventListener('click', () => doUndo(false));
    $('#btnRedo').addEventListener('click', () => doUndo(true));

    $$('.tab').forEach((t) => t.addEventListener('click', () => selectTab(t.dataset.tab)));
    $('#btnViewSettings').addEventListener('click', () => { selectTab('view'); showSettings(true); });

    $('#btnPrev').addEventListener('click', () => nav(-1));
    $('#btnNext').addEventListener('click', () => nav(1));
    $('#btnFitPage').addEventListener('click', () => setFit('page'));
    $('#btnFitWidth').addEventListener('click', () => setFit('width'));
    $('#btnFit100').addEventListener('click', () => setZoom(1));
    $('#btnZoomOut').addEventListener('click', () => stepZoom(-1));
    $('#btnZoomIn').addEventListener('click', () => stepZoom(1));
    const bo = $('#btnOpenEmpty');
    if (bo) bo.addEventListener('click', openFlow);

    $('#btnTogglePages').addEventListener('click', () => togglePanel(el.pagesPanel));
    $('#btnToggleSettings').addEventListener('click', () => togglePanel(el.settingsPanel));
    $('#btnShowPages').addEventListener('click', () => showPages(true));
    const sp2 = $('#btnShowPages2'); if (sp2) sp2.addEventListener('click', () => showPages(true));

    // redact
    $('#btnRedact').addEventListener('click', execRedact);
    $('#btnClearSel').addEventListener('click', () => { clearSelection(); setMsg('選択を解除しました'); });
    $('#redactTarget').addEventListener('change', updateSelHint);
    // find & bulk redact (検索して墨消し)
    $('#btnFind').addEventListener('click', onFindRedact);
    $('#btnFindRedact').addEventListener('click', onFindRedact);
    // replace
    $('#btnReplace').addEventListener('click', execReplace);
    // page
    $('#btnRot90').addEventListener('click', () => doRotate(90));
    $('#btnRot270').addEventListener('click', () => doRotate(270));
    $('#btnDeletePage').addEventListener('click', doDeletePage);
    $('#btnInsertPdf').addEventListener('click', doInsertPdf);
    $('#btnExtract').addEventListener('click', doExtract);
    $('#btnSplit').addEventListener('click', doSplit);
    // ocr
    $('#btnOcr').addEventListener('click', execOcr);
    $('#btnOcrLayer').addEventListener('click', execOcrLayer);
    // export
    $('#btnCompress').addEventListener('click', doCompress);
    $('#btnWatermark').addEventListener('click', doWatermark);
    $('#btnPageNumbers').addEventListener('click', doPageNumbers);
    $('#btnMetaGet').addEventListener('click', doMetaGet);
    $('#btnEncrypt').addEventListener('click', doEncrypt);
    $('#btnDecrypt').addEventListener('click', doDecrypt);
    $('#btnSanitize').addEventListener('click', doSanitize);
    // view
    $('#btnFonts').addEventListener('click', () => run('フォント更新', async () => {
      state.fonts = await api('/api/fonts') || []; renderFonts(); setMsg('フォント一覧を更新しました', 'ok'); return true;
    }));

    // overlay selection (pointer capture)
    el.overlay.addEventListener('pointerdown', onPointerDown);
    el.overlay.addEventListener('pointermove', onPointerMove);
    el.overlay.addEventListener('pointerup', onPointerUp);
    el.overlay.addEventListener('pointercancel', onPointerUp);

    // open fallback modal
    $('#openModalOk').addEventListener('click', doFallbackOpen);
    $('#openModalCancel').addEventListener('click', () => { $('#openModal').hidden = true; });
    $('#openPath').addEventListener('keydown', (e) => { if (e.key === 'Enter') doFallbackOpen(); });
    $('#btnPickFile').addEventListener('click', () => $('#fileInput').click());
    $('#fileInput').addEventListener('change', (e) => doUploadFile(e.target.files && e.target.files[0]));

    // confirm dialog: backdrop click = 中止
    $('#confirmModal').addEventListener('click', (e) => { if (e.target.id === 'confirmModal') settleConfirm(null); });

    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', () => { if (state.fit !== 'custom') applyLayout(); });
    if (window.ResizeObserver) new ResizeObserver(() => { if (state.fit !== 'custom') applyLayout(); }).observe(el.wrap);

    setInterval(checkExternalUpdate, 2000);
  }

  function selectTab(name) {
    $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    $$('.panel').forEach((p) => { p.hidden = p.dataset.panel !== name; });
    showSettings(true);
  }
  function togglePanel(p) {
    p.classList.toggle('collapsed');
    refitIfAuto(); // RO is the general safety net; refit explicitly too (panel toggle changes the wrap box)
  }
  function showPages(on) { el.pagesPanel.classList.toggle('collapsed', !on); refitIfAuto(); }
  function showSettings(on) { el.settingsPanel.classList.toggle('collapsed', !on); refitIfAuto(); }
  function refitIfAuto() { if (state.fit !== 'custom' && state.view.bmpW) applyLayout(); }

  /* ---------- init ---------- */
  selectTab('redact');
  updateSelHint();
  applyNoDocState();
  bind();
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
