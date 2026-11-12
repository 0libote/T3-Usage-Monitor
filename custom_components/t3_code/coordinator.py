"""Poll usage and quotas independently, and expire quotas between server polls."""

import asyncio
import logging
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from typing import Any

from aiohttp import ClientError
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import T3AuthError, T3Client, T3Error, T3Rpc
from .const import DOMAIN
from .models import Snapshot, expire_snapshot, normalize

LOGGER = logging.getLogger(__name__)

PROVIDER_TIMEOUT = 180

# The poll's shared socket. fetch_snapshot opens one session per poll and
# publishes it here so the fetch helpers reuse that socket instead of opening
# their own; outside a poll the helpers open a short-lived session each.
# (A ContextVar, not a parameter, because test doubles pin the helpers'
# long-standing call shapes.)
_current_rpc: ContextVar[T3Rpc | None] = ContextVar("t3_current_rpc", default=None)


async def _fetch_usage(client: T3Client, descriptor: dict, today: date, time_zone: str) -> dict:
    """Read usage buckets, reusing the poll's socket when one is already open."""
    payload = {
        "sinceDay": today.replace(day=1).isoformat(),
        "untilDay": today.isoformat(),
        "timeZone": time_zone,
        "resolution": "day",
        "awaitRefresh": True,
    }
    shared = _current_rpc.get()
    try:
        async with asyncio.timeout(PROVIDER_TIMEOUT):
            if shared is None:
                async with client.rpc(descriptor) as fresh_rpc:
                    return await fresh_rpc.call("server.getUsageSummary", payload)
            return await shared.call("server.getUsageSummary", payload)
    except TimeoutError as err:
        raise T3Error("T3 timed out while answering the usage request") from err


async def _fetch_providers(client: T3Client, descriptor: dict) -> tuple[list[dict[str, Any]], bool]:
    """Bound the refresh and its fallback with one shared timeout."""

    async def _refresh(target: T3Rpc) -> tuple[list[dict[str, Any]], bool]:
        refreshed = await target.call("server.refreshProviders", {})
        return refreshed["providers"], False

    try:
        async with asyncio.timeout(PROVIDER_TIMEOUT):
            shared = _current_rpc.get()
            if shared is not None:
                try:
                    return await _refresh(shared)
                except T3AuthError:
                    raise
                except (T3Error, ClientError, KeyError, TypeError):
                    # The poll's socket may have died with the usage call; retry
                    # below on a fresh session before falling back to config.
                    pass
            try:
                async with client.rpc(descriptor) as fresh_rpc:
                    return await _refresh(fresh_rpc)
            except T3AuthError:
                raise
            except (T3Error, ClientError, KeyError, TypeError):
                # A timeout/closed socket cannot be reused. Config is only a fallback
                # for discovering unavailable sensors; cached quotas are never current.
                try:
                    async with client.rpc(descriptor) as fresh_rpc:
                        config = await fresh_rpc.call("server.getConfig", {})
                        return config["providers"], True
                except T3AuthError:
                    raise
                except (T3Error, KeyError, TypeError):
                    return [], True
    except TimeoutError:
        return [], True


async def fetch_snapshot(
    client: T3Client,
    time_zone: str,
    expected_id: str,
    *,
    allow_partial: bool = False,
    now: datetime | None = None,
) -> Snapshot:
    """Check identity before authentication, then read usage and quotas over one socket."""
    descriptor = await client.descriptor()
    if descriptor["environmentId"] != expected_id:
        raise T3AuthError("This address now belongs to a different T3 server")
    start = now if now is not None else dt_util.utcnow()
    today = start.astimezone(dt_util.get_time_zone(time_zone)).date()
    async with client.rpc(descriptor) as rpc:
        token = _current_rpc.set(rpc)
        try:
            try:
                summary: Any = await _fetch_usage(client, descriptor, today, time_zone)
            except T3AuthError:
                raise
            except T3Error:
                if not allow_partial:
                    raise
                summary = None
            providers_result = await _fetch_providers(client, descriptor)
        finally:
            _current_rpc.reset(token)
    if not isinstance(providers_result, tuple):
        raise T3Error("T3 returned an invalid provider response")
    providers, limits_failed = providers_result
    # One instant for the day check and the snapshot, so usage windows and
    # quota ages cannot straddle midnight differently.
    poll_now = now if now is not None else dt_util.utcnow()
    current_day = poll_now.astimezone(dt_util.get_time_zone(time_zone)).date()
    if current_day != today:
        # A slow quota refresh can carry yesterday's usage past midnight.
        # The re-fetch is bounded so one slow poll cannot stall past the
        # shortest interval; a failed re-fetch still fails the cycle
        # because yesterday's buckets would be wrong for the new day.
        today = current_day
        try:
            async with asyncio.timeout(PROVIDER_TIMEOUT):
                summary = await _fetch_usage(client, descriptor, today, time_zone)
        except T3AuthError:
            raise
        except (T3Error, TimeoutError) as err:
            summary = err
    if isinstance(summary, T3Error):
        if not allow_partial:
            raise summary
        summary = None
    snapshot = normalize(
        descriptor,
        summary,
        providers,
        today,
        time_zone,
        # Same instant the current day was read, so usage windows and quota
        # ages cannot straddle midnight differently.
        poll_now,
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
        data = self.data
        if data is None:
            return
        try:
            self.data = expire_snapshot(data, now)
            # Local expiry must not mark a failed server connection successful or
            # postpone the next network poll (async_set_updated_data would do both).
            self.async_update_listeners()
        finally:
            # A malformed snapshot must not kill the timer chain: on failure
            # self.data still holds the previous snapshot, so rescheduling
            # from it keeps expiry alive until the next successful poll.
            try:
                self._schedule_expiry(self.data)
            except (KeyError, TypeError, AttributeError):
                LOGGER.warning("T3 quota expiry timer could not be rescheduled")

    async def async_shutdown(self) -> None:
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        await super().async_shutdown()

    async def _async_update_data(self) -> Snapshot:
        try:
            entry = self.config_entry
            assert entry is not None
            snapshot = await fetch_snapshot(
                self.client, self.hass.config.time_zone, entry.unique_id, allow_partial=True
            )
        except T3AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except T3Error as err:
            raise UpdateFailed(str(err)) from err
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise UpdateFailed("T3 returned an invalid usage response") from err
        self._schedule_expiry(snapshot)
        return snapshot
