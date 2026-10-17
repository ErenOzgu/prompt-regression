import pytest

from prompt_regression import suite as suite_mod
from prompt_regression.suite import SuiteError

from .conftest import PY, case, write_suite

OK = case("a", "hello", '{ type = "contains", value = "hello" }')


def test_a_good_suite_loads(tmp_path):
    s = suite_mod.load(write_suite(tmp_path, OK, suite_extra="runs = 4\nmin_pass_rate = 0.5"))
    assert s.name == "t"
    assert s.runs == 4
    assert [c.id for c in s.cases] == ["a"]
    assert s.threshold(s.cases[0]) == 0.5
    assert s.prompt == "{{x}}"


def test_a_case_can_set_its_own_threshold(tmp_path):
    cases = OK + case("b", "x", '{ type = "not_empty" }', extra="min_pass_rate = 0.8")
    s = suite_mod.load(write_suite(tmp_path, cases))
    assert [s.threshold(c) for c in s.cases] == [1.0, 0.8]


def test_the_prompt_can_be_replaced_for_a_comparison(tmp_path):
    path = write_suite(tmp_path, OK)
    other = tmp_path / "other.txt"
    other.write_text("other {{x}}", encoding="utf-8")
    s = suite_mod.load(path, other)
    assert s.prompt == "other {{x}}"
    assert s.prompt_path == other


def test_the_fingerprint_follows_the_prompt_the_system_text_and_the_provider(tmp_path):
    a = suite_mod.load(write_suite(tmp_path, OK)).fingerprint()
    assert a == suite_mod.load(write_suite(tmp_path, OK)).fingerprint()
    assert a != suite_mod.load(write_suite(tmp_path, OK, prompt="changed {{x}}")).fingerprint()
    assert a != suite_mod.load(write_suite(tmp_path, OK, suite_extra='system = "be brief"')).fingerprint()
    other_provider = f'type = "cmd"\ncommand = ["{PY}", "model.py", "--more"]'
    assert a != suite_mod.load(write_suite(tmp_path, OK, provider=other_provider)).fingerprint()


def test_the_cases_do_not_change_the_fingerprint(tmp_path):
    a = suite_mod.load(write_suite(tmp_path, OK)).fingerprint()
    more = OK + case("b", "x", '{ type = "not_empty" }')
    assert a == suite_mod.load(write_suite(tmp_path, more)).fingerprint()


def test_system_text_can_come_from_a_file(tmp_path):
    (tmp_path / "sys.txt").write_text("You are terse.", encoding="utf-8")
    s = suite_mod.load(write_suite(tmp_path, OK, suite_extra='system_file = "sys.txt"'))
    assert s.system == "You are terse."


def test_unused_variables_are_noted_not_refused(tmp_path):
    cases = '[[case]]\nid = "a"\nvars = { x = "1", extra = "2" }\nchecks = [{ type = "not_empty" }]\n'
    s = suite_mod.load(write_suite(tmp_path, cases))
    assert any("extra" in n for n in s.notes)


