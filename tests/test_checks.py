import sys

import pytest

from prompt_regression import checks
from prompt_regression.checks import CheckError, Context

CTX = Context({"ticket": "order 4411 costs 1,250.50 dollars"}, "c1")


def go(spec, output, ctx=CTX):
    return checks.run(spec, output, ctx)


# (spec, output that passes, output that fails)
CASES = [
    ({"type": "contains", "value": "refund"}, "your refund is on its way", "nothing here"),
    ({"type": "contains", "value": "REFUND", "ignore_case": True}, "a refund", "a rebate"),
    ({"type": "not_contains", "value": "sorry"}, "all fine", "we are sorry"),
    ({"type": "not_contains", "value": "SORRY", "ignore_case": True}, "all fine", "so Sorry"),
    ({"type": "equals", "value": "yes"}, "  yes\n", "yes!"),
    ({"type": "equals", "value": "yes", "strip": False}, "yes", " yes"),
    ({"type": "equals", "value": "Yes", "ignore_case": True}, "yES", "no"),
    ({"type": "starts_with", "value": "{"}, "\n  {}", "x {"),
    ({"type": "matches", "pattern": r"^\d{3}$"}, "123", "12a"),
    ({"type": "matches", "pattern": "hello", "flags": "i"}, "HELLO there", "bye"),
    ({"type": "not_matches", "pattern": r"\bTODO\b"}, "done", "TODO: fix"),
    ({"type": "one_of", "values": ["a", "b"]}, " b \n", "c"),
    ({"type": "one_of", "values": ["a"], "ignore_case": True}, "A", "b"),
    ({"type": "not_empty"}, "x", "  \n"),
    ({"type": "json_valid"}, '{"a": 1}', "{a: 1}"),
    ({"type": "json_valid", "allow_fences": True}, '```json\n{"a": 1}\n```', "```json\n{oops}\n```"),
    ({"type": "json_has", "keys": ["a", "b.c"]}, '{"a": 1, "b": {"c": 2}}', '{"a": 1, "b": {}}'),
    ({"type": "json_has", "keys": ["items.1"]}, '{"items": [1, 2]}', '{"items": [1]}'),
    (
        {"type": "json_types", "schema": {"n": "number", "s": "string"}},
        '{"n": 1, "s": "x"}',
        '{"n": "1", "s": "x"}',
    ),
    ({"type": "json_types", "schema": {"n": "integer"}}, '{"n": 1}', '{"n": 1.5}'),
    ({"type": "json_types", "schema": {"n": "number"}}, '{"n": 2}', '{"n": true}'),
    (
        {"type": "json_types", "schema": {"x": "null", "l": "array", "o": "object", "b": "boolean"}},
        '{"x": null, "l": [], "o": {}, "b": false}',
        '{"x": 0, "l": [], "o": {}, "b": false}',
    ),
    ({"type": "json_enum", "path": "k", "values": ["a", "b"]}, '{"k": "a"}', '{"k": "z"}'),
    ({"type": "json_enum", "path": "k", "values": [1, 2]}, '{"k": 2}', '{"k": 3}'),
    ({"type": "max_chars", "value": 5}, "abcde", "abcdef"),
    ({"type": "min_chars", "value": 3}, "abc", "ab"),
    ({"type": "max_words", "value": 2}, "one two", "one two three"),
    ({"type": "min_words", "value": 2}, "one two", "one"),
    ({"type": "max_lines", "value": 2}, "a\nb\n\n", "a\nb\nc"),
    ({"type": "numbers_in_input"}, "it costs 4411 and 1,250.50", "it costs 4412"),
    ({"type": "numbers_in_input", "allow": [7]}, "7 things", "8 things"),
]


@pytest.mark.parametrize(
    ("spec", "good", "bad"), CASES, ids=[f"{c[0]['type']}-{i}" for i, c in enumerate(CASES)]
)
def test_each_check_passes_and_fails_and_says_why(spec, good, bad):
    checks.validate(spec, "t")
    assert go(spec, good).ok, go(spec, good).detail
    failed = go(spec, bad)
    assert not failed.ok
    assert failed.detail, "a failing check must say why"


def test_not_contains_notices_text_at_the_very_start():
    assert not go({"type": "not_contains", "value": "sorry"}, "sorry about that").ok
    assert go({"type": "not_contains", "value": "sorry"}, "").ok


def test_every_check_type_is_covered_here():
    assert {c[0]["type"] for c in CASES} | {"cmd"} == set(checks.SPECS)


