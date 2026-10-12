# Meta-Architecture: The Additive-Contract Polyrepo

**Spec version: 0.4.0** — see `CHANGELOG.md`. This document versions itself by its own rules
(§3 semver; §12 governance): section numbers never change meaning, revisions add. Projects
record the spec version they adopted in their ADR-0001 and note it when they diverge. **Keep
this line in every vendored copy** — it is how a later audit knows which spec a project was
built against.

> **What this is.** A portable architecture pattern, distilled from working multi-service
> projects and revised from their field lessons (`docs/lessons/`). Drop this file into a new
> workspace (or paste it into the workspace `CLAUDE.md`) and any agent or developer can
> bootstrap a project ecosystem that follows it. Everything here is domain-agnostic —
> placeholders like `{ecosystem}` are yours to fill.
>
> **Placeholders used throughout:**
> `{ecosystem}` — the project family name · `{org}` — git host org/user ·
> `{workspace}` — the superproject repo · `{service}` — any individual service repo ·
> `{ecosystem}-interfaces` — the shared contract package · `{deploy}` — the deployment repo

---

## 1. Core Mission

Many small services, developed at wildly different speeds, by different people (or agents),
that still compose into one working system — without a coordination meeting for every change.

The mechanism is a single rule applied at two scales:

**Contracts only grow. They never shrink or mutate.**

A consumer built against version N of a data shape can always read version N+k, because N+k
contains everything N had plus new optional fields the consumer is free to ignore. This makes
version skew a normal, survivable condition instead of a failure mode — which is what lets
each repo, and each feature within a repo, move at its own pace.

> **Start small, leave room to grow.** The full polyrepo topology below is the *destination*,
> not the starting point. New projects default to Stage 0 — a modular monolith practicing the
> same contract disciplines in one repo — and graduate when the triggers in §10 fire.

---

## 2. Repo Topology

**Polyrepo.** Each service is an independent git repo with its own semver and release cycle.
This enforces backwards-compatibility discipline: skew is real, graceful degradation is
required, each service is owned independently.

Three special repos exist alongside the services:

| Repo | Role |
|------|------|
| `{workspace}` | Superproject manifest — records *which commit of each service* belongs together |
| `{ecosystem}-interfaces` | The shared outward contract — all cross-service data shapes and constants |
| `{deploy}` | Deployment authority — docker-compose (or equivalent) with pinned, tagged releases |

### The superproject manifest

`{workspace}` is a git repo whose service directories are git submodules — each a full,
independent clone of its own repo. The superproject holds no service code; its commits are
version manifests. Its branches encode two tiers:

- **`master`** — the known-good set. Each submodule pinned to a validated commit. Check it
  out and you get a combination proven to work together.
- **`dev`** — the latest-testing set. `.gitmodules` on this branch sets a tracking branch per
  submodule, so `git submodule update --remote` follows each service's dev tip. **Commit the
  resulting pins** — every `dev` commit is then both "latest" and "exactly reproducible."

Because `.gitmodules` is tracked, the `master` and `dev` copies differ by design.

**Promotion is pin-copying, never a branch merge.** Merging `dev → master` would drag dev's
tracking config onto master. Instead: check out master, point each submodule at the commit
dev validated, commit, tag.

