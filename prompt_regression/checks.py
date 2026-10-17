"""The checks a case can make on a model's output. All of them are deterministic.

There is no model-as-judge among them on purpose: a judge has the same failure modes the suite is there to
catch. When you do want one, ``cmd`` hands the output to a command of yours and uses its exit code.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any

JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}
FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\n(.*?)\n?```\s*$", re.DOTALL)
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")
_MISSING = object()


class CheckError(Exception):
    """A check is written wrongly (unknown type, missing or unknown parameter, bad value)."""


@dataclass(frozen=True)
class Outcome:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Context:
    variables: dict[str, Any]
    case_id: str = ""


def _flags(text: str) -> int:
    table = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
    bad = [c for c in text if c not in table]
    if bad:
        raise CheckError(f"unknown regex flag {bad[0]!r}; use i, m or s")
    out = 0
    for c in text:
        out |= table[c]
    return out


def _short(text: str, n: int = 60) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= n else text[: n - 1] + "…"


def _json(output: str, fences: bool) -> Any:
    text = output.strip()
    if fences:
        m = FENCE.match(text)
        if m:
            text = m.group(1)
    return json.loads(text)


def _dig(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _has_type(value: Any, wanted: str) -> bool:
    got = _type_of(value)
    return got == wanted or (wanted == "number" and got == "integer")


def _norm(text: str, ignore_case: bool) -> str:
    return text.lower() if ignore_case else text


def contains(out, c, value, ignore_case=False):  # noqa: ANN001
    ok = _norm(value, ignore_case) in _norm(out, ignore_case)
    return Outcome(ok, "" if ok else f"{_short(value)!r} is not in the output")


def not_contains(out, c, value, ignore_case=False):  # noqa: ANN001
    at = _norm(out, ignore_case).find(_norm(value, ignore_case))
    return Outcome(at < 0, "" if at < 0 else f"{_short(value)!r} is in the output (at character {at})")


def equals(out, c, value, strip=True, ignore_case=False):  # noqa: ANN001
    got = out.strip() if strip else out
    ok = _norm(got, ignore_case) == _norm(value, ignore_case)
    return Outcome(ok, "" if ok else f"the output is {_short(got)!r}, not {_short(value)!r}")


def starts_with(out, c, value, ignore_case=False):  # noqa: ANN001
    ok = _norm(out.lstrip(), ignore_case).startswith(_norm(value, ignore_case))
    return Outcome(
        ok, "" if ok else f"the output starts with {_short(out.lstrip(), 30)!r}, not {_short(value, 30)!r}"
    )


def matches(out, c, pattern, flags=""):  # noqa: ANN001
    ok = re.search(pattern, out, _flags(flags)) is not None
    return Outcome(ok, "" if ok else f"nothing matches /{_short(pattern)}/")


def not_matches(out, c, pattern, flags=""):  # noqa: ANN001
    m = re.search(pattern, out, _flags(flags))
    return Outcome(m is None, "" if m is None else f"/{_short(pattern)}/ matches {_short(m.group(0), 30)!r}")


def one_of(out, c, values, ignore_case=False, strip=True):  # noqa: ANN001
    got = out.strip() if strip else out
    ok = _norm(got, ignore_case) in {_norm(v, ignore_case) for v in values}
    return Outcome(ok, "" if ok else f"{_short(got)!r} is none of {', '.join(repr(v) for v in values)}")


def not_empty(out, c):  # noqa: ANN001
    return Outcome(bool(out.strip()), "" if out.strip() else "the output is empty")


def json_valid(out, c, allow_fences=False):  # noqa: ANN001
    try:
        _json(out, allow_fences)
    except ValueError as exc:
        return Outcome(False, f"not JSON: {exc}")
    return Outcome(True)


def json_has(out, c, keys, allow_fences=False):  # noqa: ANN001
    try:
        data = _json(out, allow_fences)
    except ValueError as exc:
        return Outcome(False, f"not JSON: {exc}")
    gone = [k for k in keys if _dig(data, k) is _MISSING]
    return Outcome(not gone, "" if not gone else "missing " + ", ".join(gone))


def json_types(out, c, schema, allow_fences=False):  # noqa: ANN001
    try:
        data = _json(out, allow_fences)
    except ValueError as exc:
        return Outcome(False, f"not JSON: {exc}")
    wrong = []
    for path, wanted in schema.items():
        value = _dig(data, path)
        if value is _MISSING:
            wrong.append(f"{path} is missing")
        elif not _has_type(value, wanted):
            wrong.append(f"{path} is {_type_of(value)}, not {wanted}")
    return Outcome(not wrong, "; ".join(wrong))


def json_enum(out, c, path, values, allow_fences=False):  # noqa: ANN001
    try:
        data = _json(out, allow_fences)
    except ValueError as exc:
        return Outcome(False, f"not JSON: {exc}")
    value = _dig(data, path)
    if value is _MISSING:
        return Outcome(False, f"{path} is missing")
    return Outcome(
        value in values,
        "" if value in values else f"{path} is {_short(json.dumps(value))}, not one of {json.dumps(values)}",
    )


def _bound(kind: str, measure, low: bool):  # noqa: ANN001
    def check(out, c, value):  # noqa: ANN001
        n = measure(out)
        ok = n >= value if low else n <= value
        word = "fewer than" if low else "more than"
        return Outcome(ok, "" if ok else f"{n} {kind}, {word} {value} allowed")

    return check


def numbers_in_input(out, c, allow=()):  # noqa: ANN001
    """Every number in the output must also be in the case's input (or be listed in ``allow``)."""
    source = " ".join(str(v) for v in c.variables.values())
    seen = {n.replace(",", "") for n in NUMBER.findall(source)} | {str(a) for a in allow}
    extra = [n for n in dict.fromkeys(NUMBER.findall(out)) if n.replace(",", "") not in seen]
    return Outcome(not extra, "" if not extra else "not in the input: " + ", ".join(extra))


