"""Normalize T3 contracts into sensors without double-counting tokens."""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from .api import T3ProtocolError
from .const import MAX_QUOTA_AGE

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
    expires_at: datetime | None = None


@dataclass
class Snapshot:
    """Only normalized metrics; no credentials, email addresses, or local paths."""

    environment: dict[str, Any]
    readings: dict[str, Reading]
    diagnostics: dict[str, Any] = field(default_factory=dict)


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
    known_providers = sorted({b["provider"] for b in buckets})
    for period, start in (("today", today), ("month", today.replace(day=1))):
        selected = [b for b in buckets if start.isoformat() <= b["day"] <= today.isoformat()]
        reset = datetime.combine(start, time.min, ZoneInfo(time_zone))
        for provider in [None, *known_providers]:
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
    checked_expiry: datetime | None,
) -> datetime | None:
    """Add a quota window which expires at its reset or maximum reading age."""
    window_id = window.get("id")
    used = window.get("usedPercent")
    if not isinstance(window_id, str) or used is None:
        return None
    used = _number(used)
    if used > 100:
        raise T3ProtocolError("T3 returned an invalid quota percentage")
    reset_at = _timestamp(window.get("resetsAt"))
    deadlines = [stamp for stamp in (reset_at, checked_expiry) if stamp is not None]
    expires_at = min(deadlines) if deadlines else None
    stale = bool(attrs["stale"]) or (expires_at is not None and expires_at <= now)
    window_attrs = {
        **attrs,
        "window": window.get("kind"),
        "stale": stale,
        "unavailable_reason": attrs["unavailable_reason"] or ("stale" if stale else None),
        "provider_label": label,
        "window_label": window.get("label") or window_id,
        "resets_at": window.get("resetsAt") if reset_at else None,
        "used_percent": used,
    }
    prefix = f"limit/{instance_id}/{window_id}"
    name = f"{label} {window_attrs['window_label']}"
    for metric, value, kind in (
        ("used", used, "percent"),
        ("remaining", 100 - used, "percent"),
        ("reset", reset_at, "timestamp"),
    ):
        readings[f"{prefix}/{metric}"] = Reading(
            f"{name} {metric}",
            None if unavailable or stale else value,
            kind,
            window_attrs,
            expires_at=expires_at,
        )
    return expires_at


def _limits_status(accounts: list[dict[str, Any]], refresh_failed: bool) -> Reading:
    """Distinguish partial failures, stale readings, and unsupported accounts."""
    statuses = {account["status"] for account in accounts}
    if refresh_failed:
        status = "failed"
    elif "failed" in statuses:
        status = "partial" if "ok" in statuses else "failed"
    elif "stale" in statuses:
        status = "partial" if "ok" in statuses else "stale"
    elif statuses == {"unsupported"}:
        status = "unsupported"
    else:
        status = "ok"
    # Accounts without quota metrics still need a local status expiry timer.
    deadlines = [
        stamp
        for account in accounts
        if account["status"] == "ok" and (stamp := _timestamp(account["expires_at"])) is not None
    ]
    return Reading(
        "Limits refresh status",
        status,
        "status",
        {"accounts": accounts, "refresh_failed": refresh_failed},
        expires_at=min(deadlines) if deadlines else None,
    )