def test_a_failure_detail_for_json_names_the_problem():
    assert "not JSON" in go({"type": "json_has", "keys": ["a"]}, "nope").detail
    assert "k is missing" in go({"type": "json_enum", "path": "k", "values": ["a"]}, "{}").detail
    assert (
        "n is string, not number"
        in go({"type": "json_types", "schema": {"n": "number"}}, '{"n": "1"}').detail
    )


def test_json_with_a_fence_is_refused_unless_allowed():
    fenced = '```json\n{"a": 1}\n```'
    assert not go({"type": "json_valid"}, fenced).ok
    assert go({"type": "json_valid", "allow_fences": True}, fenced).ok


def test_numbers_in_input_ignores_digits_inside_words():
    assert go({"type": "numbers_in_input"}, "sha256 and utf8 and v2x").ok


def test_numbers_in_input_reads_every_variable():
    ctx = Context({"a": "has 12", "b": "and 34"}, "c")
    assert go({"type": "numbers_in_input"}, "12 34", ctx).ok
    assert not go({"type": "numbers_in_input"}, "12 35", ctx).ok


class TestCmd:
    def test_exit_code_decides(self):
        ok = {
            "type": "cmd",
            "command": [sys.executable, "-c", "import sys; sys.exit(0 if 'x' in sys.stdin.read() else 1)"],
        }
        assert go(ok, "xyz").ok
        out = go(ok, "abc")
        assert not out.ok
        assert out.detail.startswith("exit 1")

    def test_stderr_becomes_the_reason(self):
        spec = {
            "type": "cmd",
            "command": [sys.executable, "-c", "import sys; sys.stderr.write('too long\\n'); sys.exit(2)"],
        }
        assert go(spec, "").detail == "exit 2: too long"

    def test_the_case_id_is_in_the_environment(self):
        code = "import os, sys; sys.exit(0 if os.environ['PROMPT_REGRESSION_CASE'] == 'c1' else 1)"
        assert go({"type": "cmd", "command": [sys.executable, "-c", code]}, "").ok

    def test_a_missing_program_fails_the_check_and_nothing_else(self):
        out = go({"type": "cmd", "command": ["definitely-not-a-program-xyz"]}, "")
        assert not out.ok
        assert "cannot run" in out.detail

    def test_a_slow_program_times_out(self):
        spec = {"type": "cmd", "command": [sys.executable, "-c", "import time; time.sleep(5)"], "timeout": 1}
        out = go(spec, "")
        assert not out.ok
        assert "longer than 1 s" in out.detail

    def test_no_shell_is_involved(self):
        spec = {"type": "cmd", "command": ["echo", "hi; touch /tmp/prompt-regression-should-not-exist"]}
        assert go(spec, "").ok
        import os

        assert not os.path.exists("/tmp/prompt-regression-should-not-exist")


class TestValidate:
    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ({"type": "nope"}, "unknown check type 'nope'"),
            ({}, "unknown check type None"),
            ({"type": "contains"}, "contains needs 'value'"),
            ({"type": "contains", "value": "x", "colour": 1}, "no parameter 'colour'"),
            ({"type": "contains", "value": 3}, "contains.value must be a str, not int"),
            ({"type": "max_chars", "value": "3"}, "max_chars.value must be a int, not str"),
            ({"type": "max_chars", "value": True}, "must be a int, not bool"),
            ({"type": "matches", "pattern": "("}, "bad regular expression"),
            ({"type": "matches", "pattern": "x", "flags": "q"}, "unknown regex flag 'q'"),
            ({"type": "json_types", "schema": {"a": "text"}}, "unknown types"),
            ({"type": "one_of", "values": [1]}, "must be strings"),
            ({"type": "json_enum", "path": "a", "values": [[1]]}, "plain strings or numbers"),
            ({"type": "cmd", "command": []}, "non-empty list of strings"),
            ({"type": "cmd", "command": [1]}, "non-empty list of strings"),
        ],
    )
    def test_a_badly_written_check_is_refused_before_anything_runs(self, spec, message):
        with pytest.raises(CheckError, match=message):
            checks.validate(spec, "case x")

    def test_the_message_says_where(self):
        with pytest.raises(CheckError, match="^case x, check 2: "):
            checks.validate({"type": "nope"}, "case x, check 2")


class TestLabel:
    def test_short_names_for_reports(self):
        assert checks.label({"type": "not_empty"}) == "not_empty"
        assert checks.label({"type": "contains", "value": "refund"}) == "contains 'refund'"
        assert checks.label({"type": "json_has", "keys": ["a", "b"]}) == 'json_has ["a", "b"]'
        assert checks.label({"type": "json_enum", "path": "k", "values": ["a"]}) == 'json_enum k in ["a"]'

    def test_long_values_are_cut(self):
        assert len(checks.label({"type": "contains", "value": "x" * 500})) < 60
