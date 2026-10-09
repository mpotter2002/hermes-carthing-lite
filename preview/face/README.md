# Hermes V1 companion display

This preview is a text-first companion surface for the existing default Hermes identity. It does not connect to Hermes services or infer activity. Without an explicit mode it remains the unchanged disconnected static preview: top-left HERMES, the exact supplied head, and Hermes.

Use `?companion=1` to opt into local snapshot polling. The legacy `?demo=idle` and `?demo=working` URLs remain separate, explicitly labeled demo modes; they are not live data.

## Snapshot contract

The named contract is `HermesCompanionSnapshotV1`:

    {
      version: 1,
      profile: "default",
      session_id: string,
      observed_at: number,
      activity: "unknown" | "idle" | "thinking" | "running",
      tool: string | null,
      reply: null | { id: string, text: string },
      image: null | { src: string, alt: string },
      cards: Array<{ id: string, title: string, text: string }>
    }

`observed_at` is epoch milliseconds. Only profile `default` is accepted. Observations older than 15 seconds or in the future are rejected. Reply text is bounded at 32 KiB. There may be at most two passive cards in fixed order, Status followed by an optional Reply excerpt; identifiers, labels, tool names, alt text, and card text are also bounded. Extra or malformed fields are rejected.

Image sources are limited to a single lowercase SHA-256 basename followed by `.png` in this preview directory. The producer validates the strict PNG subset described in `docs/companion-content.md`. JPEG, WebP, GIF and other formats are rejected. Absolute paths, path separators, traversal, URLs, SVG, data, and script sources are rejected. The display never accepts a network image URL.

Invalid, stale, future, unreadable, or missing snapshots visibly show Unknown, clear prior content, and use the static head. Content is tagged to the exact accepted `session_id`; an identity change clears the previous session before rendering the replacement. All supplied copy is assigned with `textContent`. Cards are always passive text with fixed IDs and titles. There are no links, approvals, commands, execution hooks, or host effects.

Parent integration can call:

    window.HermesCompanion.applySnapshot(snapshot)
    window.HermesCompanion.showFace()
    window.HermesCompanion.showContent()

In `?companion=1`, the browser also reads `companion.json` with `XMLHttpRequest` every two seconds. Reads do not overlap, stop while the page is hidden, time out after 1.5 seconds, and are cleaned up on page hide. A read, parse, or validation failure becomes Unknown. Some Chromium `file://` configurations do not permit local XHR; `applySnapshot` remains available in that case. No server or listener is added as a workaround. The parent may separately project the file through its existing ADB channel.

## Layout and content

The logical layout is portrait 480x800. `device.html` rotates it into the native 800x480 viewport, leaving controls at the physical bottom. Top-left HERMES remains. A latest reply replaces the large bottom Hermes name when present; the fallback remains Hermes. The response uses the existing system fallback font at 35.88px and is lifted 55px. Long replies scroll vertically in the lower response area.

Long replies, local images, and cards enable a full-content view. Full content replaces the face and response area; Back returns to the face. Browser click/touch scrolling and button events are implemented, but the Car Thing dial, Back key, and touch mapping have not been confirmed on device.

The requested NOUS lettering remains unresolved. Official references found previously do not establish a redistributable wordmark font, so no font was acquired or bundled; the current system fallback remains.

## Artwork, activity, and transitions

`hermes-head.json` is unchanged. Its SHA256 is `6b245f2307e736913866b6377bc3b496db84ad68709d54f4b8473f752e11a838`. All 1003 dots remain, including 137 body-tagged dots.

`desktop-glyph.js` remains the byte-identical desktop source. Its SHA256 is `038da56a3b61a60f062de6579bc6c746324fbf4c82b2d01c4ebe8ed1b8a5c357`. Idle uses the authored idle motion. Both Thinking and Running use the existing authored working/orbit motion because the bust-indexed authored thinking poses are incompatible with this exact head. No extra thinking pose, head turn, or fake parallax is claimed.

Unknown is static. Real labels only come from accepted snapshots. Working animation retains its 2px downward render translation. Static rendering returns to the exact original positions.

Full-content transitions use a bounded 800ms deterministic render-only scatter from the exact head. The source artwork is never mutated. Dots scatter and fade before content appears, then visibly reassemble to the exact head on return. Reversals continue from the current midpoint. Reduced motion, `?motion=off`, and hidden pages settle with a static switch. No automatic view change interrupts reading.

## Rendering

WebGL renders analytic antialiased GPU points from one reusable 28,084-byte vertex array: seven floats per dot for position, radius, RGB, and authored alpha. Canvas2D fallback honors the same alpha. The authored geometry, ordering, palette, idle, and orbit formulas remain unchanged.

One requestAnimationFrame chain caps WebGL drawing near 20Hz. Canvas2D fallback remains at 4Hz with two 24x24 palette textures. Hidden pages, reduced motion, and `motion=off` cancel animation. WebGL context loss falls back to a static Canvas2D head without a retry loop.

Previously recorded device measurements for the merged renderer remain evidence for that renderer, not proof of this companion revision: about 20 draws/s, 35.43% normalized one-core CPU idle and 36.17% working, with 50.7ms p95 intervals. This worker did not run Chromium or perform device acceptance.

## Checks

From the repository root:

    python3 -m unittest discover -s tests -q
    node tests/face_renderer.test.cjs
    node tests/face_gpu.test.cjs
    node tests/face_companion.test.cjs
    node tests/face_companion_browser.test.cjs

`tests/face-layout-proof.html` embeds the production page at 480x800. It checks the unchanged legacy copy, 1003 dots, contained square canvas, 55px lift, 35.88px response font, lower-area scrolling, and hidden companion controls. It is not physical-input or device proof.

## Remaining gates

This revision does not establish a supported live canonical Bot Chat connection, change Hermes, select an SDK, add backend/auth/network services, or alter startup, firmware, audio, mounts, or global configuration. Live default-bot acceptance, local-file XHR behavior, rotated layout, transition smoothness, and physical controls still require parent review on the actual Chromium69 device.
