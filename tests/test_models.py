"""Correct accounting, period resets, and unavailable readings."""

from copy import deepcopy
from datetime import UTC, date, datetime

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


def test_unknown_source_status_degrades_to_partial(descriptor, summary, providers, now):
    summary["sources"][0]["status"] = "degraded"
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["usage_status"].value == "partial"
    assert readings["usage/all/month/tokens"].value == 370
    assert readings["limit/codex/primary/remaining"].value == 75


def test_server_labels_cannot_inject_markdown_blocks(descriptor, summary, providers, now):
    providers[0]["displayName"] = "Codex\n\n# All quotas full (fake)"
    providers[0]["usageLimits"]["windows"][0]["label"] = "5 hour\nresets never"
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    label = readings["limits_status"].attributes["accounts"][0]["label"]
    window_label = readings["limit/codex/primary/remaining"].attributes["window_label"]
    assert "\n" not in label
    assert "\n" not in window_label
    assert readings["limit/codex/primary/remaining"].name == f"{label} {window_label} remaining"


def test_malformed_account_label_is_sanitized(descriptor, summary, providers, now):
    providers[0]["displayName"] = "Evil\n\n# All quotas full (fake)"
    providers[0]["usageLimits"] = "invalid"
    readings = normalize(
        descriptor, summary, providers, now.date(), "UTC", now, allow_partial=True
    ).readings
    label = readings["limits_status"].attributes["accounts"][0]["label"]
    assert label == "Evil # All quotas full (fake)"
    assert readings["limits_status"].value == "failed"


def test_no_history_is_zero_but_failed_sources_unavailable(descriptor, summary, now):
    summary["buckets"] = []
    readings = normalize(descriptor, summary, [], now.date(), "UTC", now).readings
    assert readings["usage/all/month/tokens"].value == 0
    summary["sources"][0]["status"] = "failed"
    readings = normalize(descriptor, summary, [], now.date(), "UTC", now).readings
    assert readings["usage/all/month/tokens"].value is None
    assert readings["usage_status"].value == "partial"


@pytest.mark.parametrize("reason", ["unsupported", "probeFailed", "private-unknown-reason"])
def test_unavailable_quota_never_becomes_zero(descriptor, summary, providers, now, reason):
    providers[0]["usageLimits"]["unavailable"] = {"reason": reason}
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limit/codex/primary/used"].value is None
    assert readings["limit/codex/primary/remaining"].value is None
    assert "private-unknown-reason" not in str(readings)


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


def test_malformed_account_still_rejected_during_pairing(descriptor, summary, providers, now):
    providers[0]["usageLimits"]["resetCredits"]["availableCount"] = -1
    with pytest.raises(T3ProtocolError):
        normalize(descriptor, summary, providers, now.date(), "UTC", now)


def test_single_bad_bucket_skipped_with_partial_status(descriptor, summary, now):
    """One bad bucket is skipped; good buckets still count."""
    bad = deepcopy(summary["buckets"][0])
    bad["day"] = "not-a-day"
    summary["buckets"].append(bad)
    snapshot = normalize(descriptor, summary, [], now.date(), "UTC", now, allow_partial=True)
    assert snapshot.readings["usage/all/today/tokens"].value == 370
    assert snapshot.readings["usage_status"].value == "partial"
    assert snapshot.diagnostics["skipped_buckets"] == 1


def test_all_bad_buckets_rejected_strict(descriptor, summary, now):
    """No usable buckets fails closed in strict mode."""
    summary["buckets"][0]["day"] = "not-a-day"
    with pytest.raises(T3ProtocolError):
        normalize(descriptor, summary, [], now.date(), "UTC", now)


def test_empty_sources_partial_without_values(descriptor, summary, now):
    """Empty sources read as partial with no values."""
    summary["sources"] = []
    readings = normalize(
        descriptor, summary, [], now.date(), "UTC", now, allow_partial=True
    ).readings
    assert readings["usage_status"].value == "partial"
    assert readings["usage/all/today/tokens"].value is None


def test_empty_buckets_keep_zero_with_ok_sources(descriptor, summary, now):
    """Empty history with healthy sources stays zero, not failed."""
    summary["buckets"] = []
    readings = normalize(descriptor, summary, [], now.date(), "UTC", now).readings
    assert readings["usage_status"].value == "ok"
    assert readings["usage/all/month/tokens"].value == 0


@pytest.mark.parametrize("value", [1.5, -0.5])
def test_fractional_tokens_rejected(descriptor, summary, now, value):
    """Fractional or negative token counts are rejected."""
    summary["buckets"][0]["totals"]["outputTokens"] = value
    with pytest.raises(T3ProtocolError):
        normalize(descriptor, summary, [], now.date(), "UTC", now)


