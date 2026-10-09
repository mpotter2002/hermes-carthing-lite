const assert = require('node:assert/strict');
const face = require('../preview/face/face.js');

const NOW = 1700000000000;
function snapshot(overrides) {
  const result = Object.assign({
    version: 1,
    profile: 'default',
    session_id: 'session-1',
    observed_at: NOW - 100,
    activity: 'idle',
    tool: null,
    reply: {id: 'reply-1', text: 'Safe reply'},
    image: null,
    cards: []
  }, overrides || {});
  if (!Object.prototype.hasOwnProperty.call(overrides || {}, 'cards')) {
    const label = {unknown:'Unknown',idle:'Idle',thinking:'Thinking',running:'Running'}[result.activity] + (result.tool ? ' · ' + result.tool : '');
    result.cards = [{id:'status',title:'Status',text:label}];
    if (result.reply) result.cards.push({id:'reply-excerpt',title:'Reply excerpt',text:result.reply.text.slice(0,8192)});
  }
  return result;
}

assert.equal(face.SNAPSHOT_SHAPE, "HermesCompanionSnapshotV1");
for (const activity of ['idle','thinking','running']) {
  assert.equal(face.validateSnapshot(snapshot({activity,cards:[]}), NOW).ok, false, 'normal snapshots require fixed cards');
}
assert.equal(face.validateSnapshot(snapshot({activity:'unknown',reply:null,image:null,tool:null,cards:[]}),NOW).ok,true);
assert.equal(face.validateSnapshot(snapshot({activity:'unknown',cards:[]}),NOW).ok,false,'Unknown with reply still requires cards');
assert.equal(face.validateSnapshot(snapshot({cards:[{id:'status',title:'Status',text:'Idle'}]}),NOW).ok,false,'reply requires its fixed excerpt');
assert.equal(face.validateSnapshot(snapshot(), NOW).ok, true);
for (const bad of [
  snapshot({version: 2}),
  snapshot({profile: 'engineer'}),
  snapshot({session_id: ''}),
  snapshot({observed_at: NOW - 15001}),
  snapshot({observed_at: NOW + 1}),
  snapshot({activity: 'busy'}),
  snapshot({reply: {id: 'x', text: 'x'.repeat(32769)}}),
  snapshot({cards: new Array(5).fill({id: 'x', title: 't', text: 'c'})}),
  snapshot({image: {src: '../secret.png', alt: 'bad'}}),
  snapshot({image: {src: 'https://example.test/x.png', alt: 'bad'}}),
  snapshot({image: {src: 'image.svg', alt: 'bad'}}),
  snapshot({extra: true})
]) assert.equal(face.validateSnapshot(bad, NOW).ok, false);
for (const src of ['reply.png', 'photo-1.JPG', 'card_image.webp', 'still.gif', 'a'.repeat(64) + '.jpg']) {
  assert.equal(face.validateSnapshot(snapshot({image: {src, alt: 'local'}}), NOW).ok, false, src);
}
assert.equal(face.validateSnapshot(snapshot({image:{src:'a'.repeat(64)+'.png',alt:'safe'}}), NOW).ok, true);
for (const cards of [[{id:'arbitrary',title:'Action',text:'x'}], [{id:'status',title:'Not Status',text:'Idle'}], [{id:'reply-excerpt',title:'Reply excerpt',text:'reply'}], [{id:'status',title:'Status',text:'Thinking'}]]) {
  assert.equal(face.validateSnapshot(snapshot({cards}),NOW).ok,false);
}
console.log('PASS named snapshot contract, bounds, freshness, default identity and local image safety');

