"""Poll one T3 server for usage history and fresh provider limits."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import T3AuthError, T3Client, T3Error, T3ProtocolError
from .const import DOMAIN
from .models import Snapshot, normalize

LOGGER = logging.getLogger(__name__)


async def fetch_snapshot(client: T3Client, time_zone: str, expected_id: str) -> Snapshot:
    """Check identity before sending the bearer token, then request this month."""
    descriptor = await client.descriptor()
    if descriptor["environmentId"] != expected_id:
        raise T3AuthError("This address now belongs to a different T3 server")
    now = dt_util.utcnow()
    today = now.astimezone(dt_util.get_time_zone(time_zone)).date()
    async with client.rpc(descriptor) as rpc:
        summary = await rpc.call(
            "server.getUsageSummary",
            {
                "sinceDay": today.replace(day=1).isoformat(),
                "untilDay": today.isoformat(),
                "timeZone": time_zone,
                "resolution": "day",
                "awaitRefresh": True,
            },
        )
        config = await rpc.call("server.getConfig", {})
        providers = config.get("providers", [])
        limits_failed = False
        try:
            refreshed = await rpc.call("server.refreshProviders", {})
            providers = refreshed["providers"]
        except T3AuthError, T3ProtocolError:
            raise
        except T3Error:
            # Token/cost history remains useful when a provider probe fails.
            limits_failed = True
    return normalize(
        descriptor, summary, providers, today, time_zone, now, limits_failed=limits_failed
    )


class T3Coordinator(DataUpdateCoordinator[Snapshot]):
    """Keep all entities on one refresh and let HA handle retry/reauth."""

    def __init__(self, hass: HomeAssistant, entry, client: T3Client, interval: int) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> Snapshot:
        try:
            return await fetch_snapshot(
                self.client, self.hass.config.time_zone, self.entry.unique_id
            )
        except T3AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except T3Error as err:
            raise UpdateFailed(str(err)) from err
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise UpdateFailed("T3 returned an invalid usage response") from err
