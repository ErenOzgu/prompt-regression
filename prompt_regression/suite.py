"""A suite file (TOML): the prompt, the provider, and the cases with their checks. Validated before anything runs."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import checks, template

CASE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
SUITE_KEYS = {"name", "prompt", "system", "system_file", "runs", "min_pass_rate"}
CASE_KEYS = {"id", "vars", "checks", "min_pass_rate", "note"}
PROVIDER_KEYS = {
    "cmd": {"type", "command", "timeout"},
    "anthropic": {"type", "model", "max_tokens", "temperature"},
    "replay": {"type", "file"},
}


class SuiteError(Exception):
    """The suite file is wrong or unreadable."""


@dataclass
class Case:
    id: str
    vars: dict[str, Any]
    checks: list[dict[str, Any]]
    min_pass_rate: float | None = None
    note: str = ""


@dataclass
class Suite:
    name: str
    path: Path
    prompt_path: Path
    prompt: str
    system: str
    provider: dict[str, Any]
    cases: list[Case]
    runs: int = 1
    min_pass_rate: float = 1.0
    notes: list[str] = field(default_factory=list)

    def threshold(self, case: Case) -> float:
        return case.min_pass_rate if case.min_pass_rate is not None else self.min_pass_rate

    def fingerprint(self) -> str:
        """Identifies the prompt, system text and provider settings, so a baseline says what it was taken with."""
        blob = json.dumps([self.prompt, self.system, self.provider], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _rate(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
        raise SuiteError(f"{where}: min_pass_rate must be a number above 0 and at most 1")
    return float(value)


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SuiteError(f"cannot read {what} {path}: {exc.strerror}") from exc


def load(path: str | Path, prompt: str | Path | None = None) -> Suite:
    """Read and check a suite. ``prompt`` replaces the prompt file named in the suite (for comparisons)."""
    p = Path(path)
    try:
        data = tomllib.loads(_read(p, "suite"))
    except tomllib.TOMLDecodeError as exc:
        raise SuiteError(f"{p}: not valid TOML: {exc}") from exc
    unknown = set(data) - {"suite", "provider", "case"}
    if unknown:
        raise SuiteError(
            f"{p}: unknown top-level section {sorted(unknown)[0]!r}; use [suite], [provider] and [[case]]"
        )
    s = data.get("suite")
    if not isinstance(s, dict):
        raise SuiteError(f"{p}: needs a [suite] section")
    bad = set(s) - SUITE_KEYS
    if bad:
        raise SuiteError(
            f"{p}: [suite] has no setting {sorted(bad)[0]!r}; it takes: {', '.join(sorted(SUITE_KEYS))}"
        )
    for key in ("name", "prompt"):
        if not isinstance(s.get(key), str) or not s[key].strip():
            if key == "prompt" and prompt is not None:
                continue
            raise SuiteError(f"{p}: [suite] needs {key!r} as a string")
    base = p.resolve().parent
    prompt_path = Path(prompt) if prompt is not None else base / s["prompt"]
    text = _read(prompt_path, "prompt")
    if "system" in s and "system_file" in s:
        raise SuiteError(f"{p}: use either system or system_file, not both")
    system = s.get("system", "")
    if "system_file" in s:
        system = _read(base / s["system_file"], "system prompt")
    if not isinstance(system, str):
        raise SuiteError(f"{p}: system must be a string")
    runs = s.get("runs", 1)
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise SuiteError(f"{p}: runs must be a whole number of at least 1")
    min_rate = _rate(s["min_pass_rate"], f"{p}: [suite]") if "min_pass_rate" in s else 1.0

    provider = data.get("provider")
    if not isinstance(provider, dict) or provider.get("type") not in PROVIDER_KEYS:
        raise SuiteError(f"{p}: [provider] needs type = one of {', '.join(PROVIDER_KEYS)}")
    extra = set(provider) - PROVIDER_KEYS[provider["type"]]
    if extra:
        raise SuiteError(f"{p}: [provider] of type {provider['type']} has no setting {sorted(extra)[0]!r}")
    _provider_settings(p, provider, base)

    raw = data.get("case")
    if not isinstance(raw, list) or not raw:
        raise SuiteError(f"{p}: needs at least one [[case]]")
    needed = template.names(text) + template.names(system)
    cases: list[Case] = []
    seen: set[str] = set()
    for i, c in enumerate(raw, 1):
        cid = c.get("id") if isinstance(c, dict) else None
        where = f"{p}: case {cid or i}"
        if not isinstance(cid, str) or not CASE_ID.match(cid):
            raise SuiteError(f"{p}: case {i} needs an id of letters, digits, '.', '_' or '-'")
        if cid in seen:
            raise SuiteError(f"{where}: the id is used twice")
        seen.add(cid)
        bad = set(c) - CASE_KEYS
        if bad:
            raise SuiteError(
                f"{where}: no setting {sorted(bad)[0]!r}; it takes: {', '.join(sorted(CASE_KEYS))}"
            )
        variables = c.get("vars", {})
        if not isinstance(variables, dict):
            raise SuiteError(f"{where}: vars must be a table")
        missing = [n for n in dict.fromkeys(needed) if n not in variables]
        if missing:
            raise SuiteError(
                f"{where}: the prompt uses {', '.join('{{' + n + '}}' for n in missing)} but vars does not define it"
            )
        specs = c.get("checks")
        if not isinstance(specs, list) or not specs:
            raise SuiteError(f"{where}: needs a list of checks")
        for j, spec in enumerate(specs, 1):
            if not isinstance(spec, dict):
                raise SuiteError(
                    f'{where}: check {j} must be a table like {{ type = "contains", value = "x" }}'
                )
            try:
                checks.validate(spec, f"{where}, check {j}")
            except checks.CheckError as exc:
                raise SuiteError(str(exc)) from exc
        rate = _rate(c["min_pass_rate"], where) if "min_pass_rate" in c else None
        cases.append(Case(cid, variables, specs, rate, str(c.get("note", ""))))
    notes = []
    unused = sorted({k for c in cases for k in c.vars} - set(needed))
    if unused:
        notes.append(
            f"vars never used by the prompt: {', '.join(unused)} (still available to numbers_in_input)"
        )
    return Suite(s.get("name", p.stem), p, prompt_path, text, system, provider, cases, runs, min_rate, notes)


def _provider_settings(p: Path, provider: dict[str, Any], base: Path) -> None:
    kind = provider["type"]
    if kind == "cmd":
        cmd = provider.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
            raise SuiteError(
                f"{p}: [provider] command must be a non-empty list of strings (no shell is used)"
            )
        t = provider.get("timeout", 60)
        if isinstance(t, bool) or not isinstance(t, int | float) or t <= 0:
            raise SuiteError(f"{p}: [provider] timeout must be a positive number of seconds")
    elif kind == "anthropic":
        if not isinstance(provider.get("model"), str):
            raise SuiteError(f"{p}: [provider] of type anthropic needs model")
        mt = provider.get("max_tokens", 1024)
        if isinstance(mt, bool) or not isinstance(mt, int) or mt < 1:
            raise SuiteError(f"{p}: [provider] max_tokens must be a whole number of at least 1")
        temp = provider.get("temperature")
        if temp is not None and (
            isinstance(temp, bool) or not isinstance(temp, int | float) or not 0 <= temp <= 1
        ):
            raise SuiteError(f"{p}: [provider] temperature must be between 0 and 1")
    elif kind == "replay" and not isinstance(provider.get("file"), str):
        raise SuiteError(f"{p}: [provider] of type replay needs file")
