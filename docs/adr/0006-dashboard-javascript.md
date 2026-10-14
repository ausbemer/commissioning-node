# ADR 0006: Dashboard JavaScript — first-party scripts allowed

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Austin Emerson
- **Shape:** standard (spec §7)

## Context

Since the dashboard was first built (ADR-0003 step 4), every page has been served with
`Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; …`, so no script
runs at all. That was an implementation choice, not a spec rule: spec §8.1 only requires that
anything shipped verbatim without a build step ("static JS/CSS, embedded scripts") is loaded
or compiled in CI, and that a gate needing a toolchain not every environment has skips loudly.

The redesign (dashboard 0.7.0) ran into the limits of the ban three times in one day:

- **The day/night slider** needed a server round trip and a cookie to persist (ADR-0003
  amendment, 2026-10-13).
- **The System section** on the overview closed itself every 5 s, because the page reloads
  itself to stay current. It needed a server-kept query (`/?system=open`) to stay open.
- **Leave the tailnet** should be a slide-to-confirm, so a fat-fingered tap can't drop the
  node off the tailnet. A real drag gesture (the knob snaps back unless released at the far
  end) cannot be built without script. The no-script substitutes (a styled range input, an
  arm switch) are weaker: in most browsers a tap on a range track jumps the thumb there.

The operator asked for the ban to be lifted so the dashboard isn't held to a worse product
(2026-10-13).

What the CSP actually protects is narrower than "no JavaScript". Every value on every page
is escaped; the CSP is the second line, so that a value which slips past escaping still
cannot run as script. That protection depends on refusing **inline** and **injected**
script, not on refusing script files the node itself ships.

## Decision

1. **Scripts the node ships are allowed; no other script is.** The CSP gains
   `script-src 'self'`. Still refused: inline `<script>` blocks and `on*` attributes, `eval`
   and its relatives, and any script from another origin. Nothing loads from outside the node
   (no CDN, no web fonts), so the dashboard keeps working on a site with no internet.
2. **Scripts are files in `modules/dashboard/static/`**, served by core at `/static/<name>`
   from the list of files present at startup (no path is joined from the request). A static
   file is sorted into a door like a page: refused for an unknown tailnet caller or a wrong
   `Host`, and on the fallback only with a session.
3. **Every page still works without script** (progressive enhancement). The server never
   trusts the script: a may-disturb action still needs `confirm=yes` in the form and still
   passes `authorize()`. The slide fills in that field; without script the page shows the
   required checkbox and button it has today. API clients are unaffected.
4. **Auto-refresh moves to script, with a fallback.** The overview's
   `<meta http-equiv="refresh">` sits inside `<noscript>`, so it only applies without
   script. With script, the page reloads itself on the same interval but not while the
   operator is in the middle of something (a slide in progress), so a refresh never yanks a
   control away mid-gesture.
5. **CI parses every shipped script (§8.1).** `ci.sh` runs `node --check` on each file in
   `static/` and fails on a syntax error. A test checks that every page references only
   scripts that exist and that no page contains inline script or event-handler attributes.
   Where `node` is not installed the step prints `SKIPPED` visibly; it is never silent.

## Options considered

| Option | For | Against |
|--------|-----|---------|
| **A. Keep the ban** | Smallest attack surface; nothing to gate | Each interactive need becomes a server round trip or a weaker control (above); the operator rejected it |
| **B. Allow per-page inline scripts by hash** (`script-src 'sha256-…'`) | No new request path | A hash per script per page, kept in sync with the code; inline blocks are harder to lint and to gate in CI |
| **C. First-party script files, `script-src 'self'`** (chosen) | One rule; files are linted and checked like code; injected markup still cannot run script | A new static request path; a JS toolchain for the CI gate |
| **D. Drop the CSP's script rule entirely** | Anything goes | Loses the second line of defence against an escaping bug; external loads break on an offline site |

## Consequences

- **Easier:** interactive controls (slide-to-confirm, keeping state across a refresh,
  live updates later) without bending the server around each one.