def _build_account_readings(
    readings: dict[str, Reading],
    provider: dict[str, Any],
    now: datetime,
    limits_failed: bool,
) -> dict[str, Any] | None:
    """Normalize one account without publishing partially validated metrics."""
    instance_id = provider.get("instanceId")
    limits = provider.get("usageLimits")
    if limits is None:
        return None
    if not isinstance(instance_id, str) or not instance_id or not isinstance(limits, dict):
        raise T3ProtocolError("T3 returned an invalid quota account")
    driver = provider.get("driver")
    label = provider.get("displayName") or PROVIDER_NAMES.get(driver, driver) or instance_id
    reason = (limits.get("unavailable") or {}).get("reason")
    # Never put arbitrary server error messages in states or diagnostics.
    if reason not in ("unsupported", "probeFailed"):
        reason = "probeFailed" if limits.get("unavailable") is not None else None
    checked_at = _timestamp(limits.get("checkedAt"))
    checked_expiry = checked_at + MAX_QUOTA_AGE if checked_at else None
    stale = checked_at is None or checked_at > now + timedelta(minutes=5) or checked_expiry <= now
    reason = "refresh_failed" if limits_failed else reason or ("stale" if stale else None)
    unavailable = reason is not None
    attrs = {
        "provider": driver,
        "instance_id": instance_id,
        "checked_at": checked_at.isoformat() if checked_at else None,
        "unavailable_reason": reason,
        "stale": stale,
    }
    expiries = [checked_expiry] if checked_expiry else []
    windows = limits.get("windows", [])
    if not isinstance(windows, list):
        raise T3ProtocolError("T3 returned invalid quota windows")
    for window in windows:
        expires_at = _add_window(
            readings, instance_id, window, label, attrs, unavailable, now, checked_expiry
        )
        if expires_at:
            expiries.append(expires_at)
    reset_credits = (limits.get("resetCredits") or {}).get("availableCount")
    if reset_credits is not None:
        readings[f"limit/{instance_id}/credits"] = Reading(
            f"{label} reset credits",
            None if unavailable else _number(reset_credits),
            "count",
            attrs,
            expires_at=checked_expiry,
        )
    expires_at = min(expiries) if expiries else None
    if reason == "unsupported":
        status = "unsupported"
    elif reason in ("probeFailed", "refresh_failed"):
        status = "failed"
    elif stale or (expires_at is not None and expires_at <= now):
        status = "stale"
        reason = "stale"
    else:
        status = "ok"
    return {
        "instance_id": instance_id,
        "provider": driver,
        "label": label,
        "status": status,
        "reason": reason,
        "checked_at": attrs["checked_at"],
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


def _build_limit_readings(
    readings: dict[str, Reading],
    providers: list[dict[str, Any]],
    now: datetime,
    limits_failed: bool,
    allow_partial: bool,
) -> None:
    """Keep healthy accounts available when another account is malformed."""
    if not isinstance(providers, list):
        raise T3ProtocolError("T3 returned an invalid provider list")
    accounts = []
    for provider in providers:
        account_readings: dict[str, Reading] = {}
        try:
            account = _build_account_readings(account_readings, provider, now, limits_failed)
        except T3ProtocolError, KeyError, TypeError, ValueError, AttributeError:
            if not allow_partial:
                raise
            account_readings.clear()
            account = {
                "instance_id": None,
                "provider": None,
                "label": f"Account {len(accounts) + 1}",
                "status": "failed",
                "reason": "invalid_response",
                "checked_at": None,
                "expires_at": None,
            }
            if isinstance(provider, dict):
                for source, target in (
                    ("instanceId", "instance_id"),
                    ("driver", "provider"),
                    ("displayName", "label"),
                ):
                    if isinstance(value := provider.get(source), str):
                        account[target] = value
        if account is not None:
            accounts.append(account)
            readings.update(account_readings)
    readings["limits_status"] = _limits_status(accounts, limits_failed)


def expire_snapshot(snapshot: Snapshot, now: datetime) -> Snapshot:
    """Invalidate expired quotas locally without resetting HA's polling/retry state."""
    readings = dict(snapshot.readings)
    for key, reading in readings.items():
        if reading.expires_at is not None and reading.expires_at <= now:
            attrs = {**reading.attributes, "stale": True}
            attrs["unavailable_reason"] = attrs.get("unavailable_reason") or "stale"
            readings[key] = replace(reading, value=None, attributes=attrs)
    status = readings["limits_status"]
    accounts = []
    for account in status.attributes["accounts"]:
        account = dict(account)
        expires_at = _timestamp(account["expires_at"])
        if account["status"] == "ok" and expires_at is not None and expires_at <= now:
            account.update(status="stale", reason="stale")
        accounts.append(account)
    readings["limits_status"] = _limits_status(accounts, status.attributes["refresh_failed"])
    return replace(snapshot, readings=readings)


def _build_usage_readings(
    summary: dict[str, Any] | None, today: date, time_zone: str
) -> tuple[dict[str, Reading], dict[str, Any]]:
    """Normalize usage separately so its failure cannot suppress good quotas."""
    if summary is None:
        readings = {
            "usage_status": Reading("Usage data status", "failed", "status"),
            "last_read": Reading("Last usage read", None, "timestamp"),
        }
        _build_period_readings(readings, [], today, time_zone, True, True)
        return readings, {}
    buckets, sources = _validate_summary(summary)
    incomplete, no_readable_sources = _source_flags(sources)
    pricing_status = summary.get("pricing", {}).get("status")
    if pricing_status not in ("fresh", "cached", "unavailable"):
        pricing_status = "unknown"
    readings = {
        "usage_status": Reading(
            "Usage data status",
            "partial" if incomplete else "ok",
            "status",
            {
                "source_count": len(sources),
                "pricing_status": pricing_status,
            },
        ),
        "last_read": Reading("Last usage read", _timestamp(summary.get("readAt")), "timestamp"),
    }
    _build_period_readings(readings, buckets, today, time_zone, incomplete, no_readable_sources)
    diagnostics = {
        "contract_version": summary["contractVersion"],
        "sources": [
            {"status": source["status"], "refreshing": bool(source.get("refreshing"))}
            for source in sources
        ],
        "pricing_status": pricing_status,
    }
    return readings, diagnostics


def normalize(
    environment: dict[str, Any],
    summary: dict[str, Any] | None,
    providers: list[dict[str, Any]],
    today: date,
    time_zone: str,
    now: datetime,
    *,
    limits_failed: bool = False,
    allow_partial: bool = False,
) -> Snapshot:
    """Use calendar windows in HA's zone; isolate failures during runtime polling."""
    if summary is None and not allow_partial:
        raise T3ProtocolError("T3 returned an invalid usage response")
    invalid = (T3ProtocolError, KeyError, TypeError, ValueError, AttributeError)
    try:
        readings, diagnostics = _build_usage_readings(summary, today, time_zone)
    except invalid:
        if not allow_partial:
            raise
        readings, diagnostics = _build_usage_readings(None, today, time_zone)
    try:
        limit_readings: dict[str, Reading] = {}
        _build_limit_readings(limit_readings, providers, now, limits_failed, allow_partial)
    except invalid:
        if not allow_partial:
            raise
        limit_readings = {"limits_status": _limits_status([], True)}
    readings.update(limit_readings)
    return Snapshot(environment, readings, diagnostics)
