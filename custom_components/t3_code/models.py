"""Normalize T3 contracts into sensors without double-counting tokens."""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from .api import T3ProtocolError

TOKEN_FIELDS = ("uncachedInputTokens", "cachedInputTokens", "cacheCreationTokens", "outputTokens")
PROVIDER_NAMES = {
    "codex": "Codex",
    "claude": "Claude",
    "opencode": "OpenCode",
    "grok": "Grok",
    "cursor": "Cursor",
    "antigravity": "Antigravity",
    "pi": "Pi",
}


@dataclass(frozen=True)
class Reading:
    """A sensor value and its display/recording metadata."""

    name: str
    value: int | float | datetime | str | None
    kind: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_reset: datetime | None = None


@dataclass
class Snapshot:
    """Only normalized metrics; no credentials, email addresses, or local paths."""

    environment: dict[str, Any]
    readings: dict[str, Reading]


def _number(value: Any) -> float | int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise T3ProtocolError("T3 returned an invalid usage value")
    return value


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def _validate_summary(summary: dict[str, Any]) -> tuple[list, list]:
    """Check the contract envelope and per-bucket numeric fields."""
    if (
        not isinstance(summary, dict)
        or summary.get("contractVersion") not in (4, 5, 6)
        or not isinstance(summary.get("buckets"), list)
        or not isinstance(summary.get("sources"), list)
    ):
        raise T3ProtocolError("Unsupported T3 usage contract; expected version 4, 5, or 6")
    buckets = summary["buckets"]
    sources = summary["sources"]
    try:
        for bucket in buckets:
            _validate_bucket(bucket)
        for source in sources:
            if source["status"] not in ("ok", "missing", "partial", "failed"):
                raise ValueError
    except (KeyError, ValueError, TypeError) as err:
        raise T3ProtocolError("T3 returned malformed usage buckets or sources") from err
    return buckets, sources


def _validate_bucket(bucket: dict[str, Any]) -> None:
    """Check one usage bucket's day, provider, and numeric totals."""
    date.fromisoformat(bucket["day"])
    if not isinstance(bucket["provider"], str) or not isinstance(bucket["totals"], dict):
        raise ValueError
    for field_name in (*TOKEN_FIELDS, "reasoningTokens"):
        _number(bucket["totals"][field_name])
    for field_name in ("costUsd", "cacheSavingsUsd", "unpricedRecords"):
        _number(bucket[field_name])


def _source_flags(sources: list[dict[str, Any]]) -> tuple[bool, bool]:
    """Return (incomplete, no_readable_sources) for status sensors."""
    incomplete = any(s["status"] != "ok" or s.get("refreshing") for s in sources)
    no_readable = bool(sources) and all(s["status"] in ("missing", "failed") for s in sources)
    return bool(incomplete), no_readable


