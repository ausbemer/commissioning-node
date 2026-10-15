# ADR 0008: Dashboard by MagicDNS name — redirected to the tailnet IP, never served

- **Status:** Accepted (2026-10-15)
- **Date:** 2026-10-15
- **Deciders:** Austin Emerson
- **Shape:** short. It amends ADR-0003's DNS-rebinding rule on the 8080 tailnet door and
  writes nothing to the world.

## Context

Tailnet operators open the dashboard by the node's tailnet IP. Any name in `Host` gets a 421
(ADR-0003 amendment, step 4 choices: DNS rebinding). The operator wants to type the node's
MagicDNS name instead, e.g. `http://pi5-gateway:8080` or
`http://pi5-gateway.tail01d907.ts.net:8080`.

**Residual risk of serving the name.** Picture a tailnet user's laptop on the site LAN that is
not using MagicDNS (`--accept-dns=false`, or a client where the DNS override did not take). It
resolves `*.ts.net`, and short names, through the site's resolver, LLMNR or mDNS, and a
site-LAN attacker can answer all of those. The attacker answers the name first with their own
page, then rebinds it to the node's tailnet IP. If the door served the name, the attacker's
script would run at the dashboard's own origin as that user. It could read the dashboard, and
since `Origin` and `Host` agree, it could post confirmed writes (Leave, Routes, Restart).

Measured on the node (tailscale 1.102.5, 2026-10-15): `Self.DNSName` is the FQDN with a
trailing dot (`pi5-gateway.tail01d907.ts.net.`). `TailnetStatus` did not carry it.

## Decision

1. **Contract 0.7.0:** optional `TailnetStatus.dns_name`, the FQDN without the trailing dot,
   `""` when unknown. tailnet-gateway fills it from `Self.DNSName` (inward 0.8.0:
   `OverlaySnapshot.dns_name`).
2. **The name is redirected, never served.** On the 8080 tailnet door, a GET whose `Host` is
   exactly the node's `dns_name`, or its first label (the short name a MagicDNS search domain
   resolves), gets a bodyless 303 to `http://<tailnet IP>:8080<same path>`
   (`doors.name_redirect()`):
   - The caller must already have resolved as a tailnet peer (`classify()` runs first). A
     refused caller is refused as before.
   - The request must have arrived on one of the node's tailnet `addresses` from the same
     status. The target is the node's tailnet IPv4, or the arrival address on an IPv6-only
     node. It is never a site-LAN address (rule 13).
   - The path is kept, but only if it is a plain absolute path. Anything else becomes `/`.
   - Every other method on the name (POST included) and every other name still get 421.
     `host_allowed()` is unchanged: it still accepts no names.
3. **Why this is safe.** A rebinding page that gets the redirect only learns a URL on another
   origin, the tailnet IP. It can neither read that origin nor post to it: the IP's `Origin`
   check refuses it. So the redirect hands the attacker nothing they didn't already have. A
   client that isn't using MagicDNS and types the name never reaches the node at all, the same
   as today.
4. **Freshness.** The redirect reads the latest `TailnetStatus` without a freshness check.
   This is deliberate: an old name or address list can only fail toward the safe default (no
   match, 421). The target address is cross-checked against the address the request actually
   arrived on.

## Options considered

- **Serve the exact FQDN as a full door.** The address bar would keep the name, but it accepts
  the residual risk above for clients not using MagicDNS on a hostile site LAN. Rejected for
  plain HTTP.
- **Serve the FQDN read-only, writes only by IP.** A rebinding page could still read the node
  card, peer logins and site hosts, and the sliders would fail from the name. Rejected.
- **Redirect any name.** Also safe, but a typo or a stranger's name pointed at the node would
  silently work. A 421 for an unknown name is clearer. Rejected.

## Consequences

- Operators type the name and land on the IP. The address bar shows the IP.
- The follow-up "Trusted certificate on the tailnet door" (TODO.md) can serve the name for real
  over HTTPS with a `*.ts.net` certificate. There, TLS defeats the rebind: the attacker cannot
  present a valid certificate for the name. That ADR revisits rule 2.
- The redirect leaks nothing a rebinding attacker lacks: the node's tailnet IP, which they must
  already know in order to rebind to it.
