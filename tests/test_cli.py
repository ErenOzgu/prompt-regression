import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from prompt_regression import __version__
from prompt_regression.checks import SPECS
from prompt_regression.cli import main
from prompt_regression.providers import recording_key

from .conftest import BY_RUN, PY, case, write_suite
from .fakes import FakeAnthropic

C = '{ type = "contains", value = "good" }'
GOOD = case("a", "good one", C) + case("b", "good two", C)
HALF = case("a", "good one", C) + case("b", "nothing", C)


def run(argv, capsys):
    code = main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_a_passing_suite_exits_zero(tmp_path, capsys):
    code, out, err = run(["run", write_suite(tmp_path, GOOD)], capsys)
    assert (code, err) == (0, "")
    assert "PASS  a" in out


def test_a_failing_case_exits_three_and_says_why(tmp_path, capsys):
    code, out, _ = run(["run", write_suite(tmp_path, HALF)], capsys)
    assert code == 3
    assert "FAIL  b" in out
    assert "'good' is not in the output" in out


def test_formats_and_output_file(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD)
    _, md, _ = run(["run", path, "-f", "md"], capsys)
    assert md.startswith("## t")
    code, out, _ = run(["run", path, "-f", "json", "-o", tmp_path / "r.json"], capsys)
    assert (code, out) == (0, "")
    assert json.loads((tmp_path / "r.json").read_text())["ok"] is True


def test_runs_case_and_jobs(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD)
    _, out, _ = run(["run", path, "--runs", "4", "--case", "b", "--jobs", "3", "-f", "json"], capsys)
    data = json.loads(out)
    assert [(c["id"], c["runs"]) for c in data["cases"]] == [("b", 4)]


def test_junit_file(tmp_path, capsys):
    code, _, _ = run(["run", write_suite(tmp_path, HALF), "--junit", tmp_path / "j.xml"], capsys)
    root = ET.parse(tmp_path / "j.xml").getroot()
    assert (code, root.get("failures")) == (3, "1")


def test_baseline_cycle(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD)
    base = tmp_path / "base.json"
    assert run(["run", path, "--save-baseline", base], capsys)[0] == 0
    code, out, _ = run(["run", path, "--baseline", base], capsys)
    assert code == 0
    assert "no case is below its baseline" in out
    # the same suite with a model that now gets one case wrong
    worse = write_suite(tmp_path, case("a", "good one", C) + case("b", "oops", C))
    code, out, _ = run(["run", worse, "--baseline", base], capsys)
    assert code == 3
    assert "REGRESSION  b: 100% in the baseline, 0% now" in out


def test_a_regression_fails_the_run_even_when_the_case_still_meets_its_own_threshold(tmp_path, capsys):
    cases = case("a", "x", C, extra="min_pass_rate = 0.3")
    path = write_suite(tmp_path, cases, script=BY_RUN.replace("BAD", "set()"))
    base = tmp_path / "base.json"
    run(["run", path, "--runs", "3", "--save-baseline", base], capsys)
    worse = write_suite(tmp_path, cases, script=BY_RUN.replace("BAD", "{1, 2}"))
    code, out, _ = run(["run", worse, "--runs", "3", "--baseline", base], capsys)
    assert code == 3
    assert "PASS  a" in out
    assert "REGRESSION  a: 100% in the baseline, 33% now" in out
    code, _, _ = run(["run", worse, "--runs", "3", "--baseline", base, "--tolerance", "0.7"], capsys)
    assert code == 0


def test_the_prompt_can_be_swapped_on_the_command_line(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD)
    other = tmp_path / "other.txt"
    other.write_text("bad {{x}}", encoding="utf-8")
    assert run(["run", path], capsys)[0] == 0
    code, out, _ = run(["run", path, "--prompt", other], capsys)
    assert code == 0  # "good one" is still in the echoed prompt
    other.write_text("nothing", encoding="utf-8")
    assert run(["run", path, "--prompt", other], capsys)[0] == 3


def test_github_summary_is_written_only_when_asked_and_available(tmp_path, capsys, monkeypatch):
    path = write_suite(tmp_path, GOOD)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    run(["run", path], capsys)
    assert not summary.exists()
    run(["run", path, "--github-summary"], capsys)
    assert summary.read_text().startswith("## t")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY")
    assert run(["run", path, "--github-summary"], capsys)[0] == 0


