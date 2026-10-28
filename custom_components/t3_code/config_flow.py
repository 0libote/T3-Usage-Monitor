"""Set up a T3 Code server using its existing pairing or bearer credentials."""

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import T3AuthError, T3Client, T3Error, T3ProtocolError, parse_connection
from .const import (
    CONF_CREDENTIAL,
    CONF_CREDENTIAL_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_DASHBOARD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import fetch_snapshot


class T3ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Validate identity, exchange credentials, and test metrics before saving."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending_auth: tuple[str, str, str, str, str] | None = None

    def _schema(self) -> vol.Schema:
        default_url = ""
        if self.source == config_entries.SOURCE_REAUTH:
            default_url = self._get_reauth_entry().data[CONF_URL]
        elif self.source == config_entries.SOURCE_RECONFIGURE:
            default_url = self._get_reconfigure_entry().data[CONF_URL]
        return vol.Schema(
            {
                vol.Required(CONF_URL, default=default_url): str,
                vol.Required(CONF_CREDENTIAL_TYPE, default="pairing"): SelectSelector(
                    SelectSelectorConfig(
                        options=["pairing", "bearer"], translation_key="credential_type"
                    )
                ),
                vol.Optional(CONF_CREDENTIAL, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

    async def _check_identity(self, identity: str) -> FlowResult | None:
        """Abort when reauth/reconfigure targets a different server."""
        if self.source == config_entries.SOURCE_REAUTH:
            if identity != self._get_reauth_entry().unique_id:
                return self.async_abort(reason="wrong_server")
        elif self.source == config_entries.SOURCE_RECONFIGURE:
            if identity != self._get_reconfigure_entry().unique_id:
                return self.async_abort(reason="wrong_server")
        else:
            await self.async_set_unique_id(identity)
            self._abort_if_unique_id_configured()
        return None

    async def _ensure_token(
        self, client: T3Client, url: str, credential: str, credential_type: str, identity: str
    ) -> None:
        """Exchange a pairing token once; reuse the bearer within this flow."""
        # A successful exchange consumes a pairing token. If probing metrics
        # fails, retain the bearer in this flow so a retry doesn't consume it again.
        cache_key = (url, credential, credential_type, identity)
        if self._pending_auth and self._pending_auth[:4] == cache_key:
            client.token = self._pending_auth[4]
        elif credential_type == "pairing":
            await client.pair(credential)
            self._pending_auth = (*cache_key, client.token)
        elif credential_type == "bearer":
            client.token = credential
        else:
            raise ValueError("Unknown credential type")

    def _finish(self, data: dict[str, Any], title: str) -> FlowResult:
        """Create or update the entry after a successful probe."""
        if self.source == config_entries.SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        if self.source == config_entries.SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=data
            )
        return self.async_create_entry(title=title, data=data)

    async def _connect(self, user_input: dict[str, Any], step_id: str) -> FlowResult:
        errors: dict[str, str] = {}
        try:
            url, credential = parse_connection(
                user_input[CONF_URL], user_input.get(CONF_CREDENTIAL, "")
            )
            client = T3Client(async_get_clientsession(self.hass), url)
            descriptor = await client.descriptor()
            if (
                self._pending_auth
                and self._pending_auth[0] == url
                and self._pending_auth[3] != descriptor["environmentId"]
            ):
                return self.async_abort(reason="wrong_server")
            if abort := await self._check_identity(descriptor["environmentId"]):
                return abort
            await self._ensure_token(
                client,
                url,
                credential,
                user_input[CONF_CREDENTIAL_TYPE],
                descriptor["environmentId"],
            )
            await fetch_snapshot(client, self.hass.config.time_zone, descriptor["environmentId"])
            data = {CONF_URL: url, CONF_TOKEN: client.token}
            return self._finish(data, descriptor.get("label", "T3 Code"))
        except ValueError:
            errors["base"] = "invalid_connection"
        except T3AuthError:
            self._pending_auth = None
            errors["base"] = "invalid_auth"
        except T3ProtocolError:
            errors["base"] = "unsupported_server"
        except T3Error:
            errors["base"] = "cannot_connect"
        except KeyError, TypeError, AttributeError:
            errors["base"] = "unsupported_server"
        return self.async_show_form(step_id=step_id, data_schema=self._schema(), errors=errors)

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return await self._connect(user_input, "user")
        return self.async_show_form(step_id="user", data_schema=self._schema())

    async def async_step_reauth(self, entry_data) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return await self._connect(user_input, "reauth_confirm")
        return self.async_show_form(step_id="reauth_confirm", data_schema=self._schema())

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return await self._connect(user_input, "reconfigure")
        return self.async_show_form(step_id="reconfigure", data_schema=self._schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return T3OptionsFlow()


class T3OptionsFlow(config_entries.OptionsFlow):
    """Set a polling interval no shorter than T3's own five-minute limit checks."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=300, max=3600)),
                    vol.Optional(
                        CONF_SHOW_DASHBOARD,
                        default=self.config_entry.options.get(CONF_SHOW_DASHBOARD, True),
                    ): bool,
                }
            ),
        )