def cmd(out, c, command, timeout=10):  # noqa: ANN001
    try:
        p = subprocess.run(
            command, input=out, capture_output=True, text=True, timeout=timeout, env=_env(c), check=False
        )
    except FileNotFoundError:
        return Outcome(False, f"cannot run {command[0]!r}")
    except subprocess.TimeoutExpired:
        return Outcome(False, f"{command[0]!r} took longer than {timeout} s")
    if p.returncode == 0:
        return Outcome(True)
    why = (p.stderr or p.stdout).strip().splitlines()
    return Outcome(False, f"exit {p.returncode}" + (f": {_short(why[0], 80)}" if why else ""))


def _env(c: Context) -> dict[str, str]:
    import os

    return {**os.environ, "PROMPT_REGRESSION_CASE": c.case_id}


# type -> (function, {param: (accepted types, required)})
STR, BOOL, INT, LIST, TABLE = str, bool, int, list, dict
SPECS: dict[str, tuple[Any, dict[str, tuple[type, bool]]]] = {
    "contains": (contains, {"value": (STR, True), "ignore_case": (BOOL, False)}),
    "not_contains": (not_contains, {"value": (STR, True), "ignore_case": (BOOL, False)}),
    "equals": (equals, {"value": (STR, True), "strip": (BOOL, False), "ignore_case": (BOOL, False)}),
    "starts_with": (starts_with, {"value": (STR, True), "ignore_case": (BOOL, False)}),
    "matches": (matches, {"pattern": (STR, True), "flags": (STR, False)}),
    "not_matches": (not_matches, {"pattern": (STR, True), "flags": (STR, False)}),
    "one_of": (one_of, {"values": (LIST, True), "ignore_case": (BOOL, False), "strip": (BOOL, False)}),
    "not_empty": (not_empty, {}),
    "json_valid": (json_valid, {"allow_fences": (BOOL, False)}),
    "json_has": (json_has, {"keys": (LIST, True), "allow_fences": (BOOL, False)}),
    "json_types": (json_types, {"schema": (TABLE, True), "allow_fences": (BOOL, False)}),
    "json_enum": (json_enum, {"path": (STR, True), "values": (LIST, True), "allow_fences": (BOOL, False)}),
    "max_chars": (_bound("characters", len, False), {"value": (INT, True)}),
    "min_chars": (_bound("characters", len, True), {"value": (INT, True)}),
    "max_words": (_bound("words", lambda s: len(s.split()), False), {"value": (INT, True)}),
    "min_words": (_bound("words", lambda s: len(s.split()), True), {"value": (INT, True)}),
    "max_lines": (_bound("lines", lambda s: len(s.strip().splitlines()), False), {"value": (INT, True)}),
    "numbers_in_input": (numbers_in_input, {"allow": (LIST, False)}),
    "cmd": (cmd, {"command": (LIST, True), "timeout": (INT, False)}),
}


