# node-updater Inward Interface — v0.2.0

The stable contract between this module's core and its features (spec §4).
Additive rules apply: MINOR adds optional things, MAJOR is a rare planned event.

**Two versions, two meanings (spec §4 dual versioning).** `__version__` (here, `0.1.0`) is the
*inward contract* version: it bumps only when this `interface/` package changes. `VERSION` is
the module's *release* version: it bumps with features and implementation.

## Package exports (`interface/__init__.py`)

The ONLY thing a feature may import from (spec §4). It exports:

- `MODULE` (`"node-updater"`), `VERSION`, `__version__`, and the `Feature` protocol.
- **Seams:** `ReleaseSource` (`read(channel, running_commit) -> ReleaseReading`, must not
  raise) and `RunningSource` (`read() -> RunningRecord | None`).
- **The one rule:** `choose_target(channel, refs, ci, last_green)` — the channel's newest
  commit whose CI passed, the newest when that differs, and why. `release_key` orders tags;
  `RELEASE_TAG`, `COMMIT`, `CHANNELS`, `RELEASES`, `DEV`, `DEV_BRANCH`, `MAX_RELEASES_WALKED`.
  The root updater (ADR-0009 step 3) must apply this rule, not a copy of it.
- **The slot:** `CheckSlot` (one check at a time, no queue), `CheckPlan`, `CheckOutcome`,
  `ReleaseReading`, `RunningRecord`, and the inward topic `CHECK_OUTCOME` (routed only from
  the slot, never on the bus).
- **Config:** `UPDATE_CHANNEL`, `CONFIG_FIELDS`, and the `ConfigView` seam (ADR-0004).

## Changelog

- v0.2.0 — the confirmed update (ADR-0009 step 3): `UpdateRequest` (the file root reads, `REQUEST_SCHEMA`), `RootResult` (root's record), the `UpdateRequester` and `ResultSource` seams, `RESULT_READING`, and `ACTIONABLE` (root's `ops/update_helper.py` uses the same list; a test checks they agree) (additive).
- v0.1.0 — initial: the seams, `choose_target`, the slot, `update_channel` (ADR-0009 step 2).
