from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path


DEFAULT_MODEL_PRICING = {
    "inputCostPer1M": None,
    "outputCostPer1M": None,
    "cacheReadCostPer1M": 0.0,
    "cacheWriteCostPer1M": 0.0,
}

# Base defaults are seeded from GitHub's Copilot "Models and pricing" table.
# Source: https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing
# Last verified: 2026-06-09.
# For models with prompt-length tiers, the tracker uses the published default
# per-token rates because session telemetry does not include prompt-length
# detail needed to price long-context tiers precisely.
PUBLIC_MODEL_PRICING = {
    "gpt-5.5": {
        "inputCostPer1M": 5.0,
        "outputCostPer1M": 30.0,
        "cacheReadCostPer1M": 0.5,
        "cacheWriteCostPer1M": 5.0,
    },
    "gpt-5.4": {
        "inputCostPer1M": 2.5,
        "outputCostPer1M": 15.0,
        "cacheReadCostPer1M": 0.25,
        "cacheWriteCostPer1M": 2.5,
    },
    "gpt-5.4-mini": {
        "inputCostPer1M": 0.75,
        "outputCostPer1M": 4.5,
        "cacheReadCostPer1M": 0.075,
        "cacheWriteCostPer1M": 0.75,
    },
    "gpt-5.4-nano": {
        "inputCostPer1M": 0.2,
        "outputCostPer1M": 1.25,
        "cacheReadCostPer1M": 0.02,
        "cacheWriteCostPer1M": 0.2,
    },
    "gpt-5.3-codex": {
        "inputCostPer1M": 1.75,
        "outputCostPer1M": 14.0,
        "cacheReadCostPer1M": 0.175,
        "cacheWriteCostPer1M": 1.75,
    },
    "gpt-5.2": {
        "inputCostPer1M": 1.75,
        "outputCostPer1M": 14.0,
        "cacheReadCostPer1M": 0.175,
        "cacheWriteCostPer1M": 1.75,
    },
    "gpt-5.2-codex": {
        "inputCostPer1M": 1.75,
        "outputCostPer1M": 14.0,
        "cacheReadCostPer1M": 0.175,
        "cacheWriteCostPer1M": 1.75,
    },
    "gpt-5-mini": {
        "inputCostPer1M": 0.25,
        "outputCostPer1M": 2.0,
        "cacheReadCostPer1M": 0.025,
        "cacheWriteCostPer1M": 0.25,
    },
    "gpt-4.1": {
        "inputCostPer1M": 2.0,
        "outputCostPer1M": 8.0,
        "cacheReadCostPer1M": 0.5,
        "cacheWriteCostPer1M": 2.0,
    },
    "gpt-4o": {
        "inputCostPer1M": 2.5,
        "outputCostPer1M": 10.0,
        "cacheReadCostPer1M": 1.25,
        "cacheWriteCostPer1M": 2.5,
    },
    "claude-opus-4.6": {
        "inputCostPer1M": 5.0,
        "outputCostPer1M": 25.0,
        "cacheReadCostPer1M": 0.5,
        "cacheWriteCostPer1M": 6.25,
    },
    "claude-opus-4.7": {
        "inputCostPer1M": 5.0,
        "outputCostPer1M": 25.0,
        "cacheReadCostPer1M": 0.5,
        "cacheWriteCostPer1M": 6.25,
    },
    "claude-opus-4.8": {
        "inputCostPer1M": 5.0,
        "outputCostPer1M": 25.0,
        "cacheReadCostPer1M": 0.5,
        "cacheWriteCostPer1M": 6.25,
    },
    "claude-opus-4.5": {
        "inputCostPer1M": 5.0,
        "outputCostPer1M": 25.0,
        "cacheReadCostPer1M": 0.5,
        "cacheWriteCostPer1M": 6.25,
    },
    "claude-sonnet-4.6": {
        "inputCostPer1M": 3.0,
        "outputCostPer1M": 15.0,
        "cacheReadCostPer1M": 0.3,
        "cacheWriteCostPer1M": 3.75,
    },
    "claude-sonnet-4.5": {
        "inputCostPer1M": 3.0,
        "outputCostPer1M": 15.0,
        "cacheReadCostPer1M": 0.3,
        "cacheWriteCostPer1M": 3.75,
    },
    "claude-sonnet-4": {
        "inputCostPer1M": 3.0,
        "outputCostPer1M": 15.0,
        "cacheReadCostPer1M": 0.3,
        "cacheWriteCostPer1M": 3.75,
    },
    "claude-haiku-4.5": {
        "inputCostPer1M": 1.0,
        "outputCostPer1M": 5.0,
        "cacheReadCostPer1M": 0.1,
        "cacheWriteCostPer1M": 1.25,
    },
    "gemini-3-pro-preview": {
        "inputCostPer1M": 2.0,
        "outputCostPer1M": 12.0,
        "cacheReadCostPer1M": 0.2,
        "cacheWriteCostPer1M": 2.0,
    },
    "gemini-3.1-pro-preview": {
        "inputCostPer1M": 2.0,
        "outputCostPer1M": 12.0,
        "cacheReadCostPer1M": 0.2,
        "cacheWriteCostPer1M": 2.0,
    },
    "gemini-3.5-flash": {
        "inputCostPer1M": 1.5,
        "outputCostPer1M": 9.0,
        "cacheReadCostPer1M": 0.15,
        "cacheWriteCostPer1M": 1.5,
    },
    "gemini-2.5-pro": {
        "inputCostPer1M": 1.25,
        "outputCostPer1M": 10.0,
        "cacheReadCostPer1M": 0.125,
        "cacheWriteCostPer1M": 1.25,
    },
}

