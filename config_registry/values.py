"""Field declarations and values: what a field may hold, and turning operator text into it.

Pure functions over `ConfigField` (ADR-0004 §1). The registry calls them on every write and
on every load, so a value read back from disk passes the same checks as one an operator typed.
"""
from __future__ import annotations

import re
from typing import Union

from interfaces import ConfigField

Value = Union[str, int, bool, list[str]]

#: Bounds (§8.8, §8.11). PROVISIONAL (ADR-0004 §3; TODO.md Evidence): not yet measured.
MAX_VALUE_LEN = 256
MAX_LIST_ITEMS = 32
MAX_SECRET_LEN = 512

KINDS = ("str", "int", "bool", "str_list")
SCOPES = ("site", "node")
# Keys become file names (secrets/<module>.<name>), so their alphabet is closed.
_MODULE_RE = re.compile(r"[a-z][a-z0-9-]{0,62}")
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


class Invalid(ValueError):
    """A value a field refuses. The message names the constraint and never echoes a secret."""


def key_of(field: ConfigField) -> str:
    return f"{field.module}.{field.name}"


def check_declaration(field: ConfigField) -> None:
    """Refuse a declaration the registry can't honour. Declarations are code: this raises."""
    key = key_of(field)
    if not _MODULE_RE.fullmatch(field.module) or not _NAME_RE.fullmatch(field.name):
        raise ValueError(f"{key}: module must be kebab-case and name snake_case")
    if field.kind not in KINDS:
        raise ValueError(f"{key}: kind {field.kind!r} is not one of {', '.join(KINDS)}")
    if field.scope not in SCOPES:
        raise ValueError(f"{key}: scope {field.scope!r} is not one of {', '.join(SCOPES)}")
    if field.secret:
        if field.kind != "str":
            raise ValueError(f"{key}: a secret must be of kind 'str'")
        if field.default is not None:
            raise ValueError(f"{key}: a secret has no default")
    if field.pattern is not None:
        try:
            re.compile(field.pattern)
        except re.error as exc:
            raise ValueError(f"{key}: pattern does not compile ({exc})") from None
    if field.default is not None:
        try:
            validate(field, field.default)
        except Invalid as exc:
            raise ValueError(f"{key}: default is invalid ({exc})") from None


def parse(field: ConfigField, text: str) -> Value:
    """Operator text (an HTML form value) → a typed value. Raises Invalid."""
    if field.kind == "int":
        try:
            return int(text.strip())
        except ValueError:
            raise Invalid("expected a whole number") from None
    if field.kind == "bool":
        word = text.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        raise Invalid("expected true or false")
    if field.kind == "str_list":
        return [item for item in re.split(r"[\s,]+", text) if item]
    if field.secret:
        # §8.9: writers sanitize. CR/LF are stripped (a pasted key often carries a newline).
        return text.replace("\r", "").replace("\n", "").strip()
    return text.strip()


def validate(field: ConfigField, value: object) -> Value:
    """Check a typed value against its declaration. Raises Invalid; returns the value."""
    if field.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise Invalid("expected a whole number")
        if field.minimum is not None and value < field.minimum:
            raise Invalid(f"must be at least {field.minimum}")
        if field.maximum is not None and value > field.maximum:
            raise Invalid(f"must be at most {field.maximum}")
        return value
    if field.kind == "bool":
        if not isinstance(value, bool):
            raise Invalid("expected true or false")
        return value
    if field.kind == "str_list":
        if not isinstance(value, list):
            raise Invalid("expected a list")
        limit = min(field.max_items or MAX_LIST_ITEMS, MAX_LIST_ITEMS)
        if len(value) > limit:
            raise Invalid(f"at most {limit} items")
        for i, item in enumerate(value, 1):
            try:
                _check_str(field, item, MAX_VALUE_LEN)
            except Invalid as exc:
                raise Invalid(f"item {i}: {exc}") from None
        return list(value)
    return _check_str(field, value, MAX_SECRET_LEN if field.secret else MAX_VALUE_LEN)


def _check_str(field: ConfigField, value: object, cap: int) -> str:
    if not isinstance(value, str):
        raise Invalid("expected text")
    if not value:
        raise Invalid("must not be empty")
    limit = min(field.max_len or cap, cap)
    if len(value) > limit:
        raise Invalid(f"at most {limit} characters")
    if _CONTROL_RE.search(value):
        raise Invalid("must not contain control characters")
    if field.pattern is not None and not re.fullmatch(field.pattern, value):
        # The pattern is the declaration's, not the value: safe to show for a secret too.
        raise Invalid(f"must match {field.pattern}")
    return value
