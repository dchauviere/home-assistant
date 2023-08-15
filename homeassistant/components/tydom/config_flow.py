"""Config flow for Tydom integration."""
from __future__ import annotations

from collections import OrderedDict
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components import zeroconf
from homeassistant.const import (
    CONF_NAME,
    CONF_HOST,
    CONF_CODE,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN
from .pytydom import TydomClient, DeviceInfo

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
        vol.Optional("port"): int,
        vol.Required("serial"): str,
        vol.Optional("password"): str,
    }
)


class TydomFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tydom."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._host: str | None = "192.168.8.8"
        self._port: int | None = None
        self._serial: str | None = "026B86"
        self._password: str = ""
        self._device_info: DeviceInfo | None = None
        self._reauth_entry: ConfigEntry | None = None
        self._device_name: str | None = None
        self._auth_mode: bool | None = None

    @property
    def _name(self) -> str | None:
        return self.context.get(CONF_NAME)

    @_name.setter
    def _name(self, value: str) -> None:
        self.context[CONF_NAME] = value
        self.context["title_placeholders"] = {"name": self._name}

    async def _async_step_user_base(
        self, user_input: dict[str, Any] | None = None, error: str | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            self._serial = user_input[CONF_CODE]
            self._password = user_input[CONF_PASSWORD]
            return await self._async_try_fetch_device_info()

        fields: dict[Any, type] = OrderedDict()
        fields[vol.Required(CONF_HOST, default=self._host or vol.UNDEFINED)] = str
        fields[vol.Optional(CONF_PORT, default=self._port or 443)] = int
        fields[vol.Required(CONF_CODE, default=self._serial or vol.UNDEFINED)] = str
        fields[vol.Optional(CONF_PASSWORD, default=self._password)] = str

        errors = {}
        if error is not None:
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        return await self._async_step_user_base(user_input=user_input)

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user-confirmation of discovered node."""
        if user_input is not None:
            return await self._async_try_fetch_device_info()
        return self.async_show_form(
            step_id="discovery_confirm", description_placeholders={"name": self._name}
        )

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        mac_address: str | None = discovery_info.properties.get("SerialNumber")

        # Mac address was added in Sept 20, 2021.
        # https://github.com/esphome/esphome/pull/2303
        if mac_address is None:
            return self.async_abort(reason="mdns_missing_serial")

        # mac address is lowercase and without :, normalize it
        self._serial = format_mac(mac_address)[6:]

        # Hostname is format: livingroom.local.
        self._name = discovery_info.hostname[: -len(".local.")]
        self._device_name = self._name
        self._host = discovery_info.host
        self._port = discovery_info.port

        # Check if already configured
        await self.async_set_unique_id(self._serial)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port}
        )

        return await self.async_step_discovery_confirm()

    async def _async_try_fetch_device_info(self) -> FlowResult:
        error = await self.fetch_device_info()

        if error is not None:
            return await self._async_step_user_base(error=error)
        return await self._async_authenticate_or_add()

    async def _async_authenticate_or_add(self) -> FlowResult:
        # Only show authentication step if device uses password
        if self._auth_mode:
            return await self.async_step_authenticate()

        return self._async_get_entry()

    @callback
    def _async_get_entry(self) -> FlowResult:
        config_data = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            # The API uses protobuf, so empty string denotes absence
            CONF_PASSWORD: self._password,
            CONF_CODE: self._serial,
        }

        assert self._name is not None
        return self.async_create_entry(
            title=self._name,
            data=config_data,
        )

    async def async_step_authenticate(
        self, user_input: dict[str, Any] | None = None, error: str | None = None
    ) -> FlowResult:
        """Handle getting password for authentication."""
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            error = await self.try_login()
            if error:
                return await self.async_step_authenticate(error=error)
            return self._async_get_entry()

        errors = {}
        if error is not None:
            errors["base"] = error

        return self.async_show_form(
            step_id="authenticate",
            data_schema=vol.Schema({vol.Required("password"): str}),
            description_placeholders={"name": self._name},
            errors=errors,
        )

    async def fetch_device_info(self) -> str | None:
        """Fetch device info from API and return any errors."""
        assert self._host is not None
        assert self._port is not None
        assert self._serial is not None

        cli = TydomClient(self._host, self._port)

        try:
            self._auth_mode = await cli.authorize(self._serial)
        except BaseException as e:
            return f"connection_error: {e}"
        finally:
            cli.stop()

        self._name = f"tydom-{self._serial}"
        await self.async_set_unique_id(self._serial, raise_on_progress=False)
        if not self._reauth_entry:
            self._abort_if_unique_id_configured(
                updates={CONF_HOST: self._host, CONF_PORT: self._port}
            )

        return None

    async def try_login(self) -> str | None:
        """Try logging in to device and return any errors."""
        assert self._host is not None
        assert self._port is not None
        assert self._serial is not None

        cli = TydomClient(self._host, self._port)

        try:
            await cli.authorize(self._serial, self._password)
        except Exception:
            return "connection_error"
        finally:
            cli.stop()

        return None


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