**Detached-HEAD rule (the #1 submodule footgun).** Submodules check out at pinned commits in
detached HEAD. Always `git checkout <branch>` inside a service before editing, or commits
land on no branch.

### Three version surfaces — keep them in their lanes

| Surface | Pins | Answers |
|---------|------|---------|
| Superproject `master`/`dev` | source commits | "which code do I check out to develop/test together?" |
| Each service's dependency pin (`{ecosystem}-interfaces @ vX.Y.Z`) | contract version | **"can these two services interoperate?"** — the sole interop authority |
| `{deploy}` | built/tagged images | "what actually ships?" |

Skew across services is expected and legal. A pin referencing a tag that doesn't exist is the
only crime: **every contract version is a tagged release, and bumping the version string is
not a release — cutting the tag is.** Enforce this in CI (§8.7).

### From `{workspace}` to `{deploy}` — only promoted sets ship

`{workspace}` `master` records which combination is proven to work together; `{deploy}`
records what runs. The rule between them:

- **Every `{deploy}` release names exactly one `{workspace}` tag**, and every artifact it ships
  (image, package, binary) is built from the source commit that tag pins. Never from a `dev`
  pin, an untagged commit, or a hand-picked mix of two tags — a mix is a combination nobody
  validated.
- **Provenance is recorded, not reconstructed.** Each artifact carries the source commit it
  was built from (an OCI `org.opencontainers.image.revision` label or equivalent), and the
  `{deploy}` release records the `{workspace}` tag. "What's running?" then answers "which
  validated set?" without archaeology.
- **No side door.** An urgent fix takes the ordinary path at speed — `dev` → promote → tag →
  deploy — never a deploy-only patch. `{deploy}` may lag `{workspace}` `master` (not every
  promoted set ships); it never leads it.
- **Machine-checked (§8.1):** `{deploy}` CI verifies that every artifact's recorded source
  commit equals the pin in the `{workspace}` tag the release names.

At Stage 0 (§10) there is no `{workspace}`: the monolith's own release tag stands in for it,
and the same provenance rule applies to whatever the one deployable ships as.

---

## 3. The Outward Contract (`{ecosystem}-interfaces`)

A pure library: data models (e.g. Pydantic), topic/channel name constants, capability
constants. **No logic.** Never import from a service repo — if two services need a type, it
belongs here.

- Every type that crosses a service boundary lives and versions in this package.
- Consistency is **location + versioning, not shape** — pick each type's shape from its data
  semantics; don't force uniform structure for its own sake.
- All models tolerate unknown fields (`extra="ignore"` in Pydantic terms), so consumers built
  against an older version silently ignore fields added later. This is what makes MINOR bumps
  safe by construction.
- Releases are cut from the known-good line: bump version on `dev` → promote to `master` →
  tag `vX.Y.Z`. Consumers bump their pin when they want the new fields, not before.

### Semver for contracts

| Change | Bump |
|--------|------|
| Add an optional field to a model | MINOR |
| Add a new model, topic, or constant | MINOR |
| Rename, remove, or change type/meaning of anything existing | MAJOR |
| Doc or bug fix with no shape change | PATCH |

### What counts as "additive" — the precise definition

Semver alone is too coarse; these edge cases break consumers despite looking additive:

| Change | Verdict | Why |
|--------|---------|-----|
| New optional field with a default | ✅ additive | old consumers ignore it |
| New model / topic / constant | ✅ additive | nothing existing changes |
| New enum value | ⚠️ **breaking** for exhaustive matchers | treat as MAJOR, or mandate consumers handle unknown enum values from day one |
| Widening a type (`int` → `int \| None`) | ⚠️ breaking for consumers that assumed non-null | MAJOR |
| Tightening a validator / narrowing a range | ❌ breaking | payloads that used to parse now fail — MAJOR |
| Changing the *semantics* of an existing field (same name, same type, new meaning) | ❌ **forbidden** | invisible to semver and to schema diffing — add a new field and deprecate the old one instead |

Additivity applies **inside dict-valued fields too**: adding keys to the entries of a
`list[dict]` / mapping field is a MINOR; keys are *absent* when unknown, never null-stuffed.
And the shape of a dict inside an outward model is **outward contract**, not the producing
service's private business — it versions here.

**Type nested shapes from the first field.** A schema-diffing checker (§8.1) sees an untyped
`dict` as `{"type": "object"}` and cannot detect a removed, renamed, retyped, or newly-required
inner key — the rule above becomes honor-system exactly where shapes grow fastest. Give every
nested shape its own model (open to unknown keys, like every model here) the day the field is
born. Retrofitting is not incremental — typing some fields creates a two-tier convention, and
typing all of them changes what the checker's baseline asserts, which is a MAJOR act — and
long-lived *persisted* nested dicts compound the cost. Any convention-only dict that remains is
a declared blind spot: list it in the deprecation/MAJOR ledger (§8.3), never leave it implicit.

### Same model, multiple producers: plant a discriminator

When two or more producers will publish the *same* model (now or foreseeably), give it one
load-bearing discriminator field (e.g. `origin`) **before the second producer exists**.
Adding it later is additive but retro-tagging old data is not possible. Consumers correlate
on the discriminator; the model stays shared.

### The third contract surface: out-of-band documents

Some artifacts are versioned data but not bus messages: reports, exported files, on-disk
stores. Give each its own version constant (e.g. `REPORT_VERSION`), evolving independently of
the wire contract. Its versioning starts carrying compatibility weight at the **first
cross-version reader** (a remote consumer, a migration) — from that moment the additive rules
and a §8.1-style check apply to it as well. Name these surfaces early so they don't drift
unversioned.

---

## 4. Structure Standard & Ontology

Every service repo follows the same layout. **The core is I/O only** — it registers features,
routes data, and publishes output. All logic lives in feature modules.

```
{service}/
├── CLAUDE.md                  # service context, ecosystem role, current feature list
├── interface/                 # ← the INWARD contract (see below)
│   ├── __init__.py            # VERSION + __version__ (see dual versioning) + exports
│   ├── models.py              # data models — the inward schema
│   ├── feature.py             # Feature Protocol/ABC every feature must implement
│   └── README.md              # human-readable interface doc (from _templates/)
├── core/
│   ├── registry.py            # feature registration + routing
│   ├── input.py               # subscriptions — whatever this service reads
│   └── output.py              # publications — must conform to {ecosystem}-interfaces
├── features/
│   └── {feature_name}.py      # one file per feature, implements the Feature protocol
├── docs/adr/                  # architecture decision records for this service
├── requirements.txt           # pins {ecosystem}-interfaces @ vX.Y.Z  ← interop authority
└── main.py
```

No `Dockerfile` in the standard layout: **deployment form is an ADR decision, not a default.**
Containers fit hostable services; a bare-metal, kernel-coupled appliance (drivers, raw
sockets, host daemons) may reject them on principled grounds — record the choice and its
consequences in an ADR either way (see the ops tier below).

### The complete ontology — six boxes, not two

Real projects need more categories than "module" and "contract package." Each has a
membership rule and a machine-checkable import discipline (§8.1):

| Tier | What it is | Import rules | Stage-0 home (§10) | Stage-1 fate |
|------|-----------|--------------|--------------------|--------------|
| **Contract package** (`interfaces/`) | cross-boundary data shapes + constants, no logic | imports nothing internal | `interfaces/` | the tagged contract repo |
| **Module / service** | interface/ + core/ + features/, speaks the bus | never imports another module's internals | `modules/{module}/` | its own repo |
| **Neutral shared package** | domain-neutral logic over open standards/formats | **stateless; imports no module; holds no bus reference** | a top-level package named for what it does (`{name}/`) | a shared library package |
| **Bus-coupled infra** | composition-root-wired plumbing that produces/consumes bus traffic but is not a module (no Feature, no interface/) | may hold a bus reference; imports no module internals | a top-level package named for what it does (`{name}/`) | part of the service that owns it, or graduates |
| **Ops / provisioning tier** | host installer (idempotent, `--dry-run`), read-only doctor script, service units, pinned host binaries, vendored drivers with provenance | not imported by anything | `ops/` | the deploy repo + per-host provisioning |
| **Composition root** (`main.py`) | composes modules onto the bus; owns the serve loop | imports everything, is imported by nothing | the root `main.py` | per-service entrypoints |

Two of these earn special notes:

- **Neutral packages are where standards-logic lives.** Decoding an open format (pcap, CAN,
  a protocol catalog) can't sit in a logic-free core, can't be a module (others would have to
  import it), and isn't a contract type. Without this tier every project reinvents it under
  pressure. Purity is machine-checked, not honor-system.
- **The composition root grows — budget for it.** It ends up owning: the multi-cadence serve
  loop + signal handling; one-shot CLI modes that isolate side-effecting modules onto
  **private bus instances** (so a manual run can't cascade into the daemon's modules or reap
  its state); self-versioning; defensive config resolution (malformed values degrade and
  clamp, never crash boot); capability/version aggregation. Describe daemon-vs-oneshot
  behavior explicitly.

### Dual versioning — two versions per module, from day one

A single inward semver conflates "did the contract change?" with "did the code change?" —
modules ship real features with an unchanged interface and look frozen. Every module carries
two versions, both declared in `interface/__init__.py` (the one package both core and
features may import):

- `VERSION` — the **release version**; bumps with features/implementation.
- `__version__` — the **inward contract version**; bumps only when `interface/` changes.

Add a `Since` column to every interface export table and a standing per-module Deprecations
table (§8.3 needs a place to live). At Stage 1, `VERSION` becomes the service's repo semver.

### The inward contract

`interface/` is the stable contract between a service's core and its features — a deliberate
miniature of the ecosystem contract, with its own independent semver and the same additive
rules. Two tiers, same principle, different blast radius:

| | Outward (`{ecosystem}-interfaces`) | Inward (`{service}/interface/`) |
|---|---|---|
| Scope | every service | one service's features |
| Change cost | ecosystem-wide coordination on MAJOR | one repo's feature branches on MAJOR |
| Pace | slow, tagged, ceremonial | fast, iterated freely |

The inward tier exists so a service can evolve its internal schema rapidly without touching
the ecosystem contract. It may mirror outward shapes but is never required to.

**Module identity lives in the interface, not the core.** The module's name constant (used
for capability flags and logging) belongs in `interface/__init__.py`, importable by both
`core/` and features — never in `core/`. Features may not import `core` internals (below),
yet they need the module identity, so it lives in the one package they are allowed to import.