DEFAULT_USD_PER_PREMIUM_REQUEST = 0.04

def _default_pricing() -> dict[str, object]:
    return {
        "currency": "USD",
        "costUnitLabel": "premium requests",
        "usdPerPremiumRequest": DEFAULT_USD_PER_PREMIUM_REQUEST,
        "models": {
            "default": DEFAULT_MODEL_PRICING.copy(),
            **{
                model_name: pricing.copy()
                for model_name, pricing in PUBLIC_MODEL_PRICING.items()
            },
        },
    }


def pricing_path_for(data_dir: Path) -> Path:
    return data_dir / "pricing.json"


def ensure_pricing_file(data_dir: Path) -> Path:
    path = pricing_path_for(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(_default_pricing(), indent=2) + "\n", encoding="utf-8")
        return path

    raw_pricing = json.loads(path.read_text(encoding="utf-8"))
    if _should_seed_public_model_pricing(raw_pricing):
        upgraded = _default_pricing()
        upgraded["currency"] = _string_or_default(raw_pricing.get("currency"), "USD")
        upgraded["costUnitLabel"] = _string_or_default(
            raw_pricing.get("costUnitLabel"),
            "premium requests",
        )
        upgraded["usdPerPremiumRequest"] = _as_float_or_none(
            raw_pricing.get("usdPerPremiumRequest")
        )
        if upgraded["usdPerPremiumRequest"] is None:
            upgraded["usdPerPremiumRequest"] = DEFAULT_USD_PER_PREMIUM_REQUEST
        path.write_text(json.dumps(upgraded, indent=2) + "\n", encoding="utf-8")
    return path


def load_pricing(data_dir: Path) -> dict[str, object]:
    path = ensure_pricing_file(data_dir)
    return normalize_pricing(json.loads(path.read_text(encoding="utf-8")))


def normalize_pricing(pricing: Mapping[str, object] | None) -> dict[str, object]:
    raw_pricing = pricing if isinstance(pricing, Mapping) else {}
    raw_models = raw_pricing.get("models")
    models: dict[str, dict[str, float | None]] = {}
    if isinstance(raw_models, Mapping):
        for model_name, value in raw_models.items():
            if isinstance(value, Mapping):
                models[str(model_name)] = _normalize_model_pricing(value)
    models.setdefault("default", DEFAULT_MODEL_PRICING.copy())
    return {
        "currency": _string_or_default(raw_pricing.get("currency"), "USD"),
        "costUnitLabel": _string_or_default(raw_pricing.get("costUnitLabel"), "premium requests"),
        "usdPerPremiumRequest": _as_float_or_none(raw_pricing.get("usdPerPremiumRequest")),
        "models": models,
    }


def pricing_currency(pricing: Mapping[str, object]) -> str:
    return _string_or_default(pricing.get("currency"), "USD").upper()


def premium_request_rate(pricing: Mapping[str, object]) -> float | None:
    return _as_float_or_none(pricing.get("usdPerPremiumRequest"))


def model_pricing_for(model_name: str, pricing: Mapping[str, object]) -> dict[str, float | None] | None:
    model_pricing = _pricing_for_model(model_name, pricing)
    if model_pricing is None:
        return None
    return _normalize_model_pricing(model_pricing)


