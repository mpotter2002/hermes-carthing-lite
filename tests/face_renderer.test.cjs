const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const enginePath = path.join(root, 'preview/face/desktop-glyph.js');
assert.equal(crypto.createHash('sha256').update(fs.readFileSync(enginePath)).digest('hex'), '038da56a3b61a60f062de6579bc6c746324fbf4c82b2d01c4ebe8ed1b8a5c357');
require(enginePath);
const renderer = path.join(root, 'preview/face/face.js');
const { drawHead, createDemo } = require(renderer);
const asset = JSON.parse(fs.readFileSync(path.join(root, 'preview/face/hermes-head.json')));
const original = JSON.stringify(asset);
function recorder() {
  const arcs = [];
  const context = {
    canvas: { width: 480, height: 480 },
    clearRect() { arcs.length = 0; }, fillRect() { arcs.length = 0; }, save() {}, restore() {}, scale() {}, beginPath() {}, moveTo() {}, drawImage() {},
    arc(x, y, r) { arcs.push({ x, y, r, c: this.fillStyle, a: this.globalAlpha ?? 1 }); }, fill() {}
  };
  return { context, arcs };
}
const still = recorder(); drawHead(still.context, asset);
assert.deepEqual(still.arcs, asset.dots.map(d => ({ x: (d.x + 0.5) * 10, y: (d.y + 0.5) * 10, r: d.r * 10, c: d.c, a: 1 })));
for (const state of ['idle', 'working']) {
  const demo = createDemo(asset, state);
  const reference = new globalThis.BotGlyph.Glyph(JSON.parse(original), { botId: 'hermes', set: 'bold', variantMode: 'fixed', variants: { working: 'orbit' }, blinkPeriod: 3.8, breathe: 2.2 });
  reference.jump(state, 0, state === 'working' ? 'orbit' : 'idle');
  assert.equal(demo.av._g.dots.length, 1003);
  assert.equal(demo.av._g.PA, null, 'bust-indexed poses must not be attached');
  assert.equal(demo.target().state, state);
  for (const time of [0, .5, 1, 2.4]) {
    const actual = recorder(), expected = recorder();
    demo.draw(actual.context, time, 480); reference.draw(expected.context, time, 480);
    assert.deepEqual(actual.arcs, expected.arcs, 'demo must reuse authored desktop frames');
    assert.ok(actual.arcs.every(d => ['#0000F2', '#3A3AFF'].includes(d.c)));
  }
}
assert.equal(JSON.stringify(asset), original, 'engine must only mutate its runtime copy');
assert.throws(() => createDemo(asset, 'live'), /Unsupported/);
console.log('PASS unchanged desktop engine, exact head, authored idle/orbit frames, no bust poses, immutable source');

for (const state of ['idle', 'working']) {
  const glyph = createDemo(asset, state);
  let canvases = 0;
  const sprites = require(renderer).createSprites(asset, () => {
    canvases++;
    const rec = recorder();
    return { getContext: () => rec.context, rec };
  });
  for (let frame = 0; frame < 120; frame++) {
    const time = frame * .1;
    const actual = recorder(), expected = recorder();
    actual.context.drawImage = (sprite, x, y, w, h) => {
      assert.equal(w, h);
      actual.arcs.push({x: x+w/2, y: y+h/2, r: w*10/24, c: sprite.rec.context.fillStyle, a: actual.context.globalAlpha});
    };
    glyph.draw(expected.context, time, 480);
    require(renderer).drawCached(actual.context, glyph, time, 480, sprites);
    assert.equal(actual.arcs.length, expected.arcs.length);
    actual.arcs.forEach((d,i) => {
      const e = expected.arcs[i];
      assert.ok(Math.abs(d.x-e.x)<1e-10 && Math.abs(d.y-e.y-(state==='working'?2:0))<1e-10 && Math.abs(d.r-e.r)<1e-10);
      assert.equal(d.c,e.c); assert.equal(d.a,e.a);
      assert.ok(d.x-d.r>=0 && d.y-d.r>=0 && d.x+d.r<=480 && d.y+d.r<=480);
    });
  }
  assert.equal(canvases, 2, 'cache only two tiny palette textures, not full animation frames');
}
console.log('PASS cached authored geometry, ordered colors, bounded orbit and fixed texture memory');

