"""Allowlisted diagnostics without credentials, addresses, account names, or paths."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import T3ConfigEntry
from .const import CONF_SCAN_INTERVAL, CONF_SHOW_DASHBOARD, DEFAULT_SCAN_INTERVAL, MAX_QUOTA_AGE
from .models import expire_snapshot


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: T3ConfigEntry
) -> dict[str, Any]:
    """Export normalized health information, never raw config or server responses."""
    coordinator = getattr(entry, "runtime_data", None)
    result: dict[str, Any] = {
        "entry_state": entry.state.value,
        "options": {
            CONF_SCAN_INTERVAL: entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            CONF_SHOW_DASHBOARD: entry.options.get(CONF_SHOW_DASHBOARD, True),
        },
        "max_quota_age_seconds": int(MAX_QUOTA_AGE.total_seconds()),
    }
    if coordinator is None or coordinator.data is None:
        return result
    snapshot = expire_snapshot(coordinator.data, dt_util.utcnow())
    result.update(
        {
            "last_update_success": coordinator.last_update_success,
            "last_error_type": type(coordinator.last_exception).__name__
            if coordinator.last_exception
            else None,
            "server": {
                "version": snapshot.environment.get("serverVersion"),
                "orchestration_protocol": snapshot.environment.get("orchestrationProtocolVersion"),
            },
            "usage": {
                **snapshot.diagnostics,
                "status": snapshot.readings["usage_status"].value,
                "last_read": snapshot.readings["last_read"].value,
            },
            "limits": {
                "status": snapshot.readings["limits_status"].value,
                "accounts": [
                    {
                        "account": index,
                        **{
                            key: account[key]
                            for key in ("status", "reason", "checked_at", "expires_at")
                        },
                    }
                    for index, account in enumerate(
                        snapshot.readings["limits_status"].attributes["accounts"], start=1
                    )
                ],
            },
        }
    )
    return result
