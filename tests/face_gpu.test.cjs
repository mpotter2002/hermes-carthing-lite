const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
require('../preview/face/desktop-glyph.js');
const {createDemo} = require('../preview/face/face.js');
const {createFrame} = require('../preview/face/gpu.js');
const asset = JSON.parse(fs.readFileSync(path.join(__dirname,'../preview/face/hermes-head.json')));
for (const state of ['idle','working']) {
  const glyph = createDemo(asset,state);
  const frame = createFrame(asset);
  const storage = frame.data;
  for (let i=0;i<120;i++) {
    const arcs=[];
    const ctx={clearRect(){},beginPath(){},arc(x,y,r){arcs.push([x,y,r,this.fillStyle]);},fill(){}};
    glyph.draw(ctx,i*.1,480);
    frame.draw(glyph,i*.1);
    assert.equal(frame.data,storage,'reuse the same bounded vertex buffer');
    assert.equal(frame.count,arcs.length);
    for(let n=0;n<arcs.length;n++) {
      const [x,y,r,c]=arcs[n], k=n*6;
      assert.ok(Math.abs(frame.data[k]-x)<.0001);
      assert.ok(Math.abs(frame.data[k+1]-y-(state==='working'?2:0))<.0001);
      assert.ok(Math.abs(frame.data[k+2]-r)<.0001);
      assert.ok(x-r>=0 && y+(state==='working'?2:0)-r>=0 && x+r<=480 && y+(state==='working'?2:0)+r<=480);
      const color=parseInt(c.slice(1),16);
      for(let ch=0;ch<3;ch++) assert.ok(Math.abs(frame.data[k+3+ch]-((color>>(16-ch*8))&255)/255)<1e-6);
    }
  }
  frame.still();
  assert.equal(frame.count,1003);
  asset.dots.forEach((d,i)=>{assert.ok(Math.abs(frame.data[i*6]-(d.x+.5)*10)<.0001);assert.ok(Math.abs(frame.data[i*6+2]-d.r*10)<.0001);});
}
console.log('PASS GPU geometry matches authored frames, palette and exact static source; orbit fits; vertex storage bounded');