function mount(query, reduce, gpu) {
  let timer, mediaListener, visibilityListener, xhr, gpuDraws = 0;
  const reduced = { matches: reduce, addListener(fn) { mediaListener = fn; } };
  const attributes = {}, rec = recorder();
  const canvas = { getContext() { return rec.context; }, setAttribute(k, v) { attributes[k] = v; }, addEventListener(type, fn) {this.lost=fn;}, cloneNode() { return this; }, parentNode: {replaceChild(){}} };
  const browser = {
    BotGlyph: globalThis.BotGlyph, location: { search: query }, performance: { now() { return 500; } },
    document: { createElement() { return {getContext() {return recorder().context;}}; }, hidden: false, getElementById(id) { return id === 'face' ? canvas : null; }, addEventListener(type, fn) { visibilityListener = fn; } },
    matchMedia() { return reduced; },
    setTimeout(fn, delay) { assert.equal(gpu, undefined, 'GPU animation must use display-synchronized scheduling'); assert.equal(delay, 250); timer = fn; return 1; }, clearTimeout() { timer = undefined; },
    XMLHttpRequest: function () { xhr = this; this.open = () => {}; this.send = () => {}; }
  };
  if (gpu) browser.FaceGPU = {createPainter() {return {draw() {gpuDraws++;}};}};
  browser.requestAnimationFrame = fn => { timer = fn; return 2; };
  browser.cancelAnimationFrame = () => {timer = undefined;};
  vm.runInNewContext(fs.readFileSync(renderer, 'utf8'), browser);
  xhr.status = 200; xhr.responseText = original; xhr.onload();
  assert.equal(attributes['data-dot-count'], '1003'); assert.equal(attributes['data-error'], undefined);
  return { canvas, attributes, browser, reduced, timer() { return timer; }, gpuDraws() { return gpuDraws; }, media() { mediaListener(); }, visibility() { visibilityListener(); } };
}
assert.equal(mount('', false).timer(), undefined, 'unknown activity defaults to static, not invented idle');
assert.equal(mount('?demo=working&motion=off', false).timer(), undefined);
const demo = mount('?demo=idle', true);
assert.equal(demo.timer(), undefined);
demo.reduced.matches = false; demo.media(); assert.equal(typeof demo.timer(), 'function');
demo.reduced.matches = true; demo.media(); assert.equal(demo.timer(), undefined);
const working = mount('?demo=working', false);
assert.equal(working.attributes['data-demo'], 'working');
working.browser.document.hidden = true; working.visibility(); assert.equal(working.timer(), undefined);
console.log('PASS explicit demo selection, static legacy fallback, bounded4Hz timer, reduced/hidden motion cleanup');
const accelerated = mount('?demo=working', false, true);
assert.equal(accelerated.attributes['data-renderer'], 'webgl');
assert.equal(typeof accelerated.timer(), 'function');
assert.equal(accelerated.gpuDraws(),1);
accelerated.timer()(516); accelerated.timer()(548);
assert.equal(accelerated.gpuDraws(),1,'GPU may schedule on display frames but remains capped near 20 draws/second');
accelerated.timer()(549);
assert.equal(accelerated.gpuDraws(),2);
accelerated.browser.document.hidden = true; accelerated.visibility();
assert.equal(accelerated.timer(), undefined);
console.log('PASS accelerated scheduling and hidden cancellation');
const lost = mount('?demo=working', false, true);
lost.canvas.lost();
assert.equal(lost.attributes['data-renderer'], 'canvas2d');
assert.equal(lost.timer(), undefined, 'context loss must settle to the static head');
console.log('PASS context loss falls back to static exact head without retry loop');
