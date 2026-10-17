import json
import xml.etree.ElementTree as ET

from prompt_regression import baseline, report, runner
from prompt_regression import suite as suite_mod
from prompt_regression.providers import ProviderError

from .conftest import Scripted, case, write_suite

C = '{ type = "contains", value = "good" }'


def make(tmp_path, answer, cases=None, runs=3, **kw):
    cases = cases or (
        case("solid", "x", C) + case("shaky", "x", C, extra="min_pass_rate = 0.5") + case("broken", "x", C)
    )
    s = suite_mod.load(write_suite(tmp_path, cases, **kw))
    return runner.run_suite(s, Scripted(answer), runs=runs)


def mixed(cid, run):
    if cid == "shaky":
        return "bad" if run == 2 else "good"
    if cid == "broken":
        return ProviderError("rate limited") if run == 1 else "I said: nothing useful"
    return "good"


def test_text_report_marks_pass_fail_and_flaky(tmp_path):
    out = report.text(make(tmp_path, mixed))
    assert "PASS  solid   3/3\n" in out
    assert "PASS  shaky   2/3  flaky  (needs 50%)" in out
    assert "FAIL  broken  0/3  (needs 100%)" in out
    assert "3 cases, 2 pass, 1 fail, 1 flaky (3 runs each; 80 tokens in, 40 out)" in out
    assert "1 of 3 runs got no output: rate limited" in out
    assert "e.g. run 2 said: 'I said: nothing useful'" in out


def test_a_passing_case_is_one_line(tmp_path):
    out = report.text(make(tmp_path, lambda c, r: "good"))
    assert "e.g." not in out
    assert out.count("\n") == 6
    assert out.splitlines()[0].startswith("t  [scripted; prompt prompt.txt, fingerprint ")


def test_the_baseline_verdict_is_part_of_the_text(tmp_path):
    base = baseline.make(make(tmp_path, lambda c, r: "good"))
    res = make(tmp_path, mixed)
    out = report.text(res, baseline.judge(res, base))
    assert "REGRESSION  broken: 100% in the baseline, 0% now" in out
    assert "REGRESSION  shaky: 100% in the baseline, 67% now" in out
    assert "no case is below" not in out


def test_no_regression_says_so(tmp_path):
    res = make(tmp_path, lambda c, r: "good")
    out = report.text(res, baseline.judge(res, baseline.make(res)))
    assert "no case is below its baseline" in out


def test_markdown_has_a_table_and_the_reasons(tmp_path):
    out = report.markdown(make(tmp_path, mixed))
    assert "| `solid` | 3/3 | 100% ✅ | 100% |" in out
    assert "| `shaky` | 2/3 | 67% ✅ flaky | 50% |" in out
    assert "| `broken` | 0/3 | 0% ❌ | 100% |" in out
    assert "**broken**" in out
    assert out.count("|---|---|---|---|") == 1


def test_markdown_code_spans_cope_with_backticks():
    assert report._code("plain") == "`plain`"
    assert report._code("a`b") == "``a`b``"
    assert report._code("`x") == "`` `x ``"


def test_json_report_is_complete_and_parses(tmp_path):
    res = make(tmp_path, mixed)
    data = json.loads(report.render_json(res))
    assert data["ok"] is False
    assert data["counts"] == {"cases": 3, "passed": 2, "failed": 1, "flaky": 1}
    by = {c["id"]: c for c in data["cases"]}
    assert by["shaky"]["pass_rate"] == 2 / 3
    assert by["shaky"]["flaky"] is True
    assert by["broken"]["errors"] == ["rate limited"]
    assert by["broken"]["checks"][0]["check"] == "contains 'good'"
    assert data["regressions"] == []


def test_json_ok_is_false_when_the_baseline_is_broken_even_if_every_case_passes(tmp_path):
    good = make(tmp_path, lambda c, r: "good")
    base = baseline.make(good)
    base["cases"]["solid"]["pass_rate"] = 1.0
    shaky_but_ok = make(tmp_path, lambda cid, run: "bad" if (cid, run) == ("shaky", 1) else "good")
    assert shaky_but_ok.ok
    data = json.loads(report.render_json(shaky_but_ok, baseline.judge(shaky_but_ok, base)))
    assert data["ok"] is False
    assert data["regressions"][0]["case"] == "shaky"


def test_junit_is_valid_xml_with_one_testcase_per_case(tmp_path):
    xml = report.junit(make(tmp_path, mixed))
    root = ET.fromstring(xml)
    assert root.tag == "testsuite"
    assert (root.get("tests"), root.get("failures"), root.get("errors")) == ("3", "1", "0")
    cases = {t.get("name"): t for t in root.findall("testcase")}
    assert set(cases) == {"solid", "shaky", "broken"}
    assert cases["solid"].find("failure") is None
    assert cases["shaky"].find("failure") is None  # 2 of 3 is enough for 50%
    failure = cases["broken"].find("failure")
    assert "0 of 3 runs passed; 100% needed" in failure.get("message")
    assert "rate limited" in failure.text


def test_junit_counts_regressions_as_failures(tmp_path):
    base = baseline.make(make(tmp_path, lambda c, r: "good"))
    res = make(tmp_path, mixed)
    root = ET.fromstring(report.junit(res, baseline.judge(res, base)))
    cases = {t.get("name"): t for t in root.findall("testcase")}
    assert "regression: 100% in the baseline, 67% now" in cases["shaky"].find("failure").get("message")
    assert root.get("failures") == "2"


def test_junit_survives_characters_xml_cannot_hold(tmp_path):
    res = make(tmp_path, lambda c, r: "bad \x00 \x1b[31m red \x07", cases=case("a", "x", C))
    ET.fromstring(report.junit(res))  # does not raise


def test_compare_text_and_markdown(tmp_path):
    a = make(tmp_path, lambda cid, run: "bad" if cid == "broken" else "good")
    b = make(tmp_path, lambda cid, run: "bad" if cid == "solid" else "good")
    changes = baseline.compare(a, b)
    text = report.compare_text("one.txt", "two.txt", changes)
    assert "A = one.txt\nB = two.txt" in text
    assert "solid " in text
    assert "1 regressed, 1 improved, 1 unchanged going from A to B" in text
    md = report.compare_markdown("one.txt", "two.txt", changes)
    assert "| `solid` | 100% | 0% | ❌ regressed |" in md
    assert "| `broken` | 0% | 100% | ✅ improved |" in md