def describe_cost_strategy(pricing: Mapping[str, object]) -> str:
    models = pricing.get("models")
    has_token_pricing = isinstance(models, Mapping) and any(
        _has_token_pricing(config) for config in models.values() if isinstance(config, Mapping)
    )
    has_premium_request_rate = premium_request_rate(pricing) is not None
    if has_token_pricing and has_premium_request_rate:
        return "model token rates with premium request fallback"
    if has_token_pricing:
        return "model token rates"
    if has_premium_request_rate:
        return "premium request conversion"
    return "not configured"


def estimate_currency_amount(premium_request_units: float, pricing: dict[str, object]) -> float | None:
    rate = premium_request_rate(pricing)
    if rate is None:
        return None
    return premium_request_units * rate


def estimate_model_currency_amount(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    premium_request_units: float,
    pricing: Mapping[str, object],
) -> float | None:
    token_cost = estimate_model_token_cost(
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        pricing=pricing,
    )
    if token_cost is not None:
        return token_cost
    return estimate_currency_amount(premium_request_units, dict(pricing))


def estimate_model_row_currency_amount(row: Mapping[str, object], pricing: Mapping[str, object]) -> float | None:
    return estimate_model_currency_amount(
        model_name=str(row["model_name"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cache_read_tokens=int(row["cache_read_tokens"]),
        cache_write_tokens=int(row["cache_write_tokens"]),
        premium_request_units=float(row["premium_request_cost"]),
        pricing=pricing,
    )


def estimate_total_currency_amount(rows: Iterable[Mapping[str, object]], pricing: Mapping[str, object]) -> float | None:
    total = 0.0
    has_value = False
    for row in rows:
        amount = estimate_model_row_currency_amount(row, pricing)
        if amount is None:
            continue
        total += amount
        has_value = True
    return total if has_value else None


def estimate_model_token_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    pricing: Mapping[str, object],
) -> float | None:
    model_pricing = _pricing_for_model(model_name, pricing)
    if model_pricing is None or not _has_token_pricing(model_pricing):
        return None
    input_rate = _as_float_or_none(model_pricing.get("inputCostPer1M"))
    output_rate = _as_float_or_none(model_pricing.get("outputCostPer1M"))
    if input_rate is None or output_rate is None:
        return None
    cache_read_rate = _as_float_or_default(model_pricing.get("cacheReadCostPer1M"), 0.0)
    cache_write_rate = _as_float_or_default(model_pricing.get("cacheWriteCostPer1M"), 0.0)
    return (
        (input_tokens / 1_000_000.0) * input_rate
        + (output_tokens / 1_000_000.0) * output_rate
        + (cache_read_tokens / 1_000_000.0) * cache_read_rate
        + (cache_write_tokens / 1_000_000.0) * cache_write_rate
    )


def _normalize_model_pricing(pricing: Mapping[str, object]) -> dict[str, float | None]:
    return {
        "inputCostPer1M": _as_float_or_none(pricing.get("inputCostPer1M")),
        "outputCostPer1M": _as_float_or_none(pricing.get("outputCostPer1M")),
        "cacheReadCostPer1M": _as_float_or_default(pricing.get("cacheReadCostPer1M"), 0.0),
        "cacheWriteCostPer1M": _as_float_or_default(pricing.get("cacheWriteCostPer1M"), 0.0),
    }


def _pricing_for_model(model_name: str, pricing: Mapping[str, object]) -> Mapping[str, object] | None:
    models = pricing.get("models")
    if not isinstance(models, Mapping):
        return None
    if isinstance(models.get(model_name), Mapping):
        return models[model_name]
    if isinstance(models.get("default"), Mapping):
        return models["default"]
    return None


def _has_token_pricing(pricing: Mapping[str, object]) -> bool:
    return (
        _as_float_or_none(pricing.get("inputCostPer1M")) is not None
        and _as_float_or_none(pricing.get("outputCostPer1M")) is not None
    )


def _as_float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float_or_default(value: object, default: float) -> float:
    parsed = _as_float_or_none(value)
    return default if parsed is None else parsed


def _string_or_default(value: object, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _should_seed_public_model_pricing(pricing: Mapping[str, object]) -> bool:
    models = pricing.get("models")
    if not isinstance(models, Mapping):
        return True

    custom_model_names = [name for name in models.keys() if str(name) != "default"]
    if custom_model_names:
        return False

    default_model = models.get("default")
    if not isinstance(default_model, Mapping):
        return True

    return not _has_token_pricing(default_model)

