import pytest

from prompt_regression import runner
from prompt_regression import suite as suite_mod
from prompt_regression.providers import ProviderError

from .conftest import BY_RUN, PY, Scripted, case, write_suite

CONTAINS = '{ type = "contains", value = "good" }'


def load(tmp_path, cases, **kw):
    return suite_mod.load(write_suite(tmp_path, cases, **kw))


def test_every_case_runs_the_requested_number_of_times(tmp_path):
    s = load(tmp_path, case("a", "good", CONTAINS) + case("b", "good", CONTAINS))
    p = Scripted(lambda cid, run: "good")
    res = runner.run_suite(s, p, runs=4)
    assert [len(c.runs) for c in res.cases] == [4, 4]
    assert len(p.calls) == 8
    assert [r.index for r in res.cases[0].runs] == [1, 2, 3, 4]
    assert res.ok


def test_the_suite_setting_is_the_default_and_the_option_overrides_it(tmp_path):
    s = load(tmp_path, case("a", "good", CONTAINS), suite_extra="runs = 5")
    assert runner.run_suite(s, Scripted(lambda c, r: "good")).runs == 5
    assert runner.run_suite(s, Scripted(lambda c, r: "good"), runs=2).runs == 2


def test_the_prompt_is_rendered_with_the_case_variables(tmp_path):
    s = load(
        tmp_path, case("a", "hello there", CONTAINS), prompt="Say: {{x}}!", suite_extra='system = "sys {{x}}"'
    )
    p = Scripted(lambda c, r: "good")
    runner.run_suite(s, p)
    assert p.calls[0][:2] == ("Say: hello there!", "sys hello there")


def test_a_threshold_below_one_lets_an_occasional_miss_through(tmp_path):
    cases = case("strict", "x", CONTAINS) + case("lenient", "x", CONTAINS, extra="min_pass_rate = 0.6")
    s = load(tmp_path, cases)
    res = runner.run_suite(s, Scripted(lambda cid, run: "bad" if run == 2 else "good"), runs=3)
    strict, lenient = res.cases
    assert (strict.passed, strict.ok, strict.flaky) == (2, False, True)
    assert (lenient.passed, lenient.ok, lenient.flaky) == (2, True, True)
    assert not res.ok
    assert res.counts() == {"cases": 2, "passed": 1, "failed": 1, "flaky": 2}


def test_all_runs_failing_is_not_flaky(tmp_path):
    res = runner.run_suite(load(tmp_path, case("a", "x", CONTAINS)), Scripted(lambda c, r: "bad"), runs=3)
    assert (res.cases[0].passed, res.cases[0].flaky, res.cases[0].pass_rate) == (0, False, 0.0)


def test_a_failing_check_records_why_and_the_output(tmp_path):
    res = runner.run_suite(load(tmp_path, case("a", "x", CONTAINS)), Scripted(lambda c, r: "nope"))
    bad = res.cases[0].first_failure()
    assert bad.output == "nope"
    assert bad.checks[0].detail == "'good' is not in the output"
    assert res.cases[0].check_rates() == [("contains 'good'", 0.0, "'good' is not in the output")]


def test_a_provider_error_fails_that_run_and_the_suite_carries_on(tmp_path):
    def answer(cid, run):
        return ProviderError("rate limited") if (cid, run) == ("a", 2) else "good"

    s = load(tmp_path, case("a", "x", CONTAINS) + case("b", "x", CONTAINS))
    res = runner.run_suite(s, Scripted(answer), runs=3)
    a, b = res.cases
    assert (a.passed, b.passed) == (2, 3)
    assert a.runs[1].error == "rate limited"
    assert a.runs[1].checks[0].detail == "no output"
    assert not a.runs[1].ok


def test_a_check_that_crashes_the_command_fails_that_check_only(tmp_path):
    bad_cmd = f'{{ type = "cmd", command = ["{PY}", "-c", "raise SystemExit(7)"] }}'
    res = runner.run_suite(load(tmp_path, case("a", "x", CONTAINS, bad_cmd)), Scripted(lambda c, r: "good"))
    ok, failed = res.cases[0].runs[0].checks
    assert ok.ok
    assert not failed.ok
    assert failed.detail.startswith("exit 7")


def test_only_selects_cases_and_unknown_ones_are_refused(tmp_path):
    s = load(tmp_path, case("a", "x", CONTAINS) + case("b", "x", CONTAINS))
    res = runner.run_suite(s, Scripted(lambda c, r: "good"), only=["b"])
    assert [c.case.id for c in res.cases] == ["b"]
    with pytest.raises(runner.UnknownCase, match="no case named zzz; the suite has a, b"):
        runner.run_suite(s, Scripted(lambda c, r: "good"), only=["zzz"])


def test_jobs_gives_the_same_result_in_the_same_order(tmp_path):
    cases = "".join(case(f"c{i}", "x", CONTAINS) for i in range(6))
    s = load(tmp_path, cases)

    def answer(cid, run):
        return "good" if (int(cid[1:]) + run) % 3 else "bad"

    one = runner.run_suite(s, Scripted(answer), runs=3, jobs=1)
    many = runner.run_suite(s, Scripted(answer), runs=3, jobs=4)
    shape = lambda r: [(c.case.id, [x.ok for x in c.runs], [x.index for x in c.runs]) for c in r.cases]  # noqa: E731
    assert shape(one) == shape(many)


def test_seconds_and_tokens_add_up(tmp_path):
    res = runner.run_suite(load(tmp_path, case("a", "x", CONTAINS)), Scripted(lambda c, r: "good"), runs=3)
    assert res.tokens() == (30, 15)
    assert res.seconds() == pytest.approx(0.03)


def test_tokens_are_none_when_the_provider_does_not_report_them(tmp_path):
    s = load(tmp_path, case("a", "good", CONTAINS))
    from prompt_regression.providers import CmdProvider

    res = runner.run_suite(s, CmdProvider([PY, "model.py"], cwd=tmp_path))
    assert res.tokens() is None
    assert res.ok


def test_a_real_command_provider_sees_the_run_number(tmp_path):
    s = load(tmp_path, case("a", "x", CONTAINS), script=BY_RUN.replace("BAD", "{2}"))
    from prompt_regression.providers import CmdProvider

    res = runner.run_suite(s, CmdProvider([PY, "model.py"], cwd=tmp_path), runs=3)
    assert [r.ok for r in res.cases[0].runs] == [True, False, True]


def test_long_output_is_shortened_in_the_result_not_in_the_check(tmp_path):
    s = load(tmp_path, case("a", "x", '{ type = "max_chars", value = 100 }'))
    res = runner.run_suite(s, Scripted(lambda c, r: "y" * 5000))
    bad = res.cases[0].first_failure()
    assert len(bad.output) == runner.SHOW
    assert "5000 characters" in bad.checks[0].detail
