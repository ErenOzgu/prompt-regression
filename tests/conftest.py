"""Helpers: write a suite to a temporary directory, and a few scripted providers."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from prompt_regression.providers import Completion, ProviderError

PY = sys.executable

ECHO = "import sys\nsys.stdout.write(sys.stdin.read())\n"
BY_RUN = textwrap.dedent(
    """
    import os, sys
    sys.stdin.read()
    run = int(os.environ["PROMPT_REGRESSION_RUN"])
    sys.stdout.write("bad" if run in BAD else "good")
    """
)


def write_suite(
    tmp_path: Path,
    cases: str,
    prompt: str = "{{x}}",
    provider: str | None = None,
    suite_extra: str = "",
    script: str = ECHO,
) -> Path:
    """A suite whose model is a script that, by default, just echoes the prompt back."""
    (tmp_path / "model.py").write_text(script, encoding="utf-8")
    (tmp_path / "prompt.txt").write_text(prompt, encoding="utf-8")
    provider = provider or f'type = "cmd"\ncommand = [{PY!r}, "model.py"]'.replace("'", '"')
    path = tmp_path / "suite.toml"
    path.write_text(
        f'[suite]\nname = "t"\nprompt = "prompt.txt"\n{suite_extra}\n[provider]\n{provider}\n\n{cases}',
        encoding="utf-8",
    )
    return path


def case(cid: str, x: str, *checks: str, extra: str = "") -> str:
    body = ",\n  ".join(checks)
    return f"[[case]]\nid = {cid!r}\n{extra}\nvars = {{ x = {x!r} }}\nchecks = [\n  {body},\n]\n".replace(
        "'", '"'
    )


class Scripted:
    """A provider that answers from a function of (case id, run)."""

    def __init__(self, answer) -> None:  # noqa: ANN001
        self.answer = answer
        self.name = "scripted"
        self.calls: list[tuple[str, str, str, int]] = []

    def complete(self, prompt: str, system: str, case_id: str, run: int) -> Completion:
        self.calls.append((prompt, system, case_id, run))
        got = self.answer(case_id, run)
        if isinstance(got, Exception):
            raise got
        return Completion(got, 0.01, 10, 5)


@pytest.fixture
def scripted():
    return Scripted


@pytest.fixture
def broken():
    return ProviderError("boom")
