"""The README makes claims about the code and quotes its output. These keep both honest."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from prompt_regression import suite as suite_mod
from prompt_regression.checks import DOCS, SPECS
from prompt_regression.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
EXAMPLES = ROOT / "examples"


def table() -> str:
    rows = ["| Check | Parameters | Passes when |", "|---|---|---|"]
    for kind, (_, params) in SPECS.items():
        shown = (
            ", ".join(f"`{k}`" + ("" if req else " (optional)") for k, (_, req) in params.items()) or "none"
        )
        rows.append(f"| `{kind}` | {shown} | {DOCS[kind]} |")
    return "\n".join(rows)


def test_every_check_has_a_description():
    assert set(DOCS) == set(SPECS)
    assert all(DOCS[k].strip() for k in DOCS)


def test_the_checks_table_is_what_the_code_says():
    m = re.search(r"<!-- checks:start -->\n(.*?)\n<!-- checks:end -->", README, re.DOTALL)
    assert m, "the markers are missing"
    assert m.group(1) == table()


def options() -> set[str]:
    out = set()
    parser = build_parser()
    subs = next(a for a in parser._actions if a.choices)  # noqa: SLF001
    for sub in [parser, *subs.choices.values()]:
        for action in sub._actions:  # noqa: SLF001
            out.update(o for o in action.option_strings if o not in ("-h", "--help"))
    return out


def test_every_command_and_option_is_in_the_readme():
    missing = [o for o in sorted(options()) if f"`{o}" not in README and f"`-f`, `{o}`" not in README]
    assert not missing, f"not documented: {missing}"
    subs = next(a for a in build_parser()._actions if a.choices)  # noqa: SLF001
    for name in subs.choices:
        assert f"| `{name}" in README, f"command {name} is not in the command table"


def test_every_documented_option_exists():
    documented = set(re.findall(r"^\| `(-{1,2}[a-z-]+)", README, re.MULTILINE))
    documented |= set(re.findall(r"`-f`, `(--[a-z-]+)`", README))
    assert documented
    assert documented <= options() | {"--version"}


def read(name: str) -> str:
    return (EXAMPLES / "reports" / name).read_text(encoding="utf-8").rstrip("\n")


def test_the_quoted_output_is_the_committed_output():
    assert read("run-v1.txt") in README
    assert "\n".join(read("run-v2-vs-baseline.txt").splitlines()[-8:]) in README
    assert read("compare-v1-v3.txt") in README


def test_the_toml_example_in_the_readme_is_a_valid_suite(tmp_path):
    block = re.search(r"```toml\n(.*?)```", README, re.DOTALL).group(1)
    shutil.copytree(EXAMPLES, tmp_path / "ex")
    path = tmp_path / "ex" / "from-readme.toml"
    path.write_text(block, encoding="utf-8")
    s = suite_mod.load(path)
    assert s.cases[0].id == "refund"
    assert s.runs == 3


def test_the_example_suite_is_valid_and_uses_most_check_types():
    s = suite_mod.load(EXAMPLES / "triage.toml")
    used = {c["type"] for case in s.cases for c in case.checks}
    assert {
        "json_valid",
        "json_types",
        "json_enum",
        "json_has",
        "numbers_in_input",
        "max_chars",
        "starts_with",
        "not_contains",
    } <= used
    assert len(s.cases) == 6


def test_headings_the_readme_links_to_exist():
    headings = {
        re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
        for h in re.findall(r"^#+ (.*)$", README, re.MULTILINE)
    }
    for anchor in re.findall(r"\]\(#([a-z0-9-]+)\)", README):
        assert anchor in headings, anchor


def test_images_the_readme_shows_exist():
    for path in re.findall(r'src="([^"]+)"', README):
        assert (ROOT / path).is_file(), path


def test_the_example_reports_are_current():
    p = subprocess.run(
        [sys.executable, str(EXAMPLES / "make_reports.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0, p.stdout + p.stderr


def test_the_example_prompts_behave_as_the_readme_says():
    from prompt_regression.cli import main

    base = str(EXAMPLES / "baseline.json")
    suite = str(EXAMPLES / "triage.toml")
    assert main(["run", suite, "--baseline", base, "-o", "/dev/null"]) == 0
    assert (
        main(
            [
                "run",
                suite,
                "--baseline",
                base,
                "--prompt",
                str(EXAMPLES / "prompts/triage_v2.txt"),
                "-o",
                "/dev/null",
            ]
        )
        == 3
    )


@pytest.mark.parametrize("n", ["v1", "v2", "v3"])
def test_each_example_prompt_exists_and_uses_the_ticket_variable(n):
    text = (EXAMPLES / "prompts" / f"triage_{n}.txt").read_text(encoding="utf-8")
    assert "{{ticket}}" in text
