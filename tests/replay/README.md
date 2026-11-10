# Replay corpus

Recorded episodes re-run against the live code (spec §8.12). It's empty at init: no
behavioural or safety logic has met the field yet. The format, the ledgers that go with it,
and the gating rules are in `tools/ledgers/README.md`.

**nav-relay (ADR-0010):** a `candump -l` log from a real NMEA 2000 bus goes in
`tests/replay/nav-relay/` once one is recorded, with positions shifted to a made-up place
(site data). Its test feeds every frame through `n2k.decode` and `current_fix` and pins the
sentences that come out. Until then, `tests/test_nav_relay.py` builds frames from the standard's
layouts.
