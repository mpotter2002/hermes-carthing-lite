const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
require('../preview/face/desktop-glyph.js');
const {createDemo,createTransition} = require('../preview/face/face.js');
const {createFrame,createPainter} = require('../preview/face/gpu.js');
let clearColor;
const gl = new Proxy({getShaderParameter(){return true;},getProgramParameter(){return true;},clearColor(...value){clearColor=value;}}, {get(target,key){return key in target ? target[key] : function(){};}});
let contextOptions;
createPainter({width:480,height:800,getContext(kind,options){contextOptions=options;return gl;}}, JSON.parse(fs.readFileSync(path.join(__dirname,'../preview/face/hermes-head.json'))));
assert.equal(contextOptions.alpha,true,'WebGL context must allow composition with the text and controls');
assert.deepEqual(clearColor,[0,0,0,0],'empty canvas pixels cannot paint over the interface beneath the GPU surface');
const asset = JSON.parse(fs.readFileSync(path.join(__dirname,'../preview/face/hermes-head.json')));
for (const state of ['idle','working']) {
  const glyph = createDemo(asset,state);
  const frame = createFrame(asset);
  const storage = frame.data;
  for (let i=0;i<120;i++) {
    const arcs=[];
    const ctx={globalAlpha:1,clearRect(){},beginPath(){},arc(x,y,r){arcs.push([x,y,r,this.fillStyle,this.globalAlpha]);},fill(){}};
    glyph.draw(ctx,i*.1,480);
    frame.draw(glyph,i*.1);
    assert.equal(frame.data,storage,'reuse the same bounded vertex buffer');
    assert.equal(frame.count,arcs.length);
    for(let n=0;n<arcs.length;n++) {
      const [x,y,r,c,a]=arcs[n], k=n*7;
      assert.ok(Math.abs(frame.data[k]-x)<.0001);
      assert.ok(Math.abs(frame.data[k+1]-y-(state==='working'?2:0))<.0001);
      assert.ok(Math.abs(frame.data[k+2]-r)<.0001);
      assert.ok(x-r>=0 && y+(state==='working'?2:0)-r>=0 && x+r<=480 && y+(state==='working'?2:0)+r<=480);
      const color=parseInt(c.slice(1),16);
      for(let ch=0;ch<3;ch++) assert.ok(Math.abs(frame.data[k+3+ch]-((color>>(16-ch*8))&255)/255)<1e-6);
      assert.ok(Math.abs(frame.data[k+6]-a)<1e-6);
    }
  }
  frame.still();
  assert.equal(frame.count,1003);
  assert.equal(frame.data.byteLength, 1003*7*4, 'fixed seven-float vertex storage includes authored alpha');
  asset.dots.forEach((d,i)=>{assert.ok(Math.abs(frame.data[i*7]-(d.x+.5)*10)<.0001);assert.ok(Math.abs(frame.data[i*7+2]-d.r*10)<.0001);assert.equal(frame.data[i*7+6],1);});
}
const portraitFrame=createFrame(asset,800);
portraitFrame.still();
assert.equal(portraitFrame.data.byteLength,1003*7*4);
asset.dots.forEach((dot,i)=>{
  assert.ok(Math.abs(portraitFrame.data[i*7]-(4+(dot.x+.5)*472/48))<.0001);
  assert.ok(Math.abs(portraitFrame.data[i*7+1]-((dot.y+.5)*472/48+112))<.0001,'portrait face keeps accepted placement');
});
const transition=createTransition(asset,800); transition.settle(1,1600);
const transitionGlyph={target(){return {state:'transition'};},draw(ctx,time){
  ctx.clearRect(); transition.frame(time,true).forEach(dot=>{ctx.fillStyle=dot.c;ctx.globalAlpha=dot.a;ctx.beginPath();ctx.arc(dot.x,dot.y,dot.r);ctx.fill();});
}};
const transitionStorage=portraitFrame.data;
portraitFrame.draw(transitionGlyph,1600);
assert.equal(portraitFrame.data,transitionStorage,'dot window reuses the fixed portrait GPU vertex buffer');
assert.equal(portraitFrame.count,1003);
assert.ok(Array.from({length:portraitFrame.count},(_,i)=>portraitFrame.data[i*7+1]).some(y=>y>694),'transition coordinates are not face-offset twice');
assert.ok(Array.from({length:portraitFrame.count},(_,i)=>portraitFrame.data[i*7+6]).every(a=>a===1),'all window dots stay visible');
console.log('PASS portrait GPU frame placement and fixed-buffer 1003-dot window endpoint');

const alphaGlyph = new globalThis.BotGlyph.Glyph(JSON.parse(JSON.stringify(asset)), {botId:'hermes',set:'bold',variantMode:'fixed',variants:{working:'ring'},blinkPeriod:3.8,breathe:2.2});
alphaGlyph.jump('working',0,'ring');
const alphaFrame=createFrame(asset); alphaFrame.draw(alphaGlyph,1.2);
assert.ok(Array.from({length:alphaFrame.count},(_,i)=>alphaFrame.data[i*7+6]).some(a=>a<.99),'GPU retains selected authored ring alpha');
console.log('PASS GPU geometry, authored alpha, palette and exact static source; orbit fits; vertex storage bounded');
