import pytest

from failtriage.models import Category
from failtriage.prompts import PROMPT_VERSION, load_prompt


def test_the_default_prompt_is_loaded_from_its_versioned_file() -> None:
    prompt = load_prompt()

    assert prompt.version == PROMPT_VERSION == "classify-v1"
    assert prompt.text.strip()


def test_the_prompt_names_every_category() -> None:
    text = load_prompt().text

    assert all(category.value in text for category in Category)


def test_the_prompt_asks_for_a_reason_when_the_model_disagrees() -> None:
    assert "disagreement_reason" in load_prompt().text


def test_an_unknown_prompt_version_is_an_error() -> None:
    with pytest.raises(ValueError, match="classify-v0"):
        load_prompt("classify-v0")
