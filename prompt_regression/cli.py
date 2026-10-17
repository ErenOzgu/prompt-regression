"""prompt-regression run | record | compare | validate | checks"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, baseline, report
from .baseline import BaselineError
from .checks import DOCS, SPECS
from .providers import Provider, ProviderError, Recorder, ReplayProvider, make
from .runner import SuiteResult, UnknownCase, run_suite
from .suite import Suite, SuiteError, load

OK, ERROR, USAGE, FAILED = 0, 1, 2, 3


class UsageError(Exception):
    """The command line asks for something that cannot be done."""


def _provider(suite: Suite, model: str | None = None, replay: str | None = None) -> Provider:
    base = suite.path.resolve().parent
    if replay:
        return ReplayProvider(Path(replay))
    if model and suite.provider["type"] != "anthropic":
        raise UsageError("--model only applies to a suite whose provider type is anthropic")
    try:
        return make(suite.provider, base, model)
    except ProviderError as exc:
        if "ANTHROPIC_API_KEY" in str(exc):
            raise UsageError(str(exc)) from exc
        raise


def _emit(body: str, output: str | None) -> None:
    if output:
        Path(output).write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)


def _only(args: argparse.Namespace) -> list[str] | None:
    return args.case or None


def _run(suite: Suite, provider: Provider, args: argparse.Namespace) -> SuiteResult:
    if args.jobs < 1:
        raise UsageError("--jobs must be at least 1")
    if args.runs is not None and args.runs < 1:
        raise UsageError("--runs must be at least 1")
    try:
        return run_suite(suite, provider, args.runs, args.jobs, _only(args))
    except UnknownCase as exc:
        raise UsageError(str(exc)) from exc


def cmd_run(args: argparse.Namespace) -> int:
    suite = load(args.suite, args.prompt)
    result = _run(suite, _provider(suite, args.model, args.replay), args)
    verdict = None
    if args.baseline:
        if args.tolerance < 0:
            raise UsageError("--tolerance cannot be negative")
        verdict = baseline.judge(result, baseline.load(args.baseline), args.tolerance)
    if args.save_baseline:
        baseline.save(result, args.save_baseline)
    if args.junit:
        Path(args.junit).write_text(report.junit(result, verdict), encoding="utf-8")
    render = {"text": report.text, "md": report.markdown, "json": report.render_json}[args.format]
    _emit(render(result, verdict), args.output)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if args.github_summary and summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report.markdown(result, verdict) + "\n")
    return OK if result.ok and not (verdict and verdict.drops) else FAILED


def cmd_record(args: argparse.Namespace) -> int:
    suite = load(args.suite, args.prompt)
    if suite.provider["type"] == "replay":
        raise UsageError(
            "this suite replays a recording already; record from a suite whose provider is cmd or anthropic"
        )
    rec = Recorder(_provider(suite, args.model))
    result = _run(suite, rec, args)
    order = {c.case.id: i for i, c in enumerate(result.cases)}
    rows = sorted(rec.rows, key=lambda r: (order[r["case"]], r["run"]))  # the same file whatever --jobs was
    Path(args.output).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    sys.stdout.write(report.text(result))
    wanted = sum(len(c.runs) for c in result.cases)
    print(f"recorded {len(rec.rows)} of {wanted} completions in {args.output}")
    return OK if len(rec.rows) == wanted else ERROR


def cmd_compare(args: argparse.Namespace) -> int:
    if bool(args.prompt) == bool(args.model):
        raise UsageError("compare needs exactly one of --prompt A B or --model A B")
    results, labels = [], []
    for i in (0, 1):
        if args.prompt:
            suite = load(args.suite, args.prompt[i])
            provider, label = _provider(suite), args.prompt[i]
        else:
            suite = load(args.suite)
            provider, label = _provider(suite, args.model[i]), args.model[i]
        results.append(_run(suite, provider, args))
        labels.append(label)
    changes = baseline.compare(results[0], results[1])
    render = {"text": report.compare_text, "md": report.compare_markdown}[args.format]
    _emit(render(labels[0], labels[1], changes), args.output)
    return FAILED if any(c.status == "regressed" for c in changes) else OK


def cmd_validate(args: argparse.Namespace) -> int:
    suite = load(args.suite, args.prompt)
    n_checks = sum(len(c.checks) for c in suite.cases)
    print(
        f"{suite.path}: ok, {len(suite.cases)} cases and {n_checks} checks; provider {suite.provider['type']}; fingerprint {suite.fingerprint()}"
    )
    for note in suite.notes:
        print(f"note: {note}")
    return OK


def cmd_checks(args: argparse.Namespace) -> int:
    for kind, (_, params) in SPECS.items():
        shown = ", ".join(f"{k}{'' if req else '?'}" for k, (_, req) in params.items())
        print(f"{kind:<18} {shown}\n{'':<18} {DOCS[kind]}")
    print("\nA name ending in ? is optional.")
    return OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prompt-regression",
        description="Regression tests for prompts. Exit codes: 0 fine, 1 error, 2 bad usage, 3 failures or regressions.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, runs: bool = True) -> None:
        sp.add_argument("suite", help="the suite file (TOML)")
        if runs:
            sp.add_argument("--runs", type=int, help="times to run each case (default: the suite's, else 1)")
            sp.add_argument("--jobs", type=int, default=1, help="completions to ask for at once (default 1)")
        sp.add_argument("--case", action="append", help="run only this case id; repeatable")

    r = sub.add_parser("run", help="run the suite and report")
    common(r)
    r.add_argument("--prompt", help="use this prompt file instead of the one in the suite")
    r.add_argument("--model", help="use this model (anthropic provider only)")
    r.add_argument(
        "--replay", metavar="FILE", help="answer from this recording instead of the suite's provider"
    )
    r.add_argument(
        "--baseline", metavar="FILE", help="fail if a case passes less often than it did in this baseline"
    )
    r.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="how far below the baseline a pass rate may fall (default 0)",
    )
    r.add_argument("--save-baseline", metavar="FILE", help="write this run's pass rates as a baseline")
    r.add_argument("--junit", metavar="FILE", help="write JUnit XML for a CI test report")
    r.add_argument(
        "--github-summary", action="store_true", help="append a markdown report to $GITHUB_STEP_SUMMARY"
    )
    r.add_argument("-f", "--format", choices=["text", "md", "json"], default="text")
    r.add_argument("-o", "--output", help="write the report here instead of standard output")
    r.set_defaults(run=cmd_run)

    rec = sub.add_parser("record", help="run against the real provider and keep the completions for replay")
    common(rec)
    rec.add_argument("-o", "--output", required=True, metavar="FILE", help="the recording (JSON lines)")
    rec.add_argument("--prompt")
    rec.add_argument("--model")
    rec.set_defaults(run=cmd_record)

    c = sub.add_parser(
        "compare", help="run the suite twice, with two prompts or two models, and show what changed"
    )
    common(c)
    c.add_argument("--prompt", nargs=2, metavar=("A", "B"), help="two prompt files")
    c.add_argument("--model", nargs=2, metavar=("A", "B"), help="two models (anthropic provider only)")
    c.add_argument("-f", "--format", choices=["text", "md"], default="text")
    c.add_argument("-o", "--output")
    c.set_defaults(run=cmd_compare)

    v = sub.add_parser("validate", help="check the suite file without calling any model")
    common(v, runs=False)
    v.add_argument("--prompt")
    v.set_defaults(run=cmd_validate)

    k = sub.add_parser("checks", help="list the kinds of check and their parameters")
    k.set_defaults(run=cmd_checks)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else USAGE
    try:
        return args.run(args)
    except (SuiteError, BaselineError, ProviderError) as exc:
        print(f"prompt-regression: {exc}", file=sys.stderr)
        return ERROR
    except UsageError as exc:
        print(f"prompt-regression: {exc}", file=sys.stderr)
        return USAGE
    except BrokenPipeError:
        return OK


if __name__ == "__main__":
    sys.exit(main())
