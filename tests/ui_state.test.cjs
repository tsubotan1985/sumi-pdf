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

test('redactPipeline picks the right server calls for each target', () => {
  assert.deepEqual(app.redactPipeline('text_image'), ['redact', 'img-redact']);
  assert.deepEqual(app.redactPipeline('text'), ['redact']);
  assert.deepEqual(app.redactPipeline('image'), ['img-redact']);
});

test('redactFill converts UI colors to float RGB (text) and int RGB (image)', () => {
  assert.deepEqual(app.redactFill('black').redact, [0, 0, 0]);
  assert.deepEqual(app.redactFill('black').image, [0, 0, 0]);
  assert.deepEqual(app.redactFill('white').redact, [1, 1, 1]);
  assert.deepEqual(app.redactFill('white').image, [255, 255, 255]);
  assert.equal(app.redactFill('background').matchBg, true); // let the engine sample the page bg
});
