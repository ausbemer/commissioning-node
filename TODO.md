# commissioning-node — TODO

What's left. Each item that needs a decision gets an ADR in `docs/adr/` before code.
Growth is trigger-driven: nothing here is scheduled until it's needed.

## Up next (start here next session)

- [ ] **Dashboard design** — the next piece of work (operator, 2026-10-13).
- [ ] **Finish the tailnet writer rehearsal** (ADR-0005), on the node:
      1. ~~`sudo ops/install.sh`, then the doctor~~ — done 2026-10-13: the firewall unit owns
         IPv4 forwarding, the old sysctl file is gone, doctor clean.
      2. ~~Leave and Join once on `/config`~~ — done 2026-10-13 16:21: the join record now
         carries `key_sha256` (ruling 1). Join timed in `tools/ledgers/tailnet-writer/`.
      3. ~~Ephemeral removal~~ — done 2026-10-13 by an unplanned power cut (~2 h): booted
         `NeedsLogin`, auto-rejoined in 5 s (quirks Q-005, ADR-0005 amendment). Still to
         do: a short power cycle (should come back `Running`, no rejoin).
      4. ~~Make the writer enabled by default~~ — done 2026-10-13; `CN_TAILNET_WRITER=0` stays
         as the off-switch. On the node, remove the drop-in at
         `/etc/systemd/system/commissioning-node.service.d/tailnet-writer.conf`.
      5. The key is an OAuth client secret (ADR-0005 amendment). Verify that a rejoin with it
         needs no device approval (save it with `?preauthorized=true`, accepted since
         2026-10-13), that `--auth-key=file:` honours the suffix, and that it joins
         ephemeral; record all three in quirks.md.
- [x] **ADR-0003 steps 5 and 6: config registry + tailnet-gateway writer** (ADR-0004,
      ADR-0005 with amendments A1–A3). Merged to `dev` 2026-10-13 after four review rounds
      (rulings in ADR-0005); routes rehearsed and approved on the node.
- [x] **Step 4 confirmed on the node** (2026-10-13): doctor clean, fallback login from a
      LAN laptop. Certificate fingerprint `4D:E0:3A:96:…:86:BA:C4:F0` (printed in full by
      the doctor).

## Next features (each starts read-only; actuation needs an ADR with the Safety section)

- [x] **Identity + admin fallback** (`docs/adr/0003-operator-doors-identity-and-admin-fallback.md`,
      Accepted). Steps 1–6 done (2026-10-12/13); step 6's rehearsal finishes under Up next.
- [ ] **Overview network map.** Mockup 3 ("Network Map", 2026-10-13) opens the overview on a
      map: site LAN and its cameras → this gateway → the tailnet, each link coloured by its
      state. The style was adopted without the map. To decide first: which facts each link
      shows and who is the authority for each (route approved, stream serving, endpoint fresh,
      camera reachable — several don't exist on the bus yet), how stale links look, and how it
      scales past a handful of cameras. Inline SVG, no JavaScript (CSP).
- [ ] **Dashboard by MagicDNS name.** The tailnet door accepts only the node's tailnet IP in
      `Host` (DNS rebinding, ADR-0003 amendment). Accepting the exact MagicDNS FQDN needs it
      in the contract (`TailnetStatus` has no DNS name) and a decision on the residual risk:
      a client not using MagicDNS resolves `*.ts.net` through a resolver the site may control.
- [ ] **Trusted certificate on the tailnet door** (after "Dashboard by MagicDNS name"). Serve
      the tailnet door over HTTPS with a `tailscale cert` (Let's Encrypt, `*.ts.net`), so the
      everyday door has no browser warning. The fallback door keeps its self-signed
      certificate and fingerprint check (ADR-0003 accepts the warning there). Decide in an ADR:
      renewal (a renewed level, §5), where the key lives, and what the door does if the
      certificate is missing or expired. `tailscale cert` is a vendor call, so it lives in
      tailnet-gateway's `core/`.
- [x] **tailnet-gateway: auto-configuration.** Site networks are detected and proposed as the
      default routes (`auto`), applied on a confirmed Join or Routes (ADR-0005 A3).
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
- [ ] Install `nodejs` on the dev node (dev/CI only) so `ci.sh` parses the dashboard's scripts
      (ADR-0006); until then that gate prints SKIPPED.
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
      `SOURCE_V6_PREFIX = 64`, `MAX_PENDING_LOGINS = 64`, `MAX_SESSIONS = 32`; `http_surface.CONN_TIMEOUT_S = 15`,
      `MAX_BODY = 16 KiB`. Ledger: `tools/ledgers/admin-auth/` (scrypt N is already there).
- [ ] `dashboard.interface.models.MAX_PER_KIND = 256`
- [ ] `tailnet_writer`: `REJOIN_BACKOFF_MIN_S = 60`, `REJOIN_BACKOFF_MAX_S = 1800` (ADR-0005 A2);
      `site_networks.IP_TIMEOUT_S = 2.0`.
- [ ] `tailscale_writer`: `UP_TIMEOUT_S = 60`, `SET_TIMEOUT_S = 15`, `LOGOUT_TIMEOUT_S = 15`,
      `KILL_MARGIN_S = 10`, `OUTPUT_CAP = 4096` (ADR-0005 §2). Ledger started:
      `tools/ledgers/tailnet-writer/` (2 joins, worst 5.2 s); needs a join from another site.
- [ ] `config_registry`: `MAX_VALUE_LEN = 256`, `MAX_LIST_ITEMS = 32`, `MAX_SECRET_LEN = 512`,
      `MAX_FILE_BYTES = 64 KiB` (ADR-0004 §3; measure a real auth key's length on 1.102.5).
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