@pytest.mark.parametrize(
    ("cases", "kwargs", "message"),
    [
        ("", {}, "needs at least one"),
        (OK + OK, {}, "the id is used twice"),
        (
            '[[case]]\nid = "bad id!"\nvars = {x="1"}\nchecks=[{type="not_empty"}]',
            {},
            "needs an id of letters",
        ),
        ('[[case]]\nvars = {x="1"}\nchecks=[{type="not_empty"}]', {}, "case 1 needs an id"),
        (
            '[[case]]\nid = "a"\nvars = {}\nchecks=[{type="not_empty"}]',
            {},
            r"uses \{\{x\}\} but vars does not define it",
        ),
        ('[[case]]\nid = "a"\nvars = {x="1"}\n', {}, "needs a list of checks"),
        ('[[case]]\nid = "a"\nvars = {x="1"}\nchecks=[]', {}, "needs a list of checks"),
        ('[[case]]\nid = "a"\nvars = {x="1"}\nchecks=["nope"]', {}, "check 1 must be a table"),
        ('[[case]]\nid = "a"\nvars = {x="1"}\nchecks=[{type="zzz"}]', {}, "unknown check type 'zzz'"),
        (
            '[[case]]\nid = "a"\nvars = {x="1"}\nchecks=[{type="not_empty"}]\ncolour = 1',
            {},
            "no setting 'colour'",
        ),
        ('[[case]]\nid = "a"\nvars = 3\nchecks=[{type="not_empty"}]', {}, "vars must be a table"),
        (OK, {"suite_extra": "runs = 0"}, "runs must be a whole number"),
        (OK, {"suite_extra": "runs = 1.5"}, "runs must be a whole number"),
        (OK, {"suite_extra": "min_pass_rate = 0"}, "min_pass_rate must be a number above 0"),
        (OK, {"suite_extra": "min_pass_rate = 1.5"}, "min_pass_rate must be a number above 0"),
        (OK, {"suite_extra": "colour = 1"}, "has no setting 'colour'"),
        (OK, {"suite_extra": 'system = "a"\nsystem_file = "b"'}, "either system or system_file"),
        (OK, {"suite_extra": 'system_file = "missing.txt"'}, "cannot read system prompt"),
        (OK, {"provider": 'type = "carrier-pigeon"'}, "type = one of"),
        (OK, {"provider": 'type = "cmd"'}, "command must be a non-empty list"),
        (OK, {"provider": 'type = "cmd"\ncommand = "echo hi"'}, "no shell is used"),
        (OK, {"provider": 'type = "cmd"\ncommand = ["x"]\ntimeout = 0'}, "timeout must be a positive"),
        (OK, {"provider": 'type = "cmd"\ncommand = ["x"]\nmodel = "m"'}, "has no setting 'model'"),
        (OK, {"provider": 'type = "anthropic"'}, "needs model"),
        (OK, {"provider": 'type = "anthropic"\nmodel = "m"\nmax_tokens = 0'}, "max_tokens"),
        (OK, {"provider": 'type = "anthropic"\nmodel = "m"\ntemperature = 2'}, "temperature must be between"),
        (OK, {"provider": 'type = "replay"'}, "needs file"),
    ],
)
def test_mistakes_are_refused_with_a_message_that_names_them(tmp_path, cases, kwargs, message):
    with pytest.raises(SuiteError, match=message):
        suite_mod.load(write_suite(tmp_path, cases, **kwargs))


def test_the_message_names_the_file_and_the_case(tmp_path):
    bad = case("first", "a", '{ type = "not_empty" }') + case("second", "a", '{ type = "contains" }')
    path = write_suite(tmp_path, bad)
    with pytest.raises(SuiteError) as err:
        suite_mod.load(path)
    assert str(path) in str(err.value)
    assert "case second, check 1" in str(err.value)
    assert "contains needs 'value'" in str(err.value)


def test_toml_errors_and_missing_files_are_suite_errors(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[suite\nname=", encoding="utf-8")
    with pytest.raises(SuiteError, match="not valid TOML"):
        suite_mod.load(bad)
    with pytest.raises(SuiteError, match="cannot read suite"):
        suite_mod.load(tmp_path / "nope.toml")
    with pytest.raises(SuiteError, match="cannot read prompt"):
        suite_mod.load(write_suite(tmp_path, OK), tmp_path / "nope.txt")


def test_unknown_sections_and_a_missing_suite_section(tmp_path):
    path = write_suite(tmp_path, OK)
    path.write_text(path.read_text() + "\n[extras]\na = 1\n", encoding="utf-8")
    with pytest.raises(SuiteError, match="unknown top-level section 'extras'"):
        suite_mod.load(path)
    path.write_text('[provider]\ntype = "replay"\nfile = "x"\n', encoding="utf-8")
    with pytest.raises(SuiteError, match=r"needs a \[suite\] section"):
        suite_mod.load(path)
