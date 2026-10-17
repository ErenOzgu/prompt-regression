import json

import pytest

from prompt_regression import baseline, runner
from prompt_regression import suite as suite_mod

from .conftest import Scripted, case, write_suite

C = '{ type = "contains", value = "good" }'


def result(tmp_path, answer, runs=3, **kw):
    cases = case("a", "x", C) + case("b", "x", C)
    s = suite_mod.load(write_suite(tmp_path, cases, **kw))
    return runner.run_suite(s, Scripted(answer), runs=runs)


def test_a_baseline_keeps_pass_rates_checks_and_the_fingerprint(tmp_path):
    res = result(tmp_path, lambda cid, run: "bad" if (cid, run) == ("b", 1) else "good")
    data = baseline.make(res)
    assert data["schema"] == 1
    assert data["fingerprint"] == res.suite.fingerprint()
    assert data["runs"] == 3
    assert data["cases"]["a"]["pass_rate"] == 1.0
    assert data["cases"]["b"]["pass_rate"] == 0.6667
    assert data["cases"]["b"]["checks"] == {"contains 'good'": 0.6667}


def test_save_and_load_round_trip(tmp_path):
    res = result(tmp_path, lambda c, r: "good")
    path = tmp_path / "b.json"
    baseline.save(res, path)
    assert baseline.load(path) == baseline.make(res)
    assert path.read_text().endswith("\n")


def test_the_same_run_is_never_below_its_own_baseline_even_with_thirds(tmp_path):
    # 2/3 is stored as 0.6667 and must not look like a drop when it comes out as 0.66666...
    res = result(tmp_path, lambda cid, run: "bad" if run == 2 else "good")
    verdict = baseline.judge(res, baseline.make(res))
    assert verdict.drops == []
    assert not verdict.changed


def test_a_case_that_passes_less_often_is_a_drop(tmp_path):
    good = baseline.make(result(tmp_path, lambda c, r: "good"))
    worse = result(tmp_path, lambda cid, run: "bad" if (cid, run) == ("b", 1) else "good")
    v = baseline.judge(worse, good)
    assert [(d.case, d.before, d.after) for d in v.drops] == [("b", 1.0, 0.6667)]


def test_improvement_is_not_a_drop(tmp_path):
    flaky = baseline.make(result(tmp_path, lambda cid, run: "bad" if run == 1 else "good"))
    assert baseline.judge(result(tmp_path, lambda c, r: "good"), flaky).drops == []


def test_tolerance_allows_a_small_fall(tmp_path):
    good = baseline.make(result(tmp_path, lambda c, r: "good"))
    worse = result(tmp_path, lambda cid, run: "bad" if (cid, run) == ("b", 1) else "good")
    assert baseline.judge(worse, good, tolerance=0.34).drops == []
    assert len(baseline.judge(worse, good, tolerance=0.3).drops) == 1


def test_cases_added_or_missing_are_listed_not_judged(tmp_path):
    base = baseline.make(result(tmp_path, lambda c, r: "good"))
    base["cases"]["gone"] = {"pass_rate": 1.0, "checks": {}}
    del base["cases"]["a"]
    v = baseline.judge(result(tmp_path, lambda c, r: "good"), base)
    assert (v.missing, v.new, v.drops) == (["gone"], ["a"], [])


def test_a_different_prompt_is_flagged_as_changed(tmp_path):
    base = baseline.make(result(tmp_path, lambda c, r: "good"))
    v = baseline.judge(result(tmp_path, lambda c, r: "good", prompt="other {{x}}"), base)
    assert v.changed


def test_compare_lists_both_directions(tmp_path):
    a = result(tmp_path, lambda cid, run: "bad" if cid == "b" else "good")
    b = result(tmp_path, lambda cid, run: "bad" if cid == "a" else "good")
    changes = baseline.compare(a, b)
    assert [(c.case, c.status) for c in changes] == [("a", "regressed"), ("b", "improved")]
    assert [c.status for c in baseline.compare(a, a)] == ["same", "same"]


@pytest.mark.parametrize(
    ("text", "message"),
    [("not json", "is not JSON"), ("[]", "not a baseline"), ('{"schema": 2, "cases": {}}', "not a baseline")],
)
def test_a_wrong_file_is_refused(tmp_path, text, message):
    path = tmp_path / "b.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match=message):
        baseline.load(path)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(baseline.BaselineError, match="cannot read the baseline"):
        baseline.load(tmp_path / "nope.json")


def test_the_file_is_plain_readable_json(tmp_path):
    path = tmp_path / "b.json"
    baseline.save(result(tmp_path, lambda c, r: "good"), path)
    assert json.loads(path.read_text())["cases"]["a"]["checks"]["contains 'good'"] == 1.0