@pytest.mark.parametrize(
    "extra",
    [
        ["--runs", "0"],
        ["--jobs", "0"],
        ["--tolerance", "-0.1", "--baseline", "x"],
        ["--case", "zzz"],
        ["--model", "m"],
    ],
)
def test_bad_options_exit_two_with_a_message(tmp_path, capsys, extra):
    base = tmp_path / "base.json"
    run(["run", write_suite(tmp_path, GOOD), "--save-baseline", base], capsys)
    extra = [str(base) if a == "x" else a for a in extra]
    code, out, err = run(["run", tmp_path / "suite.toml", *extra], capsys)
    assert code == 2
    assert err.startswith("prompt-regression: ")


def test_argparse_errors_exit_two(tmp_path, capsys):
    assert run(["run", "--nope"], capsys)[0] == 2
    assert run([], capsys)[0] == 2


def test_version(capsys):
    code, out, _ = run(["--version"], capsys)
    assert (code, out.strip()) == (0, f"prompt-regression {__version__}")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["run", "nope.toml"], "cannot read suite"),
        (["validate", "nope.toml"], "cannot read suite"),
    ],
)
def test_unreadable_files_exit_one(tmp_path, capsys, argv, message, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _, err = run(argv, capsys)
    assert code == 1
    assert message in err


def test_a_broken_suite_and_a_broken_baseline_exit_one(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD)
    (tmp_path / "base.json").write_text("{}", encoding="utf-8")
    code, _, err = run(["run", path, "--baseline", tmp_path / "base.json"], capsys)
    assert (code, "not a baseline" in err) == (1, True)
    bad = write_suite(tmp_path, case("a", "x", '{ type = "zzz" }'))
    code, _, err = run(["run", bad], capsys)
    assert (code, "unknown check type 'zzz'" in err) == (1, True)


def test_validate_calls_no_model(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD, provider='type = "cmd"\ncommand = ["no-such-program-xyz"]')
    code, out, _ = run(["validate", path], capsys)
    assert code == 0
    assert "ok, 2 cases and 2 checks; provider cmd; fingerprint " in out


def test_validate_prints_notes(tmp_path, capsys):
    cases = '[[case]]\nid = "a"\nvars = { x = "1", extra = "2" }\nchecks = [{ type = "not_empty" }]\n'
    _, out, _ = run(["validate", write_suite(tmp_path, cases)], capsys)
    assert "note: vars never used by the prompt: extra" in out


def test_checks_lists_every_kind(capsys):
    code, out, _ = run(["checks"], capsys)
    assert code == 0
    for kind in SPECS:
        assert kind in out
    assert "ignore_case?" in out


def test_record_then_replay(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD)
    rec = tmp_path / "rec.jsonl"
    code, out, _ = run(["record", path, "--runs", "2", "-o", rec], capsys)
    assert code == 0
    assert "recorded 4 of 4 completions" in out
    rows = [json.loads(line) for line in rec.read_text().splitlines()]
    assert [(r["case"], r["run"]) for r in rows] == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]
    assert rows[0]["key"] == recording_key("good one", "", 1)
    assert rows[0]["text"] == "good one"
    # the recording answers even when the real command is gone
    gone = write_suite(tmp_path, GOOD, provider='type = "cmd"\ncommand = ["no-such-program-xyz"]')
    assert run(["run", gone, "--runs", "2", "--replay", rec], capsys)[0] == 0
    assert run(["run", gone, "--runs", "2"], capsys)[0] == 3


def test_recordings_do_not_depend_on_jobs(tmp_path, capsys):
    path = write_suite(tmp_path, "".join(case(f"c{i}", f"good {i}", C) for i in range(5)))
    one, many = tmp_path / "one.jsonl", tmp_path / "many.jsonl"
    run(["record", path, "--runs", "3", "-o", one], capsys)
    run(["record", path, "--runs", "3", "--jobs", "4", "-o", many], capsys)
    assert one.read_text() == many.read_text()


def test_a_changed_prompt_cannot_be_replayed(tmp_path, capsys):
    path = write_suite(tmp_path, GOOD)
    rec = tmp_path / "rec.jsonl"
    run(["record", path, "-o", rec], capsys)
    changed = tmp_path / "changed.txt"
    changed.write_text("new wording {{x}}", encoding="utf-8")
    code, out, _ = run(["run", path, "--replay", rec, "--prompt", changed], capsys)
    assert code == 3
    assert "no recording for case a run 1" in out


