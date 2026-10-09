(function (root) {
  'use strict';
  function drawHead(ctx, asset) {
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    ctx.save();
    ctx.scale(ctx.canvas.width / asset.grid, ctx.canvas.height / asset.grid);
    asset.dots.forEach(function (dot) {
      ctx.fillStyle = dot.c;
      ctx.beginPath();
      ctx.arc(dot.x + 0.5, dot.y + 0.5, dot.r, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.restore();
  }
  function createDemo(asset, state) {
    if (state !== 'idle' && state !== 'working') throw new Error('Unsupported local demo');
    var glyph = new root.BotGlyph.Glyph(JSON.parse(JSON.stringify(asset)), {
      botId: 'hermes', set: 'bold', variantMode: 'fixed', variants: { working: 'orbit' },
      blinkPeriod: 3.8, breathe: 2.2
    });
    glyph.jump(state, 0, state === 'working' ? 'orbit' : 'idle');
    return glyph;
  }
  function createSprites(asset, makeCanvas) {
    var sprites = {};
    asset.dots.forEach(function (dot) {
      if (sprites[dot.c]) return;
      var tile = makeCanvas();
      tile.width = tile.height = 24;
      var ctx = tile.getContext('2d');
      ctx.fillStyle = dot.c;
      ctx.beginPath(); ctx.arc(12, 12, 10, 0, Math.PI * 2); ctx.fill();
      sprites[dot.c] = tile;
    });
    return sprites;
  }
  function drawCached(ctx, glyph, now, px, sprites) {
    var x, y, radius;
    var offset = glyph.target().state === 'working' ? 2 * px / 480 : 0;
    var bridge = {
      fillStyle: '', globalAlpha: 1,
      clearRect: function (a, b, w, h) { ctx.clearRect(a, b, w, h); },
      beginPath: function () {},
      arc: function (a, b, r) { x = a; y = b + offset; radius = r; },
      fill: function () {
        var size = radius * 2.4;
        ctx.globalAlpha = bridge.globalAlpha;
        ctx.drawImage(sprites[bridge.fillStyle], x - size / 2, y - size / 2, size, size);
      }
    };
    glyph.draw(bridge, now, px);
    ctx.globalAlpha = 1;
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports = { drawHead: drawHead, createDemo: createDemo, createSprites: createSprites, drawCached: drawCached }; return; }
  var canvas = document.getElementById('face');
  var reduced = root.matchMedia('(prefers-reduced-motion: reduce)');
  var search = root.location.search;
  var match = /(?:\?|&)demo=(idle|working)(?:&|$)/.exec(search);
  var state = match ? match[1] : null;
  var forceStatic = /(?:\?|&)motion=off(?:&|$)/.test(search);
  var asset, glyph, sprites, painter, timer = null, started = 0, lastFrame = -Infinity;
  function tick(timestamp) {
    timer = null;
    if (!glyph || document.hidden || reduced.matches || forceStatic) return;
    var now = (root.performance.now() - started) / 1000;
    if (painter) {
      var at = typeof timestamp === 'number' ? timestamp : root.performance.now();
      if (at - lastFrame >= 49) { painter.draw(glyph, now); lastFrame = at; }
      timer = root.requestAnimationFrame(tick);
    } else {
      drawCached(canvas.getContext('2d'), glyph, now, canvas.width, sprites);
      timer = root.setTimeout(tick, 250);
    }
  }
  function restart() {
    if (timer !== null) {
      if (painter) root.cancelAnimationFrame(timer); else root.clearTimeout(timer);
    }
    lastFrame = -Infinity;
    timer = null;
    if (!asset) return;
    if (painter) painter.draw(null, 0);
    else drawHead(canvas.getContext('2d'), asset);
    if (state && !reduced.matches && !forceStatic && !document.hidden) tick();
  }
  reduced.addListener(restart);
  document.addEventListener('visibilitychange', restart);
  function useCanvasFallback() {
    if (timer !== null) root.cancelAnimationFrame(timer);
    timer = null;
    painter = null;
    var replacement = canvas.cloneNode(false);
    canvas.parentNode.replaceChild(replacement, canvas);
    canvas = replacement;
    canvas.setAttribute('data-renderer', 'canvas2d');
  }
  function failed() {
    canvas.setAttribute('data-error', 'true');
    canvas.setAttribute('aria-label', 'Supplied head artwork could not be loaded.');
  }
  var request = new XMLHttpRequest();
  request.open('GET', 'hermes-head.json', true);
  request.onload = function () {
    try {
      if (request.status !== 0 && request.status !== 200) throw new Error('load');
      asset = JSON.parse(request.responseText);
      if (asset.kind !== 'head' || asset.bot !== 'hermes' || !Array.isArray(asset.dots)) throw new Error('head');
      glyph = state ? createDemo(asset, state) : null;
      sprites = state ? createSprites(asset, function () { return document.createElement('canvas'); }) : null;
      if (state && root.FaceGPU) {
        try { painter = root.FaceGPU.createPainter(canvas, asset); }
        catch (error) { useCanvasFallback(); }
      }
      if (painter) canvas.addEventListener('webglcontextlost', function () {
        useCanvasFallback();
        forceStatic = true;
        restart();
      });
      canvas.setAttribute('data-renderer', painter ? 'webgl' : 'canvas2d');
      started = root.performance.now();
      restart();
      canvas.setAttribute('data-loaded', 'true');
      canvas.setAttribute('data-dot-count', String(asset.dots.length));
      canvas.setAttribute('data-demo', state || 'none');
    } catch (error) { failed(); }
  };
  request.onerror = failed;
  request.send();
}(typeof window !== 'undefined' ? window : globalThis));
