const assert = require('node:assert/strict');
const face = require('../preview/face/face.js');

const NOW = 1700000000000;
function snapshot(overrides) {
  return Object.assign({
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
}

assert.equal(face.SNAPSHOT_SHAPE, "HermesCompanionSnapshotV1");
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
for (const src of ['reply.png', 'photo-1.JPG', 'card_image.webp', 'still.gif']) {
  assert.equal(face.validateSnapshot(snapshot({image: {src, alt: 'local'}}), NOW).ok, true, src);
}
console.log('PASS named snapshot contract, bounds, freshness, default identity and local image safety');

const asset = JSON.parse(require('node:fs').readFileSync(require('node:path').join(__dirname, '../preview/face/hermes-head.json')));
const transition = face.createTransition(asset, 800);
assert.equal(transition.value(0), 0);
transition.setTarget(1, 0);
assert.equal(transition.value(400), 0.5);
const midpoint = transition.frame(400);
const transitionStorage = midpoint;
assert.equal(midpoint.length, 1003);
assert.ok(midpoint.some((dot, i) => dot.x !== asset.dots[i].x + 0.5 || dot.y !== asset.dots[i].y + 0.5));
assert.ok(midpoint.every(dot => dot.a > 0 && dot.a < 1));
transition.setTarget(0, 400);
assert.equal(transition.value(600), 0.25, 'interrupted transition reverses continuously');
assert.equal(transition.frame(600), transitionStorage, 'transition reuses fixed dot storage');
assert.equal(transition.value(800), 0);
assert.deepEqual(transition.frame(800), asset.dots.map(dot => ({x: (dot.x + 0.5) * 10, y: (dot.y + 0.5) * 10, r: dot.r * 10, c: dot.c, a: 1})));
transition.setTarget(1, 800);
assert.equal(transition.value(1600), 1);
assert.ok(transition.frame(1600).every(dot => dot.a === 0));
transition.settle(0, 1600);
assert.equal(transition.value(1600), 0);
console.log('PASS deterministic scatter midpoint, exact head endpoint, bounded settle and continuous reversal');

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
  createElement() { return node(); }
};
const view = face.createCompanionView(doc);
const activities = [];
const controller = face.createCompanionController(view, () => NOW, activity => activities.push(activity));
const hostile = snapshot({reply:{id:'r1',text:'<img src=x onerror=alert(1)>'},cards:[{id:'c1',title:'<b>title</b>',text:'<script>bad()</script>'}]});
assert.equal(controller.applySnapshot(hostile), true);
assert.equal(nodes.reply.textContent, hostile.reply.text);
assert.equal(nodes['content-reply'].textContent, hostile.reply.text);
assert.equal(nodes.cards.children[0].children[0].textContent, hostile.cards[0].title);
assert.equal(nodes.cards.children[0].children[1].textContent, hostile.cards[0].text);
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