- **Harder:** scripts are code to review and gate, and a JS toolchain joins the dev
  environment. `node` is not installed on this node today, so the CI gate shows SKIPPED here
  until it is (`apt install nodejs`; dev/CI only, never a runtime dependency).
- **Kept:** the cookie theme and `/?system=open` stay as they are. They work, and they are
  the no-script fallback.
- **Revisit** if the dashboard ever needs a build step (bundling, TypeScript). Then the gate
  becomes the codegen freshness check of §8.1.

## Implementation

1. CSP `script-src 'self'`; core serves `static/` (allowlist, behind the doors).
2. The Tailnet card leaves `/config`; its operations, notes and last result move to the
   gateway section of the overview. Leave gets the slide-to-confirm.
3. Overview refresh: `<noscript>` meta refresh, and a script refresh that pauses during a
   slide.
4. `ci.sh` JS gate (`node --check`, loud SKIPPED) and the page-level test.

## Amendments

### 2026-10-13 — the refresh and the theme stay in place (`connect-src 'self'`)

**Problem (operator).** A full reload repaints the page from the top before the browser
restores the scroll position, so a page scrolled down blinked to the top every 5 s. The
day/night links navigate (`/theme/<t>` → 303 → the page), and a navigation always lands
at the top. A reload also cleared a ticked confirm box on Join, Rename or Routes.

**Decision.** The CSP gains `connect-src 'self'`: the node's own script may call back to
the node, and nowhere else. Decision 4 changes from "the page reloads itself" to:

- **Refresh is a swap.** The script fetches the same URL and replaces the banner and
  `<main>` with the fresh ones. Scroll stays put and focus is kept by position. The refresh
  waits while a slide is moved *or a confirm box is ticked*. An error, a redirect (an
  expired fallback session) or a page without those parts falls back to a full reload.
- **The theme switches in place.** A slider click fetches `/theme/<t>` without following its
  redirect, so the server still sets the HttpOnly cookie, then sets `data-theme` on the root.
  If that fails, the link is followed as before.

**Unchanged:** no inline script, no `eval`, no other origin, and every page works without
script (meta refresh in `<noscript>`, theme links, `/?system=open`). The server trusts the
script no more than before: both requests are GETs it already serves through the doors.

**Risk.** `connect-src 'self'` adds nothing an injected *markup* fragment can use, because it
still cannot run script. It matters only if script is already running, and then same-origin
requests were already possible by navigation. A test pins `connect-src` to exactly `'self'`,
and another checks that every page has the banner and `<main>` the swap relies on.

### 2026-10-13 — one slider on the overview

At the operator's request the overview's tailnet actions are a single slider with no
notes: **Leave the Tailnet** when joined, **Join the Tailnet** when not. Join joins Leave as
a slide-to-confirm (accent, not danger). The slider reads the action itself; its warning
shows only without script, where it is the required confirm. Rename and Routes move to
`/config`, beside the hostname and the advertised routes, as ordinary may-disturb forms
with the warning inline. The last result stays visible as the gateway's *Last action* fact
(ADR-0005 A2: every outcome is visible).

### 2026-10-13 — a confirmed slide waits for its outcome

A tailnet write runs on tailnet-gateway's worker after the POST has been answered, and its
result arrives on a later tick. So the page the POST redirects to was drawn before the change,
and `/config` (no auto-refresh) kept showing the old slider ("Stop advertising routes" after
the routes were gone). Now a confirmed slide posts in the background and holds at
"<action>…". It polls the page once a second until the outcome shows, then swaps. The outcome
shows when the form for that action is gone, or when a new result
(`data-last-apply`, the finished time of the last outcome) has been seen on two polls in a
row. It gives up after 30 s and swaps anyway. A refusal (the POST's text answer) shows on the
slider for 6 s, then the slider resets. On `/config` only the `[data-live]` regions (the
tailnet rows and the marker) are swapped, never a field being typed. A failed Rename, Routes
or Routes off shows its reason under its field. Without script nothing changes: the form
posts and the page reloads.
