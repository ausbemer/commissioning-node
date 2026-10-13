# commissioning-node — TODO

What's left. Each item that needs a decision gets an ADR in `docs/adr/` before code.
Growth is trigger-driven: nothing here is scheduled until it's needed.

## Next features (each starts read-only; actuation needs an ADR with the Safety section)

- [ ] **Identity + admin fallback ADR** (ADR-0001 posture). Drafted as
      `docs/adr/0003-operator-doors-identity-and-admin-fallback.md` (Accepted); implement in
      its order. Steps 2–4 done: peer table and resolver; ops (admin password, TLS key, 8080
      firewall, operator, doctor); the dashboard's three doors, fallback login, sessions,
      CSRF, login limits and `authorize()`. Next: step 5, the config registry ADR.
- [ ] **Config registry** (§6 sanctioned exception). Features declare fields, the
      dashboard reads and edits them, and secrets are flagged at declaration. Document the
      graduation cost.
- [ ] **tailnet-gateway: auto-configuration.** Detect the site subnet, then *propose*
      advertised routes and hostname for operator confirmation (§11 rung 2). Then a writer
      feature (`tailscale set` / `up`) with a Safety ADR. Auth keys are secrets.
- [ ] **camera-config: VAPIX probe** (read-only). Fill `model` / `firmware` from the camera.
      Then discovery, which proposes cameras for an operator to adopt (rung 2).
- [ ] **camera-config: VAPIX writer.** Safety ADR. The first write to a camera is
      operator-confirmed (rung 3). May-disturb warnings go in the action manifest (rung 4),
      and declared state is restored exception-safely (rung 6). Camera credentials are secrets.
- [ ] **stream-relay: MediaMTX adapter.** Write paths via the MediaMTX API, then set
      `live=true` only once it is actually serving. Safety ADR (single writer, rung 9).
- [ ] **Action manifest** (§5). Expose `camera/register` and later commands as name + schema
      + `requires` + availability + risk warnings, which the dashboard and an LLM caller both
      consume.
- [ ] **One-shot CLI modes** in `main.py` (e.g. register a camera) on a private Bus instance (§4).

## Ops tier (`ops/`, created with its first member, §10)

- [x] Idempotent installer, doctor, firewall and units (`ops/`, ADR-0002). Tailscale
      1.102.5 and MediaMTX v1.21.1 are pinned in `ops/pins.sh`.
- [x] Run it on the node (`sudo ops/install.sh`, 2026-10-12).
- [x] ADR-0003 step 3 applied on the node (2026-10-13): TLS key, admin password, operator,
      8080 rule; doctor clean. `tailscale up --operator=<user>` waits for the dashboard's
      join flow (ADR-0003 step 6) or a manual join.
- [ ] Provenance (§2, ADR-0002): run a tagged checkout on shipped nodes, and have the
      doctor and node card report the running commit.
- [ ] A check that `relay_planner.MEDIAMTX_RTSP_PORT` matches `rtspAddress` in the MediaMTX
      config.
- [ ] Decide on the RevPi's stock all-interface services (80/443/41443, SSH) as part of the
      admin-fallback ADR.
- [ ] **Site teardown and redeploy procedure.** The node moves between sites. List the
      site-specific state (camera inventory, credentials, tailnet routes, MediaMTX paths,
      logs) and declare how each is wiped or preserved before the node leaves (§10
      "disposable, never lossy").

## Evidence (§8.11) — provisional constants awaiting a measurement + ledger entry

- [ ] `main.TICK_PERIOD_S = 5.0`
- [ ] `tailscale_cli.STATUS_TIMEOUT_S = 5.0`
- [ ] `freshness.STALE_MIN_S = 5.0`, `STALE_MAX_S = 120.0`, `PERIODS = 3` (the staleness
      window for every renewed level: stream endpoints and tailnet caller identity)
- [ ] `relay_planner.MAX_CAMERAS = 512`
- [ ] Admin fallback (`dashboard/interface/admin.py`): `SESSION_IDLE_S = 900`,
      `SESSION_MAX_S = 28800`, `LOGIN_FAILS_PER_SRC = 5` per `LOGIN_WINDOW_S = 900`,
      `LOGIN_GLOBAL_GAP_S = 1.0`, `MAX_SESSIONS = 32`; `http_surface.CONN_TIMEOUT_S = 15`,
      `MAX_BODY = 16 KiB`. Ledger: `tools/ledgers/admin-auth/` (scrypt N is already there).
- [ ] `dashboard.interface.models.MAX_PER_KIND = 256`
- [ ] Capture a real `tailscale status --json` sample and replace the documented-shape fixture
      in `tests/test_tailscale_cli.py`. Verify the field mapping in `docs/quirks.md`.

## Later (recorded, not scaffolded)

- [ ] Fleet of nodes (§14): a design-only ADR covering peer card polling, membership as a
      level, and shared deterministic rules. `NodeCard` already exists.
- [ ] Broker choice (undecided): ADR when a lever fires (§10).

## Non-goals (from init interview)

None stated yet.

## Optional connectors

None offered: Stage 0 is local-only. GitHub, a status board (Linear, Asana or Notion), and
Datadog or PagerDuty become relevant at Stage 1 or once services run in production.
