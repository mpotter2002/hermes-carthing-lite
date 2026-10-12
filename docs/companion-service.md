# Persistent companion service

`src/companion_service.py` keeps the Car Thing face mirroring the default Bot Chat
without a timed preview. It reuses the read-only companion client: one pinned
canonical runtime, allowlisted `session.events.since` / `session.status` calls,
no session creation and no writes to Hermes.

## Pieces

- Pi: systemd user unit `deploy/hermes-carthing-companion.service`. Linger keeps it
  running without a login session. It restarts on failure (15 s delay) and stops
  restarting with exit status 78 when the saved login is missing or rejected.
  Limits: `MemoryMax=256M`, `CPUQuota=40%`, `Nice=10`. Logs go to the user journal
  and record state changes only.
- Car Thing: the existing supervisord `program:chromium` boots
  `file:///var/hermes-face-v1-preview/device.html?companion=1&transport=push`.
  Without a feed the page shows the face with Unknown status.
- Device link: each snapshot is pushed through the existing exact-page CDP bridge.
  When USB is absent, the device reboots, or the page is not the face page, the
  service retries with backoff from 2 s up to 30 s. It never navigates the device.

## Login session at rest

`companion_service.py login` runs the existing native PKCE sign-in once. Only the
rotating refresh token, provider and user id are saved, in
`~/.local/state/hermes-carthing-companion/session.json` (file 0600, directory 0700,
no symlinks). Access tokens stay in memory. Every rotation rewrites the file
atomically. A rejected refresh (HTTP 400/401/403) deletes the file and exits 78.
Network failures and 5xx keep the session and retry.

## Monitoring

`~/.local/state/hermes-carthing-companion/status.json` is rewritten at most once a
minute with device connects, delivery failures, applied snapshots, last delivery
time, current activity and peak RSS.

```
systemctl --user status hermes-carthing-companion
journalctl --user -u hermes-carthing-companion --since today
```

## Install

```
cp deploy/hermes-carthing-companion.env.example ~/.config/hermes-carthing-companion.env
chmod 600 ~/.config/hermes-carthing-companion.env   # then set origin and serial
python3 src/companion_service.py login --hermes-source ~/.hermes/hermes-agent \
  --profile-home ~/.hermes --origin "$ORIGIN" --output ~/projects/carthing-companion-staging/companion.json
cp deploy/hermes-carthing-companion.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now hermes-carthing-companion
```

## Rollback

```
systemctl --user disable --now hermes-carthing-companion
rm ~/.config/systemd/user/hermes-carthing-companion.service ~/.local/state/hermes-carthing-companion/session.json
systemctl --user daemon-reload
```

On the Car Thing, restore the saved `supervisord.conf` backup (root is mounted
read-only, so remount rw, copy, remount ro), then run
`supervisorctl reread && supervisorctl update chromium`.
