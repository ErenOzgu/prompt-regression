"""The four ways to show a result: text, markdown, JSON and JUnit XML."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from .baseline import Change, Verdict
from .runner import CaseResult, SuiteResult

_BAD_XML = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")


def pct(rate: float) -> str:
    return f"{rate * 100:.0f}%"


def _failures(c: CaseResult) -> list[str]:
    lines = []
    runs = len(c.runs)
    errors = [r for r in c.runs if r.error]
    if errors:
        lines.append(f"{len(errors)} of {runs} runs got no output: {errors[0].error}")
    for label, rate, detail in c.check_rates():
        if rate < 1 and detail != "no output":
            lines.append(f"{label}: held in {round(rate * runs)} of {runs} runs. {detail}")
    return lines


def _headline(r: SuiteResult) -> str:
    n = r.counts()
    toks = r.tokens()
    extra = f"; {toks[0]} tokens in, {toks[1]} out" if toks else ""
    return (
        f"{n['cases']} cases, {n['passed']} pass, {n['failed']} fail"
        + (f", {n['flaky']} flaky" if n["flaky"] else "")
        + f" ({r.runs} run{'s' if r.runs != 1 else ''} each{extra})"
    )


def text(r: SuiteResult, verdict: Verdict | None = None) -> str:
    out = [
        f"{r.suite.name}  [{r.provider}; prompt {r.suite.prompt_path.name}, fingerprint {r.suite.fingerprint()}]"
    ]
    width = max((len(c.case.id) for c in r.cases), default=4)
    for c in r.cases:
        mark = "PASS" if c.ok else "FAIL"
        flaky = "  flaky" if c.flaky else ""
        need = f"  (needs {pct(c.threshold)})" if not c.ok or c.threshold < 1 else ""
        out.append(f"  {mark}  {c.case.id:<{width}}  {c.passed}/{len(c.runs)}{flaky}{need}")
        if not c.ok or c.flaky:
            out += [f"          {line}" for line in _failures(c)]
            bad = c.first_failure()
            if bad and bad.output:
                out.append(
                    f"          e.g. run {bad.index} said: {bad.output[:160].replace(chr(10), ' / ')!r}"
                )
    out += ["", _headline(r)]
    out += _verdict_lines(verdict)
    for note in r.suite.notes:
        out.append(f"note: {note}")
    return "\n".join(out) + "\n"


def _verdict_lines(v: Verdict | None) -> list[str]:
    if v is None:
        return []
    lines = []
    for d in v.drops:
        lines.append(f"REGRESSION  {d.case}: {pct(d.before)} in the baseline, {pct(d.after)} now")
    if v.missing:
        lines.append("not run this time (in the baseline): " + ", ".join(v.missing))
    if v.new:
        lines.append("new since the baseline: " + ", ".join(v.new))
    if not v.drops:
        lines.append("no case is below its baseline")
    if v.changed:
        lines.append("note: the prompt, system text or provider settings differ from the baseline's")
    return lines


def _code(s: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", s)), default=0)
    return (
        "`" * (longest + 1)
        + (" " if s.startswith("`") or s.endswith("`") else "")
        + s
        + (" " if s.startswith("`") or s.endswith("`") else "")
        + "`" * (longest + 1)
    )


def markdown(r: SuiteResult, verdict: Verdict | None = None) -> str:
    out = [
        f"## {r.suite.name}",
        "",
        f"{_headline(r)}. Provider: {_code(r.provider)}.",
        "",
        "| Case | Passed | Rate | Needs |",
        "|---|---|---|---|",
    ]
    for c in r.cases:
        status = ("✅" if c.ok else "❌") + (" flaky" if c.flaky else "")
        out.append(
            f"| {_code(c.case.id)} | {c.passed}/{len(c.runs)} | {pct(c.pass_rate)} {status} | {pct(c.threshold)} |"
        )
    for c in r.cases:
        lines = _failures(c) if (not c.ok or c.flaky) else []
        if lines:
            out += ["", f"**{c.case.id}**", ""] + [f"- {line}" for line in lines]
    if verdict is not None:
        out += [""] + [f"- {line}" for line in _verdict_lines(verdict)]
    return "\n".join(out) + "\n"


def as_dict(r: SuiteResult, verdict: Verdict | None = None) -> dict:
    return {
        "suite": r.suite.name,
        "fingerprint": r.suite.fingerprint(),
        "provider": r.provider,
        "runs": r.runs,
        "ok": r.ok and (verdict is None or not verdict.drops),
        "counts": r.counts(),
        "cases": [
            {
                "id": c.case.id,
                "ok": c.ok,
                "passed": c.passed,
                "runs": len(c.runs),
                "pass_rate": c.pass_rate,
                "needs": c.threshold,
                "flaky": c.flaky,
                "checks": [{"check": lb, "pass_rate": rate, "detail": d} for lb, rate, d in c.check_rates()],
                "errors": [r_.error for r_ in c.runs if r_.error],
            }
            for c in r.cases
        ],
        "regressions": [
            {"case": d.case, "before": d.before, "after": d.after} for d in (verdict.drops if verdict else [])
        ],
    }


def render_json(r: SuiteResult, verdict: Verdict | None = None) -> str:
    return json.dumps(as_dict(r, verdict), indent=2, ensure_ascii=False) + "\n"


def junit(r: SuiteResult, verdict: Verdict | None = None) -> str:
    """JUnit XML: one test case per case. A case fails when its pass rate is under what it needs."""
    drops = {d.case: d for d in (verdict.drops if verdict else [])}

    def clean(s: str) -> str:
        return _BAD_XML.sub("?", s)

    n = r.counts()
    suite = ET.Element(
        "testsuite",
        name=clean(r.suite.name),
        tests=str(n["cases"]),
        failures=str(sum(1 for c in r.cases if not c.ok or c.case.id in drops)),
        errors="0",
        skipped="0",
        time=f"{r.seconds():.3f}",
    )
    props = ET.SubElement(suite, "properties")
    for k, v in (("provider", r.provider), ("runs", str(r.runs)), ("fingerprint", r.suite.fingerprint())):
        ET.SubElement(props, "property", name=k, value=clean(v))
    for c in r.cases:
        t = ET.SubElement(
            suite,
            "testcase",
            classname=clean(r.suite.name),
            name=clean(c.case.id),
            time=f"{sum(x.seconds for x in c.runs):.3f}",
        )
        reasons = []
        if not c.ok:
            reasons.append(f"{c.passed} of {len(c.runs)} runs passed; {pct(c.threshold)} needed")
        if c.case.id in drops:
            d = drops[c.case.id]
            reasons.append(f"regression: {pct(d.before)} in the baseline, {pct(d.after)} now")
        if reasons:
            f = ET.SubElement(t, "failure", message=clean("; ".join(reasons)), type="PromptRegression")
            body = _failures(c)
            bad = c.first_failure()
            if bad and bad.output:
                body.append(f"run {bad.index} said: {bad.output[:300]}")
            f.text = clean("\n".join(body))
    ET.indent(suite)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suite, encoding="unicode") + "\n"


def compare_text(label_a: str, label_b: str, changes: list[Change]) -> str:
    width = max((len(c.case) for c in changes), default=4)
    out = [f"A = {label_a}", f"B = {label_b}", "", f"  {'case':<{width}}  {'A':>5}  {'B':>5}  "]
    for c in changes:
        out.append(
            f"  {c.case:<{width}}  {pct(c.a):>5}  {pct(c.b):>5}  {c.status if c.status != 'same' else ''}"
        )
    n = {s: sum(c.status == s for c in changes) for s in ("regressed", "improved", "same")}
    out += [
        "",
        f"{n['regressed']} regressed, {n['improved']} improved, {n['same']} unchanged going from A to B",
    ]
    return "\n".join(out) + "\n"


def compare_markdown(label_a: str, label_b: str, changes: list[Change]) -> str:
    out = [f"A = {_code(label_a)}  \nB = {_code(label_b)}", "", "| Case | A | B | |", "|---|---|---|---|"]
    mark = {"regressed": "❌ regressed", "improved": "✅ improved", "same": ""}
    out += [f"| {_code(c.case)} | {pct(c.a)} | {pct(c.b)} | {mark[c.status]} |" for c in changes]
    return "\n".join(out) + "\n"
