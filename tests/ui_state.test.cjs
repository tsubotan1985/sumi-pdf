// tests/ui_state.test.cjs
// Pure-function unit tests for the SUMIPDF front-end state layer (web/app.js).
// DOM-free: run with `node --test tests/ui_state.test.cjs`.
const test = require('node:test');
const assert = require('node:assert/strict');

const app = require('../web/app.js');

test('clampPage keeps the page index inside the document', () => {
  assert.equal(app.clampPage(5, 3), 2);
  assert.equal(app.clampPage(-2, 3), 0);
  assert.equal(app.clampPage(1, 3), 1);
  assert.equal(app.clampPage(0, 0), 0); // empty document never goes negative
  assert.equal(app.clampPage(9, 0), 0);
});

test('clampDpi stays within the renderer-supported 30..400 range', () => {
  assert.equal(app.clampDpi(10), 30);
  assert.equal(app.clampDpi(999), 400);
  assert.equal(app.clampDpi(120), 120);
  assert.equal(app.clampDpi(NaN), 120); // non-numeric falls back to a sane default
});

test('computeScale: fit-page uses the limiting axis', () => {
  assert.equal(app.computeScale({ fit: 'page', viewW: 600, viewH: 800, pageW: 300, pageH: 400 }), 2);
  assert.equal(app.computeScale({ fit: 'page', viewW: 600, viewH: 300, pageW: 300, pageH: 400 }), 0.75);
});

test('computeScale: fit-width and custom zoom', () => {
  assert.equal(app.computeScale({ fit: 'width', viewW: 600, viewH: 300, pageW: 200, pageH: 400 }), 3);
  assert.equal(app.computeScale({ fit: 'custom', viewW: 600, viewH: 300, pageW: 200, pageH: 400, zoom: 1.5 }), 1.5);
});

test('computeScale: degenerate inputs never divide by zero', () => {
  assert.equal(app.computeScale({ fit: 'page', viewW: 0, viewH: 0, pageW: 0, pageH: 0 }), 1);
  assert.equal(app.computeScale({ fit: 'width', viewW: 600, viewH: 300, pageW: 0, pageH: 400 }), 1);
});

test('fmtZoom shows percent, not dpi', () => {
  assert.equal(app.fmtZoom(1), '100%');
  assert.equal(app.fmtZoom(0.85), '85%');
  assert.equal(app.fmtZoom(2.5), '250%');
  assert.equal(app.fmtZoom(0), '100%');
});

test('viewRectToPdf flips the vertical axis (view top -> high pdf y)', () => {
  const r = app.viewRectToPdf({ rect: [0, 0, 20, 20], scale: 1, pageH: 100 });
  assert.equal(r.ok, true);
  assert.deepEqual(r.rect, [0, 80, 20, 100]); // pdf_y1 hits the page top height
});

test('viewRectToPdf handles a mid-page selection and normalizes order', () => {
  const a = app.viewRectToPdf({ rect: [110, 220, 10, 20], scale: 2, pageH: 400 });
  assert.equal(a.ok, true);
  assert.deepEqual(a.rect, [5, 290, 55, 390]);
});

test('viewRectToPdf refuses to convert when scale is not usable', () => {
  const r = app.viewRectToPdf({ rect: [0, 0, 10, 10], scale: 0, pageH: 100 });
  assert.equal(r.ok, false);
  assert.equal(r.rect, null);
});

test('rectTooSmall rejects accidental clicks', () => {
  assert.equal(app.rectTooSmall([0, 0, 2, 50], 3), true);
  assert.equal(app.rectTooSmall([0, 0, 50, 2], 3), true);
  assert.equal(app.rectTooSmall([0, 0, 50, 50], 3), false);
  assert.equal(app.rectTooSmall(null, 3), true);
});

test('basename works for windows and posix paths', () => {
  assert.equal(app.basename('C:\\Users\\a\\sumino-demo.pdf'), 'sumino-demo.pdf');
  assert.equal(app.basename('/mnt/c/work/sumino-demo.pdf'), 'sumino-demo.pdf');
  assert.equal(app.basename('sumino-demo.pdf'), 'sumino-demo.pdf');
  assert.equal(app.basename(''), '');
});

test('sanitizeFilename strips directories and forces a .pdf suffix', () => {
  assert.equal(app.sanitizeFilename('../../evil name.pdf'), 'evil name.pdf');
  assert.equal(app.sanitizeFilename('C:\\x\\report'), 'report.pdf');
  assert.equal(app.sanitizeFilename('a/b/c.PDF'), 'c.PDF');
});

test('allowedKeys maps the UI print checkbox to the API print_ key', () => {
  const out = app.allowedKeys({ print: false, copy: true, modify: false, annotate: true, forms: true, assemble: false, print_high: false });
  assert.deepEqual(out, { print_: false, copy: true, modify: false, annotate: true, forms: true, assemble: false, print_high: false });
  assert.equal('print' in out, false);
});

test('canDeletePage forbids removing the last remaining page', () => {
  assert.equal(app.canDeletePage(1), false);
  assert.equal(app.canDeletePage(0), false);
  assert.equal(app.canDeletePage(2), true);
  assert.equal(app.canDeletePage(5), true);
});

