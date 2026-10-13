"""config_registry — the one sanctioned bypass of the bus: config (spec §6, ADR-0004).

Bus-coupled tier (§4): composition-root-wired plumbing that imports no module. It holds no
bus reference, because config is not a message. Modules never import this package: each
declares its fields (`interfaces.ConfigField`) and reads them through a protocol in its own
`interface/`, which `main.py` satisfies with an `OwnerView` from here.

Secrets (§8.9) are flagged at declaration, stored as 0600 files, and never returned as a
value to anyone. The owning module gets their path; the operator surface gets set/unset.

The graduation cost (ADR-0004 D3): an extracted service can't reach this in-process store.
It takes its own store along behind the same view; non-secret edits become bus commands, and
secrets never do.
"""
from .store import ConfigRegistry, FieldState, OperatorSurface, OwnerView, WriteResult
from .values import MAX_LIST_ITEMS, MAX_SECRET_LEN, MAX_VALUE_LEN, Invalid, Value

__all__ = ["ConfigRegistry", "OwnerView", "OperatorSurface", "FieldState", "WriteResult",
           "Value", "Invalid", "MAX_VALUE_LEN", "MAX_LIST_ITEMS", "MAX_SECRET_LEN"]
