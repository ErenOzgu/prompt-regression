"""Run a suite: every case, several times, each completion through every check."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import checks, template
from .providers import Provider, ProviderError
from .suite import Case, Suite

SHOW = 400


class UnknownCase(ValueError):
    """``only`` names a case the suite does not have."""


@dataclass
class CheckResult:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class RunResult:
    index: int
    checks: list[CheckResult]
    seconds: float = 0.0
    error: str = ""
    output: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None

    @property
    def ok(self) -> bool:
        return not self.error and all(c.ok for c in self.checks)


@dataclass
class CaseResult:
    case: Case
    threshold: float
    runs: list[RunResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(r.ok for r in self.runs)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.runs) if self.runs else 0.0

    @property
    def ok(self) -> bool:
        return bool(self.runs) and self.pass_rate >= self.threshold

    @property
    def flaky(self) -> bool:
        return 0 < self.passed < len(self.runs)

    def check_rates(self) -> list[tuple[str, float, str]]:
        """Per check: the share of runs where it held, and the first failure's detail."""
        out = []
        for i, spec_label in enumerate(self.runs[0].checks if self.runs else []):
            held = [r.checks[i].ok for r in self.runs if i < len(r.checks)]
            detail = next(
                (r.checks[i].detail for r in self.runs if i < len(r.checks) and not r.checks[i].ok), ""
            )
            out.append((spec_label.label, sum(held) / len(held), detail))
        return out

    def first_failure(self) -> RunResult | None:
        """The first failing run, preferring one that produced output to show."""
        failed = [r for r in self.runs if not r.ok]
        return next((r for r in failed if r.output), failed[0] if failed else None)


@dataclass
class SuiteResult:
    suite: Suite
    provider: str
    cases: list[CaseResult]
    runs: int

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.cases)

    def counts(self) -> dict[str, int]:
        return {
            "cases": len(self.cases),
            "passed": sum(c.ok for c in self.cases),
            "failed": sum(not c.ok for c in self.cases),
            "flaky": sum(c.flaky for c in self.cases),
        }

    def seconds(self) -> float:
        return sum(r.seconds for c in self.cases for r in c.runs)

    def tokens(self) -> tuple[int, int] | None:
        runs = [r for c in self.cases for r in c.runs if r.tokens_in is not None]
        return (sum(r.tokens_in or 0 for r in runs), sum(r.tokens_out or 0 for r in runs)) if runs else None


def one_run(suite: Suite, provider: Provider, case: Case, index: int) -> RunResult:
    labels = [checks.label(spec) for spec in case.checks]
    try:
        prompt = template.render(suite.prompt, case.vars)
        system = template.render(suite.system, case.vars) if suite.system else ""
        got = provider.complete(prompt, system, case.id, index)
    except (ProviderError, template.TemplateError) as exc:
        return RunResult(index, [CheckResult(lb, False, "no output") for lb in labels], error=str(exc))
    ctx = checks.Context(case.vars, case.id)
    results = []
    for spec, lb in zip(case.checks, labels, strict=True):
        outcome = checks.run(spec, got.text, ctx)
        results.append(CheckResult(lb, outcome.ok, outcome.detail))
    return RunResult(index, results, got.seconds, "", got.text[:SHOW], got.tokens_in, got.tokens_out)


def run_suite(
    suite: Suite,
    provider: Provider,
    runs: int | None = None,
    jobs: int = 1,
    only: list[str] | None = None,
) -> SuiteResult:
    n = runs or suite.runs
    cases = [c for c in suite.cases if not only or c.id in only]
    if only:
        unknown = sorted(set(only) - {c.id for c in suite.cases})
        if unknown:
            raise UnknownCase(
                f"no case named {', '.join(unknown)}; the suite has {', '.join(c.id for c in suite.cases)}"
            )
    results = [CaseResult(c, suite.threshold(c)) for c in cases]
    tasks = [(cr, i) for cr in results for i in range(1, n + 1)]
    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            done = list(pool.map(lambda t: one_run(suite, provider, t[0].case, t[1]), tasks))
    else:
        done = [one_run(suite, provider, cr.case, i) for cr, i in tasks]
    for (cr, _), res in zip(tasks, done, strict=True):
        cr.runs.append(res)
    return SuiteResult(suite, provider.name, results, n)