test('formatApiError prefers the server {detail} field', () => {
  assert.equal(app.formatApiError({ status: 404, detail: 'not found: x.pdf' }), 'not found: x.pdf');
  assert.equal(app.formatApiError({ status: 400, message: 'boom' }), 'boom');
  assert.match(app.formatApiError({ status: 500 }), /500/);
  assert.equal(app.formatApiError(null), '不明なエラー');
});

test('nextDpiForScale renders crisply without exposing dpi to the user', () => {
  assert.equal(app.nextDpiForScale(1, 2), 144);
  assert.equal(app.nextDpiForScale(0.1, 1), 30); // clamped up
  assert.equal(app.nextDpiForScale(10, 2), 400); // clamped down
});

test('ocrLayerSummary reports words/lang/text_head and never an "inserted" field', () => {
  const s = app.ocrLayerSummary({ words: 12, lang: 'jpn', text_head: 'あいう' });
  assert.match(s, /12/);
  assert.match(s, /jpn/);
  assert.equal(/inserted/.test(s), false);
});

test('replaceSummary omits the (never returned) font field', () => {
  const s = app.replaceSummary({ replaced: 2, dropped: 1 });
  assert.match(s, /2/);
  assert.equal(/font/i.test(s), false);
});

test('redactFill converts UI colors to float RGB (text) and int RGB (image)', () => {
  assert.deepEqual(app.redactFill('black').redact, [0, 0, 0]);
  assert.deepEqual(app.redactFill('black').image, [0, 0, 0]);
  assert.deepEqual(app.redactFill('white').redact, [1, 1, 1]);
  assert.deepEqual(app.redactFill('white').image, [255, 255, 255]);
  assert.equal(app.redactFill('background').matchBg, true); // let the engine sample the page bg
});

/* ---------- Task 1: selection kept in PDF pt (zoom-invariant) ---------- */

const approxRect = (a, b, eps = 1e-6, msg = '') => {
  assert.ok(
    Array.isArray(a) && a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= eps),
    (msg || 'rects equal') + ': ' + JSON.stringify(a) + ' ~= ' + JSON.stringify(b),
  );
};

test('pdfRectToView is the exact inverse of viewRectToPdf (round trip)', () => {
  const cases = [
    { rect: [10, 20, 110, 60], scale: 1.5, pageH: 400 },
    { rect: [0, 0, 297.5, 421], scale: 0.5, pageH: 842 }, // full page exactly (297.5×421 px at 50%)
    { rect: [300, 700, 40, 100], scale: 2, pageH: 842 }, // unnormalized input order
  ];
  for (const c of cases) {
    const pdf = app.viewRectToPdf(c).rect;
    const view = app.pdfRectToView({ rect: pdf, scale: c.scale, pageH: c.pageH }).rect;
    const pdf2 = app.viewRectToPdf({ rect: view, scale: c.scale, pageH: c.pageH }).rect;
    approxRect(pdf2, pdf, 1e-6, 'pdf -> view -> pdf round trip');
    assert.ok(view[0] <= view[2] && view[1] <= view[3], 'view rect normalized, y-down');
    // pdf rect is y-up inside the page
    assert.ok(pdf[1] <= pdf[3] && pdf[3] <= c.pageH + 1e-9 && pdf[1] >= -1e-9);
  }
});

test('pdf-pt selection is invariant across scale changes (bug A regression)', () => {
  // pointerup at 85%: the same on-screen rect must keep targeting the same
  // characters after zooming to 106% — the pdf-pt rect must not move.
  const pageH = 842;
  const viewAt85 = [100, 200, 300, 220];
  const sel = app.viewRectToPdf({ rect: viewAt85, scale: 0.85, pageH: pageH }).rect;
  for (const z of [0.54, 1.06, 2.0]) {
    const view = app.pdfRectToView({ rect: sel, scale: z, pageH: pageH }).rect;
    const target = app.viewRectToPdf({ rect: view, scale: z, pageH: pageH }).rect;
    approxRect(target, sel, 1e-6, 'sel targets the same pdf region at zoom ' + z);
  }
  // the view-space rect itself DOES move with zoom (tracks the same glyphs)
  const viewAt106 = app.pdfRectToView({ rect: sel, scale: 1.06, pageH: pageH }).rect;
  assert.ok(Math.abs(viewAt106[2] - viewAt106[0] - (viewAt85[2] - viewAt85[0]) * 1.06 / 0.85) < 1e-6);
});

test('pdfRectToView refuses an unusable scale like viewRectToPdf', () => {
  assert.equal(app.pdfRectToView({ rect: [0, 0, 1, 1], scale: 0, pageH: 100 }).ok, false);
  assert.equal(app.pdfRectToView({ rect: [0, 0, 1, 1], scale: -2, pageH: 100 }).rect, null);
});

/* ---------- Task 2: one pinned page for the whole redact pipeline ---------- */

/* ---------- Task 6: text+image = ONE atomic request; native rot/crop guard ---------- */

