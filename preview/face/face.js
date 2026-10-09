(function (root) {
  'use strict';
  var SNAPSHOT_SHAPE = 'HermesCompanionSnapshotV1';
  var WINDOW_FRAME_SHAPE = 'HermesDotWindowFrameV1';
  var FACE_LEFT = 4, FACE_TOP = 112, FACE_SIZE = 472;
  var WINDOW_GEOMETRY = {
    left: 16, right: 464, top: 90, bottom: 724, layers: 5, bobAmplitude: 1.25,
    contentLeft: 32, contentRight: 448, contentTop: 106, contentBottom: 708
  };
  var SNAPSHOT_KEYS = ['activity', 'cards', 'image', 'observed_at', 'profile', 'reply', 'session_id', 'tool', 'version'];
  function objectWithKeys(value, keys) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    var actual = Object.keys(value).sort();
    if (actual.length !== keys.length) return false;
    for (var i = 0; i < keys.length; i += 1) if (actual[i] !== keys[i]) return false;
    return true;
  }
  function boundedString(value, maximum, allowEmpty) {
    return typeof value === 'string' && value.length <= maximum && (allowEmpty || value.length > 0);
  }
  function safeImageSource(src) {
    return boundedString(src, 68, false) && /^[0-9a-f]{64}\.png$/.test(src);
  }
  function validateSnapshot(snapshot, now) {
    var i, card;
    if (!objectWithKeys(snapshot, SNAPSHOT_KEYS)) return { ok: false, reason: 'shape' };
    if (snapshot.version !== 1 || snapshot.profile !== 'default') return { ok: false, reason: 'identity' };
    if (!boundedString(snapshot.session_id, 256, false)) return { ok: false, reason: 'session' };
    if (typeof snapshot.observed_at !== 'number' || !isFinite(snapshot.observed_at) || Math.floor(snapshot.observed_at) !== snapshot.observed_at || snapshot.observed_at < now - 15000 || snapshot.observed_at > now) return { ok: false, reason: 'freshness' };
    if (['unknown', 'idle', 'thinking', 'running'].indexOf(snapshot.activity) < 0) return { ok: false, reason: 'activity' };
    if (snapshot.tool !== null && !boundedString(snapshot.tool, 256, false)) return { ok: false, reason: 'tool' };
    if (snapshot.reply !== null && (!objectWithKeys(snapshot.reply, ['id', 'text']) || !boundedString(snapshot.reply.id, 256, false) || !boundedString(snapshot.reply.text, 32768, true))) return { ok: false, reason: 'reply' };
    if (snapshot.image !== null && (!objectWithKeys(snapshot.image, ['alt', 'src']) || !safeImageSource(snapshot.image.src) || !boundedString(snapshot.image.alt, 512, true))) return { ok: false, reason: 'image' };
    if (!Array.isArray(snapshot.cards) || snapshot.cards.length > 2) return { ok: false, reason: 'cards' };
    var bareUnknown = snapshot.activity === 'unknown' && snapshot.tool === null && snapshot.reply === null && snapshot.image === null;
    if (!(bareUnknown && snapshot.cards.length === 0) && snapshot.cards.length !== (snapshot.reply === null ? 1 : 2)) return { ok: false, reason: 'cards' };
    for (i = 0; i < snapshot.cards.length; i += 1) {
      card = snapshot.cards[i];
      if (!objectWithKeys(card, ['id', 'text', 'title']) || !boundedString(card.text, 8192, true)) return { ok: false, reason: 'card' };
      if (i === 0 && (card.id !== 'status' || card.title !== 'Status' || card.text !== activityLabel(snapshot))) return { ok: false, reason: 'card' };
      if (i === 1 && (card.id !== 'reply-excerpt' || card.title !== 'Reply excerpt' || snapshot.reply === null)) return { ok: false, reason: 'card' };
    }
    return { ok: true, value: snapshot };
  }
  function createTransition(asset, duration) {
    var progress = 0, target = 0, changedAt = 0, start = 0;
    var geometry = WINDOW_GEOMETRY, sourceScale = FACE_SIZE / asset.grid;
    var dots = asset.dots.map(function (dot) { return { x: 0, y: 0, r: 0, c: dot.c, p: dot.p, a: 1 }; });
    var destinations = asset.dots.map(function (dot, index) {
      var layer = index % geometry.layers;
      var sequence = Math.floor(index / geometry.layers);
      var count = Math.ceil((asset.dots.length - layer) / geometry.layers);
      var left = geometry.left + layer * 2.5, right = geometry.right - layer * 2.5;
      var top = geometry.top + layer * 2.5, bottom = geometry.bottom - layer * 2.5;
      var width = right - left, height = bottom - top, perimeter = 2 * (width + height);
      var slot = (sequence * 73 + layer * 29) % count;
      var distance = (slot + 0.5) * perimeter / count;
      var x, y;
      if (distance < width) { x = left + distance; y = top; }
      else if (distance < width + height) { x = right; y = top + distance - width; }
      else if (distance < 2 * width + height) { x = right - (distance - width - height); y = bottom; }
      else { x = left; y = bottom - (distance - 2 * width - height); }
      return { x: x, y: y, r: Math.max(0.65, Math.min(1.35, dot.r * 4.5)), phase: index * 2.399963229728653 };
    });
    function value(now) {
      var distance = (now - changedAt) / duration;
      progress = target > start ? Math.min(target, start + distance) : Math.max(target, start - distance);
      return progress;
    }
    function setTarget(next, now) {
      value(now);
      start = progress;
      target = next ? 1 : 0;
      changedAt = now;
    }
    function frame(now, still) {
      var p = value(now), eased = p * p * (3 - 2 * p);
      asset.dots.forEach(function (dot, index) {
        var sourceX = FACE_LEFT + (dot.x + 0.5) * sourceScale, sourceY = (dot.y + 0.5) * sourceScale + FACE_TOP;
        var destination = destinations[index], out = dots[index], bob = still ? 0 : geometry.bobAmplitude * eased;
        var angle = now * 0.0011 + destination.phase;
        out.x = sourceX + (destination.x - sourceX) * eased + Math.sin(angle) * bob;
        out.y = sourceY + (destination.y - sourceY) * eased + Math.cos(angle) * bob;
        out.r = dot.r * sourceScale + (destination.r - dot.r * sourceScale) * eased;
        out.a = 1;
      });
      return dots;
    }
    function settle(next, now) {
      progress = target = start = next ? 1 : 0; changedAt = now;
    }
    return { value: value, setTarget: setTarget, settle: settle, frame: frame, isRunning: function (now) { return value(now) !== target; } };
  }
  function drawHead(ctx, asset) {
    var portrait = ctx.canvas.height > 480;
    var scale = (portrait ? FACE_SIZE : ctx.canvas.width) / asset.grid;
    var left = portrait ? FACE_LEFT : 0, top = portrait ? FACE_TOP : 0;
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    asset.dots.forEach(function (dot) {
      ctx.fillStyle = dot.c;
      ctx.beginPath();
      ctx.arc(left + (dot.x + 0.5) * scale, (dot.y + 0.5) * scale + top, dot.r * scale, 0, Math.PI * 2);
      ctx.fill();
    });
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
    var x, y, radius, portrait = ctx.canvas.height > 480;
    var faceScale = portrait ? FACE_SIZE / 480 : 1;
    var offset = (portrait ? FACE_TOP : 0) + (glyph.target().state === 'working' ? 2 * px / 480 * faceScale : 0);
    var left = portrait ? FACE_LEFT : 0;
    var bridge = {
      fillStyle: '', globalAlpha: 1,
      clearRect: function () { ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height); },
      beginPath: function () {},
      arc: function (a, b, r) { x = left + a * faceScale; y = b * faceScale + offset; radius = r * faceScale; },
      fill: function () {
        var size = radius * 2.4;
        ctx.globalAlpha = bridge.globalAlpha;
        ctx.drawImage(sprites[bridge.fillStyle], x - size / 2, y - size / 2, size, size);
      }
    };
    glyph.draw(bridge, now, px);
    ctx.globalAlpha = 1;
  }
  function activityLabel(snapshot) {
    var labels = { unknown: 'Unknown', idle: 'Idle', thinking: 'Thinking', running: 'Running' };
    return labels[snapshot.activity] + (snapshot.tool ? ' · ' + snapshot.tool : '');
  }
  function createCompanionView(doc) {
    var companion = doc.querySelector('.companion');
    var activity = doc.getElementById('activity');
    var reply = doc.getElementById('reply');
    var fullReply = doc.getElementById('content-reply');
    var image = doc.getElementById('content-image');
    var cards = doc.getElementById('cards');
    var content = doc.getElementById('content');
    var show = doc.getElementById('show-content');
    var back = doc.getElementById('back');
    var controls = doc.getElementById('controls') || (show && show.parentNode);
    function empty(node) { while (node.firstChild) node.removeChild(node.firstChild); }
    function showFace() {
      companion.setAttribute('data-view', 'face');
      content.hidden = true; back.hidden = true;
    }
    function clear(label) {
      showFace();
      companion.removeAttribute('data-session-id');
      activity.hidden = false; activity.textContent = label || 'Unknown';
      reply.textContent = 'Hermes'; fullReply.textContent = '';
      image.hidden = true; image.removeAttribute('src'); image.setAttribute('alt', '');
      empty(cards);
      if (controls) controls.hidden = false;
      show.hidden = true; show.textContent = 'Full content'; back.textContent = 'Back';
    }
    function render(snapshot) {
      var hasFull = !!snapshot.image || snapshot.cards.length > 0 || !!(snapshot.reply && snapshot.reply.text.length > 280);
      companion.setAttribute('data-session-id', snapshot.session_id);
      activity.hidden = false; activity.textContent = activityLabel(snapshot);
      reply.textContent = snapshot.reply ? snapshot.reply.text : 'Hermes';
      fullReply.textContent = snapshot.reply ? snapshot.reply.text : '';
      image.hidden = !snapshot.image;
      if (snapshot.image) { image.setAttribute('src', snapshot.image.src); image.setAttribute('alt', snapshot.image.alt); }
      else { image.removeAttribute('src'); image.setAttribute('alt', ''); }
      empty(cards);
      snapshot.cards.forEach(function (card) {
        var section = doc.createElement('section'), title = doc.createElement('h2'), text = doc.createElement('p');
        section.className = 'card'; title.className = 'card-title'; text.className = 'card-text';
        title.textContent = card.title;
        text.textContent = card.text; text.hidden = false;
        section.appendChild(title); section.appendChild(text); cards.appendChild(section);
      });
      if (controls) controls.hidden = false;
      show.hidden = !hasFull; show.textContent = 'Full content'; back.textContent = 'Back';
      return hasFull;
    }
    function showContent() {
      companion.setAttribute('data-view', 'content'); content.hidden = false; back.hidden = false; show.hidden = true;
    }
    return { clear: clear, render: render, showFace: showFace, showContent: showContent };
  }
  function createCompanionController(view, now, activityChanged) {
    var current = null, hasFull = false;
    function applySnapshot(snapshot) {
      var checked = validateSnapshot(snapshot, now());
      if (!checked.ok) {
        current = null; hasFull = false; view.clear('Unknown'); activityChanged('unknown'); return false;
      }
      if (current && current.session_id !== snapshot.session_id) view.clear('Unknown');
      current = snapshot; view.showFace(); hasFull = view.render(snapshot); activityChanged(snapshot.activity);
      return true;
    }
    return {
      applySnapshot: applySnapshot,
      current: function () { return current; },
      hasFullContent: function () { return hasFull; },
      invalidate: function () { return applySnapshot(null); }
    };
  }
  var api = { SNAPSHOT_SHAPE: SNAPSHOT_SHAPE, WINDOW_FRAME_SHAPE: WINDOW_FRAME_SHAPE, WINDOW_GEOMETRY: WINDOW_GEOMETRY, validateSnapshot: validateSnapshot, safeImageSource: safeImageSource, createTransition: createTransition, createCompanionView: createCompanionView, createCompanionController: createCompanionController, drawHead: drawHead, createDemo: createDemo, createSprites: createSprites, drawCached: drawCached };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  var canvas = document.getElementById('face');
  var reduced = root.matchMedia('(prefers-reduced-motion: reduce)');
  var search = root.location.search;
  var match = /(?:\?|&)demo=(idle|working)(?:&|$)/.exec(search);
  var state = match ? match[1] : null;
  var companionMode = /(?:\?|&)companion=1(?:&|$)/.test(search);
  var pushedTransport = /(?:\?|&)transport=push(?:&|$)/.test(search);
  var forceStatic = /(?:\?|&)motion=off(?:&|$)/.test(search);
  var asset, glyph, sprites, painter, transition, transitionSubject, transitionStillSubject, controller, view;
  var timer = null, started = 0, lastFrame = -Infinity, desiredContent = false, transitioning = false;
  var pollTimer = null, pollRequest = null, stopped = false, currentActivity = null;
  function transitionGlyph(still) {
    return {
      target: function () { return { state: 'transition' }; },
      draw: function (ctx, now) {
        var dots = transition.frame(started + now * 1000, still);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        dots.forEach(function (dot) {
          ctx.globalAlpha = dot.a; ctx.fillStyle = dot.c; ctx.beginPath(); ctx.arc(dot.x, dot.y, dot.r, 0, Math.PI * 2); ctx.fill();
        });
        ctx.globalAlpha = 1;
      }
    };
  }
  function animated() {
    return transition && (transitioning || desiredContent) ? transitionSubject : glyph;
  }
  function finishTransition(nowMs) {
    if (!transitioning || transition.isRunning(nowMs)) return false;
    transitioning = false;
    if (desiredContent) view.showContent();
    return true;
  }
  function tick(timestamp) {
    timer = null;
    if (document.hidden || reduced.matches || forceStatic) return;
    var at = typeof timestamp === 'number' ? timestamp : root.performance.now();
    var now = (at - started) / 1000, subject = animated(), drew = false;
    if (!subject) return;
    if (painter) {
      if (at - lastFrame >= 49) { painter.draw(subject, now); lastFrame = at; drew = true; }
      if (drew) finishTransition(at);
      if (transitioning || desiredContent || glyph) timer = root.requestAnimationFrame(tick);
    } else {
      if (subject === transitionSubject) subject.draw(canvas.getContext('2d'), now, canvas.width);
      else drawCached(canvas.getContext('2d'), subject, now, canvas.width, sprites);
      finishTransition(at);
      if (transitioning || desiredContent || glyph) timer = root.setTimeout(tick, 250);
    }
  }
  function cancelRenderTimer() {
    if (timer !== null) {
      if (painter) root.cancelAnimationFrame(timer); else root.clearTimeout(timer);
      timer = null;
    }
  }
  function drawSettled(now) {
    if (!asset) return;
    if (desiredContent && transition) {
      var elapsed = (now - started) / 1000;
      if (painter) painter.draw(transitionStillSubject, elapsed);
      else transitionStillSubject.draw(canvas.getContext('2d'), elapsed, canvas.width);
    } else if (painter) painter.draw(null, 0);
    else drawHead(canvas.getContext('2d'), asset);
  }
  function restart() {
    cancelRenderTimer(); lastFrame = -Infinity;
    if (!asset) return;
    var now = root.performance.now();
    if (document.hidden || reduced.matches || forceStatic) {
      if (transition) transition.settle(desiredContent ? 1 : 0, now);
      transitioning = false;
      if (desiredContent && view) view.showContent(); else if (view) view.showFace();
      drawSettled(now); return;
    }
    if (transitioning || desiredContent || glyph) tick(now);
    else drawSettled(now);
  }
  function setActivity(activity) {
    if (!asset || currentActivity === activity) return;
    currentActivity = activity;
    glyph = activity === 'idle' ? createDemo(asset, 'idle') : (activity === 'thinking' || activity === 'running') ? createDemo(asset, 'working') : null;
    started = root.performance.now(); restart();
  }
  function getController() {
    if (!controller) {
      view = createCompanionView(document);
      controller = createCompanionController(view, function () { return Date.now(); }, setActivity);
    }
    return controller;
  }
  function showContent() {
    var active = getController();
    if (!active.current() || !active.hasFullContent()) return false;
    desiredContent = true;
    if (!transition || reduced.matches || forceStatic || document.hidden) { view.showContent(); restart(); return true; }
    transitioning = true; transition.setTarget(1, root.performance.now()); restart(); return true;
  }
  function showFace() {
    desiredContent = false;
    if (!view) return true;
    view.showFace();
    if (transition && !reduced.matches && !forceStatic && !document.hidden) { transitioning = true; transition.setTarget(0, root.performance.now()); }
    restart(); return true;
  }
  root.HermesCompanion = {
    SNAPSHOT_SHAPE: SNAPSHOT_SHAPE,
    WINDOW_FRAME_SHAPE: WINDOW_FRAME_SHAPE,
    WINDOW_GEOMETRY: WINDOW_GEOMETRY,
    applySnapshot: function (snapshot) {
      var active = getController(), previous = active.current();
      var keepContent = !!(desiredContent && previous && snapshot && snapshot.session_id === previous.session_id);
      if (!keepContent) {
        desiredContent = false; transitioning = false;
        if (transition) transition.settle(0, root.performance.now());
      }
      var accepted = active.applySnapshot(snapshot);
      if (accepted && keepContent && active.hasFullContent()) {
        desiredContent = true; view.showContent();
      } else if (keepContent) {
        desiredContent = false; transitioning = false;
        if (transition) transition.settle(0, root.performance.now());
        view.showFace(); restart();
      }
      return accepted;
    },
    showFace: showFace,
    showContent: showContent
  };
  function stopPolling() {
    if (pollTimer !== null) { root.clearTimeout(pollTimer); pollTimer = null; }
    if (pollRequest) { pollRequest.abort(); pollRequest = null; }
  }
  function schedulePoll() {
    if (!stopped && companionMode && !document.hidden && pollTimer === null && !pollRequest) pollTimer = root.setTimeout(poll, 2000);
  }
  function poll() {
    var done = false;
    pollTimer = null;
    if (stopped || document.hidden || pollRequest) return;
    if (pushedTransport) {
      var active = getController();
      if (!validateSnapshot(active.current(), Date.now()).ok) root.HermesCompanion.applySnapshot(null);
      schedulePoll(); return;
    }
    pollRequest = new XMLHttpRequest();
    var thisRequest = pollRequest;
    function complete(ok) {
      if (done || pollRequest !== thisRequest) return; done = true;
      pollRequest = null;
      if (ok) {
        try { root.HermesCompanion.applySnapshot(JSON.parse(thisRequest.responseText)); }
        catch (error) { root.HermesCompanion.applySnapshot(null); }
      } else root.HermesCompanion.applySnapshot(null);
      schedulePoll();
    }
    thisRequest.open('GET', 'companion.json', true); thisRequest.timeout = 1500;
    thisRequest.onload = function () { complete(thisRequest.status === 0 || thisRequest.status === 200); };
    thisRequest.onerror = function () { complete(false); };
    thisRequest.ontimeout = function () { complete(false); };
    thisRequest.send();
  }
  function visibilityChanged() {
    restart();
    if (!companionMode) return;
    if (document.hidden) stopPolling(); else poll();
  }
  reduced.addListener(restart);
  document.addEventListener('visibilitychange', visibilityChanged);
  if (root.addEventListener) root.addEventListener('pagehide', function () { stopped = true; stopPolling(); cancelRenderTimer(); });
  function useCanvasFallback() {
    cancelRenderTimer(); painter = null;
    var replacement = canvas.cloneNode(false);
    canvas.parentNode.replaceChild(replacement, canvas); canvas = replacement;
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
      transition = createTransition(asset, 800); transitionSubject = transitionGlyph(false); transitionStillSubject = transitionGlyph(true);
      glyph = state ? createDemo(asset, state) : null;
      sprites = (state || companionMode) ? createSprites(asset, function () { return document.createElement('canvas'); }) : null;
      if ((state || companionMode) && root.FaceGPU) {
        try { painter = root.FaceGPU.createPainter(canvas, asset); }
        catch (error) { useCanvasFallback(); }
      }
      if (painter) canvas.addEventListener('webglcontextlost', function () { useCanvasFallback(); forceStatic = true; restart(); });
      canvas.setAttribute('data-renderer', painter ? 'webgl' : 'canvas2d');
      started = root.performance.now(); restart();
      canvas.setAttribute('data-loaded', 'true'); canvas.setAttribute('data-dot-count', String(asset.dots.length));
      canvas.setAttribute('data-demo', state || 'none');
      if (controller && controller.current()) setActivity(controller.current().activity);
      else if (companionMode) root.HermesCompanion.applySnapshot(null);
      if (companionMode) poll();
      var showButton = document.getElementById('show-content'), backButton = document.getElementById('back');
      if (showButton) showButton.addEventListener('click', showContent);
      if (backButton) backButton.addEventListener('click', showFace);
    } catch (error) { failed(); }
  };
  request.onerror = failed;
  request.send();
}(typeof window !== 'undefined' ? window : globalThis));
