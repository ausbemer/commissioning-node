---
name: ecosystem-init
description: Bootstrap a new Additive-Contract Polyrepo project ecosystem. Trigger on "init", "/ecosystem-init", "initialize the ecosystem", "bootstrap this project", "set up the project", or when a workspace contains META-ARCHITECTURE.md but no scaffolded code. Interviews the user, then scaffolds a Stage 0 modular monolith (default) or Stage 1 polyrepo with full git wiring.
---

# Ecosystem Init

You are bootstrapping a project that follows the Additive-Contract Polyrepo
meta-architecture. Execute the phases in order. Do not skip the interview and do not
write files before the user confirms the plan in Phase 3.

## Phase 0 — Preflight & provisioning

**Provisioning policy: ask-first, auto-fix cheap things.** Auto-install pip dependencies
and set safe *local* defaults without asking. Prompt the user before anything that needs
them — a git commit identity, and any OAuth connector. Never invent a git identity and
never push to a remote without consent. Surface everything the run needs **up front here**,
not mid-scaffold.

1. Confirm `META-ARCHITECTURE.md` exists in the project root and **read it**. If it is
   missing, stop and ask the user to drop it in — do not proceed from memory. Note the
   **spec version** from its first lines (`**Spec version: X.Y.Z**`) — it goes into
   ADR-0001 and the generated CLAUDE.md. If the version line is missing, ask the user which
   version they copied; never guess it, and never edit the spec to add it.
2. **Hard requirements — verify all; block until satisfied:**
   - **Shell/Bash execution** — needed for every git, test, and script step. If it is not
     available, stop: this skill cannot scaffold without it.
   - **`git` installed AND a commit identity configured** (`git config user.name` and
     `user.email`). If either is unset, **prompt the user** for the values and set them —
     do not invent authorship.
   - **Python 3.11+, `pip`, and the packages `pydantic>=2` and `pytest`.** Missing packages
     are a cheap auto-fix: install them without asking (`pip install ...`). The interpreter
     version is handled in step 3.
3. **Python version check (default stack).** The reference code targets Python 3.11+.
   Check `python3 --version`. If it is below 3.11:
   - Do **not** silently proceed and do **not** halt outright. Surface it to the user:
     state the detected version, that the scaffold/tests are validated on 3.11+, and the
     concrete risk (e.g. `X | None` union syntax and other 3.10- gaps may need `Optional`).
   - Offer two choices: (a) proceed anyway, recording the actual interpreter as the
     documented target in `requirements.txt` + generated `CLAUDE.md` and adjusting syntax
     to the detected version so tests pass, or (b) pause while they install 3.11+.
   - Whichever they pick, the generated project must be internally consistent: the Python
     version you scaffold for is the version the tests are made to pass on.
4. Detect prior scaffolding: if `interfaces/` (or an `*-interfaces` sibling repo),
   `modules/`, or `.gitmodules` already exists, this is **not a fresh init**. Tell the
   user what you found and offer: (a) add a new module/service, (b) graduate Stage 0 →
   Stage 1 per spec §10, or (c) abort. Never overwrite an initialized project.
5. **Optional capabilities are provisioned in Phase 3**, not here — the needed set depends
   on the stage chosen in the interview. Tell the user they will be offered once the stage
   is known, so nothing surprises them mid-scaffold.

## Phase 1 — Interview

Ask in three short rounds (use the structured-question tool if available; keep each round
to at most four questions). Derive everything derivable instead of asking:
repo/package names come from the ecosystem name; initial version is always `0.1.0`;
topic constants come from module names.

**Naming rule (Python default stack) — apply consistently, do not re-invent:**
- **Human/repo/topic names are kebab-case:** the ecosystem name, service/repo names,
  and topic string constants (e.g. `pi-multitool`, `sensor-hub`, `sensor/reading`).
- **Python package/module directories are snake_case:** a kebab name maps to its package
  dir by replacing hyphens with underscores (`pi-multitool` -> importable as needed,
  module dir `sensor-hub` -> `modules/sensor_hub/`). Kebab-case is NOT a valid Python
  identifier, so the on-disk package dir and every `import` use the snake_case form.
- Keep the kebab name as the display/repo name in generated CLAUDE.md tables; use the
  snake_case form for directory names, `__init__.py` packages, and imports.
- Topic **string values** stay kebab/slash (`"sensor/reading"`); only the Python
  **identifiers** that hold them are snake_case (`SENSOR_READING = "sensor/reading"`).
- For non-Python stacks, apply that language's package-naming convention analogously and
  note the mapping in the generated CLAUDE.md.

