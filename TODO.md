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
         as the off-switch. ~~On the node, remove the drop-in~~ — gone (checked 2026-10-15).
      5. The key is an OAuth client secret (ADR-0005 amendment). Verify that a rejoin with it
         needs no device approval (save it with `?preauthorized=true`, accepted since
         2026-10-13), that `--auth-key=file:` honours the suffix, and that it joins
         ephemeral; record all three in quirks.md.
- [ ] **Join fails when tailscaled has lost its operator** (seen 2026-10-21). tailscaled
      booted 2026-10-18 with `profile not found` (state cleared, cause unknown) and no
      `OperatorUser`, so three Joins from the dashboard failed with `Access denied: checkprefs
      access denied`; fixed by hand with `sudo tailscale set --operator=admin`. Fix:
      1. ~~Find what cleared the profile~~ — confirmed 2026-10-25: a Leave's `tailscale
         logout` deletes the profile, and `OperatorUser` with it (quirks Q-010). Every Leave
         therefore breaks the next Join, so steps 2 and 3 are now urgent.
      2. ~~Keep the operator a renewed level~~ — built 2026-10-25 (ADR-0002 A4, accepted):
         root oneshot on tailscaled start, a state-change path unit, a 5-min timer.
         **Still to do on the node:** `sudo ops/install.sh`, then the A4 rehearsal (Leave,
         operator back within 5 s?; path unit stopped, the timer brings it back), and
         record the timings in `tools/ledgers/tailscale-operator/` and Q-010.
      3. ~~The writer maps `checkprefs access denied` to a plain Last action~~ — done
         2026-10-25 (`NO_OPERATOR_MESSAGE`). Still open: the overview shows the doctor's
         operator check.
- [x] **ADR-0003 steps 5 and 6: config registry + tailnet-gateway writer** (ADR-0004,
      ADR-0005 with amendments A1–A3). Merged to `dev` 2026-10-13 after four review rounds
      (rulings in ADR-0005); routes rehearsed and approved on the node.
- [x] **Step 4 confirmed on the node** (2026-10-13): doctor clean, fallback login from a
      LAN laptop. Certificate fingerprint `4D:E0:3A:96:…:86:BA:C4:F0` (printed in full by
      the doctor).

## Next features (each starts read-only; actuation needs an ADR with the Safety section)

- [x] **Identity + admin fallback** (`docs/adr/0003-operator-doors-identity-and-admin-fallback.md`,
      Accepted). Steps 1–6 done (2026-10-12/13); step 6's rehearsal finishes under Up next.
- [ ] **Overview network map** (`docs/adr/0007-overview-network-map.md`, Accepted 2026-10-13).
      An operator-run gentle nmap scan of the site networks (new module `site-survey`),
      drawn as a map; VAPIX flags Axis cameras, Tailscale flags tailnet nodes. Step 1 (nmap
      pinned, two measured scans) and step 2 (contract 0.6.0 + `site-survey`, on
      `feature/site-survey`) step 3 (the Scan slider and the map, dashboard 0.9.0) and step 4
      (tailnet nodes on the map) done 2026-10-13; step 5 (VAPIX identification) next. Separately: the overview never judges `TailnetStatus` freshness (ADR-0007, Options).
- [ ] **Restart vs. an operation in flight** (release review, 2026-10-13). A service restart
      from `/config` while a Join, Rename or Routes is still running loses its outcome (no
      Last action), and the join record misses what tailscaled did, so a later rejoin may
      apply old values. Refuse the restart while a tailnet apply or a scan has no result yet
      (observe `tailnet/apply` as the dashboard already observes `site/scan`), or have main
      wait for both worker slots to drain before exiting. Say so in `RESTART_WARNING`.
- [x] **Dashboard by MagicDNS name** (ADR-0008, 2026-10-15). A GET on the node's MagicDNS
      FQDN or short name is redirected to its tailnet IP, never served (contract 0.7.0:
      `TailnetStatus.dns_name`). Still to do: try it from a tailnet laptop on the node.
- [ ] **Trusted certificate on the tailnet door** (after "Dashboard by MagicDNS name"). Serve
      the tailnet door over HTTPS with a `tailscale cert` (Let's Encrypt, `*.ts.net`), so the
      everyday door has no browser warning, and serve it on the name itself rather than
      redirecting (TLS defeats the rebind; revisit ADR-0008 rule 2). The fallback door keeps its self-signed
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
- [ ] **One-click node update** (ADR-0009, accepted 2026-10-25). Step 1 done (the daemon
      user and the running commit recorded by root; the doctor reports them) and step 2 done
      (contract 0.8.0, `node-updater`: the check, the Overview's Software row and banner,
      Check now, `update_channel` on `/config`). Next: step 3, the root updater and
      `node/update` behind a disabled flag; then the rehearsal. Also: tag a `v0.2.1` from a
      green commit (`v0.2.0`'s CI failed, so `releases` has nothing current to offer). On the node: re-run
      `sudo ops/install.sh` once, to write `daemon-user` and `running.json`.
- [ ] Provenance (§2, ADR-0002): ~~the doctor reports the running commit~~ (ADR-0009 step
      1). Still: run a tagged checkout on shipped nodes (ADR-0009's `releases` channel). The
      overview shows the running commit (step 2); the node card doesn't yet.
- [ ] **Site LAN into the tailnet: loose ends** (ADR-0005, 2026-10-21). The RevPi side is
      done (ADR-0002 A3, firewalld policy `site-to-tailnet`, seen working 2026-10-23). Still:
      run `sudo ops/install.sh` on the RevPi to confirm it adopts the hand-made policy with no
      change, and on the Pi 5 to replace the hand-edited `eth0` rule with the repo's. Measure
      that a tailnet peer drops a forwarded source outside the approved routes (quirks.md).
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
- [ ] `site_survey` (ADR-0007): `MAX_RATE = 100`, `MAX_PREFIX = 22`, `HOST_TIMEOUT_S = 15`,
      `SCAN_TIMEOUT_S = 300`, `KILL_MARGIN_S = 10`. Ledger: `tools/ledgers/site-survey/` (2 scans,
      home lab); needs a scan from a real commissioning site.
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
