import pytest

from prompt_regression import template


def test_names_in_order_without_repeats():
    assert template.names("{{b}} {{a}} {{ b }}") == ["b", "a"]


def test_render_fills_variables_and_allows_spaces():
    assert template.render("Hi {{ name }}, {{name}}!", {"name": "Ada"}) == "Hi Ada, Ada!"


def test_a_missing_variable_is_an_error_naming_it():
    with pytest.raises(template.TemplateError, match=r"\{\{who\}\}"):
        template.render("{{who}}", {})


def test_values_are_not_expanded_again():
    # a ticket that happens to contain {{secret}} must reach the model as written
    assert template.render("{{a}}", {"a": "{{b}}", "b": "x"}) == "{{b}}"


def test_non_strings_are_written_with_str():
    assert template.render("{{n}} {{ok}}", {"n": 3, "ok": True}) == "3 True"


def test_text_without_placeholders_is_unchanged():
    assert template.render("{ not a placeholder } {{1x}}", {}) == "{ not a placeholder } {{1x}}"