def test_integral_float_tokens_sum_as_int(descriptor, summary, now):
    """Integral floats like 100.0 sum as ints."""
    summary["buckets"][0]["totals"]["outputTokens"] = 100.0
    value = (
        normalize(descriptor, summary, [], now.date(), "UTC", now)
        .readings["usage/all/today/tokens"]
        .value
    )
    assert value == 420
    assert isinstance(value, int)


def test_quota_rounding_dust_clamped(descriptor, summary, providers, now):
    """Float dust near 100% is clamped, not rejected."""
    providers[0]["usageLimits"]["windows"][0]["usedPercent"] = 100.0000001
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limit/codex/primary/used"].value == 100
    assert readings["limit/codex/primary/remaining"].value == 0
    assert readings["limits_status"].value == "ok"


def test_quota_overflow_rejected_strict(descriptor, summary, providers, now):
    """A real overflow like 101% is rejected."""
    providers[0]["usageLimits"]["windows"][0]["usedPercent"] = 101
    with pytest.raises(T3ProtocolError):
        normalize(descriptor, summary, providers, now.date(), "UTC", now)


def test_naive_timestamps_assumed_utc(descriptor, summary, providers, now):
    """Timestamps without an offset are read as UTC."""
    providers[0]["usageLimits"]["checkedAt"] = "2026-10-19T12:00:00"
    providers[0]["usageLimits"]["windows"][0]["resetsAt"] = "2099-01-01T00:00:00"
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limit/codex/primary/remaining"].value == 75
    assert readings["limits_status"].value == "ok"


def test_quota_keys_escaped_without_collision(descriptor, summary, now):
    """Slashes in server IDs cannot collide sensor keys."""
    providers = [
        {
            "instanceId": "a/b",
            "driver": "d",
            "displayName": "X",
            "usageLimits": {
                "checkedAt": "2026-10-19T12:00:00Z",
                "windows": [
                    {
                        "id": "c",
                        "kind": "s",
                        "label": "W",
                        "usedPercent": 10,
                        "resetsAt": "2099-01-01T00:00:00Z",
                    }
                ],
            },
        },
        {
            "instanceId": "a",
            "driver": "d",
            "displayName": "Y",
            "usageLimits": {
                "checkedAt": "2026-10-19T12:00:00Z",
                "windows": [
                    {
                        "id": "b/c",
                        "kind": "s",
                        "label": "W",
                        "usedPercent": 90,
                        "resetsAt": "2099-01-01T00:00:00Z",
                    }
                ],
            },
        },
    ]
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limit/a%2Fb/c/remaining"].value == 90
    assert readings["limit/a/b%2Fc/remaining"].value == 10


def test_non_dict_reset_credits_skipped_strict(descriptor, summary, providers, now):
    """A malformed optional field is skipped, not fatal."""
    providers[0]["usageLimits"]["resetCredits"] = 5
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limits_status"].value == "ok"
    assert "limit/codex/credits" not in readings


def test_empty_providers_unsupported_without_failure(descriptor, summary, now):
    """No quota accounts reads as unsupported, not ok."""
    readings = normalize(descriptor, summary, [], now.date(), "UTC", now).readings
    assert readings["limits_status"].value == "unsupported"


def test_empty_providers_failed_after_refresh_failure(descriptor, summary, now):
    """No quota accounts after a failed refresh reads as failed."""
    readings = normalize(
        descriptor, summary, [], now.date(), "UTC", now, limits_failed=True, allow_partial=True
    ).readings
    assert readings["limits_status"].value == "failed"


def test_far_future_reset_outlives_check_cap(descriptor, summary, providers):
    """A far-future reset outlives the two-hour check cap."""
    from custom_components.t3_code.models import expire_snapshot

    checked = datetime(2026, 10, 19, 12, tzinfo=UTC)
    providers[0]["usageLimits"]["checkedAt"] = "2026-10-19T12:00:00Z"
    providers[0]["usageLimits"]["windows"][0]["resetsAt"] = "2099-01-01T00:00:00Z"
    snapshot = normalize(descriptor, summary, providers, date(2026, 10, 19), "UTC", checked)
    assert snapshot.readings["limit/codex/primary/remaining"].value == 75
    # Two hours after the check (the old cap) the window must still be valid.
    aged = expire_snapshot(snapshot, datetime(2026, 10, 19, 14, 0, 1, tzinfo=UTC))
    assert aged.readings["limit/codex/primary/remaining"].value == 75
    assert aged.readings["limits_status"].value == "ok"