test('redactCallSpecs sends text_image as a single img:true request (atomic)', () => {
  const fill = app.redactFill('white');
  const calls = app.redactCallSpecs(0, [1, 2, 3, 4], 'text_image', fill);
  assert.deepEqual(calls.map((c) => c.path), ['/api/redact']);   // 1 request, not 2
  assert.equal(calls[0].body.page, 0);                            // still pinned
  assert.deepEqual(calls[0].body.rects, [[1, 2, 3, 4]]);
  assert.deepEqual(calls[0].body.fill, fill.redact);
  assert.equal(calls[0].body.match_bg, fill.matchBg);
  assert.equal(calls[0].body.img, true);                          // image side rides along
  assert.deepEqual(calls[0].body.img_fill, fill.image);
  // single-op targets keep their dedicated endpoints, one pinned call each
  assert.deepEqual(app.redactCallSpecs(3, [0, 0, 5, 5], 'text', fill).map((c) => [c.path, c.body.page]),
    [['/api/redact', 3]]);
  assert.equal(app.redactCallSpecs(3, [0, 0, 5, 5], 'text', fill)[0].body.img, undefined);
  assert.deepEqual(app.redactCallSpecs(7, [0, 0, 5, 5], 'image', fill).map((c) => [c.path, c.body.page]),
    [['/api/img-redact', 7]]);
});

test('pageGuardBlock: UI rotation, native /Rotate and CropBox origin all block', () => {
  const GUARD = '回転/CropBox設定があるページは誤消去防止のため中止';
  assert.equal(app.pageGuardBlock(0, { 0: 90 }, []), true);            // UI-tracked rotation
  assert.equal(app.pageGuardBlock(0, {}, [{ rot: 90, crop_x0: 0, crop_y0: 0 }]), true); // native /Rotate
  assert.equal(app.pageGuardBlock(0, {}, [{ rot: 0, crop_x0: 30, crop_y0: 0 }]), true); // CropBox x-origin
  assert.equal(app.pageGuardBlock(0, {}, [{ rot: 0, crop_x0: 0, crop_y0: 20 }]), true); // CropBox y-origin
  assert.equal(app.pageGuardBlock(1, { 2: 90 }, []), false);           // other page rotated: no block
  assert.equal(app.pageGuardBlock(0, {}, [{ rot: 0, crop_x0: 0, crop_y0: 0 }]), false);
  assert.equal(app.pageGuardBlock(5, {}, []), false);                  // no meta: legacy behaviour
  assert.equal(app.pageGuardBlock(0, {}, [{ rot: 360, crop_x0: 0, crop_y0: 0 }]), false); // 360 == 0
  assert.ok(GUARD.length > 0); // message contract lives in app.js execRedact
});

test('remapRotAfterDelete shifts rotations after the deleted page', () => {
  assert.deepEqual(app.remapRotAfterDelete({ 2: 90, 0: 180 }, 1), { 1: 90, 0: 180 });
  assert.deepEqual(app.remapRotAfterDelete({ 2: 90 }, 2), {});         // deleted page's rot dropped
  assert.deepEqual(app.remapRotAfterDelete({}, 0), {});
});

test('remapRotAfterInsert shifts rotations from the insert point on', () => {
  assert.deepEqual(app.remapRotAfterInsert({ 1: 90 }, 1, 3), { 4: 90 });
  assert.deepEqual(app.remapRotAfterInsert({ 1: 90 }, null, 3), { 1: 90 }); // append: unchanged
});

/* ---------- Task 8/9: DOM confirm dialog replaces window.confirm ---------- */

test('discardChoices offers the 3-way guard: abort / discard / save', () => {
  const btns = app.discardChoices('別の文書を開く');
  assert.deepEqual(btns.map((b) => b.id), ['abort', 'discard', 'save']);
  assert.equal(btns[0].label, '中止');
  assert.equal(btns[1].label, '破棄して別の文書を開く');
  assert.equal(btns[2].label, '保存してから別の文書を開く');
  assert.equal(btns[2].primary, true);          // saving is the safe default
});

test('discardProceeds: follow-up runs on discard, or only after a successful save', () => {
  assert.equal(app.discardProceeds('discard', false), true);
  assert.equal(app.discardProceeds('save', true), true);
  assert.equal(app.discardProceeds('save', false), false);  // failed save aborts the follow-up
  assert.equal(app.discardProceeds('abort', true), false);  // 中止 never proceeds
  assert.equal(app.discardProceeds(null, true), false);     // Escape / backdrop = 中止
  assert.equal(app.discardProceeds(undefined, true), false);
});

test('no window.confirm remains; index.html hosts the DOM dialog and OCR engine line', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
  assert.equal(src.includes('window.confirm'), false, 'window.confirm is unavailable in WebView2');
  const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
  assert.ok(html.includes('id="confirmModal"'), 'index.html must host the DOM confirm dialog');
  assert.ok(html.includes('OCRエンジン: tesseract'), 'OCR panel must name the engine in use');
  assert.ok(html.includes('このページの文字を置換'), 'replace panel heading must say page-scoped');
});
