# ADR 0003: Operator doors — tailnet identity, LAN admin fallback, and the first secrets

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** Austin Emerson
- **Shape:** standard (spec §7)

## Context

ADR-0001 names two operator doors: the tailnet (identity read from the transport, §15) and a
local admin-credential fallback for when the tailnet is unavailable. It defers the fallback's
mechanism to this ADR. Until this ADR is accepted and implemented, every surface is
loopback-only and read-only. `modules/dashboard/core/http_surface.py` enforces that: it
refuses non-loopback binds and returns 405 for anything but GET.

The next feature needs writes. The dashboard should set the Tailscale hostname and tags, and
take an auth key, so that an operator can join the node to a new site's tailnet without SSH.
That is exactly the first-boot case the fallback exists for: on a new site the node is not on
any tailnet yet.

Measured on this node (RevPi, 2026-10-12):

- **Interfaces:** `lan0` 192.168.36.153/24 (the site LAN), `lan1` down, `tailscale0` (logged
  out), `lo`.
- **Existing listeners:**
  - SSH on 22, on every interface.
  - Apache on 80/443/41443, on every interface. All three redirect to
    `https://…:41443/cockpit-revpi/`, the RevPi web GUI, which proxies to Cockpit on
    127.0.0.1:9090. Cockpit logs in through PAM.
  - Ours: the dashboard on 127.0.0.1:8080, MediaMTX RTSP on :8554 (firewalled to tailnet +
    loopback, ADR-0002), and the MediaMTX API on 127.0.0.1:9997.
  - 8443 is free.
- **PAM `pam_faillock`** is active with Debian defaults (3 failures lock the account for
  600 s). It covers sudo, SSH and Cockpit together. On 2026-10-12, three non-interactive sudo
  calls locked the operator out of all three for about 10 minutes.
- **Tailscale 1.102.5 CLI** (from `--help`):
  - `up --auth-key` accepts `file:<path>`, so a key never needs to sit in a process's argv.
  - `up` takes `--hostname` and `--advertise-tags`; `set` takes `--hostname`.
  - `up` and `set` accept `--operator=<user>` (lets that user run tailscale without sudo);
    none is set yet.
  - `whois [--json] <ip>` maps a tailnet IP to its machine and user.

## Decision

Three doors, one ownership check. **D1–D3 were decided by the operator.** Everything after
them is this ADR's mechanism for those decisions.

- **D1 — Fallback door: an HTTPS listener on the site LAN with an admin password.** It is
  browser-only and needs no SSH client.
- **D2 — Any tailnet user may edit config.** Who may reach the node is the tailnet's ACLs'
  job. The application does not keep its own allowlist. It still records **who** made each
  change.
- **D3 — The Tailscale auth key is pasted into the dashboard.** That makes it the project's
  first network secret (§8.9).

### 1. The doors

| Door | Listener | Who | Identity recorded |
|------|----------|-----|-------------------|
| **local** | `127.0.0.1:8080` (unchanged) | Processes on the node, including an operator's SSH port-forward | `local` |
| **tailnet** | `:8080` on the node's tailnet addresses | Any tailnet peer (D2) | The peer's login, or the node name for a tagged peer |
| **fallback** | `:8443` TLS, all interfaces | Holder of the admin password (D1) | `admin@fallback` |

- **The dashboard binds `:8080` on all addresses and sorts each request by the local address
  it arrived on.** This is enforced at each consumer (§11 rung 13):
  - **In the app:** a request on `127.0.0.1`/`::1` is the *local* door. A request on any other
    address must belong to a peer that resolves through the tailnet identity function (§2);
    if it doesn't, the request is refused with a 403. This refusal is never silent.
  - **In the firewall:** the `inet commissioning_node` table drops 8080 unless it arrives on
    `lo` or `tailscale0`. This is the same pattern as the MediaMTX ports (ADR-0002, CLAUDE.md
    rule 15).
  - **Not by address range:** the app does not match on 100.64.0.0/10. A site LAN can use
    CGNAT space too, so membership in the tailnet is proved by resolving the peer, not
    inferred from its address.
- **The fallback is a separate TLS listener on 8443.** Every request except the login page
  needs a valid admin session. It is reachable from the tailnet as well, which does no harm:
  the password still applies.
- **The RevPi's own services stay as they are and are out of scope.** Those are SSH,
  Apache/Cockpit, and the PAM `pi` account. They are the *host's* admin surface, not ours.
  This ADR doesn't widen or reuse them. Hardening them is recorded for the site-teardown and
  hardening work (Consequences).

### 2. Tailnet identity: one function, fed from the bus

