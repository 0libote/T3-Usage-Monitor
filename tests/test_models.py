"""Correct accounting, period resets, and unavailable readings."""

from copy import deepcopy
from datetime import date

import pytest

from custom_components.t3_code.api import T3ProtocolError
from custom_components.t3_code.models import normalize


def test_no_reasoning_double_count_and_calendar_reset(descriptor, summary, providers, now):
    readings = normalize(
        descriptor, summary, providers, date(2026, 10, 19), "America/New_York", now
    ).readings
    assert readings["usage/all/today/tokens"].value == 370
    assert readings["usage/all/today/reasoning_tokens"].value == 30
    assert readings["usage/all/today/input_tokens"].value == 320
    assert readings["usage/all/month/cost"].value == 0.01
    assert readings["usage/all/today/tokens"].last_reset.isoformat() == "2026-10-19T00:00:00-04:00"
    assert readings["usage/all/month/tokens"].last_reset.isoformat() == "2026-10-01T00:00:00-04:00"


def test_today_month_and_provider_split(descriptor, summary, now):
    earlier = deepcopy(summary["buckets"][0])
    earlier.update(day="2026-10-01", provider="claude")
    summary["buckets"].append(earlier)
    readings = normalize(descriptor, summary, [], date(2026, 10, 19), "UTC", now).readings
    assert readings["usage/all/today/tokens"].value == 370
    assert readings["usage/all/month/tokens"].value == 740
    assert readings["usage/claude/month/tokens"].value == 370


def test_today_keeps_zero_for_provider_without_usage_today(descriptor, summary, now):
    earlier = deepcopy(summary["buckets"][0])
    earlier.update(day="2026-10-01", provider="claude")
    summary["buckets"].append(earlier)
    readings = normalize(descriptor, summary, [], date(2026, 10, 19), "UTC", now).readings
    assert readings["usage/claude/today/tokens"].value == 0
    assert readings["usage/claude/today/cost"].value == 0
    assert readings["usage/claude/month/tokens"].value == 370


def test_no_history_is_zero_but_failed_sources_unavailable(descriptor, summary, now):
    summary["buckets"] = []
    readings = normalize(descriptor, summary, [], now.date(), "UTC", now).readings
    assert readings["usage/all/month/tokens"].value == 0
    summary["sources"][0]["status"] = "failed"
    readings = normalize(descriptor, summary, [], now.date(), "UTC", now).readings
    assert readings["usage/all/month/tokens"].value is None
    assert readings["usage_status"].value == "partial"


@pytest.mark.parametrize("reason", ["unsupported", "probeFailed"])
def test_unavailable_quota_never_becomes_zero(descriptor, summary, providers, now, reason):
    providers[0]["usageLimits"]["unavailable"] = {"reason": reason}
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limit/codex/primary/used"].value is None
    assert readings["limit/codex/primary/remaining"].value is None


def test_expired_quota_stale(descriptor, summary, providers, now):
    providers[0]["usageLimits"]["windows"][0]["resetsAt"] = "2026-10-18T00:00:00Z"
    reading = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings[
        "limit/codex/primary/remaining"
    ]
    assert reading.value is None
    assert reading.attributes["stale"]


@pytest.mark.parametrize("field,value", [("contractVersion", 99), ("buckets", None)])
def test_incompatible_contract(descriptor, summary, now, field, value):
    summary[field] = value
    today = now.date()
    with pytest.raises(T3ProtocolError):
        normalize(descriptor, summary, [], today, "UTC", now)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "100"])
def test_malformed_tokens(descriptor, summary, now, value):
    summary["buckets"][0]["totals"]["outputTokens"] = value
    today = now.date()
    with pytest.raises(T3ProtocolError):
        normalize(descriptor, summary, [], today, "UTC", now)


@pytest.mark.parametrize(
    ("reason", "status"), [("unsupported", "unsupported"), ("probeFailed", "failed")]
)
def test_account_status_without_windows(descriptor, summary, providers, now, reason, status):
    limits = providers[0]["usageLimits"]
    limits["windows"] = []
    limits["unavailable"] = {"reason": reason, "message": "private-server-error"}
    reading = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings[
        "limits_status"
    ]
    assert reading.value == status
    assert reading.attributes["accounts"][0]["reason"] == reason
    assert "private-server-error" not in str(reading)


def test_one_failed_account_keeps_healthy_account(descriptor, summary, providers, now):
    failed = deepcopy(providers[0])
    failed["instanceId"] = "second-account"
    failed["usageLimits"]["unavailable"] = {"reason": "probeFailed"}
    readings = normalize(descriptor, summary, [*providers, failed], now.date(), "UTC", now).readings
    assert readings["limits_status"].value == "partial"
    assert readings["limit/codex/primary/remaining"].value == 75
    assert readings["limit/second-account/primary/remaining"].value is None
