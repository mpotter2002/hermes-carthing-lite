/* glyph.js: BotGlyph state engine for the dot-bust avatars (Hermes Agent).
 * Pure per-dot pose functions + a tiny controller. No dependencies, no DOM except Glyph.draw(ctx,...).
 * Input: the bust JSON {grid, bot, color, highlight?, dots:[{x, y, r, c, p, lid?, lc?}]}, optionally with .poses attached
 * (states/poses/<god>.json: the same bust re-rendered from the 3D scan at yaw -30..30 and pitch -16..16; used by MOVEMENT variants).
 * Every animation only changes a dot's position, radius or opacity; the colour is always the bot's own colour (+ its highlight tier).
 * States: idle | working | connecting | thinking | done (one-shot, settles to idle) | error (entry + loop).
 * Two variant sets: 'bold' (default; families DISSOLVE and MOVEMENT) and 'subtle' (the first round, kept as an effects library). */
(function (root) {
'use strict';
const TAU = Math.PI * 2;
const cl = x => (x < 0 ? 0 : x > 1 ? 1 : x);
const smooth = x => { x = cl(x); return x * x * (3 - 2 * x); };
const easeOut = x => 1 - Math.pow(1 - cl(x), 3);
const frac = x => x - Math.floor(x);
const wrap = x => frac(x + 0.5) - 0.5;                       // signed distance on a unit circle
const gauss = (x, w) => Math.exp(-(x * x) / (w * w));
const lerp = (a, b, w) => a + (b - a) * w;
const bell = x => Math.sin(Math.PI * cl(x));
function hash(s) { let h = 2166136261 >>> 0; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function rng(seed) { let s = (seed >>> 0) || 1; return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296); }
const h2 = (a, b) => frac(Math.sin(a * 12.9898 + b * 78.233) * 43758.5453);   // cheap per-dot, per-step noise
// keyframed value: K = [[t, v], ...] (t ascending, seconds), smoothstep between keys
function kf(K, t) { if (t <= K[0][0]) return K[0][1]; for (let i = 1; i < K.length; i++) if (t <= K[i][0]) return lerp(K[i - 1][1], K[i][1], smooth((t - K[i - 1][0]) / (K[i][0] - K[i - 1][0]))); return K[K.length - 1][1]; }

const STATES = ['idle', 'working', 'connecting', 'thinking', 'done', 'error'];
const SETS = {
  bold: {
    idle: ['idle'],
    working: ['ring', 'orbit', 'focus'],
    connecting: ['gather', 'scan', 'listen'],
    thinking: ['vortex', 'galaxy', 'halo', 'muse'],
    done: ['confetti', 'spin', 'proud'],
    error: ['shatter', 'static', 'slump', 'shake'],
  },
  subtle: {
    idle: ['idle'],
    working: ['shimmer', 'contour', 'pulse'],
    connecting: ['radar', 'tide', 'pingpong'],
    thinking: ['rising', 'murmur', 'ponder'],
    done: ['glow', 'nod', 'sparkle'],
    error: ['dim', 'thin', 'sag'],
  },
};
const VARIANTS = SETS.subtle;      // back-compat alias (round 1)
const TSETS = { bold: ['swirl', 'scatter', 'turn'], subtle: ['assemble', 'sweep', 'dissolve'] };
const TRANSITIONS = TSETS.subtle;  // back-compat alias
const FAMILY = { idle: 'idle' };
for (const k of ['vortex', 'galaxy', 'halo', 'ring', 'orbit', 'gather', 'confetti', 'shatter', 'static', 'swirl', 'scatter']) FAMILY[k] = 'dissolve';
for (const k of ['muse', 'focus', 'scan', 'listen', 'spin', 'proud', 'slump', 'shake', 'turn']) FAMILY[k] = 'movement';
for (const s of STATES) for (const v of SETS.subtle[s]) if (v !== 'idle') FAMILY[v] = 'subtle';
for (const k of TSETS.subtle) FAMILY[k] = 'subtle';
const TDUR = { assemble: 0.8, sweep: 0.7, dissolve: 0.6, swirl: 1.2, scatter: 1.0, turn: 0.9, blend: 0.25, rm: 0.2 };   // seconds
const DONE_LEN = { glow: 1.0, nod: 1.0, sparkle: 1.2, confetti: 2.2, spin: 1.6, proud: 1.6, rm: 0.6 };                // one-shot, then idle
const ENTRY = { dim: 0.8, thin: 0.8, sag: 0.8, shatter: 1.2, static: 0.6, slump: 1.0, shake: 1.2 };                     // error entry
const ERROR_ENTRY = 0.8;
// loop periods (s); all divide 4.8 so previews loop seamlessly
const PERIOD = { shimmer: 2.4, contour: 1.6, pulse: 1.2, radar: 2.4, tide: 2.4, pingpong: 2.4, rising: 2.4, murmur: 4.8, ponder: 4.8, dim: 2.4, thin: 2.4, sag: 2.4,
  ring: 2.4, orbit: 1.2, focus: 2.4, gather: 2.4, scan: 4.8, listen: 4.8, vortex: 4.8, galaxy: 4.8, halo: 4.8, muse: 4.8, shatter: 2.4, static: 2.4, slump: 4.8, shake: 4.8 };
const BREATH_AMP = { idle: 1, working: 0.5, connecting: 0.5, thinking: 0.6, done: 1, error: 1 };

// ---------- per-avatar constants (once) ----------
function prep(av) {
  if (av._g) return av;
  const N = av.grid, R = rng(hash(String(av.bot || av.god || 'bot'))), col = av.color || av.dots[0].c;
  const dots = av.dots.slice(); const PZ = av.poses && typeof av.poses.r === 'string' ? av.poses : null;
  if (PZ) for (const [x, y, p] of PZ.extra) dots.push({ x, y, r: 0, c: col, p, extra: true });
  const live = dots.filter(d => d.r > 0.05);
  const occ = new Set(live.map(d => d.x + ',' + d.y));
  let sx = 0, sy = 0, sw = 0, ymin = 1e9, ymax = -1e9, xmin = 1e9, xmax = -1e9;
  for (const d of live) { sx += d.x * d.r; sy += d.y * d.r; sw += d.r; ymin = Math.min(ymin, d.y); ymax = Math.max(ymax, d.y); xmin = Math.min(xmin, d.x); xmax = Math.max(xmax, d.x); }
  const cx = sx / sw, cy = sy / sw;
  // neck = narrowest row between 40% and 85% of the height; head = everything above it
  const rows = {}; for (const d of live) { const w = rows[d.y] || (rows[d.y] = [1e9, -1e9]); w[0] = Math.min(w[0], d.x); w[1] = Math.max(w[1], d.x); }
  let neckY = Math.round(ymin + 0.62 * (ymax - ymin)), best = 1e9;
  for (let y = Math.round(ymin + 0.4 * (ymax - ymin)); y <= ymin + 0.85 * (ymax - ymin); y++) if (rows[y]) { const w = rows[y][1] - rows[y][0]; if (w < best - 0.5) { best = w; neckY = y; } }
  let hxs = 0, hys = 0, hn = 0, hx0 = 1e9, hx1 = -1e9;
  for (const d of live) if (d.y < neckY) { hxs += d.x; hys += d.y; hn++; hx0 = Math.min(hx0, d.x); hx1 = Math.max(hx1, d.x); }
  const hx = hn ? hxs / hn : cx, hy = hn ? hys / hn : cy, hr = Math.max(4, Math.max((hx1 - hx0) / 2, (neckY - ymin) / 2));
  const eyes = dots.filter(d => d.p === 'eye' || d.p === 'glint'); const ey = eyes.map(d => d.y);
  const e0 = ey.length ? Math.min(...ey) : 0, e1 = ey.length ? Math.max(...ey) : 1;
  // shards for error/shatter: seeded points inside the bust, one (lower-right) falls
  // shards for error/shatter: one around the face + six around it (jittered ring); the lower-right one falls
  const seeds = [[cx + (R() - 0.5) * 2, cy - 2 + (R() - 0.5) * 2, (R() - 0.5) * 0.08]], rr = 0.62 * Math.max(xmax - xmin, ymax - ymin) / 2, a0 = R() * TAU / 6;
  for (let k = 0; k < 6; k++) { const an = a0 + k * TAU / 6 + (R() - 0.5) * 0.5; seeds.push([cx + rr * Math.cos(an) * (0.85 + 0.3 * R()), cy + rr * Math.sin(an) * (0.85 + 0.3 * R()), (R() < 0.5 ? -1 : 1) * (0.08 + 0.1 * R())]); }
  let fall = 1; seeds.forEach((s, k) => { if (k && (s[0] - cx) + 1.4 * (s[1] - cy) > (seeds[fall][0] - cx) + 1.4 * (seeds[fall][1] - cy)) fall = k; });
  let maxD = 1; const hlR = av.highlight ? Math.min(...av.dots.filter(d => d.c === av.highlight).map(d => d.r).concat([9])) : 9;
  const yawT = PZ && PZ.yaw, pitT = PZ && PZ.pitch;
  let PA = null;                                             // pose grid: Uint8 [pitch][yaw][cell], r = byte / scale
  if (PZ) { const bin = typeof atob === 'function' ? atob(PZ.r) : Buffer.from(PZ.r, 'base64').toString('binary'); PA = new Uint8Array(bin.length); for (let k = 0; k < bin.length; k++) PA[k] = bin.charCodeAt(k); }
  dots.forEach((d, i) => {
    const dist = Math.hypot(d.x - cx, d.y - cy); maxD = Math.max(maxD, dist);
    let edge = 0;                                            // 1 on the silhouette, 0.6 one cell in
    for (let k = 1; k <= 2 && !edge; k++)
      for (let dy = -k; dy <= k && !edge; dy++) for (let dx = -k; dx <= k; dx++)
        if (!occ.has((d.x + dx) + ',' + (d.y + dy))) { edge = k === 1 ? 1 : 0.6; break; }
    let s1 = 0, b1 = 1e9, b2 = 1e9;
    seeds.forEach((s, k) => { const q = Math.hypot(d.x - s[0], d.y - s[1]); if (q < b1) { b2 = b1; b1 = q; s1 = k; } else if (q < b2) b2 = q; });
    d.g = {
      i, dist, dc: Math.hypot(d.x - N / 2, d.y - N / 2),     // dc = distance from grid centre (idle ripple, as in demo.html)
      a01: frac(Math.atan2(d.y - cy, d.x - cx) / TAU + 0.25), // 0 at 12 o'clock, clockwise
      ux: (d.x - cx) / (dist || 1), uy: (d.y - cy) / (dist || 1),
      ny: (d.y - ymin) / Math.max(1, ymax - ymin), side: (d.x - cx) / Math.max(1, (xmax - xmin) / 2),
      er: (d.y - e0) / Math.max(1, e1 - e0), edge, rnd: R(), rnd2: R(), rnd3: R(), sx: R() * N, sy: R() * N,
      hw: smooth((neckY + 1.5 - d.y) / 3),                    // 1 = head, 0 = shoulders
      shard: s1, crack: b2 - b1 < 1.3,
    };
    if (PZ) { d.g.pose = true; d.g.r0 = PA[(pitT.indexOf(0) * yawT.length + yawT.indexOf(0)) * PZ.ncells + i] / PZ.scale; }
  });
  av._g = { N, cx, cy, maxD, dots, col, hlR, hx, hy, hr, neckY, ymin, ymax, xmin, xmax, px: hx, py: neckY + 1, seeds, fall, yawT, pitT, PA, nc: PZ ? PZ.ncells : 0, sc: PZ ? PZ.scale : 1 };
  return av;
}
// ---------- movement helpers ----------
function seg(T, v) { const n = T.length; v = Math.max(T[0], Math.min(T[n - 1], v)); let i = 1; while (i < n - 1 && T[i] < v) i++; return [i - 1, (v - T[i - 1]) / (T[i] - T[i - 1])]; }
/* radius change when the head turns (yaw > 0: towards the viewer's right) / looks up (pitch > 0): bilinear in the grid of 3D re-renders */
function poseDelta(d, G, yaw, pitch) {
  if (!d.g.pose) return 0; const [yi, wy] = seg(G.yawT, yaw), [pi, wp] = seg(G.pitT, pitch), ny = G.yawT.length, nc = G.nc, i = d.g.i, A = G.PA;
  const at = (p, y) => A[(p * ny + y) * nc + i];
  const r = (at(pi, yi) * (1 - wy) + at(pi, yi + 1) * wy) * (1 - wp) + (at(pi + 1, yi) * (1 - wy) + at(pi + 1, yi + 1) * wy) * wp;
  return r / G.sc - d.g.r0;
}
function look(o, d, G, yaw, pitch) {
  if (!yaw && !pitch) return;
  if (d.g.pose) { o.r = Math.max(0, o.r + poseDelta(d, G, yaw, pitch)); return; }
  // fallback without pose data: shift head dots (fake parallax)
  const w = d.g.hw, z = Math.max(0, 1 - Math.abs(d.x - G.hx) / (G.hr + 1));
  o.x += w * (yaw / 30) * (0.6 + 1.4 * z); o.y -= w * (pitch / 16) * (0.4 + 0.9 * z);
}
function roll(o, d, G, ang) {                // tilt the head about the neck
  if (!ang) return; const a = ang * d.g.hw, c = Math.cos(a), s = Math.sin(a), dx = o.x - G.px, dy = o.y - G.py;
  o.x = G.px + dx * c - dy * s; o.y = G.py + dx * s + dy * c;
}
const isEye = d => d.p === 'eye' || d.p === 'glint';
function lids(o, d, amt) { if (isEye(d) && d.lid && d.g.er < 0.15 + 0.85 * amt) o.r = lerp(o.r, Math.max(o.r, d.lid), cl(amt * 1.5)); }
function goTo(o, x, y, w) { o.x = lerp(o.x, x, w); o.y = lerp(o.y, y, w); }

// ---------- idle (unchanged from demo.html: breathing ripple + blink) ----------
function eyeR(d, blink) {
  if (isEye(d) && blink > 0 && d.lid) {
    if (d.g.er <= blink * 1.15) return Math.abs(d.g.er - 0.62) < 0.17 && blink > 0.8 ? d.lid * 0.38 : d.lid;
    if (d.p === 'glint') return d.r * (1 - blink);
  }
  return d.r;
}
function blinkAt(T, av, period) {         // 0..1..0 over 0.26 s, once per period, offset per avatar
  const bt = (T + (av.dots.length % 7) * 0.37) % period;
  return bt < 0.26 ? Math.sin(Math.PI * bt / 0.26) : 0;
}
function base(o, d, T, P, amp) {
  o.x = d.x; o.y = d.y; o.a = 1;
  o.r = eyeR(d, P.blink) * (1 + 0.07 * amp * Math.sin(T * P.breathe - d.g.dc * 0.45));
}

// ---------- variants: f(o, d, t, P, G) with t = seconds since the state was entered ----------
const MOD = {
  idle() {},
  // ===== subtle set (round 1) =====
  shimmer(o, d, t, P, G) { const w = -0.3 * G.N + frac(t / PERIOD.shimmer) * 1.6 * G.N; const k = gauss((d.x + d.y) * 0.5 - w, 3.2); o.r = Math.min(0.62, o.r + 0.17 * k); },
  contour(o, d, t, P, G) { const k = gauss(wrap(d.g.a01 - frac(t / PERIOD.contour)), 0.1) * d.g.edge; o.r = Math.min(0.64, o.r * (1 + 0.8 * k) + 0.14 * k); },
  pulse(o, d, t, P, G) { const ph = frac(t / PERIOD.pulse); const pos = -4 + ph * (G.maxD * 1.25 + 4);
    const k = gauss(d.g.dist - pos, 2.6) * smooth(ph / 0.15); o.r = Math.min(0.62, o.r * (1 + 0.4 * k)); o.x += 0.22 * k * d.g.ux; o.y += 0.22 * k * d.g.uy; },
  radar(o, d, t) { const da = frac(d.g.a01 - frac(t / PERIOD.radar)); const k = da > 0.5 ? Math.exp(-(1 - da) * 7) : gauss(da, 0.02); o.a = 0.45 + 0.55 * k; o.r *= 0.9 + 0.22 * k; },
  tide(o, d, t, P, G) { const q = t / PERIOD.tide - (1 - d.g.dist / G.maxD) * 0.3; const v = 0.5 - 0.5 * Math.cos(TAU * q); o.a = 0.4 + 0.6 * v; o.r *= 0.92 + 0.1 * v; },
  pingpong(o, d, t) { const ph = frac(t / PERIOD.pingpong); const s = d.g.side;
    const k = smooth((-s + 0.1) / 0.6) * gauss(wrap(ph - 0.12), 0.11) + smooth((s + 0.1) / 0.6) * gauss(wrap(ph - 0.62), 0.11);
    o.a = 0.55 + 0.45 * k; o.r *= 0.92 + 0.22 * k; o.x += 0.35 * k * Math.sign(s); },
  rising(o, d, t) { const w = smooth((0.62 - d.g.ny) / 0.3); const v = 0.5 + 0.5 * Math.sin(TAU * frac(t / PERIOD.rising) + d.y * 0.42);
    o.r = Math.min(0.64, o.r * (1 + w * (0.75 * v * v * v * v - 0.14))); },
  murmur(o, d, t) { const ph = TAU * frac(t / PERIOD.murmur);
    o.x += 0.2 * Math.sin(ph + d.y * 0.38); o.y += 0.16 * Math.cos(2 * ph + d.x * 0.33); o.r *= 1 + 0.24 * Math.sin(ph + d.x * 0.26) * Math.sin(d.y * 0.24 - ph); },
  ponder(o, d, t, P, G) { if (isEye(d) && d.g.er < 0.5 && d.lid) o.r = Math.max(o.r, d.lid);
    const c = G.N * (0.5 + 0.3 * Math.sin(TAU * frac(t / PERIOD.ponder))); o.r = Math.min(0.62, o.r * (0.8 + 0.45 * gauss(d.x - c, 6))); },
  glow(o, d, t, P, G) { const p = t / DONE_LEN.glow; if (p >= 1) return; const k = gauss(d.g.dist - easeOut(p) * G.maxD * 1.2, 3) * Math.pow(1 - p, 1.2) * smooth(p / 0.08);
    o.r = Math.min(0.66, o.r * (1 + 0.8 * k) + 0.07 * k); },
  nod(o, d, t) { const p = t / DONE_LEN.nod; if (p >= 1) return; const e = Math.pow(Math.sin(Math.PI * p), 2); o.y += 1.1 * e * (1 - 0.7 * d.g.ny); o.r *= 1 + 0.12 * e; },
  sparkle(o, d, t) { const p = t / DONE_LEN.sparkle; if (p >= 1 || d.g.rnd2 > 0.28) return; const k = Math.sin(Math.PI * cl((p - d.g.rnd * 0.55) / 0.45)); o.r = Math.min(0.66, o.r * (1 + 0.9 * k)); },
  dim(o, d, t) { const e = smooth(t / ERROR_ENTRY); o.a = 1 - 0.5 * e; if (d.g.rnd2 < 0.1) o.a *= 1 - 0.55 * e * gauss(wrap(t / PERIOD.dim - d.g.rnd), 0.05); },
  thin(o, d, t) { const ed = smooth((t - d.g.rnd * 0.4) / 0.5); if ((d.x + d.y) % 2) o.r *= 1 - 0.65 * ed;
    o.a = 1 - smooth(t / ERROR_ENTRY) * (0.15 + 0.12 * (0.5 - 0.5 * Math.cos(TAU * t / PERIOD.thin))); },
  sag(o, d, t, P, G) { const e = smooth(t / ERROR_ENTRY); o.y += 0.5 * e * d.g.ny; o.a = 1 - 0.3 * e;
    const n = Math.floor(t / PERIOD.sag), ph = frac(t / PERIOD.sag); const b = Math.floor(G.N * (0.3 + 0.4 * frac(n * 0.618 + 0.2)));
    if (t > ERROR_ENTRY && ph > 0.1 && ph < 0.16 && d.y >= b && d.y < b + 2) o.x += 1; },

  // ===== bold set: THINKING =====
  // vortex (dissolve): every 4.8 s the face lifts into a spinning vortex of dots (inner rings faster) and re-forms
  vortex(o, d, t, P, G) { const g = d.g, f = frac(t / 4.8);
    const w = smooth((f - 0.16 - 0.1 * g.rnd) / 0.14) * (1 - smooth((f - 0.72 - 0.1 * g.rnd) / 0.16)); if (w <= 0) return;
    const Rv = G.N * 0.44, rs = Rv * (0.12 + 0.88 * Math.pow(g.rnd2, 0.75)), ts = Math.max(0, f - 0.16) * 4.8;
    const an = g.a01 * TAU + ts * (1.1 + 3.2 * (1 - rs / Rv)) + g.rnd3 * 0.6;
    goTo(o, G.cx + rs * Math.sin(an), G.cy - 0.82 * rs * Math.cos(an), w); o.r = lerp(o.r, 0.13 + 0.25 * (1 - rs / Rv), w); },
  // galaxy (dissolve): ~40% of the dots peel off into three spiral arms that rotate around a faint face
  galaxy(o, d, t, P, G) { const g = d.g, e = smooth(t / 1.0);
    o.r *= 1 - 0.22 * e; o.a = 1 - 0.3 * e; if (g.rnd2 > 0.36) return;
    const s = frac(g.rnd * 7.13), k = Math.floor(g.rnd3 * 3), Rg = G.N * 0.5, rho = Rg * (0.16 + 0.84 * s);
    const an = k * TAU / 3 + s * 3.6 + TAU * t / 4.8 + (g.rnd - 0.5) * 0.35;
    const wd = smooth((t - g.rnd * 0.6) / 0.8);
    goTo(o, G.hx + rho * Math.cos(an), G.hy + 0.55 * rho * Math.sin(an) + 1.5, wd); o.r = lerp(o.r, 0.4 - 0.24 * s, wd); o.a = lerp(o.a, 1, wd); },
  // halo (dissolve): dots stream up out of the head into an orbiting halo ring, circle, and fall back into place
  halo(o, d, t, P, G) { const g = d.g; if (g.ny > 0.5 || g.rnd2 > 0.55) { o.a = 1; return; }
    const f = frac(t / 4.8), u = smooth((f - 0.04 - 0.26 * g.rnd) / 0.2), dn = smooth((f - 0.68 - 0.2 * g.rnd) / 0.18); const w = u * (1 - dn); if (w <= 0) return;
    const an = g.rnd3 * TAU + TAU * t / 2.4, rx = G.hr * 1.45 + 2.4 * g.rnd2, ry = 4.2 + 1.2 * g.rnd, yc = G.ymin + 4.5;
    const front = Math.sin(an) > 0;
    const X = G.hx + rx * Math.cos(an), Y = yc + ry * Math.sin(an);
    o.x = lerp(o.x, X, w); o.y = lerp(o.y, Y, w) - (dn > 0 ? 0 : 2.5 * bell(u)); o.r = lerp(o.r, front ? 0.38 : 0.24, w); o.a = lerp(1, front ? 1 : 0.5, w); },
  // muse (movement): the head looks up-left, back, then up-right, pondering (real yaw/pitch re-renders)
  muse(o, d, t, P, G) { const f = frac(t / 4.8) * 4.8;
    const yaw = kf([[0, 0], [0.7, -24], [1.9, -24], [2.5, 0], [3.1, 24], [4.2, 24], [4.8, 0]], f);
    const pit = kf([[0, 0], [0.7, 12], [1.9, 12], [2.5, 2], [3.1, 10], [4.2, 10], [4.8, 0]], f);
    look(o, d, G, yaw, pit); roll(o, d, G, -yaw / 24 * 0.07); },

  // ===== bold set: WORKING =====
  // ring (dissolve): silhouette dots lift into a tilted ring that spins around the bust; the head looks down at the work
  ring(o, d, t, P, G) { const g = d.g, e = smooth(t / 0.6); look(o, d, G, 0, -8 * e);
    if (!(g.edge === 1 && g.rnd2 < 0.7)) return;
    const an = g.a01 * TAU + TAU * t / 2.4, wd = smooth((t - 0.4 * g.rnd) / 0.6), tl = -0.22;
    const ex = 0.47 * G.N * Math.cos(an), ey = 0.12 * G.N * Math.sin(an), front = Math.sin(an) > 0;
    goTo(o, G.cx + ex * Math.cos(tl) - ey * Math.sin(tl), G.cy + 2 + ex * Math.sin(tl) + ey * Math.cos(tl), wd);
    o.r = lerp(o.r, front ? 0.4 : 0.22, wd); o.a = lerp(1, front ? 1 : 0.45, wd); },
  // orbit (dissolve): three comet trains of dots race around the head on tilted orbits, like busy electrons
  orbit(o, d, t, P, G) { const g = d.g; o.r *= 0.92; if (g.rnd2 > 0.2) return;
    const k = Math.floor(g.rnd * 3), s = frac(g.rnd * 17.3), tilt = k * TAU / 3 + 0.3;
    const u = TAU * (t / 1.2 + k / 3) - s * 1.9, ex = 0.45 * G.N * Math.cos(u), ey = 0.13 * G.N * Math.sin(u);
    const wd = smooth((t - 0.3 * g.rnd) / 0.5);
    goTo(o, G.hx + ex * Math.cos(tilt) - ey * Math.sin(tilt), G.hy + 2 + ex * Math.sin(tilt) + ey * Math.cos(tilt), wd);
    o.r = lerp(o.r, 0.12 + 0.42 * Math.pow(1 - s, 1.2), wd); },
  // focus (movement): head bowed over the work, eyes sweeping along a line and snapping back, a small nod per line
  focus(o, d, t, P, G) { const e = smooth(t / 0.5), f = frac(t / 2.4) * 2.4;
    const yaw = kf([[0, -14], [0.9, 14], [1.2, -14], [2.1, 14], [2.4, -14]], f) * e;
    const pit = (-12 - 5 * (bell((f - 0.9) / 0.3) + bell((f - 2.1) / 0.3))) * e;
    look(o, d, G, yaw, pit); },

  // ===== bold set: CONNECTING =====
  // gather (dissolve): the bust breathes out into a loose cloud of dots and pulls back together, over and over
  gather(o, d, t, P, G) { const g = d.g, f = frac(t / 2.4);
    const w = smooth((f - 0.05 * g.rnd) / 0.28) * (1 - smooth((f - 0.42 - 0.08 * g.rnd) / 0.26)); if (w <= 0) return;
    const rad = (0.6 + 0.1 * g.dist + 1.4 * g.rnd2) * w, wob = 0.5 * w * Math.sin(TAU * f * 2 + g.rnd * TAU);
    o.x += g.ux * rad + wob * g.uy; o.y += g.uy * rad - wob * g.ux; o.r *= 1 - 0.18 * w; o.a = 1 - 0.15 * w; },
  // scan (movement): the head turns all the way left, holds, then all the way right, searching
  scan(o, d, t, P, G) { const f = frac(t / 4.8) * 4.8;
    const yaw = kf([[0, 0], [0.6, -30], [1.6, -30], [2.4, 0], [3.0, 30], [4.0, 30], [4.8, 0]], f);
    look(o, d, G, yaw, 4 * Math.abs(yaw) / 30); },
  // listen (movement): turns an ear and tilts the head, eyes half closed, leaning in; then the other side
  listen(o, d, t, P, G) { const f = frac(t / 4.8) * 4.8;
    const s = kf([[0, 0], [0.6, 1], [2.0, 1], [2.8, -1], [4.2, -1], [4.8, 0]], f);
    look(o, d, G, 16 * s, -4 * Math.abs(s)); roll(o, d, G, 0.16 * s); lids(o, d, 0.55 * Math.abs(s));
    o.x += 0.8 * s * (1 - d.g.hw); },

  // ===== bold set: DONE (one-shots that end exactly on the idle pose) =====
  // confetti (dissolve): a quick wind-up, the bust bursts into fluttering confetti that falls, flies back together, and nods
  confetti(o, d, t, P, G) { const g = d.g, p = t / DONE_LEN.confetti; if (p >= 1) return;
    if (p < 0.12) { const a = bell(p / 0.24) * 0.3; o.r *= 1 - a; o.x -= g.ux * a * 2; o.y -= g.uy * a * 2; return; }
    const tb = cl((p - 0.12) / 0.4), back = smooth((p - 0.46 - 0.1 * g.rnd) / 0.3);
    const sp = 5 + 15 * g.rnd2, bx = (g.ux + (g.rnd - 0.5) * 0.8) * sp * easeOut(tb), by = (g.uy + (g.rnd3 - 0.5) * 0.8) * sp * easeOut(tb) + 12 * tb * tb;
    o.x += bx * (1 - back); o.y += by * (1 - back);
    o.r = lerp(o.r * (0.55 + 0.45 * Math.abs(Math.sin(14 * tb + g.rnd * 6))), o.r, back);
    if (p > 0.82) look(o, d, G, 0, -12 * bell((p - 0.82) / 0.18)); },
  // spin (movement): a full victory turn (the bust flips around its vertical axis), then a nod
  spin(o, d, t, P, G) { const p = t / DONE_LEN.spin; if (p >= 1) return; const a = TAU * easeOut(cl(p / 0.7)) ;
    const c = Math.cos(a); o.x = G.cx + (o.x - G.cx) * c; if (c < 0) o.a = 0.55; o.r *= 0.75 + 0.25 * Math.abs(c);
    if (p > 0.7) look(o, d, G, 0, -12 * bell((p - 0.7) / 0.3)); },
  // proud (movement): chin up, a deep satisfied nod and a smaller second one, eyes closing contentedly
  proud(o, d, t, P, G) { const p = t / DONE_LEN.proud; if (p >= 1) return; const s = p * DONE_LEN.proud;
    look(o, d, G, kf([[0, 0], [0.5, 8], [1.0, 0], [1.6, 0]], s), kf([[0, 0], [0.22, 10], [0.55, -16], [0.85, 4], [1.15, -8], [1.6, 0]], s));
    lids(o, d, bell((s - 0.35) / 1.0)); },

  // ===== bold set: ERROR (entry, then a loop until the next state) =====
  // shatter (dissolve): cracks run through the bust, the shards part, one falls away; shards jolt and the lost piece flickers back
  shatter(o, d, t, P, G) { const g = d.g, E = ENTRY.shatter, S = G.seeds[g.shard];
    if (g.crack) o.r *= 1 - smooth((t - g.rnd * 0.25) / 0.25);
    const sep = smooth((t - 0.25) / 0.35), dx = S[0] - G.cx, dy = S[1] - G.cy;
    let X = o.x + dx * 0.16 * sep, Y = o.y + dy * 0.16 * sep; const a = S[2] * sep, cs = Math.cos(a), sn = Math.sin(a);
    const rx = X - S[0], ry = Y - S[1]; X = S[0] + rx * cs - ry * sn; Y = S[1] + rx * sn + ry * cs;
    if (g.shard === G.fall) { const tf = cl((t - 0.55) / 0.65); Y += 34 * tf * tf; X += 2 * tf;
      if (t > E) { const ph = frac((t - E) / 2.4); if (ph > 0.5 && ph < 0.58 && h2(g.i, Math.floor(t * 20)) > 0.4) { X = o.x; Y = o.y; o.a = 0.5; } } }
    else if (t > E) { const n = Math.floor((t - E) / 2.4), ph = frac((t - E) / 2.4); if (ph > 0.1 && ph < 0.16 && g.shard === n % G.seeds.length) X += 1; }
    o.x = X; o.y = Y; },
  // static (dissolve): the bust tears into offset bands; dots flicker like dead pixels; every 2.4 s it breaks into a noise field
  static(o, d, t, P, G) { const g = d.g, e = smooth(t / ENTRY.static), q = Math.floor(t * 12);
    const band = Math.floor(d.y / 3), off = Math.round((h2(band, Math.floor(t * 3)) - 0.5) * 3.2 * e);
    o.x += off * (h2(band + 7, Math.floor(t * 3)) > 0.55 ? 1 : 0);
    const n = h2(g.i, q); if (n < 0.16 * e) o.r = 0.5 * h2(g.i + 3, q); else if (n > 1 - 0.08 * e) o.r = 0;
    const ph = frac(t / 2.4); if (t > ENTRY.static && ph < 0.12) { const k = bell(ph / 0.12);
      goTo(o, lerp(G.xmin, G.xmax, h2(g.i, q + 1)), lerp(G.ymin, G.ymax, h2(g.i + 1, q + 2)), k); o.r = lerp(o.r, 0.22, k); } },
  // slump (movement): the head drops and tilts, eyes close, shoulders sag; a slow tired sway, dots drip from the chin
  slump(o, d, t, P, G) { const g = d.g, e = smooth(t / ENTRY.slump), sw = t > ENTRY.slump ? Math.sin(TAU * (t - ENTRY.slump) / 4.8) : 0;
    look(o, d, G, -6 * e, -16 * e); roll(o, d, G, -0.14 * e + 0.035 * sw); lids(o, d, e);
    o.y += e * (0.7 + 0.9 * g.hw); o.a = 1 - 0.22 * e;
    if (t > ENTRY.slump && g.hw > 0.5 && d.y >= G.neckY - 3 && g.rnd2 < 0.05) { const f = frac((t - ENTRY.slump) / 2.4 + g.rnd); o.y += 14 * f * f; o.a *= 1 - f; } },
  // shake (movement): a hard "no" head shake that dies down into a drooped, dimmed head; a small shake again every 4.8 s
  shake(o, d, t, P, G) { const E = ENTRY.shake, ph = t > E ? frac((t - E) / 4.8) : 0;
    const yaw = 26 * Math.sin(TAU * 3 * t) * Math.exp(-2.2 * t) + (ph > 0.5 && ph < 0.75 ? 12 * Math.sin(TAU * 3 * (ph - 0.5) * 4.8) * bell((ph - 0.5) / 0.25) : 0);
    const dr = smooth((t - 0.5) / 0.7); look(o, d, G, yaw, -10 * dr); lids(o, d, 0.5 * dr); o.a = 1 - 0.28 * dr; },
};
// reduced motion: no positional motion, no breathing or blink; at most a gentle opacity change, plus a held head pose (bold set).
function rmPose(o, d, st, t, G, set) {
  o.x = d.x; o.y = d.y; o.r = d.r; o.a = 1; const bold = set === 'bold';
  if (st.state === 'working') { o.a = 0.82 + 0.18 * (0.5 + 0.5 * Math.cos(TAU * t / 2.4)); if (bold) look(o, d, G, 0, -8); }
  else if (st.state === 'connecting') o.a = 0.55 + 0.15 * (0.5 + 0.5 * Math.cos(TAU * t / 2.4));
  else if (st.state === 'thinking') { if (bold) look(o, d, G, 14, 8); else if (isEye(d) && d.g.er < 0.5 && d.lid) o.r = Math.max(o.r, d.lid); o.a = 0.9; }
  else if (st.state === 'done') o.a = t < DONE_LEN.rm ? 1 - 0.2 * Math.sin(Math.PI * t / DONE_LEN.rm) : 1;
  else if (st.state === 'error') { o.a = 1 - 0.5 * smooth(t / 0.3); if (bold) look(o, d, G, 0, -12); }
  if (o.x !== d.x || o.y !== d.y) { o.x = d.x; o.y = d.y; }        // pose fallback never moves dots under reduced motion
}
function statePose(o, d, st, now, P, G) {
  const t = now - st.at;
  if (P.rm) return rmPose(o, d, st, t, G, P.set);
  base(o, d, now, P, BREATH_AMP[st.state]);
  MOD[st.variant](o, d, t, P, G);
}
// ---------- transitions: blend the outgoing pose F into the incoming pose G, per dot ----------
function mix(o, F, Gp, w) { o.x = F.x + (Gp.x - F.x) * w; o.y = F.y + (Gp.y - F.y) * w; o.r = F.r + (Gp.r - F.r) * w; o.a = F.a + (Gp.a - F.a) * w; }
const TRANS = {
  // subtle: the intro, tamed: each dot drifts ~13% toward a random point, shrinks a little, lands in the new state
  assemble(o, d, p, F, Gp) { const ps = cl((p - d.g.rnd * 0.4) / 0.6); const s = Math.sin(Math.PI * ps) * 0.13; mix(o, F, Gp, smooth(ps));
    o.x += (d.g.sx - o.x) * s; o.y += (d.g.sy - o.y) * s; o.r *= 1 - 2.2 * s; },
  // subtle: a diagonal wavefront crosses the bust; dots dip and nudge as it passes, behind it they are in the new state
  sweep(o, d, p, F, Gp, G) { const u = d.x * 0.65 + d.y * 0.35, f = -4 + p * (G.N + 8); const k = gauss(u - f, 2.2);
    mix(o, F, Gp, smooth((f - u) / 3 + 0.5)); o.r *= 1 - 0.65 * k; o.x += 0.5 * k; o.y -= 0.25 * k; },
  // subtle: dots shrink and fade out in random order and re-form in the new state
  dissolve(o, d, p, F, Gp) { const q = cl((p - d.g.rnd * 0.55) / 0.45); const sc = Math.abs(1 - 2 * q); const S = q < 0.5 ? F : Gp;
    o.x = S.x; o.y = S.y; o.r = S.r * sc; o.a = S.a * (0.3 + 0.7 * sc); },
  // bold (dissolve): the whole bust spirals out into a vortex disc and spirals back in as the new state
  swirl(o, d, p, F, Gp, G) { const g = d.g, ps = cl((p - 0.15 * g.rnd) / 0.85), w = bell(ps); mix(o, F, Gp, smooth(ps));
    const dx = o.x - G.cx, dy = o.y - G.cy, rho = Math.hypot(dx, dy); let an = Math.atan2(dy, dx);
    an += w * (2.2 + 2.4 * (1 - rho / G.maxD)); const r2 = rho * (1 - 0.55 * w) + w * 0.55 * G.N * 0.44 * (0.15 + 0.85 * g.rnd2);
    o.x = G.cx + r2 * Math.cos(an); o.y = G.cy + 0.85 * r2 * Math.sin(an); o.r *= 1 - 0.45 * w; },
  // bold (dissolve): the original intro in full: dots fly out to random points across the tile and reassemble as the new state
  scatter(o, d, p, F, Gp) { const ps = cl((p - d.g.rnd * 0.3) / 0.7); const s = Math.sin(Math.PI * ps); mix(o, F, Gp, smooth(ps));
    o.x += (d.g.sx - o.x) * s; o.y += (d.g.sy - o.y) * s; o.r *= 1 - 0.6 * s; },
  // bold (movement): the head turns away to one side and turns back already in the new state
  turn(o, d, p, F, Gp, G) { const yf = -30 * smooth(p / 0.5), yg = -30 * (1 - smooth((p - 0.5) / 0.5)), pt = -6 * bell(p);
    const f = { x: F.x, y: F.y, r: F.r, a: F.a }, gq = { x: Gp.x, y: Gp.y, r: Gp.r, a: Gp.a };
    look(f, d, G, yf, pt); look(gq, d, G, yg, pt); mix(o, f, gq, smooth((p - 0.38) / 0.24)); o.r *= 1 - 0.25 * bell((p - 0.3) / 0.4); },
  blend(o, d, p, F, Gp) { mix(o, F, Gp, smooth(p)); },
  rm(o, d, p, F, Gp) { mix(o, F, Gp, smooth(p)); },
};

// ---------- controller ----------
class Glyph {
  /* opts: botId, set 'bold'|'subtle', variantMode 'seeded'|'random'|'fixed', variants {state: name}, transitionSet (default = set),
           transitionMode 'seeded'|'random'|'fixed', transition name, reducedMotion bool, breathe (rad/s, idle ripple speed), blinkPeriod (s) */
  constructor(av, opts) {
    this.av = prep(av);
    this.o = Object.assign({ botId: av.bot || av.god, set: 'bold', variantMode: 'seeded', variants: {}, transitionMode: 'seeded', transition: null,
                             reducedMotion: false, breathe: 2.2, blinkPeriod: 3.8, random: Math.random }, opts || {});
    this.node = { kind: 'state', state: 'idle', variant: 'idle', at: 0 };
    this._F = { x: 0, y: 0, r: 0, a: 1 }; this._G = { x: 0, y: 0, r: 0, a: 1 }; this._o = { x: 0, y: 0, r: 0, a: 1 };
  }
  variantFor(state) {
    const V = SETS[this.o.set][state]; if (V.length === 1) return V[0];
    if (this.o.variantMode === 'fixed' && this.o.variants[state]) return this.o.variants[state];
    if (this.o.variantMode === 'random') return V[Math.floor(this.o.random() * V.length)];
    return V[hash(this.o.botId + ':' + state) % V.length];
  }
  transitionFor(from, to) {
    if (this.o.reducedMotion) return 'rm';
    const bold = this.o.set === 'bold', T = TSETS[this.o.transitionSet || this.o.set];
    if (to === 'done' || from === 'done') return 'blend';     // done is its own beat; it starts and ends on the idle pose
    if (bold && to === 'error') return 'blend';               // bold error variants have their own dramatic entry
    if (this.o.transitionMode === 'fixed' && this.o.transition) return this.o.transition;
    if (this.o.transitionMode === 'random') return T[Math.floor(this.o.random() * T.length)];
    return T[hash(this.o.botId + ':' + from + '>' + to) % T.length];
  }
  target() { return this.node.kind === 'trans' ? this.node.to : this.node; }
  /* jump straight into a state (no transition), e.g. first paint */
  jump(state, now, variant) { this.node = { kind: 'state', state, variant: variant || this.variantFor(state), at: now }; }
  /* request a state; interrupts anything in flight. Returns the new target. */
  set(state, now, opt) {
    opt = opt || {}; const cur = this.target();
    if (state === cur.state && !opt.force) return cur;
    const to = { kind: 'state', state, variant: opt.variant || this.variantFor(state), at: now };
    const tk = opt.transition || this.transitionFor(cur.state, state);
    let from = this.node;
    if (from.kind === 'trans' && from.from.kind === 'trans') from.from = from.from.to;   // keep nesting <= 2
    this.node = { kind: 'trans', tk, from, to, at: now, dur: TDUR[tk] };
    return to;
  }
  update(now) {
    let n = this.node;
    if (n.kind === 'trans' && now - n.at >= n.dur) n = this.node = n.to;
    if (n.kind === 'trans' && n.from.kind === 'trans' && now - n.from.at >= n.from.dur) n.from = n.from.to;
    const s = this.target();
    if (s.state === 'done' && now - s.at >= (this.o.reducedMotion ? DONE_LEN.rm : DONE_LEN[s.variant])) {   // one-shot ends exactly on idle
      if (this.node === s) this.node = { kind: 'state', state: 'idle', variant: 'idle', at: now };
      else this.node.to = { kind: 'state', state: 'idle', variant: 'idle', at: now };
    }
  }
  evalNode(n, d, now, P, out, depth) {
    if (n.kind === 'state') return statePose(out, d, n, now, P, this.av._g);
    const F = depth ? { x: 0, y: 0, r: 0, a: 1 } : this._F, Gp = depth ? { x: 0, y: 0, r: 0, a: 1 } : this._G;
    this.evalNode(n.from, d, now, P, F, depth + 1); this.evalNode(n.to, d, now, P, Gp, depth + 1);
    TRANS[n.tk](out, d, cl((now - n.at) / n.dur), F, Gp, this.av._g);
  }
  label() { const n = this.node, s = this.target(), rm = this.o.reducedMotion;
    return n.kind === 'trans' ? `${n.from.kind === 'state' ? n.from.state : '...'} -> ${s.state} (${n.tk}${FAMILY[n.tk] && n.tk !== 'blend' && n.tk !== 'rm' ? ', ' + FAMILY[n.tk] : ''})`
      : `${s.state}${rm ? ' (reduced motion)' : s.variant !== s.state ? ' / ' + s.variant + ' (' + FAMILY[s.variant] + ')' : ''}`; }
  /* draw at time now (seconds) into a square canvas of px device pixels */
  draw(ctx, now, px) {
    this.update(now);
    const av = this.av, G = av._g, N = av.grid, c = px / N, rm = this.o.reducedMotion;
    const P = { blink: rm ? 0 : blinkAt(now, av, this.o.blinkPeriod), breathe: this.o.breathe, rm, set: this.o.set };
    const o = this._o, hl = av.highlight; ctx.clearRect(0, 0, px, px); let lastA = -1, lastC = '';
    for (const d of G.dots) {
      this.evalNode(this.node, d, now, P, o, 0);
      let rad = o.r * c; if (rad < 0.05 || o.a <= 0.01) continue;
      let x = (o.x + 0.5) * c, y = (o.y + 0.5) * c;
      // crisp: at >= 3 px per cell, snap dots at rest to whole-pixel diameters (as in demo.html); smaller sizes stay anti-aliased
      if (c >= 3 && Math.abs(o.x - d.x) < 0.01 && Math.abs(o.y - d.y) < 0.01) { const D = Math.round(rad * 2); if (D > 0) { rad = D / 2; x = Math.floor(x) + (D % 2 ? 0.5 : 0); y = Math.floor(y) + (D % 2 ? 0.5 : 0); } }
      if (o.a !== lastA) { ctx.globalAlpha = lastA = Math.min(1, o.a); }
      // bots with a highlight tier (Hermes: #0000F2 + #3A3AFF): dots at the highlight size, or swelling >25% above rest, use the tint
      const near = Math.abs(o.r - d.r) <= 0.03;
      const fill = hl && (o.r > d.r * 1.25 + 0.02 || (!near && o.r >= G.hlR)) ? hl : near ? d.c : (d.c === hl ? G.col : d.c);
      if (fill !== lastC) ctx.fillStyle = lastC = fill;
      ctx.beginPath(); ctx.arc(x, y, rad, 0, TAU); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}
root.BotGlyph = { Glyph, prep, STATES, SETS, VARIANTS, TSETS, TRANSITIONS, FAMILY, TDUR, DONE_LEN, ENTRY, ERROR_ENTRY, PERIOD, hash, poseDelta };
})(typeof window !== 'undefined' ? window : globalThis);