const asset = JSON.parse(require('node:fs').readFileSync(require('node:path').join(__dirname, '../preview/face/hermes-head.json')));
assert.equal(face.WINDOW_FRAME_SHAPE, 'HermesDotWindowFrameV1');
const geometry = face.WINDOW_GEOMETRY;
assert.deepEqual(Object.keys(geometry).sort(), ['bobAmplitude','bottom','contentBottom','contentLeft','contentRight','contentTop','layers','left','right','top']);
assert.equal(geometry.top, 78, 'frame uses the empty space below the header');
assert.equal(geometry.contentTop, 94, 'first line moves up while keeping its border inset');
assert.equal(geometry.bottom, 756.4, 'frame extends down by one 32.4px text line');
assert.equal(geometry.contentBottom, 740.4, 'scroll viewport stays inset from extended border');
const transition = face.createTransition(asset, 800);
assert.equal(transition.value(0), 0);
transition.setTarget(1, 0);
assert.equal(transition.value(400), 0.5);
const midpoint = transition.frame(400);
const transitionStorage = midpoint;
assert.equal(midpoint.length, 1003);
assert.ok(midpoint.some((dot, i) => dot.x !== (asset.dots[i].x + 0.5) * 10 || dot.y !== (asset.dots[i].y + 0.5) * 10));
assert.ok(midpoint.every(dot => dot.a === 1), 'all original dots remain visible through dispersal');
const beforeReverse = midpoint.map(dot => ({...dot}));
transition.setTarget(0, 400);
assert.deepEqual(transition.frame(400), beforeReverse, 'mid-transition reversal has no positional jump');
assert.equal(transition.value(600), 0.25, 'interrupted transition reverses continuously');
assert.equal(transition.frame(600), transitionStorage, 'transition reuses fixed dot storage');
assert.equal(transition.value(800), 0);
const exactFace = transition.frame(800);
asset.dots.forEach((dot, i) => {
  assert.ok(Math.abs(exactFace[i].x - (4 + (dot.x + 0.5) * 472 / 48)) < 1e-12);
  assert.ok(Math.abs(exactFace[i].y - ((dot.y + 0.5) * 472 / 48 + 112)) < 1e-12);
  assert.ok(Math.abs(exactFace[i].r - dot.r * 472 / 48) < 1e-12);
  assert.deepEqual([exactFace[i].c, exactFace[i].p, exactFace[i].a], [dot.c, dot.p, 1]);
});
transition.setTarget(1, 800);
assert.equal(transition.value(1600), 1);
const border = transition.frame(1600, true).map(dot => ({...dot}));
assert.equal(border.length, 1003);
assert.deepEqual(border.map(dot => [dot.c, dot.p]), asset.dots.map(dot => [dot.c, dot.p]), 'border preserves source color, body tags and order');
assert.ok(border.every(dot => dot.a === 1 && dot.r > 0));
assert.ok(border.every(dot => dot.x + dot.r < geometry.contentLeft || dot.x - dot.r > geometry.contentRight || dot.y + dot.r < geometry.contentTop || dot.y - dot.r > geometry.contentBottom), 'border leaves the full scroll and control-safe content rectangle clear');
const duplicate = face.createTransition(asset, 800); duplicate.settle(1, 1600);
assert.deepEqual(duplicate.frame(1600, true), border, 'per-index border destinations are deterministic');
const bobbed = transition.frame(1850).map(dot => ({...dot}));
assert.ok(bobbed.some((dot, i) => dot.x !== border[i].x || dot.y !== border[i].y), 'reading frame gently moves');
assert.ok(bobbed.every((dot, i) => Math.hypot(dot.x-border[i].x, dot.y-border[i].y) <= geometry.bobAmplitude + 1e-9), 'reading bob stays bounded');
transition.settle(0, 2000);
assert.equal(transition.value(2000), 0);
console.log('PASS exact face and 1003-dot window endpoints, deterministic scroll-safe geometry, bounded bob/storage and continuous reversal');

function node() {
  return {
    hidden: false, textContent: '', children: [], attributes: {}, dataset: {},
    appendChild(child) { this.children.push(child); return child; },
    replaceChildren() { this.children = []; },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    removeAttribute(k) { delete this.attributes[k]; },
    addEventListener(type, fn) { this['on' + type] = fn; }
  };
}
const nodes = {};
['activity','reply','content-reply','content-image','cards','content','show-content','back','controls','companion'].forEach(id => { nodes[id] = node(); });
const doc = {
  getElementById(id) { return nodes[id]; },
  querySelector(selector) { return selector === '.companion' ? nodes.companion : null; },
  createElement(tag) { const element = node(); element.tagName = tag; return element; }
};
const view = face.createCompanionView(doc);
const activities = [];
const controller = face.createCompanionController(view, () => NOW, activity => activities.push(activity));
const hostile = snapshot({reply:{id:'r1',text:'<img src=x onerror=alert(1)>'},cards:[{id:'status',title:'Status',text:'Idle'},{id:'reply-excerpt',title:'Reply excerpt',text:'<script>bad()</script>'}]});
assert.equal(controller.applySnapshot(hostile), true);
assert.equal(nodes.reply.textContent, hostile.reply.text);
assert.equal(nodes['content-reply'].textContent, hostile.reply.text);
assert.equal(nodes.cards.children[0].children[0].textContent, hostile.cards[0].title);
assert.equal(nodes.cards.children[0].children[1].textContent, hostile.cards[0].text);
assert.equal(nodes.cards.children[1].children[1].textContent, hostile.cards[1].text);
assert.equal(nodes.cards.children[0].children[0].tagName, 'h2');
assert.equal(nodes.cards.children[0].children[1].hidden, false);
assert.equal(nodes.cards.children[0].children[0].onclick, undefined);
assert.equal(nodes.companion.attributes['data-session-id'], 'session-1');
assert.equal(activities.at(-1), 'idle');
assert.equal(controller.applySnapshot(snapshot({session_id:'session-2',reply:null,activity:'running',tool:'shell'})), true);
assert.equal(nodes.reply.textContent, 'Hermes');
assert.equal(nodes.activity.textContent, 'Running · shell');
assert.equal(nodes.companion.attributes['data-session-id'], 'session-2');
assert.equal(controller.applySnapshot(snapshot({observed_at:NOW-16000})), false);
assert.equal(nodes.activity.textContent, 'Unknown');
assert.equal(nodes.reply.textContent, 'Hermes');
assert.equal(nodes.companion.attributes['data-session-id'], undefined);
assert.equal(activities.at(-1), 'unknown');
console.log('PASS textContent-only presentation, exact-session clearing, truthful activity and stale Unknown');
