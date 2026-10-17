"""``{{name}}`` placeholders in a prompt file, filled from a case's variables. A missing variable is an error."""

from __future__ import annotations

import re

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class TemplateError(Exception):
    """The prompt refers to a variable the case does not define."""


def names(template: str) -> list[str]:
    return list(dict.fromkeys(PLACEHOLDER.findall(template)))


def render(template: str, variables: dict[str, object]) -> str:
    missing = [n for n in names(template) if n not in variables]
    if missing:
        raise TemplateError(
            "the prompt uses "
            + ", ".join("{{" + n + "}}" for n in missing)
            + " but the case does not define it"
        )
    # One pass over the template: a value that itself contains {{x}} is inserted as it is, not expanded again.
    return PLACEHOLDER.sub(lambda m: str(variables[m.group(1)]), template)
