const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
require('../preview/face/desktop-glyph.js');
const asset = fs.readFileSync(path.join(__dirname, '../preview/face/hermes-head.json'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../preview/face/face.js'), 'utf8');

function element() {
  const item = {
    hidden: false, textContent: '', children: [], attributes: {}, style: {},
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    removeAttribute(k) { delete this.attributes[k]; },
    addEventListener(type, fn) { this['on' + type] = fn; }
  };
  Object.defineProperty(item, 'firstChild', {get() { return this.children[0] || null; }});
  return item;
}
function mount(reducedMotion, gpu, search) {
  let clock = 1000, nextTimer = 1;
  const timers = new Map(), xhrs = [], listeners = {};
  const arcs = [];
  const context = {canvas:{width:480,height:480},globalAlpha:1,fillStyle:'',clearRect(){arcs.length=0;},fillRect(){arcs.length=0;},save(){},restore(){},scale(){},beginPath(){},arc(x,y,r){arcs.push({x,y,r,a:this.globalAlpha});},fill(){},drawImage(sprite,x,y,w,h){arcs.push({x:x+w/2,y:y+h/2,r:w/2,a:this.globalAlpha});}};
  const nodes = {};
  ['activity','reply','content-reply','content-image','cards','content','show-content','back'].forEach(id => { nodes[id] = element(); });
  nodes.companion = element(); nodes.controls = element();
  nodes.controls.appendChild(nodes.back); nodes.controls.appendChild(nodes['show-content']);
  const canvas = element(); canvas.width=480; canvas.height=800; canvas.getContext=()=>context; canvas.cloneNode=()=>canvas; canvas.parentNode={replaceChild(){}}; nodes.face=canvas;
  const document = {
    hidden:false,
    getElementById(id){ return nodes[id] || null; },
    querySelector(selector){ return selector === '.companion' ? nodes.companion : null; },
    createElement(tag){ if(tag==='canvas') { const c=element(); c.width=c.height=24; c.getContext=()=>context; return c; } return element(); },
    addEventListener(type,fn){ listeners[type]=fn; }
  };
  function XHR(){ this.aborted=false; xhrs.push(this); }
  XHR.prototype.open=function(method,url){this.url=url;}; XHR.prototype.send=function(){}; XHR.prototype.abort=function(){this.aborted=true;};
  const media={matches:reducedMotion,addListener(fn){listeners.media=fn;}};
  const browser={
    BotGlyph:globalThis.BotGlyph,document,location:{search:search || '?companion=1'},performance:{now(){return clock;}},XMLHttpRequest:XHR,
    matchMedia(){return media;},
    setTimeout(fn,delay){const id=nextTimer++;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
    requestAnimationFrame(fn){const id=nextTimer++;timers.set(id,{fn,delay:16});return id;},cancelAnimationFrame(id){timers.delete(id);},
    addEventListener(type,fn){listeners[type]=fn;}
  };
  if (gpu) browser.FaceGPU={createPainter(){return {draw(subject,time){
    if (subject) subject.draw(context,time,480);
    else { context.clearRect(); JSON.parse(asset).dots.forEach(dot=>context.arc((dot.x+.5)*10,(dot.y+.5)*10,dot.r*10)); }
  }};}};
  vm.runInNewContext(source,browser);
  xhrs[0].status=200; xhrs[0].responseText=asset; xhrs[0].onload();
  return {browser,nodes,canvas,document,media,xhrs,timers,arcs,listeners,setClock(v){clock=v;},runDelay(delay){const found=[...timers].find(([,t])=>t.delay===delay);assert.ok(found,'missing timer '+delay);timers.delete(found[0]);found[1].fn(clock);}};
}
function snapshot(overrides) {
  return Object.assign({version:1,profile:'default',session_id:'default-chat',observed_at:Date.now(),activity:'thinking',tool:null,reply:{id:'r',text:'x'.repeat(400)},image:null,cards:[]},overrides||{});
}

const pushed=mount(false, true, '?companion=1&transport=push');
assert.equal(pushed.xhrs.length,1,'push transport loads artwork without competing snapshot polling');
assert.equal(pushed.browser.HermesCompanion.applySnapshot(snapshot({activity:'idle'})),true);
assert.equal(pushed.nodes.activity.textContent,'Idle');

const page=mount(false);
assert.equal(page.browser.HermesCompanion.SNAPSHOT_SHAPE,'HermesCompanionSnapshotV1');
assert.equal(page.nodes.activity.textContent,'Unknown');
assert.equal(page.xhrs.length,2,'head load plus one immediate snapshot read');
assert.equal(page.xhrs[1].url,'companion.json');
assert.equal(page.browser.HermesCompanion.applySnapshot(snapshot()),true);
assert.equal(page.nodes.activity.textContent,'Thinking');
assert.equal(page.nodes.reply.textContent,'x'.repeat(400));
assert.equal(page.nodes['show-content'].hidden,false);
assert.equal(page.browser.HermesCompanion.showContent(),true);
assert.equal(page.nodes.companion.attributes['data-view'],'face','content waits for scatter completion');
page.setClock(1400); page.runDelay(250);
assert.equal(page.arcs.length,1003,'transition midpoint retains every dot');
assert.ok(page.arcs.some(dot=>dot.y>480),'dots disperse continuously toward the portrait window');
page.setClock(1800); page.runDelay(250);
assert.equal(page.nodes.companion.attributes['data-view'],'content');
assert.equal(page.arcs.length,1003,'content endpoint keeps every source dot in the window frame');
const borderAtEndpoint=page.arcs.map(dot=>({...dot}));
assert.ok(borderAtEndpoint.every(dot=>dot.x+dot.r<32||dot.x-dot.r>448||dot.y+dot.r<106||dot.y-dot.r>694),'window frame cannot cover the scroll-safe content rectangle');
assert.equal([...page.timers.values()].filter(t=>t.delay===250).length,1,'reading bob shares the one bounded animation chain');
assert.equal(page.browser.HermesCompanion.applySnapshot(snapshot({reply:{id:'r2',text:'updated '.repeat(80)}})),true);
assert.equal(page.nodes.companion.attributes['data-view'],'content','same-session refresh must not interrupt reading');
assert.deepEqual(page.arcs,borderAtEndpoint,'same-session refresh preserves current window phase');
assert.equal([...page.timers.values()].filter(t=>t.delay===250).length,1,'repeated snapshots do not add animation chains');
page.setClock(2050); page.runDelay(250);
assert.ok(page.arcs.some((dot,i)=>dot.x!==borderAtEndpoint[i].x||dot.y!==borderAtEndpoint[i].y),'window frame bobs while content remains readable');
assert.equal(page.browser.HermesCompanion.showFace(),true);
assert.equal(page.nodes.companion.attributes['data-view'],'face');
page.setClock(2450); page.runDelay(250);
assert.equal(page.arcs.length,1003,'return reassembles through a complete-dot midpoint');
page.setClock(2850); page.runDelay(250);
assert.equal(page.arcs.length,1003,'return endpoint is the exact complete head');

const continuity=mount(false);
continuity.browser.HermesCompanion.applySnapshot(snapshot({activity:'idle'}));
continuity.setClock(2400); continuity.runDelay(250);
const beforeRefresh=continuity.arcs.map(dot=>({...dot}));
continuity.browser.HermesCompanion.applySnapshot(snapshot({activity:'idle'}));
assert.deepEqual(continuity.arcs,beforeRefresh,'same-activity refresh must preserve authored motion phase');

const pollPage=mount(true);
pollPage.xhrs[1].status=200; pollPage.xhrs[1].responseText=JSON.stringify(snapshot({activity:'idle',reply:null})); pollPage.xhrs[1].onload();
assert.equal(pollPage.nodes.activity.textContent,'Idle');
assert.equal([...pollPage.timers.values()].filter(t=>t.delay===2000).length,1);
pollPage.runDelay(2000);
assert.equal(pollPage.xhrs.length,3);
assert.equal([...pollPage.timers.values()].some(t=>t.delay===2000),false,'no overlapping poll while XHR is active');
pollPage.xhrs[2].status=200; pollPage.xhrs[2].responseText='{bad json'; pollPage.xhrs[2].onload();
assert.equal(pollPage.nodes.activity.textContent,'Unknown','parse failure clears content and activity');

const still=mount(true);
still.browser.HermesCompanion.applySnapshot(snapshot({activity:'running'}));
assert.equal(still.browser.HermesCompanion.showContent(),true);
assert.equal(still.nodes.companion.attributes['data-view'],'content','reduced motion switches statically');
assert.equal(still.arcs.length,1003,'reduced motion settles to a complete static window frame');
assert.ok(still.arcs.some(dot=>dot.y>694),'reduced endpoint is the window, not the face');
still.browser.HermesCompanion.showFace();
assert.equal(still.nodes.companion.attributes['data-view'],'face');
assert.equal(still.arcs.length,1003,'reduced return settles to the exact face');
still.document.hidden=true; still.listeners.visibilitychange();
assert.equal(still.xhrs[1].aborted,true,'hidden page cancels the in-flight poll');
assert.equal([...still.timers.values()].some(t=>t.delay===2000),false,'hidden page has no poll timer');

const hiddenWindow=mount(false);
hiddenWindow.browser.HermesCompanion.applySnapshot(snapshot());
hiddenWindow.browser.HermesCompanion.showContent();
hiddenWindow.document.hidden=true; hiddenWindow.listeners.visibilitychange();
assert.equal(hiddenWindow.nodes.companion.attributes['data-view'],'content');
assert.equal(hiddenWindow.arcs.length,1003,'hidden transition settles to a static complete window frame');
assert.ok(hiddenWindow.arcs.some(dot=>dot.y>694));
assert.equal([...hiddenWindow.timers.values()].some(t=>t.delay===250),false,'hidden page cancels all render work');

const lostWindow=mount(false,true);
lostWindow.browser.HermesCompanion.applySnapshot(snapshot());
lostWindow.browser.HermesCompanion.showContent();
lostWindow.canvas.onwebglcontextlost();
assert.equal(lostWindow.canvas.attributes['data-renderer'],'canvas2d');
assert.equal(lostWindow.nodes.companion.attributes['data-view'],'content');
assert.equal(lostWindow.arcs.length,1003,'GPU loss settles to the static complete window frame');
assert.ok(lostWindow.arcs.some(dot=>dot.y>694));
assert.equal([...lostWindow.timers.values()].some(t=>t.delay===16||t.delay===250),false,'GPU loss leaves no sustained render work');
console.log('PASS companion API, dot-window lifecycle/snapshot continuity, reduced/static/GPU-loss endpoints and hidden cancellation');