def _usage_values(group: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sum token buckets and build the metric value table for one group."""
    tokens = {key: sum(b["totals"][key] for b in group) for key in TOKEN_FIELDS}
    reasoning = sum(b["totals"]["reasoningTokens"] for b in group)
    values = {
        "tokens": ("tokens", sum(tokens.values()), "tokens"),
        "input_tokens": (
            "input tokens",
            sum(tokens[k] for k in TOKEN_FIELDS[:3]),
            "tokens",
        ),
        "output_tokens": ("output tokens", tokens["outputTokens"], "tokens"),
        "cached_tokens": ("cache read tokens", tokens["cachedInputTokens"], "tokens"),
        "cache_write_tokens": (
            "cache write tokens",
            tokens["cacheCreationTokens"],
            "tokens",
        ),
        "reasoning_tokens": ("reasoning tokens", reasoning, "tokens"),
        "cost": ("estimated cost", sum(b["costUsd"] for b in group), "money"),
        "cache_savings": (
            "estimated cache savings",
            sum(b["cacheSavingsUsd"] for b in group),
            "money",
        ),
    }
    return tokens, values


def _metric_name(label: str, period: str, metric: str, metric_label: str) -> str:
    """Use headline names for totals/cost, regular names otherwise."""
    if metric in ("tokens", "cost"):
        headline = "Estimated cost" if metric == "cost" else "Tokens"
        suffix = "today" if period == "today" else "this month"
        return f"{label}{headline} {suffix}"
    return f"{label}{period.title()} {metric_label}"


def _metric_attrs(
    metric: str,
    attrs: dict[str, Any],
    tokens: dict[str, Any],
    group: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach token breakdown or savings detail to totals/cost sensors."""
    metric_attrs = dict(attrs)
    if metric == "tokens":
        metric_attrs.update(
            {
                "input_tokens": sum(tokens[k] for k in TOKEN_FIELDS[:3]),
                "output_tokens": tokens["outputTokens"],
                "cache_read_tokens": tokens["cachedInputTokens"],
                "cache_write_tokens": tokens["cacheCreationTokens"],
                "reasoning_tokens": sum(b["totals"]["reasoningTokens"] for b in group),
            }
        )
    elif metric == "cost":
        metric_attrs["cache_savings_usd"] = sum(b["cacheSavingsUsd"] for b in group)
    return metric_attrs


def _build_period_readings(
    readings: dict[str, Reading],
    buckets: list[dict[str, Any]],
    today: date,
    time_zone: str,
    incomplete: bool,
    no_readable_sources: bool,
) -> None:
    """Add today/month usage sensors for every provider present."""
    for period, start in (("today", today), ("month", today.replace(day=1))):
        selected = [b for b in buckets if start.isoformat() <= b["day"] <= today.isoformat()]
        reset = datetime.combine(start, time.min, ZoneInfo(time_zone))
        for provider in [None, *sorted({b["provider"] for b in selected})]:
            _build_provider_readings(
                readings,
                selected,
                provider,
                period,
                start,
                today,
                time_zone,
                reset,
                incomplete,
                no_readable_sources,
            )


def _build_provider_readings(
    readings: dict[str, Reading],
    selected: list[dict[str, Any]],
    provider: str | None,
    period: str,
    start: date,
    today: date,
    time_zone: str,
    reset: datetime,
    incomplete: bool,
    no_readable_sources: bool,
) -> None:
    """Add one period's sensors for a single provider (or all)."""
    group = [b for b in selected if provider is None or b["provider"] == provider]
    prefix = f"usage/{provider or 'all'}/{period}"
    label = f"{PROVIDER_NAMES.get(provider, provider)} " if provider else ""
    attrs = {
        "period_start": start.isoformat(),
        "period_end": today.isoformat(),
        "time_zone": time_zone,
        "incomplete": bool(incomplete),
        "unpriced_records": sum(b["unpricedRecords"] for b in group),
    }
    tokens, values = _usage_values(group)
    for metric, (metric_label, value, kind) in values.items():
        readings[f"{prefix}/{metric}"] = Reading(
            _metric_name(label, period, metric, metric_label),
            None if no_readable_sources else value,
            kind,
            _metric_attrs(metric, attrs, tokens, group),
            reset,
        )


def _add_window(
    readings: dict[str, Reading],
    instance_id: str,
    window: dict[str, Any],
    label: str,
    attrs: dict[str, Any],
    unavailable: bool,
    now: datetime,
) -> None:
    """Add used/remaining/reset sensors for a single quota window."""
    window_id = window.get("id")
    used = window.get("usedPercent")
    if not isinstance(window_id, str) or used is None:
        return
    used = _number(used)
    if used > 100:
        raise T3ProtocolError("T3 returned an invalid quota percentage")
    reset_at = _timestamp(window.get("resetsAt"))
    stale = reset_at is not None and reset_at <= now
    window_label = window.get("label") or window_id
    window_attrs = {
        **attrs,
        "window": window.get("kind"),
        "stale": stale,
        "provider_label": label,
        "window_label": window_label,
        "resets_at": window.get("resetsAt"),
        "used_percent": used,
    }
    prefix = f"limit/{instance_id}/{window_id}"
    name = f"{label} {window_label}"
    for metric, value in (("used", used), ("remaining", 100 - used)):
        readings[f"{prefix}/{metric}"] = Reading(
            f"{name} {metric}",
            None if unavailable or stale else value,
            "percent",
            window_attrs,
        )
    readings[f"{prefix}/reset"] = Reading(
        f"{name} reset", None if unavailable else reset_at, "timestamp", window_attrs
    )


def _window_readings(
    readings: dict[str, Reading],
    provider: dict[str, Any],
    label: str,
    attrs: dict[str, Any],
    unavailable: bool,
    now: datetime,
) -> None:
    """Add used/remaining/reset sensors for each quota window."""
    limits = provider.get("usageLimits")
    if not isinstance(limits, dict):
        return
    instance_id = provider.get("instanceId")
    for window in limits.get("windows", []):
        _add_window(readings, instance_id, window, label, attrs, unavailable, now)


def _credits_reading(
    readings: dict[str, Reading],
    provider: dict[str, Any],
    label: str,
    attrs: dict[str, Any],
    unavailable: bool,
) -> None:
    """Add the reset-credit sensor when the server reports one."""
    limits = provider.get("usageLimits")
    if not isinstance(limits, dict):
        return
    reset_credits = limits.get("resetCredits", {}).get("availableCount")
    if reset_credits is not None:
        readings[f"limit/{provider.get('instanceId')}/credits"] = Reading(
            f"{label} reset credits",
            None if unavailable else _number(reset_credits),
            "count",
            attrs,
        )


def _build_limit_readings(
    readings: dict[str, Reading],
    providers: list[dict[str, Any]],
    now: datetime,
    limits_failed: bool,
) -> None:
    """Add quota window and credit sensors for each provider instance."""
    for provider in providers:
        instance_id = provider.get("instanceId")
        limits = provider.get("usageLimits")
        if not isinstance(instance_id, str) or not isinstance(limits, dict):
            continue
        driver = provider.get("driver")
        label = provider.get("displayName") or PROVIDER_NAMES.get(driver, driver) or instance_id
        unavailable = limits_failed or bool(limits.get("unavailable"))
        attrs = {
            "provider": provider.get("driver"),
            "instance_id": instance_id,
            "checked_at": limits.get("checkedAt"),
            "unavailable_reason": "refresh_failed"
            if limits_failed
            else (limits.get("unavailable") or {}).get("reason"),
        }
        _window_readings(readings, provider, label, attrs, unavailable, now)
        _credits_reading(readings, provider, label, attrs, unavailable)


def normalize(
    environment: dict[str, Any],
    summary: dict[str, Any],
    providers: list[dict[str, Any]],
    today: date,
    time_zone: str,
    now: datetime,
    *,
    limits_failed: bool = False,
) -> Snapshot:
    """Use calendar windows in HA's zone; reasoning is already part of output."""
    buckets, sources = _validate_summary(summary)
    incomplete, no_readable_sources = _source_flags(sources)
    readings: dict[str, Reading] = {}
    readings["usage_status"] = Reading(
        "Usage data status",
        "partial" if incomplete else "ok",
        "status",
        {"source_count": len(sources), "pricing_status": summary.get("pricing", {}).get("status")},
    )
    readings["limits_status"] = Reading(
        "Limits refresh status", "failed" if limits_failed else "ok", "status"
    )
    readings["last_read"] = Reading(
        "Last usage read", _timestamp(summary.get("readAt")), "timestamp"
    )
    _build_period_readings(readings, buckets, today, time_zone, incomplete, no_readable_sources)
    _build_limit_readings(readings, providers, now, limits_failed)
    return Snapshot(environment, readings)