- **Source:**
  - `tailnet-gateway` already runs `tailscale status --json` on each tick, and that output
    includes the peer table.
  - It publishes the peers on a new topic, `tailnet/peers` (contract: a new `TailnetPeer`
    model; additive). Each peer carries its addresses, its login (if it is a user's node),
    its node name and its tags.
  - No new vendor call, and the dashboard never calls tailscale (CLAUDE.md rule 16).
- **The resolver:**
  - The function is `resolve_peer(ip) -> Identity | None`. It lives in the dashboard: today
    the dashboard is its only consumer. It moves to a neutral package if a second consumer
    appears (§4, CLAUDE.md rule 9).
  - It answers from the **latest peer snapshot, and only while that snapshot is fresh**. The
    staleness bound comes from the producer's declared tick period, as in §11 rung 11.
    Identity is a renewed level, not a one-shot grant (CLAUDE.md rule 10).
  - When the snapshot is stale or missing, it **fails toward deny**: the tailnet door refuses
    with a 503 and a "tailnet status stale" message. The local and fallback doors are
    unaffected.
- **The edge case:** a peer that joined after the last snapshot is refused until the next
  tick, with a "retry" message.
- **Why not `tailscale serve` and its identity headers:**
  - Serve sets the user headers only for user-owned peers, so a tagged peer would arrive
    with no headers and look like a local caller.
  - It also needs HTTPS certificates enabled on each tailnet, which is one more thing to set
    up on every new site.
  - Reading the peer table needs neither.

### 3. The fallback: password, TLS and sessions

- **The password is set by the operator and there is no default.**
  - It is set with a new ops script, `ops/set-admin-password.sh` (root, interactive, no echo).
  - It writes `/etc/commissioning-node/admin.cred`, mode 0600, owned by the daemon user. The
    file holds an **scrypt** hash (Python stdlib `hashlib.scrypt`, no new dependency) and its
    parameters, never the password.
  - **With no credential file, the fallback listener does not start, and the doctor warns.**
    It fails closed.
  - The script writes to a temp file and renames it into place, so a half-written credentials
    file is impossible (§8.9).
- **The TLS key is created on the node and never leaves it** (§15, "keys are born where they
  live").
  - `ops/install.sh` generates a self-signed key and certificate under
    `/etc/commissioning-node/tls/` the first time it runs (key 0600). It never regenerates
    them.
  - The doctor prints the certificate's SHA-256 fingerprint. The operator compares it with
    what the browser shows on the first connection: trust on first use, verified.
  - The RevPi has no TPM, so a file key is the strongest form available.
- **Logins and sessions:**
  - A successful login sets a random session cookie that is `Secure`, `HttpOnly` and
    `SameSite=Strict`. Sessions live in memory only, so a daemon restart logs everyone out.
  - Sessions have an idle timeout and an absolute lifetime.
  - Every write must also carry the per-session CSRF token from the page.
- **Brute-force limits:**
  - Failed logins are rate-limited per source address, with a global backoff on top.
  - This is **separate from PAM faillock**: it never locks the `pi` account, so a
    brute-force attempt on the fallback can't lock the operator out of SSH or Cockpit.
- **Constants (§8.11):** these are provisional until measured and ledgered (TODO):
  - `SESSION_IDLE_S = 900`
  - `SESSION_MAX_S = 28800`
  - `LOGIN_FAILS_PER_SRC = 5` per `LOGIN_WINDOW_S = 900`
  - the scrypt parameters (`n=2**15, r=8, p=1`; time one login on the RevPi to measure)
- **Lifecycle:**
  - The fallback stays enabled while the node is on the tailnet. The tailnet can go down,
    and D2 lets any tailnet user log the node out, so recovering on site always needs the
    LAN door.
  - The password is rotated by re-running the ops script.
  - Revisit if an operator wants to turn the fallback off per site.

### 4. One ownership door (§11 rung 10)

- **Every write goes through one function, `authorize(door, identity, action)`.**
- **Under D2 it allows every door.** The local and tailnet doors are allowed by their
  identity; the fallback door is allowed by its session.
- **It is still the single place** that:
  - attaches identity to the change record,
  - refuses in-band, with a reason, in the §5 message format (§11 rung 7),
  - will change if D2 ever narrows (to an allowlist or tag-based rules) without touching any
    handler.
- **Every applied change is logged:** who, which door, which field, old value and new value.
  Secret values are never logged; a secret field logs as `<secret set>`.

### 5. Secrets: the pattern the first ones set (§8.9)

- **There are two secrets:** the admin credential (§3) and the Tailscale auth key (D3).
- **The auth key path:**
  - The operator pastes it in the dashboard. It arrives in a POST body, over TLS (fallback),
    WireGuard (tailnet) or loopback.
  - It goes to a **config-registry field that `tailnet-gateway` declares as secret**. That
    registry is the §6 sanctioned exception, and its own ADR comes next (TODO).
  - It is stored in a file of its own, mode 0600 (temp file, then rename).
  - `tailnet-gateway`'s writer runs `tailscale up --auth-key=file:<path>`.
  - Once tailscaled confirms the join, the stored key is **deleted**: tailscaled keeps its
    own node key, and the auth key is not needed again. Ephemeral, single-use and tagged keys
    are recommended in the UI.
- **What it never touches:**
  - It never goes on the bus.
  - It is never written to a log.
  - It is never in a process's argv. `file:` is used for that reason, and `ps` would
    otherwise show the key.
  - It is never shown back in the UI: the field is write-only and reads as set or unset.
- **Teardown:** the site-teardown procedure (TODO) wipes the auth key file if one is still
  present. The admin credential is a node fact, not a site fact, and survives a move between
  sites.

## Safety (spec §11)

- **Rungs 1–3:**
  - Writes target the node's own tailscaled. The node provisioned it, so it is *owned* and
    rung 3 does not apply.
  - Camera writes keep their own rung-3 confirmation (CLAUDE.md rule 14). That is unchanged
    here.
- **Rung 4, may-disturb:** changing the hostname or tags, re-authenticating, or logging out
  can cut off every tailnet session, including the session of the operator making the change.
  - Each of these actions carries a warning and needs an explicit confirmation.
  - The warning lives in the action manifest (§5), so an LLM caller sees exactly what a human
    sees.
  - When the request arrives on the *tailnet* door, the warning says so: "this may
    disconnect you; recovery is the LAN fallback on :8443".
- **Rung 7:** every refusal is in-band, with its reason. That covers a bad password, a rate
  limit, a stale identity, an unresolved peer and an unconfirmed may-disturb action.
- **Rung 9:** `tailnet-gateway` remains the **only** writer of Tailscale state. The
  dashboard writes config-registry fields and never runs tailscale.
- **Rung 10:** §4 above.
- **Rung 11:** n/a. There is no continuous actuation; identity freshness uses the same
  derived-timeout rule.
- **Rung 12:**
  - This ADR **removes "loopback-only, read-only"** (ADR-0001). The replacements, which ship
    in the same change as the lift and never after it:
    - the three-door classification,
    - the tailnet peer resolution,
    - the admin session,
    - the 8080 firewall entry,
    - the single `authorize()`.
  - Until all of them pass review, the `http_surface.py` guard stays as it is.
- **Rung 13:** door classification is enforced in two places: the app (resolving the local
  address and the peer) and the firewall (8080 only on `lo`/`tailscale0`). The doctor checks
  both: the listeners, the firewall rule, and a fallback listener that is up only when a
  credential exists.

## Consequences

- **Easier:**
  - Joining a new site needs only a browser on the site LAN: open `:8443`, log in, paste the
    key, confirm.
  - Every change to the node's config is attributed to someone.
- **Harder or riskier, and accepted:**
  - D2 means anyone a site's tailnet ACLs let reach the node can reconfigure it, or log it
    off the tailnet. The LAN fallback is the recovery path. The ACLs of each tailnet the node
    joins are now part of its security.
  - The fallback is a password-protected surface on an untrusted LAN, and its strength is the
    strength of that password. The rate limit and the scrypt hash slow down brute force; they
    don't stop a weak password. The ops script enforces a minimum length (provisional:
    12 characters).
  - A self-signed certificate means a browser warning on first connection. Comparing the
    fingerprint is the mitigation, and it relies on the operator doing it.
- **Contract change:** a new topic `tailnet/peers` and a new `TailnetPeer` model in
  `interfaces/`. Both are additive, so run `check_additivity`.
- **Ops changes:**
  - `ops/set-admin-password.sh`
  - TLS key generation in `install.sh`
  - a firewall entry for 8080
  - `tailscale set --operator=<daemon user>` in `install.sh`, so the writer can run without
    root
  - new doctor checks: the fallback state, the certificate fingerprint, the 8080 rule
- **Docs:**
  - CLAUDE.md rule 13 and the ADR-0001 posture line get amended, to point here, when the
    implementation lands.
  - `docs/quirks.md` gains entries for `--auth-key=file:` and the peer-table fields, once
    they are verified against 1.102.5.
- **Revisit:**
  - D2, if the node is ever joined to a tailnet shared beyond the commissioning team.
  - Turning the fallback off per site.
  - The RevPi's stock all-interface services, during site-teardown and hardening.

## Implementation order

1. ~~Amend ADR-0001's posture line, and accept this ADR.~~ Done 2026-10-12.
2. Contract: `TailnetPeer` + `tailnet/peers`. Then `tailnet-gateway` publishes peers. Then the
   dashboard's `resolve_peer`. All of this is still read-only, so it is safe to merge first.
3. Ops: the admin-password script, TLS key generation, the 8080 firewall entry, the doctor
   checks.
4. Dashboard: door classification, the fallback listener + sessions + CSRF + rate limit, and
   `authorize()`. Lift the `http_surface.py` guard in this step and no earlier (rung 12).
5. Config registry: its own ADR (§6 graduation cost), with secret fields.
6. `tailnet-gateway` writer (hostname, tags, auth-key join): its own Safety ADR, with the
   action-manifest entries.

## Choices filled in by this ADR (confirmed by the deciders on acceptance)

- Ports 8080 (tailnet + local) and 8443 (fallback).
- scrypt over a PAM-backed login. PAM would reuse the `pi` account and its faillock. That
  ties the fallback to the host account and recreates the shared lockout seen on 2026-10-12.
- In-memory sessions: a restart logs everyone out.
- Identity from the published peer table rather than `tailscale serve` headers (§2).
- The fallback stays enabled while the node is on the tailnet.

## Amendments

None yet.
