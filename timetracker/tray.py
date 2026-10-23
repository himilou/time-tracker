"""Ubuntu StatusNotifierItem integration for the system tray."""

import asyncio
from collections.abc import Callable
import logging
import threading

from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method


_OBJECT_PATH = "/StatusNotifierItem"
_WATCHER_NAME = "org.kde.StatusNotifierWatcher"
_WATCHER_PATH = "/StatusNotifierWatcher"
_WATCHER_INTERFACE = "org.kde.StatusNotifierWatcher"


class StatusNotifierItem(ServiceInterface):
    def __init__(self, icon_pixmap: bytes, on_activate: Callable[[], None]) -> None:
        super().__init__("org.kde.StatusNotifierItem")
        self._icon_pixmap = icon_pixmap
        self._on_activate = on_activate

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return "time-tracker"

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return "Time Tracker"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "Active"

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "u":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> "a(iiay)":
        return [[64, 64, self._icon_pixmap]]

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconPixmap(self) -> "a(iiay)":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconPixmap(self) -> "a(iiay)":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionMovieName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> "(sa(iiay)ss)":
        return ["", [], "Time Tracker", "Click to show the time tracker"]

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> "o":
        return "/"

    @method()
    def Activate(self, _x: "i", _y: "i"):
        self._on_activate()

    @method()
    def SecondaryActivate(self, _x: "i", _y: "i"):
        self._on_activate()

    @method()
    def Scroll(self, _delta: "i", _orientation: "s"):
        pass

    @method()
    def ContextMenu(self, _x: "i", _y: "i"):
        pass


class StatusNotifierTray:
    def __init__(
        self,
        icon_pixmap: bytes,
        on_activate: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self._on_error = on_error
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus: MessageBus | None = None
        self._registered = threading.Event()
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(icon_pixmap, on_activate),
            name="time-tracker-tray",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        if (
            self._loop is not None
            and self._registered.is_set()
            and self._thread.is_alive()
        ):
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self, icon_pixmap: bytes, on_activate: Callable[[], None]) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._register(icon_pixmap, on_activate))
            self._registered.set()
            if not self._stopping.is_set():
                loop.run_forever()
        except Exception as exc:
            logging.exception("Could not initialize the system tray icon")
            self._on_error(exc)
        finally:
            if self._bus is not None:
                self._bus.disconnect()
            loop.close()

    async def _register(
        self, icon_pixmap: bytes, on_activate: Callable[[], None]
    ) -> None:
        bus = await MessageBus().connect()
        self._bus = bus
        bus.export(
            _OBJECT_PATH,
            StatusNotifierItem(icon_pixmap, on_activate),
        )
        introspection = await bus.introspect(_WATCHER_NAME, _WATCHER_PATH)
        watcher_object = bus.get_proxy_object(
            _WATCHER_NAME, _WATCHER_PATH, introspection
        )
        watcher = watcher_object.get_interface(_WATCHER_INTERFACE)
        await watcher.call_register_status_notifier_item(bus.unique_name)