**Features meet in interface objects.** When two features in one module must share *live
state* (a coordinator, an interlock, a tuning store), the sanctioned channel is a shared
mutable object **defined and versioned in the inward `interface/`**, constructed by core and
handed to both features. Features never import each other, and the bus is wrong for
intra-module state — this is the third option, and it is legal by design.

**The vendor-neutral seam** (the most-reused move in field use — apply it to *all* external
I/O, not just bus providers): define a vendor-neutral protocol in the inward `interface/`,
implement one adapter per vendor in `core/`, inject it. A second vendor is one new adapter
and zero feature changes. Hosts, cameras, buses, external servers, protocol decoders — same
shape everywhere.

### Canonical authorities — one implementation of a load-bearing fact

The neutral-package tier says shared logic *may* live in a library. For some facts that is too
weak: when two consumers computing the same *derived* value differently would be a safety
event (a collision geometry, "are we in simulation?", which sensor is the truth), the value
needs exactly **one** authority.

- **Membership.** A canonical authority is a neutral package that holds **the only**
  implementation of a named derived fact. Every consumer imports it; none re-implements it, not
  even "a quick local version." The boundary checker (§8.1) can enforce the import; review
  enforces the absence of copies.
- **Ported algorithms are verified by executing the original.** When the authority ports a
  vendor or reference algorithm, its tests run the original (compile it, call it) and diff the
  outputs. A pin bump of the original re-runs that verification — a port is only as good as its
  last cross-check.
- **Definition centralized, enforcement distributed.** The *definition* of a safety predicate
  lives in one authority; *enforcement* sits at every consumer that could violate it, each over
  its own inputs. Centralize to stop copies drifting apart; never replace per-consumer checks
  with one upstream gate that consumers trust blindly.
- **Selected inputs and coherent sets are facts too.** "Which source is authoritative" (one
  source, quality-ranked, operator-overridable) and "is this parameter set valid" (validated
  *as a set*; a surface says what it replaced) get the same one-authority treatment.

### Why this makes long-lived feature branches safe

Version bumps are driven by interface changes, not feature count. Because MINOR bumps are
additive-only, **a feature branched from interface `1.2.0` merges cleanly against `1.5.0`** —
everything it depended on still exists, untouched. One feature can take a month, another two
years; neither blocks the other. Only a MAJOR bump forces coordination across branches.

⚠️ **Scope of the guarantee:** additivity protects compatibility *at the interface layer*. It
says nothing about drift in `core/`, tests, or dependencies over two years. Prefer
"merge early, register disabled" (§8.4); treat branch longevity as a fallback, not the plan.

### Starting a new feature

1. Branch from `dev` — name: `feature/{name}`
2. Implement the `Feature` protocol from `interface/feature.py`
3. Need new data fields? Bump the interface MINOR version, update `models.py` +
   `interface/README.md` **on dev first**, then branch from that updated dev
4. Merge back to `dev` when complete. The version bump is already on `dev`; other feature
   branches are unaffected. `master` receives promotions from `dev`, never direct merges.

### Feature graduation

Features start as modules inside a service. A feature graduates to its own service when
keeping it would force the service to carry data, connections, or logic no other feature
needs — i.e. **the service is becoming a middleman for something it doesn't own.**

Path: feature speaks the inward interface (normal) → starts needing foreign data passed
through the service → that foreign scope grows until the service is bureaucratic overhead →
extract into its own repo, now speaking `{ecosystem}-interfaces` (outward) directly.

---

## 5. Runtime Tolerance: Capability Discovery

Version skew is legal at build time, so it must be survivable at runtime:

- Every service exposes a `/capabilities` endpoint (or equivalent) listing feature flags.
- Consumers check before using a feature. If absent, log a user-friendly message and disable
  that feature — **never crash**. Format:
  `"X not available — service Y at vZ predates support."`
- Because contracts grow additively and consumers discover capabilities, services pinning
  different MINOR contract versions interoperate indefinitely.

**Who checks:** only active *invokers* — anything that publishes a command and needs someone
listening — declare and check capabilities (`requires="{service}:{feature}"`, refuse with a
friendly error when absent). Passive consumers never check: an absent producer just means no
messages, and degradation is graceful by construction; checks there are ceremony with no
failure mode. Corollary: a command delivered to **zero subscribers must be a refusal at the
invoking seam**, in the message format above — silent `delivered: 0 → ok` is exactly the
failure this section exists to prevent.

**The action manifest pattern.** Expose invokable operations as a machine-readable manifest:
name + parameter schema + `requires` + current availability (+ risk warnings, §11). A human
UI and an LLM caller consume it *identically* — capability discovery upgraded into a tool
schema, and the pre-built seam for agent-driven control later.

**Devices too.** External hardware gets the same treatment behind its vendor seam (§4):
probe the device's capabilities, gate actions on what it actually supports, degrade politely
when a probe premise turns out false.

**Grants are levels, not edges.** Any grant of liveness or authority — a lease on a resource,
a membership, a "keep moving" command, a stream subscription — is a *level* held by renewal at
a declared cadence, and it lapses to the safe state on its own when renewal stops. Never a
one-shot *edge* ("start", "you are a member") whose loss or missed revocation leaves the grant
standing forever. Clamp requested durations to bounds; release, expiry, and silence all take the
same safe path. (§11 rung 11 is the actuation case.)

**Enrichment declares the direction it fails.** When a consumer enriches data from a droppable
feed (a membership list that exempts targets, a trust list that relaxes a check), the
enrichment names its **safe default**, and staleness or absence of the feed collapses to that
default — loudly, with a stated staleness threshold. "Degrade gracefully" is not enough when one
degradation is cautious and the other is dangerous: fail toward the cautious reading. A policy
feed stores the *exceptions*, names its default explicitly, and so stays unambiguous when stale
or empty.

