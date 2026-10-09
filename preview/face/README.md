# Hermes face preview

This disconnected preview represents the existing default Hermes identity. Activity is Unknown. Only explicit `?demo=idle` and `?demo=working` URLs animate. No SDK, backend, gateway credentials or live telemetry are connected.

The screen contains only HERMES, the exact supplied head and Hermes. Logical portrait is 480x800, with wheel and Back physically at the bottom. `device.html` rotates the content inside the device's native 800x480 viewport. The main name is now another 20px higher than the previous preview and its font is 31.2px, up from 26px.

## Artwork and motion

The uploaded `hermes-head.json` is unchanged. Its SHA256 is `6b245f2307e736913866b6377bc3b496db84ad68709d54f4b8473f752e11a838`. All 1003 dots remain, including 137 body-tagged dots within this authored head. No dots are removed and no replacement artwork is used.

`desktop-glyph.js` is the byte-identical desktop `assets/avatars/states/source/glyph.js`. Its SHA256 is `038da56a3b61a60f062de6579bc6c746324fbf4c82b2d01c4ebe8ed1b8a5c357`. The adapter uses authored bold idle and working/orbit, blinkPeriod 3.8 and breathe 2.2. It deep-clones the head before the engine prepares its geometry.

Desktop bust-indexed poses are incompatible with this head and are not attached. No head-turn or fake parallax is added. Working animation receives a 2px downward rendering translation to keep its orbit inside the canvas. Static rendering retains the exact original positions.

## Rendering

On WebGL-capable browsers, `gpu.js` renders the authored circles as GPU points with analytic antialiased edges. The same authored positions, radii, ordering and palette enter a reusable 24,072-byte vertex array. GPU edge antialiasing differs from Canvas2D rasterization. The source artwork and animation formulas do not change. No animation-frame bitmap cache or dependency is needed.

One requestAnimationFrame chain caps drawing near 20Hz. Hidden pages, reduced motion and `?motion=off` stop animation. A lost WebGL context falls back to a static Canvas2D head without a retry loop. Initialization failures fall back to a slower 4Hz Canvas2D renderer using two 24x24 cached palette textures. Chromium69 lacks the reduced-motion media query, so `motion=off` is its explicit static fallback.

## Actual device measurements

No screenshots were taken during timed windows. CPU is Chromium main/renderer/utility CPU normalized to one core, excluding zygotes. Draw cadence is instrumented renderer calls, not a display-panel FPS guarantee. These samples do not establish long-run leak freedom.

| Mode | Baseline, 30s | GPU, 120s | Baseline CPU | GPU CPU | GPU interval p95 |
| --- | --- | --- | --- | --- | --- |
| Idle | 3.76 draws/s | about 20 draws/s | 36.34% | 35.43% | 50.7ms |
| Working | 3.73 draws/s | about 20 draws/s | 36.98% | 36.17% | 50.7ms |

GPU draw-call durations were median 1.6ms idle and 2.1ms working. Observed maximum intervals were 76.6ms and 72.1ms respectively. Available device RAM increased from 334108 to 336108kB during idle and from 333176 to 335904kB during working. Process RSS is recorded separately and is not summed as unique memory. A same-cadence GPU trial used approximately 7.7% of one core at 4Hz. Path batching and lower-resolution canvas trials were rejected after actual device regressions.

Raw measurements and actual screenshots are in the Engineer project evidence directory, outside this source tree. Current screenshots are `device-face-smooth-idle.png` and `device-face-smooth-working.png`. GIF capture cadence is not device FPS.

## Preview and checks

From this directory run `python3 -m http.server 8000 --bind 127.0.0.1`, then open `http://localhost:8000/index.html?demo=idle` on that machine. No installation is required.

From the repository root run:

    python3 -m unittest discover -s tests -q
    node tests/face_renderer.test.cjs
    node tests/face_gpu.test.cjs

The browser fixture `tests/face-layout-proof.html` embeds the production page at 480x800. Its result must have `data-pass=true`. It checks only the two labels, 1003 dots, square canvas, contained layout, zero buttons, raised name and 31.2px font. It is not physical-input proof.

## Reversibility and remaining gates

The separate device directory is `/var/hermes-face-preview`. Browser navigation loads `file:///var/hermes-face-preview/device.html?demo=idle`. Navigate back to `file:///var/hermes-display-preview/index.html` to restore the existing dashboard view. No startup, supervisor, firmware or service configuration changed. Reboot still launches the dashboard. Backups remain intact.

Michael reported completing the physical input test. The latest bounded capture was present but empty. An earlier capture contained Enter and wheel movements, but cannot conclusively verify that latest test. Touch and Back remain unverified. Temporary listeners were removed. Performance work does not request repeated physical input tests.

Source review and Merge Gate landing are separate from owner device acceptance. Live default-bot wiring, SDK selection, audio and mount work remain out of scope.
