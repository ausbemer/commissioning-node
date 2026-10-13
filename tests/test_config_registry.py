"""config_registry (ADR-0004): declarations, values, scopes, secrets and the on-disk store.

Each test builds a registry over a temp state directory, the way the daemon does over
/var/lib/commissioning-node. "Restart" means building a second registry over the same root.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat

import pytest

import config_registry.store as store
from config_registry import ConfigRegistry
from interfaces import ConfigField

GW = "tailnet-gateway"
HOSTNAME = ConfigField(module=GW, name="hostname", kind="str", label="Tailscale hostname",
                       pattern=r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
TAGS = ConfigField(module=GW, name="tags", kind="str_list", pattern=r"tag:[a-z0-9-]+",
                   max_items=4, default=[])
AUTH_KEY = ConfigField(module=GW, name="auth_key", kind="str", secret=True,
                       label="Tailscale auth key", pattern=r"tskey-[A-Za-z0-9-]+")
RETRIES = ConfigField(module="camera-config", name="retries", kind="int", scope="node",
                      default=3, minimum=0, maximum=10)
VERBOSE = ConfigField(module="camera-config", name="verbose", kind="bool", default=False)
FIELDS = [HOSTNAME, TAGS, AUTH_KEY, RETRIES, VERBOSE]
KEY = "tskey-auth-k123-ABCdef"


def make(root, fields=FIELDS, **kw) -> ConfigRegistry:
    return ConfigRegistry(root, fields, **kw)


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# -- declarations are code: a bad one refuses to build ------------------------------------

@pytest.mark.parametrize("field, why", [
    (ConfigField(module=GW, name="x", kind="float"), "kind"),
    (ConfigField(module=GW, name="x", kind="str", scope="fleet"), "scope"),
    (ConfigField(module=GW, name="x", kind="str", secret=True, default="tskey-1"), "secret"),
    (ConfigField(module=GW, name="x", kind="int", secret=True), "secret"),
    (ConfigField(module=GW, name="../../etc/x", kind="str"), "snake_case"),
    (ConfigField(module="Tailnet/GW", name="x", kind="str"), "kebab-case"),
    (ConfigField(module=GW, name="x", kind="str", pattern="("), "pattern"),
    (ConfigField(module=GW, name="x", kind="int", default=99, maximum=10), "default"),
])
def test_bad_declarations_refuse_to_build(tmp_path, field, why) -> None:
    with pytest.raises(ValueError, match=why):
        make(tmp_path, [field])


def test_duplicate_declaration_refuses(tmp_path) -> None:
    with pytest.raises(ValueError, match="declared twice"):
        make(tmp_path, [HOSTNAME, HOSTNAME])


# -- non-secret values ---------------------------------------------------------------------

def test_unset_reads_the_default_and_nothing_is_written(tmp_path) -> None:
    reg = make(tmp_path)
    view = reg.owner_view("camera-config")
    assert view.get("retries") == 3 and not view.is_set("retries")
    assert reg.owner_view(GW).get("hostname") is None
    assert list(tmp_path.iterdir()) == []


def test_set_persists_across_a_restart_in_its_scope(tmp_path) -> None:
    surface = make(tmp_path).operator_surface()
    assert surface.set(f"{GW}.hostname", "  site-a-node ", "alice").ok
    assert surface.set(f"{GW}.tags", "tag:ops, tag:cam\ntag:x", "alice").ok
    assert surface.set("camera-config.retries", "5", "alice").ok
    assert surface.set("camera-config.verbose", "on", "alice").ok

    view = make(tmp_path).owner_view(GW)
    assert view.get("hostname") == "site-a-node" and view.is_set("hostname")
    assert view.get("tags") == ["tag:ops", "tag:cam", "tag:x"]
    assert make(tmp_path).owner_view("camera-config").get("retries") == 5
    site = json.loads((tmp_path / "site" / "config.json").read_text())
    node = json.loads((tmp_path / "node" / "config.json").read_text())
    assert site["schema_version"] == 1 and "camera-config.retries" not in site["values"]
    assert node["values"] == {"camera-config.retries": 5}
    assert mode(tmp_path / "site") == 0o700 and mode(tmp_path / "site" / "config.json") == 0o600


def test_clear_restores_the_default(tmp_path) -> None:
    surface = make(tmp_path).operator_surface()
    surface.set("camera-config.retries", "7", "alice")
    assert surface.clear("camera-config.retries", "alice").ok
    assert surface.state("camera-config.retries").value == 3
    assert not make(tmp_path).owner_view("camera-config").is_set("retries")


@pytest.mark.parametrize("key, text, why", [
    (f"{GW}.hostname", "Not_A_Label", "must match"),
    (f"{GW}.hostname", "", "must not be empty"),
    (f"{GW}.hostname", "a" * 300, "at most 256 characters"),
    (f"{GW}.hostname", "a\x00b", "control characters"),
    (f"{GW}.tags", "tag:a tag:b tag:c tag:d tag:e", "at most 4 items"),
    (f"{GW}.tags", "tag:a ops", "item 2: must match"),
    ("camera-config.retries", "eleven", "whole number"),
    ("camera-config.retries", "11", "at most 10"),
    ("camera-config.retries", "-1", "at least 0"),
    ("camera-config.verbose", "maybe", "true or false"),
    ("nobody.nothing", "x", "no such config field"),
])
def test_bad_values_are_refused_in_band_and_not_stored(tmp_path, key, text, why) -> None:
    reg = make(tmp_path)
    result = reg.operator_surface().set(key, text, "alice")
    assert not result.ok and why in result.message
    assert not (tmp_path / "site" / "config.json").exists()


def test_owner_view_sees_only_its_own_module(tmp_path) -> None:
    view = make(tmp_path).owner_view("camera-config")
    with pytest.raises(KeyError):
        view.get("hostname")
    with pytest.raises(KeyError):
        view.secret_path("auth_key")


def test_lists_are_copies(tmp_path) -> None:
    reg = make(tmp_path)
    reg.operator_surface().set(f"{GW}.tags", "tag:a", "alice")
    reg.owner_view(GW).get("tags").append("tag:evil")
    assert reg.owner_view(GW).get("tags") == ["tag:a"]


def test_change_log_records_who_old_and_new(tmp_path, caplog) -> None:
    surface = make(tmp_path).operator_surface()
    with caplog.at_level(logging.INFO, logger="config_registry"):
        surface.set(f"{GW}.hostname", "node-a", "alice@example.com via tailnet")
        surface.set(f"{GW}.hostname", "node-b", "admin@fallback")
    assert "'node-a' -> 'node-b'" in caplog.text
    assert "admin@fallback" in caplog.text


# -- secrets (§8.9) ------------------------------------------------------------------------

def test_secret_is_a_0600_file_never_a_value(tmp_path, caplog) -> None:
    reg = make(tmp_path)
    surface = reg.operator_surface()
    with caplog.at_level(logging.DEBUG, logger="config_registry"):
        result = surface.set(f"{GW}.auth_key", f"  {KEY}\r\n", "alice")
    assert result.ok and KEY not in result.message
    path = tmp_path / "site" / "secrets" / f"{GW}.auth_key"
    assert path.read_text() == KEY and mode(path) == 0o600
    assert mode(path.parent) == 0o700
    assert KEY not in caplog.text and "<secret set>" in caplog.text
    state = surface.state(f"{GW}.auth_key")
    assert state.value is None and state.is_set
    view = reg.owner_view(GW)
    assert view.secret_path("auth_key") == str(path)
    with pytest.raises(KeyError, match="secret"):
        view.get("auth_key")
    assert not (tmp_path / "site" / "config.json").exists()    # secrets never go in it


def test_secret_refusal_never_echoes_the_value(tmp_path) -> None:
    result = make(tmp_path).operator_surface().set(f"{GW}.auth_key", "hunter2-secret", "alice")
    assert not result.ok and "hunter2" not in result.message
    assert not (tmp_path / "site" / "secrets").exists()


def test_owner_clears_its_secret(tmp_path, caplog) -> None:
    reg = make(tmp_path)
    reg.operator_surface().set(f"{GW}.auth_key", KEY, "alice")
    view = reg.owner_view(GW)
    with caplog.at_level(logging.INFO, logger="config_registry"):
        assert view.clear_secret("auth_key").ok
    assert view.secret_path("auth_key") is None and not view.is_set("auth_key")
    assert not (tmp_path / "site" / "secrets" / f"{GW}.auth_key").exists()
    assert "<secret cleared>" in caplog.text and "tailnet-gateway" in caplog.text
    assert view.clear_secret("auth_key").ok          # already gone: still fine


@pytest.mark.parametrize("spoil", ["chmod", "symlink", "owner"])
def test_unsafe_secret_file_counts_as_unset_and_warns_once(tmp_path, caplog, spoil) -> None:
    reg = make(tmp_path)
    reg.operator_surface().set(f"{GW}.auth_key", KEY, "alice")
    path = tmp_path / "site" / "secrets" / f"{GW}.auth_key"
    if spoil == "chmod":
        path.chmod(0o644)
    elif spoil == "symlink":
        real = tmp_path / "elsewhere"
        real.write_text(KEY)
        real.chmod(0o600)
        path.unlink()
        path.symlink_to(real)
    else:
        reg = make(tmp_path, uid=os.geteuid() + 1)
    view = reg.owner_view(GW)
    with caplog.at_level(logging.WARNING, logger="config_registry"):
        for _ in range(3):                               # one warning per state, not per tick
            assert view.secret_path("auth_key") is None
            assert not view.is_set("auth_key")
    assert caplog.text.count("treating it as unset") == 1


# -- loading never crashes boot (§4) -------------------------------------------------------

def write_site(root, text: str) -> None:
    (root / "site").mkdir(mode=0o700)
    (root / "site" / "config.json").write_text(text)


@pytest.mark.parametrize("text", [
    "{not json",
    json.dumps({"schema_version": 2, "values": {f"{GW}.hostname": "newer"}}),
    json.dumps({"schema_version": 1, "values": ["wrong"]}),
    json.dumps(["wrong"]),
    "",
])
def test_malformed_file_falls_back_and_is_set_aside_on_the_next_write(tmp_path, text) -> None:
    write_site(tmp_path, text)
    original = (tmp_path / "site" / "config.json").read_bytes()
    reg = make(tmp_path)
    assert reg.owner_view(GW).get("hostname") is None
    assert (tmp_path / "site" / "config.json").read_bytes() == original   # untouched on load
    assert reg.operator_surface().set(f"{GW}.hostname", "node-a", "alice").ok
    aside = list((tmp_path / "site").glob("config.json.malformed-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == original


def test_oversized_file_is_not_decoded(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "MAX_FILE_BYTES", 64)
    write_site(tmp_path, json.dumps({"schema_version": 1,
                                     "values": {f"{GW}.hostname": "a" * 100}}))
    assert make(tmp_path).owner_view(GW).get("hostname") is None


def test_bad_unknown_and_misplaced_entries_are_kept_on_disk(tmp_path) -> None:
    write_site(tmp_path, json.dumps({"schema_version": 1, "values": {
        f"{GW}.hostname": "Bad_Label",               # fails its pattern → default
        "future-module.thing": {"x": 1},             # a newer build's field → kept
        "camera-config.retries": 4,                  # node-scoped, found in site → ignored
        f"{GW}.auth_key": KEY,                       # a secret never lives here → ignored
    }}))
    reg = make(tmp_path)
    assert reg.owner_view(GW).get("hostname") is None
    assert reg.owner_view("camera-config").get("retries") == 3
    assert reg.owner_view(GW).secret_path("auth_key") is None
    reg.operator_surface().set(f"{GW}.tags", "tag:a", "alice")
    values = json.loads((tmp_path / "site" / "config.json").read_text())["values"]
    assert values["future-module.thing"] == {"x": 1}
    assert values[f"{GW}.hostname"] == "Bad_Label"
    assert values[f"{GW}.tags"] == ["tag:a"]


# -- atomic writes and teardown ------------------------------------------------------------

def test_a_failed_write_changes_nothing(tmp_path, monkeypatch) -> None:
    reg = make(tmp_path)
    surface = reg.operator_surface()
    surface.set(f"{GW}.hostname", "node-a", "alice")

    def refuse(*_):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store.os, "replace", refuse)
    result = surface.set(f"{GW}.hostname", "node-b", "alice")
    assert not result.ok and "No space left" in result.message
    assert reg.owner_view(GW).get("hostname") == "node-a"
    assert sorted(p.name for p in (tmp_path / "site").iterdir()) == ["config.json"]


def test_teardown_is_deleting_site(tmp_path) -> None:
    surface = make(tmp_path).operator_surface()
    surface.set(f"{GW}.hostname", "node-a", "alice")
    surface.set(f"{GW}.auth_key", KEY, "alice")
    surface.set("camera-config.retries", "6", "alice")
    shutil.rmtree(tmp_path / "site")
    reg = make(tmp_path)
    assert reg.owner_view(GW).get("hostname") is None
    assert reg.owner_view(GW).secret_path("auth_key") is None
    assert reg.owner_view("camera-config").get("retries") == 6


# -- review 2026-10-13 ---------------------------------------------------------------------

def test_an_unreadable_file_refuses_writes_instead_of_wiping_it(tmp_path) -> None:
    write_site(tmp_path, json.dumps({"schema_version": 1, "values": {f"{GW}.tags": ["tag:a"],
                                                                      "future.x": 1}}))
    path = tmp_path / "site" / "config.json"
    original = path.read_bytes()
    path.chmod(0o000)
    try:
        result = make(tmp_path).operator_surface().set(f"{GW}.hostname", "gw", "alice")
        assert not result.ok and "unreadable" in result.message
    finally:
        path.chmod(0o600)
    assert path.read_bytes() == original


def test_a_large_malformed_file_is_set_aside_whole(tmp_path) -> None:
    write_site(tmp_path, "{" + "x" * 70_000)
    original = (tmp_path / "site" / "config.json").read_bytes()
    make(tmp_path).operator_surface().set(f"{GW}.hostname", "gw", "alice")
    aside = list((tmp_path / "site").glob("config.json.malformed-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == original


def test_directories_are_private_whatever_the_umask(tmp_path) -> None:
    old = os.umask(0o022)
    try:
        root = tmp_path / "fresh" / "state"
        make(root).operator_surface().set(f"{GW}.auth_key", KEY, "alice")
    finally:
        os.umask(old)
    for d in (root, root / "site", root / "site" / "secrets"):
        assert mode(d) == 0o700, d


def test_an_oversized_secret_file_is_unset(tmp_path) -> None:
    reg = make(tmp_path)
    reg.operator_surface().set(f"{GW}.auth_key", KEY, "alice")
    path = tmp_path / "site" / "secrets" / f"{GW}.auth_key"
    path.write_bytes(b"x" * 10_000_000)
    assert reg.owner_view(GW).secret_path("auth_key") is None


def test_leftover_temp_files_are_swept_at_load(tmp_path, caplog) -> None:
    make(tmp_path).operator_surface().set(f"{GW}.auth_key", KEY, "alice")
    leftover = tmp_path / "site" / "secrets" / f".{GW}.auth_key.abc123"
    leftover.write_text(KEY)
    with caplog.at_level(logging.WARNING, logger="config_registry"):
        make(tmp_path)
    assert not leftover.exists() and "leftover temp file" in caplog.text
    assert (tmp_path / "site" / "secrets" / f"{GW}.auth_key").exists()


# -- review round 2, 2026-10-13 ------------------------------------------------------------

def test_a_malformed_file_deleted_by_hand_unblocks_writes(tmp_path) -> None:
    write_site(tmp_path, "{not json")
    reg = make(tmp_path)
    (tmp_path / "site" / "config.json").unlink()                  # the operator's fix
    assert reg.operator_surface().set(f"{GW}.hostname", "gw", "alice").ok
    assert reg.operator_surface().set(f"{GW}.tags", "tag:a", "alice").ok
    assert not list((tmp_path / "site").glob("config.json.malformed-*"))


def test_a_malformed_file_fixed_by_hand_is_built_on(tmp_path) -> None:
    write_site(tmp_path, "{not json")
    reg = make(tmp_path)
    (tmp_path / "site" / "config.json").write_text(json.dumps(
        {"schema_version": 1, "values": {f"{GW}.tags": ["tag:fixed"]}}))
    assert reg.operator_surface().set(f"{GW}.hostname", "gw", "alice").ok
    values = json.loads((tmp_path / "site" / "config.json").read_text())["values"]
    assert values == {f"{GW}.tags": ["tag:fixed"], f"{GW}.hostname": "gw"}
    assert reg.owner_view(GW).get("tags") == ["tag:fixed"]
    assert not list((tmp_path / "site").glob("config.json.malformed-*"))


def test_the_sweep_leaves_files_that_are_not_its_own(tmp_path) -> None:
    make(tmp_path).operator_surface().set(f"{GW}.auth_key", KEY, "alice")
    keep = [tmp_path / "site" / ".keep", tmp_path / "site" / "secrets" / ".notes",
            tmp_path / "site" / ".tailnet-gateway.join.json.x1"]   # another owner's temp
    for path in keep:
        path.write_text("x")
    ours = tmp_path / "site" / ".config.json.abc"
    ours.write_text("{")
    make(tmp_path)
    assert all(p.exists() for p in keep) and not ours.exists()
