"""ops/update_helper.py — the root updater's careful parts, in Python (ADR-0009 D1, D3).

Run by ops/update.sh as root, from a root-owned copy (never from a tree the daemon user can
write). Ops tier: nothing imports this file. Subcommands:

  read-request --state DIR   Read and delete the daemon's request, refusing anything odd.
                             Prints request_id, channel, expected commit, requested_by and
                             door, one per line. Exit 3: no request; exit 2: refused (why on
                             stderr).
  resolve --tree T --channel C [--running COMMIT]
                             What channel C offers, by the rule and the GitHub adapter in tree
                             T (root's own tree, checked out at the commit about to be
                             installed): prints commit, ref, relation and a message line.
                             Exit 2 if it can't tell (why on stderr).
  result --dir D [--reset] KEY=VALUE...
                             Merge KEY=VALUE (VALUE as JSON if it parses, else a string) into
                             D/result.json, atomically, root 0644.
  field FILE KEY             Print one top-level field of a JSON file ("" if missing).

The request lives in the daemon user's state directory, so every path step there is opened
with O_NOFOLLOW relative to the one before (a planted symlink or FIFO is refused), the file
must be regular and small, and it is deleted whatever it says (a request is used once).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time

REQUEST_DIR = "updater"
REQUEST_NAME = "request.json"
RESULT_NAME = "result.json"
#: A request is a few hundred bytes.
MAX_REQUEST_BYTES = 4096
#: A request older than this is refused: it may predate a reboot or a long outage, and the
#: operator who confirmed it is gone. PROVISIONAL — tools/ledgers/node-updater/.
MAX_REQUEST_AGE_S = 600
CHANNELS = ("releases", "dev")
DOORS = ("local", "tailnet", "fallback")
COMMIT = re.compile(r"[0-9a-f]{40}")
REQUEST_ID = re.compile(r"[0-9a-f]{12}")
#: Relations the root updater acts on: newer, or a switch (diverged, or a commit GitHub
#: doesn't have, e.g. a developer tree's). Never "behind" or "identical".
ACTIONABLE = ("ahead", "diverged", "unknown")


class Refused(Exception):
    pass


def _printable(text: object, limit: int = 200) -> str:
    return "".join(c for c in str(text) if c.isprintable())[:limit]


def read_request(state: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        top = os.open(state, flags | os.O_DIRECTORY)
    except OSError as exc:
        raise Refused(f"can't open {state}: {exc.strerror}") from None
    try:
        try:
            sub = os.open(REQUEST_DIR, flags | os.O_DIRECTORY, dir_fd=top)
        except FileNotFoundError:
            raise FileNotFoundError from None
        except OSError as exc:
            raise Refused(f"{state}/{REQUEST_DIR} is not a plain directory ({exc.strerror})") from None
        try:
            try:
                fd = os.open(REQUEST_NAME, flags | os.O_NONBLOCK, dir_fd=sub)
            except FileNotFoundError:
                raise FileNotFoundError from None
            except OSError as exc:
                os.unlink(REQUEST_NAME, dir_fd=sub)
                raise Refused(f"the request is not a plain file ({exc.strerror}); deleted") from None
            try:
                info = os.fstat(fd)
                os.unlink(REQUEST_NAME, dir_fd=sub)        # used once, whatever it says
                if not stat.S_ISREG(info.st_mode):
                    raise Refused("the request is not a regular file; deleted")
                if info.st_size > MAX_REQUEST_BYTES:
                    raise Refused("the request is too large; deleted")
                raw = os.read(fd, MAX_REQUEST_BYTES + 1)
            finally:
                os.close(fd)
        finally:
            os.close(sub)
    finally:
        os.close(top)
    try:
        data = json.loads(raw)
    except ValueError:
        raise Refused("the request is not JSON") from None
    if not isinstance(data, dict) or not str(data.get("schema_version", "")).startswith("0."):
        raise Refused("the request has no known schema_version")
    checks = [
        (REQUEST_ID.fullmatch(str(data.get("request_id", ""))), "request_id"),
        (data.get("channel") in CHANNELS, "channel"),
        (COMMIT.fullmatch(str(data.get("expected_commit", ""))), "expected_commit"),
        (data.get("door") in DOORS, "door"),
        (isinstance(data.get("requested_at"), (int, float)), "requested_at"),
    ]
    for ok, name in checks:
        if not ok:
            raise Refused(f"the request's {name} is not valid")
    age = now - float(data["requested_at"])
    if not -60 <= age <= MAX_REQUEST_AGE_S:
        raise Refused(f"the request is {int(age)} s old; confirm the update again")
    return {"request_id": data["request_id"], "channel": data["channel"],
            "expected_commit": data["expected_commit"],
            "requested_by": _printable(data.get("requested_by", "")) or "unknown",
            "door": data["door"]}


def resolve(tree: str, channel: str, running: str | None) -> dict:
    """The channel's target and its relation to `running`, by tree's own rule and adapter."""
    if not os.path.isfile(os.path.join(tree, "modules", "node_updater", "core", "github_source.py")):
        raise Refused("that version predates the updater, so it can't be verified; not installed")
    sys.path.insert(0, tree)
    from modules.node_updater.core.github_source import GitHubReleaseSource  # noqa: E402
    reading = GitHubReleaseSource().read(channel, running if running and COMMIT.fullmatch(running) else None)
    if not reading.ok:
        raise Refused(f"couldn't check the channel: {reading.message}")
    if reading.target is None:
        raise Refused(f"the channel offers nothing now: {reading.message}")
    return {"commit": reading.target.commit, "ref": reading.target.ref,
            "relation": reading.relation or "unknown", "message": _printable(reading.message, 300)}


def write_result(directory: str, updates: dict, reset: bool) -> None:
    path = os.path.join(directory, RESULT_NAME)
    data: dict = {}
    if not reset:
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    data.update(updates)
    data["schema_version"] = "0.1.0"
    tmp = os.path.join(directory, f".{RESULT_NAME}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _value(raw: str) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="update_helper.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("read-request")
    p.add_argument("--state", required=True)
    p = sub.add_parser("resolve")
    p.add_argument("--tree", required=True)
    p.add_argument("--channel", required=True, choices=CHANNELS)
    p.add_argument("--running", default="")
    p = sub.add_parser("result")
    p.add_argument("--dir", required=True)
    p.add_argument("--reset", action="store_true")
    p.add_argument("pairs", nargs="*")
    p = sub.add_parser("field")
    p.add_argument("file")
    p.add_argument("key")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "read-request":
            try:
                req = read_request(args.state)
            except FileNotFoundError:
                return 3
            print("\n".join(req[k] for k in ("request_id", "channel", "expected_commit", "requested_by", "door")))
        elif args.cmd == "resolve":
            out = resolve(args.tree, args.channel, args.running or None)
            print("\n".join(out[k] for k in ("commit", "ref", "relation", "message")))
        elif args.cmd == "result":
            updates = {}
            for pair in args.pairs:
                key, sep, raw = pair.partition("=")
                if not sep or not re.fullmatch(r"[a-z_]+", key):
                    raise Refused(f"bad result field {pair!r}")
                updates[key] = _value(raw)
            write_result(args.dir, updates, args.reset)
        elif args.cmd == "field":
            try:
                with open(args.file) as f:
                    value = json.load(f).get(args.key, "")
            except (OSError, ValueError, AttributeError):
                value = ""
            print("" if value is None else value if isinstance(value, str) else json.dumps(value))
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