**The self-description card (peers).** Capability discovery generalizes from inside a process
to between nodes: each node serves a **read-only card** — identity, state, versions,
capabilities — that peers *pull*. The card is a contract surface (versioned under §3's rules)
and is **strictly disjoint from the command surface**: reading a card never acts, and nothing a
card says is an instruction. See §14.

---

## 6. Communication Conventions

- **One spine.** Services communicate through a shared bus/broker (message queue, pub/sub,
  shared DB of record — whatever fits), not point-to-point to each other.
- **No aggregation-for-its-own-sake layer.** Each consumer subscribes to exactly the streams
  it needs. A merge layer that neither transforms nor filters only adds latency.
- **Unreliable links are async.** Any intermittent connection (remote operator, cloud
  uplink) gets command queues, not synchronous RPC.
- **Provider-specific code is middleware.** Generic services define stable interfaces;
  thin adapters translate one provider's wire protocol to/from them. Swapping a provider
  means writing a new adapter — nothing above it changes. (Generalized as the vendor-neutral
  seam, §4 — it applies to every external dependency, not just the bus.)
- **State the dispatch model.** Say explicitly whether bus delivery is synchronous
  (in-process Stage 0 dispatch usually is). Synchronous dispatch means slow handlers stall
  the loop — plan the worker-thread seam, lazy-started listeners, and "queue results, drain
  on next tick" publishing *before* the first slow feature discovers it for you.
- **One sanctioned exception to bus-only: config.** Configuration is not a message — a
  declarative registry (features declare fields; an operator surface reads/edits them) may be
  process-wide shared state outside the bus. The exception must be *documented as a
  deliberate deal* with its graduation cost named in advance (an extracted service can't
  reach an in-process registry). Any further bus-bypass proposals face the same bar: named,
  justified, budgeted — never smuggled in.
- **A foreign runtime is a sidecar, never the spine.** When you adopt a third-party system that
  brings its own bus or process community (an autonomy framework, a robotics middleware), run it
  beside your system and reach it through **one bridge module**. Its bus never becomes yours.
  The bridge is where its quirks are pinned (§7 quirk registry), its algorithms are ported under
  a canonical authority (§4), and writes into it are funnelled (§11 rung 9).
- **A bridge to another transport is never transparent.** Any bridge between the spine and
  another transport (a broker, a field bus, a foreign runtime) publishes an **explicit allowlist
  of what crosses, in each direction**; nothing else crosses. The bridge is the only place in
  the system that knows the other transport exists, and its crossing list is the audited
  security boundary.

### Transport tiering — a deferred lever (default: single spine)

The default is **one reliable spine** for everything. Do **not** add a second transport on
day one. It is a lever to pull only when a real-time stream appears whose needs the broker
genuinely cannot meet.

- **Trigger.** Reach for it only when *newest-and-fast beats complete-and-ordered* — a
  high-rate stream (audio, video, a tight control loop) where a late sample is worse than a
  dropped one, and the broker's per-message hop, ordering, and acknowledgement are pure cost.
  Ordinary telemetry, commands, config, and state stay on the one spine indefinitely; a few
  milliseconds of broker latency is invisible there.
- **The split (control plane vs. data plane).** When triggered, run two spines by job:
  the **control plane** (the broker) carries commands, config, state, and capability
  discovery — low volume, reliable, ordered. A **data plane** (direct UDP / multicast / RTP,
  or DDS for real-time-grade work) carries the firehose — best-effort, newest-wins, no broker
  hop.
- **Announce over reliable, stream over fast.** A producer advertises the stream on the
  control plane (endpoint + codec + best-effort QoS — this is just capability discovery, §5);
  consumers that care then open a direct socket to that endpoint for the bytes. The data never
  touches the broker. (Same shape as WebRTC signalling vs. media.)
- **The contract is transport-independent.** Payload shapes still live in
  `{ecosystem}-interfaces`, still obey the additive-only rule, regardless of which spine
  carries them. A fast path is just another adapter behind the same stable interface
  (the "provider-specific code is middleware" rule above) — the `Bus` abstraction gains a
  second implementation; no consumer's data models change. Do **not** force a high-rate stream
  through the broker merely because it exists; add the lever instead.
- **The fast path is often provisioned, not written.** In practice the data plane may be an
  external server you install and reconcile against (a media server's native RTSP/HLS/WebRTC,
  a store-and-forward daemon) rather than a second `Bus` implementation you code. The bus
  still carries only the announcement; the ops tier (§4) owns the server.
- **The line between the planes:** bounded per-key *latest-sample* metadata (a census, a
  status card) belongs on the bus; per-event firehoses do not. The test: **if you could
  reconstruct the traffic from it, it's data plane.**

---

## 7. Decision & Status Records

- **ADRs** live in each service's `docs/adr/` and hold the *why*. Workspace-level TODO holds
  the *what's left*, each item linking to its ADR.
- **Live state** (which versions/pins/branches are where, drift between them) belongs on an
  ops/status board or tooling — never hand-maintained in docs, which go stale.
- Each repo carries its own `CLAUDE.md`: role, interfaces consumed/exposed, current features,
  and the standard "this repo is a submodule of `{workspace}`" section.
- **ADR amendment is normal, not failure.** Design-before-hardware premises get refuted by
  the bench. The ritual: amend the ADR with measured reality plus a revisit clause; gate open
  items on data ("decide once X is captured"), never on guesses.
- **Record hardware-fact → posture links.** When a measured constraint drives a software
  convention (device has no read-only mode ⇒ writes need operator approval), write the fact
  *next to the convention* — in the ADR and near the code — or future reviewers can't know
  why the gate exists.
- **Design-only ADRs are legal.** An ADR may fix the shape and guardrails of unscheduled
  future work, so a later agent inherits the constraints instead of re-deriving them.
- **Two more recognized ADR shapes.** A **decision ledger** — an ADR whose body is the measured
  numbers behind a set of constants and why each was chosen (§8.11). A **review-as-rulings**
  ADR — when a review produces numbered binding decisions, they become an ADR, not a comment
  thread.
- **Pinned foreign dependencies keep a quirk registry.** Every measured *silent* behaviour of a
  pinned vendor runtime (it drops this, it reorders that, it ignores a setting) gets an entry:
  the behaviour, how it was measured, and a detection signature. A pin bump re-verifies every
  entry — the registry is the checklist for "is the new version safe to adopt?"

---

## 8. Standing Rules (hard-won notes — codify these from day one)

These close the gaps a first implementation of this pattern discovers the hard way.

### 8.1 Every load-bearing rule is machine-checked, not promised
Don't leave conventions to the honor system — if the architecture leans on a rule, tooling
enforces it. The mandatory pair, shipped on day one:

- **The additivity checker.** Snapshot every released model's JSON schema; on a MINOR bump,
  assert the new schema is a strict superset (all old fields present, same types, new fields
  optional). Fail the release otherwise. Refresh the baseline at each tagged release.
- **The boundary checker.** AST-resolved imports enforcing the §4 ontology: modules never
  import each other's internals, features never import core, neutral packages stay pure
  (no module imports, no bus reference). Treat **unparseable files as violations**; catch
  evasive spellings (`from .. import core`, multi-name imports). Run it in CI *and* as a
  test — it's what makes Stage-1 extraction mechanical.

Corollaries: **test the checkers with planted violations** (~one test per violation class,
plus legal shapes pass — a checker that flags nothing is indistinguishable from a healthy
tree). **Machine-check environment conventions** — when the runtime environment creates a
class of silent failure, convert the fix into a convention and CI-check the convention.
**Codegen needs a freshness gate** — when the source-of-truth format is richer than what the
runtime may parse, expect a dev-time codegen step with a `--check` staleness gate in CI and
atomic writes.

**Know what the checkers cannot see.** A schema-diffing additivity checker is blind inside
untyped nested dicts (§3, "Type nested shapes from the first field"). Every known blind spot of
a checker is written down where the checker's users will see it, with the plan to close it.

**Every shipped artifact is executed in CI, not just diffed.** When the contract crosses a
language boundary (a firmware header, a client SDK), **generate** the binding from the contract
models and gate it twice: a freshness check *and* a compile/execute of the generated binding.
The additive rules bind the binding too. The same applies to anything shipped verbatim without
a build step (static JS/CSS, embedded scripts): "ships unparsed" is the same hazard class as
"generated stale," so CI loads or compiles it. A gate that needs a toolchain not every
environment has may be conditional, but it **skips loudly** (a visible SKIPPED line in CI
output). A silent skip is not a gate.

### 8.2 The forwarding trap
`extra="ignore"` has a subtle failure mode: a service that consumes a payload and
*re-publishes* it through its own (older) models silently strips fields newer downstream
consumers need. **Rule: forwarders pass through the raw payload; only terminal consumers
deserialize through their pinned models.**

The rule binds **per feature, not per service** — one service (a dashboard, say) legitimately
hosts all three roles at once. Two carve-outs:

- A **terminal consumer** feature (stores/renders, never re-publishes) may deserialize
  through its models — with the standing caveat "if it ever re-publishes, revisit."
- A **command producer** feature (builds a *new* message from operator/agent input through
  the current contract model) is ordinary producer behavior, not forwarding.

### 8.3 Deprecation policy — additive-forever is entropic
Contracts that only grow accumulate barnacles. Define removal *before* you need it:

- Deprecate in a MINOR: document it, keep it working, expose a capability flag.
- Remove only in a MAJOR, after a minimum grace window (e.g. two MINOR releases or N months,
  whichever is longer).
- A standing trigger for when accumulated deprecations justify scheduling a MAJOR
  (e.g. ≥5 deprecated fields, or any deprecated field older than a year).
- MAJOR bumps require a deprecation schedule and per-consumer triage — they are rare,
  planned events.

### 8.4 Merge early, register disabled
Since features go through a registry anyway, prefer merging to `dev` frequently with the
feature unregistered or flagged off over living on a branch for months. The long-branch
guarantee (§4) is the safety net, not the workflow.

### 8.5 Version on the wire
The interop authority (the dependency pin) is invisible at runtime. Include a
`schema_version` field in every published envelope. Skew becomes observable live, ops
tooling can show wire versions vs. pinned versions, and "why did this consumer miss a
field?" becomes answerable in seconds.

### 8.6 Semantics never mutate
The one change no tooling can catch: keeping a field's name and type but changing its
meaning. Forbidden outright — add a new field, deprecate the old one (§8.3).

### 8.7 Ship automation with the convention
Conventions that survive handoff to a fresh agent are the ones encoded as tooling. Minimum
kit to build alongside the first services:

- `promote.sh` — the pin-copy promotion (checkout master, repoint submodules, commit, tag)
- `release-contract.sh` — bump → promote → tag, refusing if tests or the §8.1 check fail
- CI check: every `{ecosystem}-interfaces @ vX.Y.Z` pin across all services references an
  existing tag
- CI check in `{deploy}` (once it exists): every shipped artifact's recorded source commit
  equals the pin in the `{workspace}` tag the release names (§2, "only promoted sets ship")
- Guard against detached-HEAD submodule edits (pre-commit hook or status check)

Two rituals the promote step must include: **refresh the §8.1 additivity baseline at every
tagged release** (else the checker drifts against a stale baseline), and a **pre-promote
smoke pass** — one inline medium-effort review over the whole release diff, hunting only
what per-merge reviews can't see (cross-feature interactions, semantics drift).

**Ship Stage-1 tooling inert, wired into CI ahead of need.** A checker that is a deliberate
no-op at Stage 0 (pin integrity with no cross-service pins yet) costs nothing and activates
itself at graduation — the convention arrives before the first chance to violate it.

### 8.8 Resource entropy — never decode for nobody
The rules above fight *contract* entropy; runtime cost accretes the same way. Standing rule:
hot paths stay cheap (identity, counters, bounded latest-value state); expensive work
(decode, reassembly, enrichment) runs **only while a consumer is actively looking** — and
consumers spin up when used, tear down when idle, so growth doesn't accrete always-on loops.
Binding on every future consumer, including control loops. Supporting patterns:

- **Latest-value vs. windowed history:** the default consumer model is
  latest-payload-per-key; time-series consumers get *bounded* ring buffers. Key stores by
  the entity (channel/device), not by topic — multi-instance announcements on one topic
  otherwise clobber each other.
- **Orchestration ticks are a topic category:** composition-root heartbeats that always-on
  producers ride instead of inventing timers. Cadences are **wall-clock deadlines, not tick
  counts** (one slow handler otherwise stretches every cadence), and a fast tick never rides
  a side-effecting command topic.
- **Runtime artifacts get retention from day one:** anything that spools to disk (captures,
  logs, stores) ships with a bounded-storage policy, not a deferred TODO.

### 8.9 Secrets are secrets from birth
Flagged at declaration in the config registry; masked in every UI; never published on the
bus, never logged, never a second plumbing path. Writers sanitize (strip CR/LF — close
injection for every caller); partial operations never half-write a credentials file. The
first secret sets the pattern — retrofit is a breach postmortem.

**The strongest secret is one that is never transmissible.** Where hardware allows (a secure
element, a TPM), a device's private key is **born in the hardware and never leaves it**.
Enrolment then carries only a *public* key and an operator approval: the device announces its
public key, the operator approves, the system's CA issues a certificate *for* that key, and no
image, flash, or transfer ever carries a secret. "The approval is the pairing." Prefer this to
any provisioning flow that ships a secret to the device, however briefly.

### 8.10 Review is risk-tiered, quota-free
For agent-driven projects especially:

- **Depth by diff surface, never by branch.** Contract bumps, hardware-touching core paths,
  and approval/safety surfaces get the deep review; docs and ops scripts stay cheap. Review
  hunts only in changed code — repo-wide invariants are CI's job *by design* (§8.1).
- **No finding quotas.** Measured failure mode: reviews prompted for N findings return
  exactly N, every time — the quota, not the diff, drives cost and manufactures weak
  findings. Loop until a round surfaces nothing new that survives verification.
- **Verify before fixing.** A finding spends implementation effort only after an adversarial
  check confirms it's real.

### 8.11 Measured constants, never guessed
The rules above fight contract and resource entropy; this one fights *evidence* entropy. A
constant that changes behaviour or safety (a threshold, a timeout, a gain, a distance) is
**backed by a measurement**, not chosen by feel:

- The measurement is **replayable**: logged inputs plus a script that recomputes the number,
  kept in a standing ledger in the repo, not a one-time note.
- The ledger entry records the number, the data it came from, and why that value. Where many
  constants are decided together, the ledger is itself an ADR (§7, decision ledger).
- Changing the constant means re-running the ledger, not editing the number.

### 8.12 A replay corpus is part of the safety case
For behavioural or safety-bearing logic, unit tests are necessary but not sufficient. Keep a
**corpus of recorded real (or rehearsed, §13) episodes** and a replay tool that re-runs them
against the live code. That corpus is a first-class acceptance gate for any behavioural change.
The corpus grows whenever the field produces a new interesting episode, and it is versioned with
the code it gates.

---

## 9. Bootstrap Checklist — instantiating this in a new project

0. **Decide the stage first.** Default to Stage 0 (§10) — a single-repo modular monolith —
   unless the project *begins* with multiple independent owners or deploy cadences. The rest
   of this checklist is the Stage 1 (polyrepo) bootstrap; §10 defines when and how to get here.
   **Record the spec version adopted** (this document's version) in the project's ADR-0001,
   and keep the version line at the top of the vendored copy — both, so either survives.
0.5. **State the security posture** — answer at init, in ADR form, before any endpoint
   exists: *where is the trust boundary?* (e.g. "the overlay network IS the boundary; one
   network login is the whole auth story; security lives in the binding" — or per-service
   auth, or something else). Whatever the answer, its silence is what hurts: an undecided
   posture becomes a standing auth/CSRF TODO re-flagged in every ADR that touches the
   surface. Include the secrets discipline (§8.9) from the first credential. If the answer is
   "the network is the boundary," §15 turns it into mechanism.
1. Create `{workspace}` repo with `CLAUDE.md` (domain context + a copy of / pointer to this
   doc), `WORKFLOW.md`, `TODO.md`, `_templates/interface_README.md`, and `master`/`dev`
   branches.
2. Create `{ecosystem}-interfaces` with the first shared types, `extra="ignore"` on all
   models, semver at `0.1.0`, **tag it**, and the §8.1 schema-superset CI check.
3. Add it as the first submodule; set `branch = dev` per submodule in dev's `.gitmodules`.
4. For each service: create the repo from the Structure Standard (§4), pin
   `{ecosystem}-interfaces @ v0.1.0`, write its `CLAUDE.md`, add as submodule, register it
   in the workspace `CLAUDE.md` project table (name, description, status, key output, role).
5. Create `{deploy}` when the first two services can run together — not before.
6. Build the §8.7 automation kit before the second contract release, while the rituals are
   still fresh.
7. Keep domain-specific content (hardware, protocols, business logic) in the workspace and
   service `CLAUDE.md`s — never in this document.

---

## 10. Growth Path: Modular Monolith → Polyrepo

The architecture above solves a coordination problem. If the project doesn't have that
problem yet — one owner, one deploy cadence — don't pay for the solution yet. Start at
Stage 0 and let the triggers, not ambition, drive graduation.

### Stage 0 — the extraction-ready monolith

One repo, one deployable. Every would-be service is a **module** inside it, and the module
*is* the Structure Standard (§4) minus the repo boundary:

```
{ecosystem}/
├── CLAUDE.md
├── interfaces/                # future {ecosystem}-interfaces — a package from day one
├── bus/                       # in-process dispatcher exposing the pub/sub API of §6
├── modules/
│   └── {module}/              # exact §4 layout: interface/, core/, features/
├── {name}/                    # neutral shared package or bus-coupled infra, when one exists
├── ops/                       # ops/provisioning tier, when one exists — never imported
├── docs/adr/
└── main.py                    # composes modules onto the bus
```

**Every top-level package belongs to exactly one §4 box**, declared in the boundary checker's
config (§8.1); an unassigned package is a §4 decision nobody made. Neutral shared packages and
bus-coupled infra sit at the top level, each named for what it does (`pcap/`, `geodesy/`,
`log_bridge/`), not grouped under a container folder — the declaration is what assigns the box,
and the import path stays the package's own name. `ops/` holds the ops tier (§4): the checker
does not scan it as runtime code, and any import *of* it is a violation. Create each of these
when its first member appears, not at init.

**Non-negotiable from day one** (free now, brutal to retrofit):

- Cross-module types live only in `interfaces/`, with semver, the additive rules of §3, and
  `extra="ignore"` on every model. It's a folder now; it becomes the tagged contract repo
  verbatim at extraction.
- Modules communicate **only through the bus abstraction** — never by importing another
  module's internals. The dispatcher is in-process today; its API is the broker's tomorrow.
- `schema_version` on every envelope (§8.5) and semantics-never-mutate (§8.6).
- Capability discovery (§5) as an in-process registry lookup.

**Deliberately deferred** (ceremony with no payoff at this scale): submodules, the
superproject, per-service tagging, `promote.sh`, the deploy repo, a real broker, per-module
CI, and a second transport (the data-plane lever of §6). Version the monolith as one unit.
The transport-tiering split in §6 is itself a deferred lever — a single reliable spine is the
default; add a data plane only when a real-time stream demands it, never at init.

### Graduation triggers — extract when one fires, not before

| Trigger | Signal |
|---------|--------|
| Second independent owner/agent | someone needs to ship without coordinating with you |
| Divergent deploy cadence | one module needs releases the others can't ride along with |
| Divergent runtime | a module needs different hardware, scaling, language, or placement |
| Ecosystem middleman | the §4 graduation smell, at module scale: the monolith carries data/connections only one module uses |

### Extraction mechanics (Stage 0 → Stage 1)

1. `interfaces/` → its own repo as `{ecosystem}-interfaces`, semver carried over, **tag it**,
   add the §8.1 superset CI check.
2. The extracted `modules/{module}/` → its own repo, unchanged layout, pinning the contract
   tag. The remaining monolith becomes just another (large) service and can shed modules
   incrementally — full decomposition is not required.
3. In-process dispatcher → real broker behind the same API; topic constants already live in
   the contract package.
4. Now run the §9 checklist from step 1: workspace, submodules, and the §8.7 automation kit.

Because Stage 0 enforced the contract disciplines, extraction is mechanical repo surgery —
not a redesign.

### Graduation is per-lever, not only per-repo

The Stage 0 → 1 story above is a wholesale repo split. The individual Stage-1 pieces are
separate levers, and **one can be pulled early, behind one module seam, without the split**: a
real broker, an out-of-process component, a second language, a hardware edge. Each one is its
own ADR naming the trigger that fired. The bus API stays the same, the contract still governs
what crosses (§6 allowlisted bridge, §8.1 cross-language gates), and the polyrepo split remains
a separate decision driven by the triggers in the table above. This is the same move as §6's
transport tiering, generalized.

### Environments: the dev node is a real node; disposables say how work survives

- **Dev/prod parity, literally.** The development environment should be an *instance of the
  real stack*: the same daemon and configuration surface, running against simulated or
  rehearsal inputs (§13). It should not be an approximation with its own code paths.
  Developing on the real thing is what keeps the real thing developable.
- **Disposable by construction, never lossy.** Ephemeral instances (cloud dev nodes, CI
  runners, sim fleets) are built from one image and reaped freely. Being disposable includes a
  declared **state-preservation path**: what in-flight state exists (uncommitted work, logs,
  recordings) and how it survives reaping (e.g. parked on a `wip/` branch, synced to storage)
  *before* the instance is destroyed.

---

## 11. Actuation & Safety — the ladder

Observing the world and *acting on it* are different classes of feature. Any project whose
software can write to hardware, shared infrastructure, or other people's systems adopts this
ladder from the first actuator. It is the §5 capability model extended with risk posture:
capabilities don't just exist or not — they carry consequences.

1. **Read-only by default.** A new module observes; it does not write. Write ability is an
   explicit, per-capability grant.
2. **Discovery suggests; the operator confirms.** Automated discovery may *propose* (a found
   device, a candidate config); adoption is a human act.
3. **First write to an unowned device is operator-confirmed, never automatic.** "Unowned"
   means anything the system didn't provision itself — someone else's bus, camera, network.
4. **May-disturb actions carry an explicit confirm** — and the warning lives in the
   machine-readable action manifest (§5), so LLM/agent callers see exactly what a human
   sees.
5. **Mutually-exclusive hardware operations get interlocks** (a per-resource lock owned by
   one coordinator object, §4 "features meet in interface objects") — never "shouldn't
   happen at the same time" by convention. The sharp form is a **lease**: the lock is a held,
   expiring grant (§5 "levels, not edges"), and there is deliberately **no force door**. A
   "break the lock anyway" parameter defeats the interlock; a stuck holder is resolved by
   letting the lease expire.
6. **Declared state is always restored, exception-safely.** An operation that changes device
   state restores the declared spec on every path; a failed restore leaves the resource
   *safe* (down + loudly warned), never silently misconfigured.
7. **Refusals are observable to the caller.** A gate that declines must say so in-band, in
   the §5 message format — journal-only refusals train operators to distrust the system.
8. **Control/TX on a safety-critical medium is its own ADR + safety review + independent
   hardware kill.** Never bolted onto an observation feature.

Rungs 1–8 govern *who confirms a write*. Rungs 9–13 are **structural**: where the safety
properties live, so they hold regardless of who is writing.

9. **One writer per safety-critical store.** Safety-critical actuation funnels through a
   **single writer** — the only code that writes to the actuator, controller, or shared store.
   That writer is the sole place the safety properties are enforced, once, where they cannot be
   bypassed. A canonical authority (§4) applied to writes.
10. **One ownership door.** Every mutating operation passes through one authorization check
    that knows who the current owner is. An act by anyone else is **recorded as a request,
    never applied**, and never silently dropped (rung 7 applied to authorization). Identity
    comes from §15.
11. **A continuous actuation authority is a dead-man level.** Outputs are held safe unless a
    *fresh* heartbeat affirmatively says otherwise. Release, expiry, and silence all take the
    same safe path. The heartbeat rides the orchestration tick (§8.8), so a stalled loop *is*
    silence. The producer **declares its own cadence**, and the consumer derives its timeout
    from it (e.g. `clamp(3 × period, min, max)`); the two sides never hard-code independent
    constants that can drift apart. Treat heartbeat-age arithmetic as safety code too: an
    unsigned age that wraps is the canonical bug.
12. **Removing a safety behaviour names its replacement.** A change that removes or weakens a
    safety behaviour (a default flip, a disabled check) states what replaces it. Until that
    replacement ships, the change stays confined behind a scenario flag or control group, never
    the default.
13. **Safety predicates are enforced at each consumer.** The *definition* is one canonical
    authority (§4); every consumer that could violate it checks it over its own inputs. Do not
    rely on a single upstream gate.

Hardware facts drive these postures (§7): when a measured constraint creates the risk
(the adapter can't listen silently ⇒ every touch disturbs the bus), the fact is recorded
next to the convention it forced.

---

## 12. Spec Governance

This document is itself a versioned, additive contract:

- **One canonical home** (its own repo). Projects vendor a *copy* at adoption and record the
  adopted version in ADR-0001; the local copy is read-only (**never edit it in-project** —
  domain facts live in the project's CLAUDE.md).
- **Semver, by §3's own rules.** Clarifications = PATCH; new sections/subsections/rules =
  MINOR; changing what an existing directive *means* = MAJOR. Section numbers never change
  meaning — new material appends (§8.8+, §11+), it never renumbers. Pre-1.0, MINOR may carry
  corrective amendments (as 0.2.0 and 0.3.0 do); this loosens at 1.0.0.
- **Every release is tagged; the changelog says what moved and which project's lessons drove
  it.**
- **The feedback loop is the point.** Each instantiation audits itself against the spec
  (what it had to build past the spec, what validated cleanly) and files a lessons document
  into `docs/lessons/` here. Spec releases absorb lessons; the next project starts further
  ahead. A project on an older spec version is fine — the spec is additive, so
  fast-forwarding is safe by construction.
- **Lessons documents are fact-checked before absorption.** Every claim cites an ADR or file,
  and a reviewer checks the citations against the source repo, including which spec version
  the project actually vendors. Corrections are recorded in the lessons doc's errata, not
  silently fixed.

---

## 13. Simulation & Rehearsal Modes

A simulator can be a test harness, or it can be **the real stack running in a simulated
world**. This section is about the second. It applies to any project whose software acts on a
physical or external world it can't freely experiment on (vehicles, plants, markets, other
people's systems).

- **Same code, different world.** In simulation mode the whole real stack runs: the same
  daemon, modules, safety gates, and operator surfaces. Only the *world* is swapped. A simulator
  joins as an ordinary producer and emits the **same wire traffic** the real sensors and buses
  would (inject it at the lowest seam you can, so the whole real path runs). Nothing downstream
  can tell the difference, and that is the point: you rehearse the real mission, then turn sim
  off.
- **The mode is exclusive, and the switch is safety-bearing.** In sim mode the live inputs are
  *ignored*, not merged. A real sensor left running beside a simulation is the canonical
  failure: two worlds blended into one wrong picture. "Is this node simulated, and which inputs
  are its own?" is a canonical authority (§4), enforced at each consumer (§11 rung 13).
- **Switching flushes.** A mode change clears every store that could carry state across the
  boundary (target tables, caches, latest-value stores), so no real datum survives into a
  simulated picture or the reverse.
- **Mode parameters are declared-cost config.** Sim levers (time warp, scenario, seed) are
  owned by the operator and go through the config registry (§6). Each declares its change cost:
  what restarts, and what re-reads it.
- **Rehearsal produces evidence.** Simulated runs are logged like real ones and feed the ledger
  (§8.11) and the replay corpus (§8.12), labelled as simulated.

---

## 14. Fleets of Homogeneous Peers

§2 and §10 describe *heterogeneous* services under one owner. A different topology appears
when **many instances of the same stack** run at once: several devices, or one real node plus
disposable simulated ones (§10, §13), that must be aware of each other.

- **Peers, not a coordinator.** Nodes discover each other off the network layer and compose
  without a central coordinator or leader election.
- **Self-description is pulled, never pushed as commands.** Each node serves its read-only card
  (§5). Every peer polls every other peer's card on its own thread, at a declared rate, and
  publishes a local membership feed. Membership is a level (§5): a peer whose card goes stale
  drops out.
- **Coordinate with shared deterministic rules.** Where peers must not conflict (who yields, who
  owns a resource), each one applies the **same deterministic rule** to the same shared
  picture and reaches the same answer alone. Rules every peer can compute alone replace
  negotiation.
- **Fleet enrichment fails toward caution.** Anything a node relaxes because a peer is "one of
  ours" is enrichment from a droppable feed (§5), and it collapses to the cautious default the
  moment membership goes stale.
- **A card is not a trust grant.** What a peer *says* about itself is data. Authority to act
  comes only from §15 and the ownership door (§11 rung 10).

---

## 15. Trust & Identity

§9 step 0.5 asks every project to state where its trust boundary is. When the answer is "the
network," turn it into mechanism:

- **The network is the identity provider.** If an authenticated overlay network is the trust
  boundary, read identity *from the transport*: resolve the peer address to a login and to an
  application identity. Do not re-authenticate in the application. Forwarded identity (a proxy
  acting for a user) is honoured only from a peer the network tags as trusted for that role.
  One function resolves identity, and the ownership door (§11 rung 10) is its consumer.
- **The certificate is the relationship.** Machine-to-machine seams outside the overlay (a LAN
  edge, a broker, an embedded board) use mutual TLS. The node runs **its own CA, one per
  node**, and signs its broker's and its devices' certificates. Pairing a device *is* issuing
  its certificate, and the broker's ACL enforces what that certificate may do.
- **Keys are born where they live.** Device keys follow §8.9's strongest form: generated in
  hardware, never exported, and enrolled by public key plus operator approval.
- **Secrets discipline still applies** (§8.9) to everything that *is* a secret: CA keys, broker
  credentials, tokens.

---

## Appendix: Why these choices

**Why monolith-first?** The polyrepo's costs (submodules, tagging ceremony, distributed
debugging) are fixed; its benefit (uncoordinated independent shipping) scales with the number
of owners. With one owner the ledger is negative. Stage 0 collects the cheap benefits —
contract discipline, module isolation — and defers the costs until a trigger proves they buy
something.

**Why polyrepo?** Version skew in a monorepo is hidden until deploy; in a polyrepo it's
visible and constant, which forces the graceful-degradation discipline that makes the system
robust. Independent release cycles also map cleanly to independent agents/owners.

**Why additive-only?** It converts the hardest coordination problem in distributed
development — "everyone upgrade together" — into a non-event. The price (contract bloat,
deprecation ceremony) is paid rarely and on a schedule you control; the benefit (no lockstep
upgrades, safe long-lived branches) is collected daily.

**Why two contract tiers?** One ecosystem-wide contract would make every internal schema
tweak a cross-team event; no shared contract would make every integration bespoke. The
inward/outward split puts the slow, ceremonial versioning only where blast radius demands
it, and lets everything else iterate at feature speed.

**Why manifest-by-superproject?** Reproducibility without centralizing development. Any
commit of `{workspace}` is a complete, checkoutable description of a tested combination —
but no service's day-to-day work happens there.

**Why a safety ladder?** Graceful degradation (§5) protects the system from version skew;
the ladder (§11) protects the *world* from the system. Both are the same idea — never let an
automated step assume authority it wasn't explicitly granted — applied in opposite
directions.
