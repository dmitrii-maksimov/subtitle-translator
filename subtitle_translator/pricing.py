"""Static OpenAI API pricing table for chat-capable models.

The OpenAI /v1/models endpoint does NOT return pricing, so we keep a
locally-maintained snapshot here and join it to the model list when we
show the dropdown.

Values are USD per 1 million tokens. `cached` is the cached-input rate
when OpenAI publishes one; None otherwise.

Refresh periodically from https://openai.com/api/pricing/ and bump
PRICING_DATE.
"""

from __future__ import annotations

import re
from typing import Optional, TypedDict


PRICING_DATE = "2026-04-15"
PRICING_SOURCE = "https://openai.com/api/pricing/"


class ModelPrice(TypedDict):
    input: float
    output: float
    cached: Optional[float]


# Keys are base model ids as accepted by the API (before any date suffix
# like -2025-04-14). Dated variants resolve via `_strip_date_suffix`.
MODEL_PRICING: dict[str, ModelPrice] = {
    "gpt-5.4": {"input": 2.50, "output": 15.00, "cached": 0.25},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "cached": 0.075},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25, "cached": 0.02},
    "gpt-5.4-pro": {"input": 30.00, "output": 180.00, "cached": None},
    "gpt-5.3-chat": {"input": 1.75, "output": 14.00, "cached": 0.175},
    "gpt-5.2": {"input": 0.875, "output": 7.00, "cached": 0.175},
    "gpt-5.2-chat": {"input": 1.75, "output": 14.00, "cached": 0.175},
    "gpt-5.2-pro": {"input": 10.50, "output": 84.00, "cached": None},
    "gpt-5.1": {"input": 0.625, "output": 5.00, "cached": 0.125},
    "gpt-5.1-chat": {"input": 0.625, "output": 5.00, "cached": 0.125},
    "gpt-5": {"input": 0.625, "output": 5.00, "cached": 0.125},
    "gpt-5-chat": {"input": 1.25, "output": 10.00, "cached": 0.125},
    "gpt-5-mini": {"input": 0.125, "output": 1.00, "cached": 0.025},
    "gpt-5-nano": {"input": 0.05, "output": 0.40, "cached": 0.005},
    "gpt-4.1": {"input": 2.00, "output": 8.00, "cached": 0.50},
    "gpt-4.1-mini": {"input": 0.20, "output": 0.80, "cached": 0.10},
    "gpt-4.1-nano": {"input": 0.05, "output": 0.20, "cached": 0.025},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cached": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached": 0.075},
    "o3": {"input": 2.00, "output": 8.00, "cached": 0.50},
    "o3-mini": {"input": 1.10, "output": 4.40, "cached": 0.55},
    "o4-mini": {"input": 1.10, "output": 4.40, "cached": 0.275},
    "o1": {"input": 15.00, "output": 60.00, "cached": 7.50},
    "o1-mini": {"input": 0.55, "output": 2.20, "cached": 0.55},
    "gpt-4-turbo": {"input": 5.00, "output": 15.00, "cached": None},
    "gpt-4": {"input": 30.00, "output": 60.00, "cached": None},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50, "cached": None},
    "gpt-3.5-turbo-16k": {"input": 3.00, "output": 4.00, "cached": None},
}


_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_FINETUNE_PREFIX = re.compile(r"^ft:([^:]+):")


def _strip_date_suffix(model_id: str) -> str:
    """Remove trailing -YYYY-MM-DD from a model id."""
    return _DATE_SUFFIX.sub("", model_id)


def _base_model(model_id: str) -> str:
    """Resolve a model id to the key we look up in MODEL_PRICING.

    Handles fine-tuned ids (``ft:gpt-4o-mini-2024-07-18:personal::xxx``
    → ``gpt-4o-mini``) and dated variants."""
    m = _FINETUNE_PREFIX.match(model_id)
    if m:
        base = m.group(1)
    else:
        base = model_id
    return _strip_date_suffix(base)


def get_pricing(model_id: str) -> Optional[ModelPrice]:
    """Return pricing for a model id, or None if unknown.

    Resolves dated and fine-tuned variants back to their base model."""
    if not model_id:
        return None
    direct = MODEL_PRICING.get(model_id)
    if direct is not None:
        return direct
    return MODEL_PRICING.get(_base_model(model_id))


def format_pricing(model_id: str) -> Optional[str]:
    """Short human-readable price tag. ``None`` if no pricing is known."""
    p = get_pricing(model_id)
    if p is None:
        return None
    return f"${p['input']:.3g} in / ${p['output']:.3g} out per 1M tok"


def is_text_completion_model(model_id: str) -> bool:
    """Filter helper: exclude obviously non-chat models (image/audio/etc).

    Used when populating the dropdown from /v1/models — it returns every
    model the account can access, including DALL·E, Whisper, TTS, etc."""
    if not model_id:
        return False
    if model_id.startswith(("ft:",)):
        return True  # fine-tuned chat models start with ft:
    bad = (
        "image",
        "audio",
        "realtime",
        "tts",
        "whisper",
        "transcribe",
        "embedding",
        "moderation",
        "search-preview",
        "dall-e",
        "babbage",
        "davinci",
        "codex",
    )
    low = model_id.lower()
    return not any(b in low for b in bad)
