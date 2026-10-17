"""Regenerates (or, with --check, verifies) the reports in examples/reports and the baseline.

The README quotes these files, and CI runs `python examples/make_reports.py --check`, so what the README
shows is what the tool prints.
"""

import contextlib
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from prompt_regression.cli import main  # noqa: E402

SUITE = str(HERE / "triage.toml")
V = {n: str(HERE / "prompts" / f"triage_{n}.txt") for n in ("v1", "v2", "v3")}

# (file, argv, expected exit code)
REPORTS = [
    ("reports/run-v1.txt", ["run", SUITE], 0),
    ("reports/run-v1-vs-baseline.txt", ["run", SUITE, "--baseline", str(HERE / "baseline.json")], 0),
    (
        "reports/run-v2-vs-baseline.txt",
        ["run", SUITE, "--prompt", V["v2"], "--baseline", str(HERE / "baseline.json")],
        3,
    ),
    ("reports/compare-v1-v3.txt", ["compare", SUITE, "--prompt", V["v1"], V["v3"]], 3),
    ("reports/run-v1.md", ["run", SUITE, "-f", "md"], 0),
]


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue().replace(str(HERE) + "/", "")


def build() -> dict[str, str]:
    code, _ = run(["run", SUITE, "--save-baseline", str(HERE / "baseline.json")])
    files = {"baseline.json": (HERE / "baseline.json").read_text(encoding="utf-8")}
    assert code == 0, "the v1 prompt is supposed to pass"
    for name, argv, expected in REPORTS:
        code, text = run(argv)
        assert code == expected, f"{name}: exit {code}, expected {expected}"
        files[name] = text
    return files


def main_() -> int:
    check = "--check" in sys.argv[1:]
    old = {n: (HERE / n).read_text(encoding="utf-8") for n in ["baseline.json"] if (HERE / n).exists()}
    files = build()
    bad = []
    for name, text in files.items():
        path = HERE / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                bad.append(name)
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if check:
        for name, text in old.items():  # build() rewrote the baseline; put the committed one back
            (HERE / name).write_text(text, encoding="utf-8")
        if bad:
            print("out of date: " + ", ".join(bad) + " (run python examples/make_reports.py)")
            return 1
        print(f"{len(files)} files are current")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