DOCS = {
    "contains": "the output has this text in it",
    "not_contains": "the output does not have this text anywhere",
    "equals": "the output is exactly this (surrounding whitespace ignored unless strip = false)",
    "starts_with": "the output begins with this (leading whitespace ignored)",
    "matches": "a regular expression finds a match; flags are i, m, s",
    "not_matches": "a regular expression finds nothing",
    "one_of": "the whole output is one of these strings",
    "not_empty": "the output has something other than whitespace",
    "json_valid": "the whole output parses as JSON; allow_fences accepts a markdown code fence around it",
    "json_has": "these keys exist; a dotted path reaches inside (`a.b`, `items.0`)",
    "json_types": "each path has this JSON type: string, number, integer, boolean, array, object, null",
    "json_enum": "the value at this path is one of these",
    "max_chars": "at most this many characters",
    "min_chars": "at least this many characters",
    "max_words": "at most this many whitespace-separated words",
    "min_words": "at least this many words",
    "max_lines": "at most this many lines (surrounding blank lines ignored)",
    "numbers_in_input": "every number in the output also appears in the case's variables (or in allow)",
    "cmd": "your command gets the output on stdin; exit code 0 passes (the check for anything else)",
}


def validate(spec: dict[str, Any], where: str) -> None:
    kind = spec.get("type")
    if kind not in SPECS:
        raise CheckError(f"{where}: unknown check type {kind!r}; known: {', '.join(sorted(SPECS))}")
    params = SPECS[kind][1]
    given = {k: v for k, v in spec.items() if k != "type"}
    for k in given:
        if k not in params:
            raise CheckError(
                f"{where}: {kind} has no parameter {k!r}; it takes: {', '.join(params) or 'nothing'}"
            )
    for k, (typ, required) in params.items():
        if k not in given:
            if required:
                raise CheckError(f"{where}: {kind} needs {k!r}")
            continue
        v = given[k]
        if (
            typ is int
            and (isinstance(v, bool) or not isinstance(v, int))
            or typ is not int
            and not isinstance(v, typ)
        ):
            raise CheckError(f"{where}: {kind}.{k} must be a {typ.__name__}, not {type(v).__name__}")
    if kind in ("matches", "not_matches"):
        try:
            re.compile(given["pattern"], _flags(given.get("flags", "")))
        except re.error as exc:
            raise CheckError(f"{where}: bad regular expression: {exc}") from exc
    if kind in ("one_of", "json_enum") and not all(
        isinstance(v, str | int | float | bool) for v in given["values"]
    ):
        raise CheckError(f"{where}: {kind}.values must be plain strings or numbers")
    if kind == "one_of" and not all(isinstance(v, str) for v in given["values"]):
        raise CheckError(f"{where}: one_of.values must be strings")
    if kind == "json_types":
        bad = {p: t for p, t in given["schema"].items() if t not in JSON_TYPES}
        if bad:
            raise CheckError(
                f"{where}: json_types has unknown types {bad}; use {', '.join(sorted(JSON_TYPES))}"
            )
    if kind == "cmd" and (not given["command"] or not all(isinstance(a, str) for a in given["command"])):
        raise CheckError(f"{where}: cmd.command must be a non-empty list of strings")


def label(spec: dict[str, Any]) -> str:
    """A short name for a check, for reports: ``contains 'refund'``."""
    kind = spec["type"]
    if kind == "json_enum":
        return f"{kind} {spec['path']} in {_short(json.dumps(spec['values']), 30)}"
    main = next(
        (spec[k] for k in ("value", "pattern", "values", "keys", "path", "schema", "command") if k in spec),
        None,
    )
    return (
        kind
        if main is None
        else f"{kind} {_short(json.dumps(main) if not isinstance(main, str) else repr(main), 40)}"
    )


def run(spec: dict[str, Any], output: str, ctx: Context) -> Outcome:
    fn = SPECS[spec["type"]][0]
    params = {k: v for k, v in spec.items() if k != "type"}
    try:
        return fn(output, ctx, **params)
    except CheckError as exc:
        return Outcome(False, str(exc))
