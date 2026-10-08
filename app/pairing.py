"""
Bluetooth pairing agent for the Shower Pi Controller.

Registers a BlueZ agent (org.bluez.Agent1) so pairing and first-time
connection requests show up as a prompt on the touchscreen instead of being
auto-accepted. Also manages a short "pairing window" during which the Pi is
discoverable, triggered by the Pair button on the UI.
"""

import asyncio
import logging
import time

from dbus_next import DBusError, Variant
from dbus_next.service import ServiceInterface, method

log = logging.getLogger("controller.pairing")

AGENT_PATH     = "/com/showerpi/agent"
ADAPTER_PATH   = "/org/bluez/hci0"
PROMPT_TIMEOUT = 30    # seconds to wait for a tap before auto-rejecting
PAIRING_WINDOW = 120   # seconds the Pi stays discoverable after "Pair new device"
CLOSE_AFTER_OK = 20    # seconds to keep the window open after an approval


def _reject(msg="Rejected"):
    return DBusError("org.bluez.Error.Rejected", msg)


class PairingManager:
    def __init__(self, state, broadcast):
        self.state = state
        self.broadcast = broadcast
        self.bus = None
        self._future = None
        self._window_task = None
        state.setdefault("bluetooth", {"prompt": None, "pairing_until": None})

    # ----- D-Bus helpers -----

    async def _props(self, path):
        intro = await self.bus.introspect("org.bluez", path)
        obj = self.bus.get_proxy_object("org.bluez", path, intro)
        return obj.get_interface("org.freedesktop.DBus.Properties")

    async def _device_name(self, path):
        try:
            props = await self._props(path)
            v = await props.call_get("org.bluez.Device1", "Alias")
            return v.value
        except Exception:
            return "Unknown device"

    async def is_trusted(self, path):
        try:
            props = await self._props(path)
            v = await props.call_get("org.bluez.Device1", "Trusted")
            return bool(v.value)
        except Exception:
            return False

    async def trust(self, path):
        try:
            props = await self._props(path)
            await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
        except Exception as e:
            log.warning("Could not mark %s trusted: %s", path, e)

    # ----- agent registration -----

    async def start(self, bus):
        self.bus = bus
        bus.export(AGENT_PATH, _Agent(self))
        intro = await bus.introspect("org.bluez", "/org/bluez")
        obj = bus.get_proxy_object("org.bluez", "/org/bluez", intro)
        mgr = obj.get_interface("org.bluez.AgentManager1")
        await mgr.call_register_agent(AGENT_PATH, "DisplayYesNo")
        await mgr.call_request_default_agent(AGENT_PATH)
        log.info("Pairing agent registered")

    # ----- on-screen prompt -----

    async def ask(self, kind, device_path, passkey=None):
        if self._future is not None and not self._future.done():
            raise _reject("Another request is pending")
        name = await self._device_name(device_path)
        self._future = asyncio.get_running_loop().create_future()
        self.state["bluetooth"]["prompt"] = {
            "kind": kind,
            "device": name,
            "passkey": None if passkey is None else f"{passkey:06d}",
        }
        await self.broadcast()
        try:
            accepted = await asyncio.wait_for(self._future, PROMPT_TIMEOUT)
        except asyncio.TimeoutError:
            accepted = False
        finally:
            self._future = None
            self.state["bluetooth"]["prompt"] = None
            await self.broadcast()
        log.info("%s request from %s: %s", kind, name, "accepted" if accepted else "rejected")
        if not accepted:
            raise _reject()

    def respond(self, accepted):
        if self._future is None or self._future.done():
            return False
        self._future.set_result(accepted)
        return True

    def cancel(self):
        if self._future is not None and not self._future.done():
            self._future.set_result(False)

    # ----- pairing window (discoverable mode) -----

    def _restart_window_task(self, seconds):
        if self._window_task is not None:
            self._window_task.cancel()
        self._window_task = asyncio.create_task(self._expire_after(seconds))

    async def _expire_after(self, seconds):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        await self.close_window()

    async def open_window(self, seconds=PAIRING_WINDOW):
        props = await self._props(ADAPTER_PATH)
        await props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", True))
        await props.call_set("org.bluez.Adapter1", "DiscoverableTimeout", Variant("u", seconds))
        await props.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", True))
        self.state["bluetooth"]["pairing_until"] = time.time() + seconds
        self._restart_window_task(seconds)
        await self.broadcast()

    async def close_window(self):
        task = self._window_task
        self._window_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        try:
            props = await self._props(ADAPTER_PATH)
            await props.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", False))
        except Exception as e:
            log.warning("Could not disable discoverable: %s", e)
        self.state["bluetooth"]["pairing_until"] = None
        await self.broadcast()

    async def close_soon(self):
        """After an approval, leave the window open briefly so pairing can finish."""
        if self.state["bluetooth"]["pairing_until"] is None:
            return
        self.state["bluetooth"]["pairing_until"] = time.time() + CLOSE_AFTER_OK
        self._restart_window_task(CLOSE_AFTER_OK)
        await self.broadcast()


class _Agent(ServiceInterface):
    def __init__(self, mgr):
        super().__init__("org.bluez.Agent1")
        self.mgr = mgr

    @method()
    def Release(self):
        log.info("Agent released by BlueZ")

    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        raise _reject("PIN pairing not supported")

    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        raise _reject("Passkey entry not supported")

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'):
        pass

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
        pass

    @method()
    async def RequestConfirmation(self, device: 'o', passkey: 'u'):
        await self.mgr.ask("confirm", device, passkey)
        await self.mgr.trust(device)
        await self.mgr.close_soon()

    @method()
    async def RequestAuthorization(self, device: 'o'):
        await self.mgr.ask("authorize", device)
        await self.mgr.trust(device)
        await self.mgr.close_soon()

    @method()
    async def AuthorizeService(self, device: 'o', uuid: 's'):
        if await self.mgr.is_trusted(device):
            return
        await self.mgr.ask("authorize", device)

    @method()
    def Cancel(self):
        self.mgr.cancel()
