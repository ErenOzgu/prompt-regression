"""A baseline is the pass rates of one good run, kept in a file. A later run that falls below it fails."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .runner import SuiteResult

SCHEMA = 1


class BaselineError(Exception):
    """The baseline file is missing or not one of ours."""


@dataclass
class Drop:
    case: str
    before: float
    after: float


@dataclass
class Change:
    case: str
    a: float
    b: float

    @property
    def status(self) -> str:
        return "regressed" if self.b < self.a - 1e-9 else "improved" if self.b > self.a + 1e-9 else "same"


@dataclass
class Verdict:
    drops: list[Drop]
    missing: list[str]  # in the baseline, not in this run
    new: list[str]  # in this run, not in the baseline
    changed: bool = False  # the prompt, system text or provider settings are not the baseline's


def make(result: SuiteResult) -> dict:
    return {
        "schema": SCHEMA,
        "suite": result.suite.name,
        "fingerprint": result.suite.fingerprint(),
        "provider": result.provider,
        "runs": result.runs,
        "cases": {
            c.case.id: {
                "pass_rate": round(c.pass_rate, 4),
                "checks": {lb: round(r, 4) for lb, r, _ in c.check_rates()},
            }
            for c in result.cases
        },
    }


def save(result: SuiteResult, path: str | Path) -> None:
    Path(path).write_text(json.dumps(make(result), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise BaselineError(f"cannot read the baseline {path}: {exc.strerror}") from exc
    except ValueError as exc:
        raise BaselineError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("cases"), dict):
        raise BaselineError(f"{path} is not a baseline made by prompt-regression (schema {SCHEMA})")
    return data


def judge(result: SuiteResult, baseline: dict, tolerance: float = 0.0) -> Verdict:
    """Which cases now pass less often than they did when the baseline was taken."""
    before = {cid: float(v["pass_rate"]) for cid, v in baseline["cases"].items()}
    now = {c.case.id: round(c.pass_rate, 4) for c in result.cases}  # the file holds four decimals
    drops = [
        Drop(cid, before[cid], now[cid])
        for cid in now
        if cid in before and now[cid] < before[cid] - tolerance - 1e-9
    ]
    return Verdict(
        drops,
        sorted(set(before) - set(now)),
        sorted(set(now) - set(before)),
        baseline.get("fingerprint") != result.suite.fingerprint(),
    )


def compare(a: SuiteResult, b: SuiteResult) -> list[Change]:
    rate_b = {c.case.id: c.pass_rate for c in b.cases}
    return [Change(c.case.id, c.pass_rate, rate_b[c.case.id]) for c in a.cases if c.case.id in rate_b]
