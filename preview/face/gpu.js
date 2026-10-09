(function (root) {
  'use strict';
  function createFrame(asset) {
    var frame = { data: new Float32Array(asset.dots.length * 6), count: 0 };
    var offset = 0, x, y, radius;
    var colors = {};
    asset.dots.forEach(function (dot) {
      var value = parseInt(dot.c.slice(1), 16);
      colors[dot.c] = [(value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255];
    });
    function put(color) {
      var k = frame.count++ * 6, rgb = colors[color];
      frame.data[k] = x; frame.data[k + 1] = y + offset; frame.data[k + 2] = radius;
      frame.data[k + 3] = rgb[0]; frame.data[k + 4] = rgb[1]; frame.data[k + 5] = rgb[2];
    }
    var bridge = {
      clearRect: function () { frame.count = 0; },
      beginPath: function () {},
      arc: function (a, b, r) { x = a; y = b; radius = r; },
      fill: function () { put(bridge.fillStyle); }
    };
    frame.draw = function (glyph, time) {
      offset = glyph.target().state === 'working' ? 2 : 0;
      glyph.draw(bridge, time, 480);
    };
    frame.still = function () {
      offset = 0; frame.count = 0;
      var scale = 480 / asset.grid;
      asset.dots.forEach(function (dot) {
        x = (dot.x + .5) * scale; y = (dot.y + .5) * scale; radius = dot.r * scale;
        put(dot.c);
      });
    };
    return frame;
  }
  function createPainter(canvas, asset) {
    var gl = canvas.getContext('webgl', { alpha: false, antialias: false, depth: false, stencil: false, preserveDrawingBuffer: false });
    if (!gl) return null;
    function shader(type, source) {
      var result = gl.createShader(type);
      gl.shaderSource(result, source); gl.compileShader(result);
      if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) throw new Error('Face shader failed');
      return result;
    }
    var program = gl.createProgram();
    var vertex = shader(gl.VERTEX_SHADER, 'attribute vec3 dot; attribute vec3 color; varying vec3 tint; varying float diameter; void main(){gl_Position=vec4(dot.x/240.0-1.0,1.0-dot.y/240.0,0.0,1.0); diameter=dot.z*2.0; gl_PointSize=diameter+2.0; tint=color;}');
    var fragment = shader(gl.FRAGMENT_SHADER, 'precision mediump float; varying vec3 tint; varying float diameter; void main(){float distance=length(gl_PointCoord-vec2(0.5))*(diameter+2.0); float alpha=1.0-smoothstep(diameter*0.5-0.5,diameter*0.5+0.5,distance); gl_FragColor=vec4(tint,alpha);}');
    gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Face shader link failed');
    gl.deleteShader(vertex); gl.deleteShader(fragment);
    gl.useProgram(program);
    var frame = createFrame(asset), buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, frame.data.byteLength, gl.DYNAMIC_DRAW);
    ['dot', 'color'].forEach(function (name, index) {
      var attribute = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(attribute); gl.vertexAttribPointer(attribute, 3, gl.FLOAT, false, 24, index * 12);
    });
    gl.viewport(0, 0, 480, 480); gl.clearColor(0, 0, 0, 1);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    return {
      draw: function (glyph, time) {
        if (glyph) frame.draw(glyph, time); else frame.still();
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, frame.data);
        gl.drawArrays(gl.POINTS, 0, frame.count);
      }
    };
  }
  root.FaceGPU = { createFrame: createFrame, createPainter: createPainter };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.FaceGPU;
}(typeof window !== 'undefined' ? window : globalThis));