def test_record_refuses_a_replay_suite_and_reports_incomplete_recordings(tmp_path, capsys):
    (tmp_path / "rec.jsonl").write_text("", encoding="utf-8")
    replay = write_suite(tmp_path, GOOD, provider='type = "replay"\nfile = "rec.jsonl"')
    assert run(["record", replay, "-o", tmp_path / "x.jsonl"], capsys)[0] == 2
    flaky = write_suite(tmp_path, GOOD, script="import sys\nsys.exit(1)\n")
    code, out, _ = run(["record", flaky, "-o", tmp_path / "x.jsonl"], capsys)
    assert code == 1
    assert "recorded 0 of 2 completions" in out


class TestAnthropicSuite:
    def suite(self, tmp_path, cases=GOOD):
        return write_suite(tmp_path, cases, provider='type = "anthropic"\nmodel = "model-a"')

    def test_it_needs_a_key(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        code, _, err = run(["run", self.suite(tmp_path)], capsys)
        assert code == 2
        assert "ANTHROPIC_API_KEY" in err

    def test_it_calls_the_api_and_the_model_can_be_overridden(self, tmp_path, capsys, monkeypatch):
        server = FakeAnthropic("a good answer")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        try:
            code, out, _ = run(["run", self.suite(tmp_path), "--model", "model-b", "--runs", "2"], capsys)
            models = {json.loads(r["body"])["model"] for r in server.requests}
        finally:
            server.close()
        assert code == 0
        assert len(server.requests) == 4
        assert models == {"model-b"}

    def test_compare_two_models(self, tmp_path, capsys, monkeypatch):
        server = FakeAnthropic("a good answer")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        try:
            code, out, _ = run(["compare", self.suite(tmp_path), "--model", "m1", "m2"], capsys)
            models = [json.loads(r["body"])["model"] for r in server.requests]
        finally:
            server.close()
        assert code == 0
        assert models == ["m1", "m1", "m2", "m2"]
        assert "A = m1\nB = m2" in out


class TestCompare:
    def prompts(self, tmp_path):
        a, b = tmp_path / "a.txt", tmp_path / "b.txt"
        a.write_text("good {{x}}", encoding="utf-8")
        b.write_text("poor {{x}}", encoding="utf-8")
        return a, b

    def test_a_worse_prompt_is_a_nonzero_exit(self, tmp_path, capsys):
        path = write_suite(tmp_path, case("a", "keep", C) + case("b", "keep", C))
        a, b = self.prompts(tmp_path)
        code, out, _ = run(["compare", path, "--prompt", a, b], capsys)
        assert code == 3
        assert "2 regressed, 0 improved, 0 unchanged going from A to B" in out

    def test_an_equal_or_better_prompt_is_fine(self, tmp_path, capsys):
        path = write_suite(tmp_path, case("a", "keep", C) + case("b", "keep", C))
        a, b = self.prompts(tmp_path)
        code, out, _ = run(["compare", path, "--prompt", b, a, "-f", "md"], capsys)
        assert code == 0
        assert "✅ improved" in out

    def test_exactly_one_kind_of_comparison(self, tmp_path, capsys):
        path = write_suite(tmp_path, GOOD)
        a, b = self.prompts(tmp_path)
        assert run(["compare", path], capsys)[0] == 2
        assert run(["compare", path, "--prompt", a, b, "--model", "x", "y"], capsys)[0] == 2
        assert run(["compare", path, "--model", "x", "y"], capsys)[0] == 2  # cmd suite: no models


def test_python_dash_m_works(tmp_path):
    p = subprocess.run(
        [PY, "-m", "prompt_regression", "--version"], capture_output=True, text=True, check=False
    )
    assert p.returncode == 0
    assert p.stdout.strip() == f"prompt-regression {__version__}"
    path = write_suite(tmp_path, HALF)
    p = subprocess.run(
        [PY, "-m", "prompt_regression", "run", str(path)], capture_output=True, text=True, check=False
    )
    assert p.returncode == 3
    assert sys.version_info >= (3, 11)