**Round 1 — shape:**
1. Ecosystem name (short, kebab-case) and a one-sentence domain description.
2. How many *independent* owners/agents will ship code **from day one**?
   (1 → Stage 0 modular monolith, the default; 2+ → offer Stage 1 polyrepo, but note
   the spec's advice that Stage 0 is right unless a trigger already holds.)
3. Stack: default **Python 3.11+ / Pydantic v2**. **Machine-enforced additivity ships
   for the Python/Pydantic stack only** (`check_additivity.py`). If the user wants another
   stack, be explicit: the load-bearing additive-rule check (§8.1) would NOT be enforced
   there — it becomes manual discipline until a stack-specific checker is written. Steer a
   first project toward Python unless they knowingly accept that gap; record the choice and
   the missing enforcement in the generated `CLAUDE.md` and `TODO.md`.
4. Eventual message broker preference (MQTT / Redis / NATS / Kafka / undecided).
   Stage 0 always starts with the in-process bus; this answer only shapes naming and
   the adapter notes.

**Round 2 — content (informed by Round 1):**
1. First modules/services: 2–4 names, one line of responsibility each.
2. First shared message types: what data actually crosses module boundaries on day one
   (names + a few fields each). If the user is unsure, propose 1–2 minimal
   envelope-worthy types from their domain description and confirm.
3. Anything that must NOT be built yet (explicit non-goals go in TODO.md).
4. (Stage 1 only) Git host org/user for remote URLs; remotes may also be added later.

**Round 3 — posture & risk (spec §9 step 0.5, §11, §13–§15).** Short; each answer can be
"not yet". These shape ADR-0001 and which evidence scaffolding exists. They don't add
modules.
1. **Trust boundary:** where is it? (e.g. "an overlay network is the boundary — identity
   read from the transport", per-service auth, "local-only, no network surface yet"). An
   undecided posture becomes a standing TODO in every later ADR, so press for an answer.
2. **Does the software act on the world?** Does it write to hardware, shared infrastructure,
   or other people's systems, now or soon? Yes → the §11 ladder applies, ADRs use the
   Safety section, and the evidence scaffold (`tools/ledgers/`, `tests/replay/`) is created.
3. **Foreign runtimes / vendor pins:** any third-party runtime or library whose behaviour
   you depend on (an autonomy framework, a device SDK, a media server)? Yes →
   `docs/quirks.md` is created, and the dependency is contained behind one bridge module
   (§6).
4. **Anticipated later (optional):** a simulation/rehearsal mode (§13), many peer nodes
   running this same stack (§14), or devices to enrol with keys (§15)? Record the answers in
   ADR-0001 "Risk profile" only. Do not scaffold them.

## Phase 2 — Read the spec sections you are about to instantiate

Re-read spec §3 (contract, incl. typed nested shapes), §4 (structure + ontology +
canonical authorities), §5 (capabilities, levels not edges), §8 (standing rules), and §9/§10
(bootstrap / growth path) so the scaffold matches the spec, not your prior. If Round 3 said
the software acts on the world, also re-read §11; if it anticipated sim/fleet/identity, skim
§13–§15 for ADR-0001's Risk profile. The spec is authoritative
over this skill if they ever disagree.

## Phase 3 — Confirm the plan

Print a compact plan: chosen stage, directory tree, first shared types, git actions
(branches, tags), and what is deliberately deferred. Get an explicit yes. Adjust on
feedback before writing anything.

**Also provision optional capabilities for the CHOSEN stage** (ask-first policy from
Phase 0). Enumerate what applies, offer to connect each, and record declines in TODO.md —
a missing optional connector never blocks the scaffold:

- **Stage 0:** none required — the monolith is local-only. Say so explicitly; do **not**
  suggest remote connectors at this stage.
- **Stage 1 (offer, tied to need):**
  - **GitHub** (or the user's git host) — remotes, PRs, pushing tags, submodule URLs.
    Suggest the connector via the registry if not connected; until then use relative/local
    submodule URLs and note the repoint step in WORKFLOW.md.
  - **A status board — Linear / Asana / Notion** — the home for live pin/branch/drift
    state, which spec §7 says must NOT be hand-maintained in docs. Offer to connect one.
  - **Datadog / PagerDuty** — runtime observability incl. schema-version-on-the-wire
    monitoring (spec §8.5). Later-stage: offer, but mark deferable until services run.

## Phase 4 — Scaffold

### Stage 0 (default): modular monolith, one repo

```
{ecosystem}/
├── CLAUDE.md                    # from templates/stage0-CLAUDE.md.template
├── META-ARCHITECTURE.md         # already present — never edit it
├── WORKFLOW.md                  # from templates/WORKFLOW.md.template
├── TODO.md                      # non-goals + next steps from the interview
├── docs/adr/0001-adopt-meta-architecture.md   # from templates/adr-0001-adopt.md.template
├── docs/quirks.md               # ONLY if Round 3 named a foreign runtime/vendor pin
├── interfaces/                  # future {ecosystem}-interfaces, a package from day one
│   ├── __init__.py              # __version__ = "0.1.0"; exports all public types
│   ├── envelope.py              # Shape (nested) + Envelope (schema_version), extra="ignore"
│   ├── models.py                # first shared types from the interview
│   └── topics.py                # topic name constants
├── bus/
│   └── __init__.py              # in-process dispatcher — adapt scripts/bus.py
├── modules/
│   └── {module}/                # exact spec §4 layout, one per interview module
│       ├── interface/           #   __init__.py: VERSION = "0.1.0" (release) +
│       │                        #   __version__ = "0.1.0" (inward contract) + MODULE
│       │                        #   + exports models/protocol; models.py;
│       │                        #   feature.py (protocol); README.md (template)
│       ├── core/                #   registry.py, input.py, output.py — I/O only
│       └── features/            #   one working example feature per module
├── tools/
│   ├── check_additivity.py      # copy of scripts/check_additivity.py
│   ├── check_boundaries.py      # copy of scripts/check_boundaries.py (§4 ontology)
│   ├── check_pins.py            # copy of scripts/check_pins.py (no-op at Stage 0)
│   └── ledgers/README.md        # ONLY if Round 3 said it acts on the world
├── tests/
│   ├── test_additivity.py       # runs the checker against the committed snapshot
│   ├── test_boundaries.py       # copy of scripts/test_boundaries.py (planted violations)
│   ├── replay/                  # ONLY if it acts on the world (§8.12) — empty corpus + README pointer
│   └── test_smoke.py            # end-to-end: modules exchange one message via bus
├── main.py                      # composes modules onto the bus
└── requirements.txt
```

Scaffolding rules:
- Every model everywhere: tolerant of unknown fields (`extra="ignore"`), and every
  published payload wraps in the envelope with `schema_version`.
- **Nested shapes are models, from the first field** (spec §3). If an interview type has
  a list or mapping of structured entries, give the entry its own model subclassing
  `Shape` (from `envelope.py`). Never use a bare `dict` / `list[dict]` / `Any`. The
  additivity checker fails on undeclared untyped fields. Do not create
  `schemas/blind_spots.txt` at init: a fresh scaffold has no excuse for a blind spot.
- **Dual versioning** (spec §4): each module's `interface/__init__.py` declares both
  `VERSION = "0.1.0"` (release) and `__version__ = "0.1.0"` (inward contract), plus
  `MODULE`. Fill `{RELEASE_VERSION}` and `{INWARD_VERSION}` in the templates.
- **The §4 ontology is declared in `tools/check_boundaries.py`.** At init the tiers are
  `interfaces/`, `bus/`, `modules/`, and the root `main.py`. Leave `NEUTRAL` and
  `BUS_COUPLED` empty unless the interview produced a shared library (e.g. a decoder for
  an open format, or one canonical computation several modules need). Such a library is a
  top-level neutral package, named for what it does, declared in `NEUTRAL`. Do not create
  `ops/` at init; it appears with the first installer or doctor script (spec §10), and the
  checker already skips it and forbids importing it. Fill the "§4 tiers in this repo" line of
  the root CLAUDE.md to match.
- Cross-module types live ONLY in `interfaces/`. Module-internal types live in that
  module's `interface/`. If you find yourself putting a type anywhere else, stop.
- The **module name constant `MODULE`** (the module's kebab-case name, e.g.
  `MODULE = "sensor-hub"`) lives in that module's `interface/__init__.py`, NOT in
  `core/`. Features and core both import it from `interface`. Rationale: features may
  never import `core` internals (spec §4), yet they need the module identity for
  capability flags and logging — so it belongs in the inward interface package, which is
  the one thing features are allowed to import. Any example import in the scaffold must
  read `from ..interface import MODULE` (or the module's snake_case package path), never
  `from ..core...`.
- Cores contain zero business logic. Each example feature should be trivial but real —
  the smoke test must pass by actually routing a message.
- Fill every template placeholder; leave no `{LIKE_THIS}` tokens in output.
- Copy the tested reference files from `scripts/` rather than re-writing them:
  `envelope.py` -> `interfaces/envelope.py`; `feature.py` -> each module's
  `interface/feature.py`; `bus.py` -> `bus/`; `check_additivity.py` -> `tools/`;
  `check_pins.py` -> `tools/` (a no-op at Stage 0, wired for Stage 1);
  `check_boundaries.py` -> `tools/`; `test_boundaries.py` -> `tests/`. Instantiate
  `gitignore.template` -> `.gitignore`, `adr-0001-adopt.md.template` ->
  `docs/adr/0001-adopt-meta-architecture.md` (spec version, stage, Round 3 answers), and
  `adr.md.template` for later `docs/adr/` entries. If Round 3 said the software acts on the
  world, instantiate `ledgers-README.md.template` -> `tools/ledgers/README.md` and create
  `tests/replay/` with a README pointing to it. If Round 3 named a foreign runtime/vendor
  pin, instantiate `quirks.md.template` -> `docs/quirks.md` with one row per dependency and
  no invented entries. Otherwise create none of these: delete the `{EVIDENCE_ROWS}` line
  and leave the WORKFLOW.md guidance to create them later.
- Install the detached-HEAD guard: copy `scripts/pre-commit` to `.git/hooks/pre-commit`
  and `chmod +x` it (Stage 1: also into each submodule's hooks dir). See WORKFLOW.md.
- Install CI: copy `scripts/ci.sh` to the project root (`chmod +x`) and instantiate
  `ci.yml.template` -> `.github/workflows/ci.yml`. `ci.sh` runs tests + additivity check
  + pin check; it is the same gate locally and in CI. It does NOT gate release tags —
  that stays in `release-contract.sh` (deliberate: no belt-and-suspenders).
- Template conditionals like `{STAGE0: "..." | STAGE1: "..."}` and inline directives like
  `{STAGE1_EXTRA_RULES: ...}` are **interpret-and-resolve, not mechanical substitution.**
  Pick the branch that matches the chosen stage, write natural prose for it, and delete the
  conditional syntax and the non-chosen branch entirely. The output must read as if written
  for this project — no `{...}` markers, pipes, or branch labels survive.
- Generate the additivity baseline: `python tools/check_additivity.py snapshot`,
  commit the resulting `schemas/` snapshot.

Post-init cleanup of kit files (both stages): the pre-init `CLAUDE.md` is **replaced** by
the generated project `CLAUDE.md` (Stage 0 root or Stage 1 workspace template). **Delete
`KIT-README.md`** — its instructions are about seeding a project, which is now done; keeping
it around confuses future readers. The `.claude/skills/ecosystem-init/` directory **stays**
(re-running init offers add-module / graduate flows). `META-ARCHITECTURE.md` stays and is
never edited.

Git wiring (Stage 0): `git init` (if needed) -> initial commit on `master` -> create
`dev` from it. Day-to-day work happens on `dev`; `master` receives promotions.

### Stage 1: polyrepo (only when Round 1 says multiple independent owners)

Follow spec §9 exactly. Summary: sibling repos `{workspace}`, `{ecosystem}-interfaces`,
one per service, each with `master`+`dev`; interfaces tagged `v0.1.0` (the tag IS the
release); services pin `{ecosystem}-interfaces @ v0.1.0` in requirements; workspace adds
each repo as a submodule with `branch = dev` in dev's `.gitmodules`, pins committed.
Install `scripts/promote.sh` and `scripts/release-contract.sh` into the workspace and
interfaces repos respectively, `scripts/check_pins.py` as the pin-references-a-real-tag CI
gate (§2/§8.7), and `scripts/pre-commit` into every submodule's hooks (detached-HEAD guard).
The contract repo gets `tools/check_additivity.py`. Each service repo gets `ci.sh`.
`check_boundaries.py` is written for the Stage 0 `modules/{m}/` layout. A service repo has
`interface/ core/ features/` at its root, so adapt it there: treat the repo root as one
module, then keep the features↛core, core↛features and features↛features rules plus the
syntax-error rule, and keep its planted-violation tests passing. If you can't adapt it in
this run, record the gap in that service's TODO.md (§8.1: an unchecked rule is a promise,
not a rule). ADR-0001 lives in the workspace and records the spec version. Use relative submodule URLs (`../{repo}`) so everything
works locally; note in WORKFLOW.md that they must be repointed when remotes exist.
Defer `{deploy}` — it is created when two services can actually run together, not at
init.

## Phase 5 — Verify, then report

1. Run `./ci.sh` (tests incl. smoke, additivity, and boundaries with planted violations;
   then the additivity, boundary and pin checks). Everything must pass. A boundary failure
   at init means the scaffold put code in an undeclared tier: fix the scaffold, never the
   checker.
2. Show `git log --oneline --all` and branch list — confirm master/dev (and tag, Stage 1).
3. Report: what was created, what was deliberately deferred (per spec §10 table), and
   the three next steps: read WORKFLOW.md, start the first real feature (branch from
   dev), and schedule nothing else — growth is trigger-driven.

## Hard rules during init

- Domain content goes in generated CLAUDE.md/TODO.md files only — never into
  META-ARCHITECTURE.md and never invented beyond what the interview gave you.
- `META-ARCHITECTURE.md` is vendored read-only: never edit it. Its version line stays,
  and ADR-0001 records the same version.
- If the user's answers imply a spec violation (e.g. "just let modules import each
  other"), flag it, cite the spec section, and ask how they want to proceed.
- Prefer boring, obviously-correct scaffold code over clever code. The scaffold is a
  teaching artifact; someone's first feature will be written by imitating it.
