"""Tests for subtitle_translator.pricing."""

from subtitle_translator.pricing import (
    MODEL_PRICING,
    get_pricing,
    format_pricing,
    is_text_completion_model,
    _base_model,
)


def test_exact_match():
    p = get_pricing("gpt-4o-mini")
    assert p == {"input": 0.15, "output": 0.60, "cached": 0.075}


def test_dated_variant_resolves_to_base():
    # OpenAI's /v1/models returns dated ids like "gpt-4o-2024-08-06";
    # they should resolve to the base "gpt-4o" entry.
    p = get_pricing("gpt-4o-2024-08-06")
    assert p is not None
    assert p["input"] == MODEL_PRICING["gpt-4o"]["input"]
    assert p["output"] == MODEL_PRICING["gpt-4o"]["output"]


def test_dated_variant_gpt5():
    p = get_pricing("gpt-5.4-mini-2026-03-17")
    assert p == MODEL_PRICING["gpt-5.4-mini"]


def test_finetune_prefix_resolves_to_base():
    # Fine-tuned ids look like "ft:gpt-4o-mini-2024-07-18:personal::ApYrhZDU"
    p = get_pricing("ft:gpt-4o-mini-2024-07-18:personal::ApYriAlW:ckpt-step-90")
    assert p == MODEL_PRICING["gpt-4o-mini"]


def test_unknown_returns_none():
    assert get_pricing("totally-fake-model-xyz") is None
    assert get_pricing("") is None


def test_format_pricing_known():
    s = format_pricing("gpt-4o-mini")
    assert "0.15" in s and "0.6" in s and "1M" in s


def test_format_pricing_unknown():
    assert format_pricing("nonexistent") is None


def test_base_model_strips_date():
    assert _base_model("gpt-4o-2024-08-06") == "gpt-4o"
    assert _base_model("gpt-5.4-mini-2026-03-17") == "gpt-5.4-mini"
    assert _base_model("gpt-3.5-turbo") == "gpt-3.5-turbo"


def test_base_model_unwraps_finetune():
    assert _base_model("ft:gpt-4o-mini-2024-07-18:personal::ApYriAlW") == "gpt-4o-mini"


def test_text_completion_filter_keeps_chat_models():
    assert is_text_completion_model("gpt-4o-mini")
    assert is_text_completion_model("gpt-5.4")
    assert is_text_completion_model("o4-mini")
    assert is_text_completion_model("ft:gpt-4o-mini-2024-07-18:personal::xx")


def test_text_completion_filter_excludes_non_chat():
    assert not is_text_completion_model("dall-e-3")
    assert not is_text_completion_model("whisper-1")
    assert not is_text_completion_model("text-embedding-3-small")
    assert not is_text_completion_model("gpt-4o-audio-preview")
    assert not is_text_completion_model("gpt-4o-realtime-preview")
    assert not is_text_completion_model("gpt-4o-transcribe")
    assert not is_text_completion_model("tts-1")
    assert not is_text_completion_model("gpt-image-1")
    assert not is_text_completion_model("")
