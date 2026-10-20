"""Poll usage and quotas independently, and expire quotas between server polls."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import T3AuthError, T3Client, T3Error
from .const import DOMAIN
from .models import Snapshot, expire_snapshot, normalize

LOGGER = logging.getLogger(__name__)


async def _fetch_usage(client: T3Client, descriptor: dict, today, time_zone: str) -> dict:
    async with client.rpc(descriptor) as rpc:
        return await rpc.call(
            "server.getUsageSummary",
            {
                "sinceDay": today.replace(day=1).isoformat(),
                "untilDay": today.isoformat(),
                "timeZone": time_zone,
                "resolution": "day",
                "awaitRefresh": True,
            },
        )


async def _fetch_providers(client: T3Client, descriptor: dict) -> tuple[list, bool]:
    try:
        async with client.rpc(descriptor) as rpc:
            refreshed = await rpc.call("server.refreshProviders", {})
            return refreshed["providers"], False
    except T3AuthError:
        raise
    except T3Error, KeyError, TypeError:
        # A timeout/closed socket cannot be reused. Config is only a fallback
        # for discovering unavailable sensors; cached quotas are never current.
        try:
            async with client.rpc(descriptor) as rpc:
                config = await rpc.call("server.getConfig", {})
                return config["providers"], True
        except T3AuthError:
            raise
        except T3Error, KeyError, TypeError:
            return [], True


async def fetch_snapshot(
    client: T3Client, time_zone: str, expected_id: str, *, allow_partial: bool = False
) -> Snapshot:
    """Check identity before authentication, then use separate bounded sockets."""
    descriptor = await client.descriptor()
    if descriptor["environmentId"] != expected_id:
        raise T3AuthError("This address now belongs to a different T3 server")
    now = dt_util.utcnow()
    today = now.astimezone(dt_util.get_time_zone(time_zone)).date()
    results = await asyncio.gather(
        _fetch_usage(client, descriptor, today, time_zone),
        _fetch_providers(client, descriptor),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, T3AuthError):
            raise result
        if isinstance(result, BaseException) and not isinstance(result, T3Error):
            raise result
    summary: Any = results[0]
    if isinstance(summary, T3Error):
        if not allow_partial:
            raise summary
        summary = None
    providers, limits_failed = results[1]
    snapshot = normalize(
        descriptor,
        summary,
        providers,
        today,
        time_zone,
        dt_util.utcnow(),
        limits_failed=limits_failed,
        allow_partial=allow_partial,
    )
    if (
        snapshot.readings["usage_status"].value == "failed"
        and snapshot.readings["limits_status"].value == "failed"
    ):
        raise T3Error("Could not refresh T3 usage or quotas")
    return snapshot


class T3Coordinator(DataUpdateCoordinator[Snapshot]):
    """Keep sensors on one polling schedule and let HA handle retry/reauth."""

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
        self._cancel_expiry: CALLBACK_TYPE | None = None

    @callback
    def _schedule_expiry(self, snapshot: Snapshot) -> None:
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        now = dt_util.utcnow()
        deadlines = [
            reading.expires_at
            for reading in snapshot.readings.values()
            if reading.value is not None
            and reading.expires_at is not None
            and reading.expires_at > now
        ]
        if deadlines:
            self._cancel_expiry = async_track_point_in_utc_time(
                self.hass, self._expire_quotas, min(deadlines)
            )

    @callback
    def _expire_quotas(self, now: datetime) -> None:
        self._cancel_expiry = None
        self.data = expire_snapshot(self.data, now)
        # Local expiry must not mark a failed server connection successful or
        # postpone the next network poll (async_set_updated_data would do both).
        self.async_update_listeners()
        self._schedule_expiry(self.data)

    async def async_shutdown(self) -> None:
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        await super().async_shutdown()

    async def _async_update_data(self) -> Snapshot:
        try:
            snapshot = await fetch_snapshot(
                self.client, self.hass.config.time_zone, self.entry.unique_id, allow_partial=True
            )
        except T3AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except T3Error as err:
            raise UpdateFailed(str(err)) from err
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise UpdateFailed("T3 returned an invalid usage response") from err
        self._schedule_expiry(snapshot)
        return snapshot
