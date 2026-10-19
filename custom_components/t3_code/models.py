"""Normalize T3 contracts into sensors without double-counting tokens."""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from .api import T3ProtocolError

TOKEN_FIELDS = ("uncachedInputTokens", "cachedInputTokens", "cacheCreationTokens", "outputTokens")


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
    if (
        not isinstance(summary, dict)
        or summary.get("contractVersion") not in (4, 5, 6)
        or not isinstance(summary.get("buckets"), list)
        or not isinstance(summary.get("sources"), list)
    ):
        raise T3ProtocolError("Unsupported T3 usage contract; expected version 4, 5, or 6")
    readings: dict[str, Reading] = {}
    buckets = summary["buckets"]
    sources = summary["sources"]
    try:
        for bucket in buckets:
            date.fromisoformat(bucket["day"])
            if not isinstance(bucket["provider"], str) or not isinstance(bucket["totals"], dict):
                raise ValueError
            for field_name in (*TOKEN_FIELDS, "reasoningTokens"):
                _number(bucket["totals"][field_name])
            for field_name in ("costUsd", "cacheSavingsUsd", "unpricedRecords"):
                _number(bucket[field_name])
        for source in sources:
            if source["status"] not in ("ok", "missing", "partial", "failed"):
                raise ValueError
    except (KeyError, ValueError, TypeError) as err:
        raise T3ProtocolError("T3 returned malformed usage buckets or sources") from err

    incomplete = any(s["status"] != "ok" or s.get("refreshing") for s in sources)
    no_readable_sources = bool(sources) and all(
        s["status"] in ("missing", "failed") for s in sources
    )
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

    for period, start in (("today", today), ("month", today.replace(day=1))):
        selected = [b for b in buckets if start.isoformat() <= b["day"] <= today.isoformat()]
        reset = datetime.combine(start, time.min, ZoneInfo(time_zone))
        for provider in [None, *sorted({b["provider"] for b in selected})]:
            group = [b for b in selected if provider is None or b["provider"] == provider]
            prefix = f"usage/{provider or 'all'}/{period}"
            label = f"{provider.title()} " if provider else ""
            attrs = {
                "period_start": start.isoformat(),
                "period_end": today.isoformat(),
                "time_zone": time_zone,
                "incomplete": bool(incomplete),
                "unpriced_records": sum(b["unpricedRecords"] for b in group),
            }
            tokens = {key: sum(b["totals"][key] for b in group) for key in TOKEN_FIELDS}
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
                "reasoning_tokens": (
                    "reasoning tokens",
                    sum(b["totals"]["reasoningTokens"] for b in group),
                    "tokens",
                ),
                "cost": ("estimated cost", sum(b["costUsd"] for b in group), "money"),
                "cache_savings": (
                    "estimated cache savings",
                    sum(b["cacheSavingsUsd"] for b in group),
                    "money",
                ),
            }
            for metric, (metric_label, value, kind) in values.items():
                readings[f"{prefix}/{metric}"] = Reading(
                    f"{label}{period.title()} {metric_label}",
                    None if no_readable_sources else value,
                    kind,
                    attrs,
                    reset,
                )

    for provider in providers:
        instance_id = provider.get("instanceId")
        limits = provider.get("usageLimits")
        if not isinstance(instance_id, str) or not isinstance(limits, dict):
            continue
        label = provider.get("displayName") or provider.get("driver") or instance_id
        unavailable = limits_failed or bool(limits.get("unavailable"))
        attrs = {
            "provider": provider.get("driver"),
            "instance_id": instance_id,
            "checked_at": limits.get("checkedAt"),
            "unavailable_reason": "refresh_failed"
            if limits_failed
            else (limits.get("unavailable") or {}).get("reason"),
        }
        for window in limits.get("windows", []):
            window_id = window.get("id")
            used = window.get("usedPercent")
            if not isinstance(window_id, str) or used is None:
                continue
            used = _number(used)
            if used > 100:
                raise T3ProtocolError("T3 returned an invalid quota percentage")
            reset_at = _timestamp(window.get("resetsAt"))
            stale = reset_at is not None and reset_at <= now
            window_attrs = {**attrs, "window": window.get("kind"), "stale": stale}
            prefix = f"limit/{instance_id}/{window_id}"
            name = f"{label} {window.get('label') or window_id}"
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
        credits = limits.get("resetCredits", {}).get("availableCount")
        if credits is not None:
            readings[f"limit/{instance_id}/credits"] = Reading(
                f"{label} reset credits", None if unavailable else _number(credits), "count", attrs
            )
    return Snapshot(environment, readings)
