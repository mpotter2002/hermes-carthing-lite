# Default Bot Chat companion client

`src/companion_client.py` is a host-side, read-only projection of the existing default-profile `Bot Chat`. It does not create, resume, activate, submit to, approve for, or lease a Hermes session. It writes one local `companion.json`; device transfer remains a separate parent-owned step.

## Audited contract

The implementation was built against Hermes source `54d89fca9e23257fccea3c26f90f0b9bcc98ecdc` and the supported native authentication and gateway contracts in that source:

- native PKCE login: `/auth/native/authorize`, `/auth/native/token`, `/auth/native/refresh`
- authenticated single-use WebSocket ticket: `POST /api/auth/ws-ticket`
- gateway socket: `/api/ws`, subprotocols `hermes-gateway-v1` and `hermes-gateway-ticket.<ticket>`
- read methods only: `session.events.since` and `session.status`
- durable history: `SessionDB(..., read_only=True)`

Credentials and refresh rotations remain in process memory. The only listener created by the client is a one-login, deadline-bounded callback on dynamic `127.0.0.1` port. The client prints an authorization URL for the user; it does not launch a browser, collect a password, or guess an account.

## Run

Use `src/companion_preview.py` for the reversible USB device preview. It enters Hermes's supported installation-bound runtime before reading the transcript. Directly invoking an old source venv can relaunch without its selected dependencies. All four location arguments are mandatory:

    python3 src/companion_preview.py \
      --hermes-source /path/to/hermes-agent \
      --profile-home /path/to/default/.hermes \
      --origin https://existing-hermes-gateway.example \
      --output /path/to/local-preview-staging/companion.json \
      --duration 1200

The preview requires an existing ADB connection and loopback CDP forward at `http://localhost:19222`; `--cdp-origin` accepts only an existing loopback endpoint. It uses the existing Chromium page, never creates a listener, and restores its original URL on exit. The eight face preview files must already be staged in `/var/hermes-face-v1-preview/`. Device system clocks and startup remain unchanged. Host snapshot age is validated before translating that age to the device clock for its freshness watchdog. Images are copied by ADB only after strict validation. Credentials remain in memory.

For an explicitly configured existing local/private gateway, an `http://` origin is accepted. Public HTTP, origin paths, credentials, query strings, fragments, redirects, and insecure TLS options are rejected. HTTPS uses Python's normal certificate validation.

Optional arguments:

- `--provider NAME`: select a configured native-login provider; omitted lets Hermes choose or render its chooser.
- `--duration SECONDS`: bounded foreground run, default `3600`, maximum `86400`.

Press Ctrl-C to stop. This is not a daemon and installs no service or autostart entry.

## Identity and failure behavior

The client resolves exact title `Bot Chat`, follows its compression tip, and reads only the default profile's `runtime/active_sessions.json`. It requires exactly one matching advertised live consumer and pins the durable session ID, runtime ID, lease ID, PID, process start time, and configured Hermes application command. It never invokes Hermes's registry snapshot helper because that helper may prune and rewrite the registry.

A PID reuse, owner/lease/runtime/lineage change, wrong application, stale runtime error, replay epoch change, replay gap, or truncated replay clears inferred phase and tool state. Disconnect publishes `unknown`, drops uncommitted stream state, and may retain only a reply re-read from SQLite. No canonical data produces the sentinel identity `default:Bot Chat` with an unknown activity and null reply.

`session.status` is the current busy/idle authority. Only the exact canonical line `Agent Running: Yes|No` is accepted. Unknown formats remain unknown. Thinking is shown only after a gap-free thinking/reasoning event during a turn. A running status without that evidence is generic running. Tool names are mapped through a fixed display allowlist; arguments, results, context, reasoning, prompts, and approval requests are never projected.

A `message.complete` event supplies only a reconciliation row address. The displayed final assistant reply must exist in read-only SQLite, have no pending tool calls, and belong to the canonical compression lineage. Hermes's supported persisted-text redactor is mandatory and failures omit the reply. Reply text is UTF-8 bounded to 32 KiB. Images are projected only from an explicit reference in that redacted final reply to a PNG inside the output directory's dedicated `incoming/` subdirectory. The strict PNG subset, byte/dimension limits and rejected formats are documented in `companion-content.md`. Cards are fixed, read-only Status and Reply excerpt records. This does not implement arbitrary generated cards or UI.

The output shape is:

    {
      "version": 1,
      "profile": "default",
      "session_id": "durable-canonical-tip",
      "observed_at": 0,
      "activity": "unknown|idle|thinking|running",
      "tool": null,
      "reply": {"id": "durable-row-id", "text": "..."},
      "image": null,
      "cards": []
    }

Writes use a mode-0600 temporary file, `fsync`, and atomic replacement. `--output` is rejected when it is inside the Hermes source or profile tree.

## Offline verification

The unit suite uses local fixtures only. It does not contact a Hermes gateway, inspect a live conversation, authenticate a real account, or access a device:

    python3 -m unittest discover -s tests -q
    git diff --check

Real login, authenticated gateway behavior, current Bot Chat integration, output staging choice, ADB transfer, and Car Thing acceptance are separate parent-owned checks.
