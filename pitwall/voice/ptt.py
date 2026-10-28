"""Push-to-Talk: Lenkradtaste (evdev unter Linux, SDL/pygame als Fallback), global.

* Funktioniert unabhängig vom Fensterfokus (das Spiel bleibt im Vordergrund).
* "Taste drücken zum Zuweisen": :meth:`PTTManager.learn` wartet auf die nächste
  Taste irgendeines Eingabegeräts und speichert Gerät + Code.
* Dashboard-Mikrofonknopf und Tastatur (``PTTManager.press/release``) gehen
  über denselben Weg.

Linux-Rechte: Der Benutzer braucht Lesezugriff auf ``/dev/input/event*``.
Lenkräder bekommen über ``uaccess`` meist automatisch Rechte; sonst siehe
``docs/linux-proton.md`` (udev-Regel).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Binding:
    backend: str  # evdev | sdl
    device: str  # Name (evdev: Name oder Pfad, sdl: Joystick-Name)
    code: int  # evdev-Keycode bzw. SDL-Button-Index

    def to_dict(self) -> dict[str, Any]:
        return {"backend": self.backend, "device": self.device, "code": self.code}


class PTTManager:
    def __init__(self, on_press: Callable[[str], None], on_release: Callable[[str], None],
                 binding: Binding | None = None, backend: str = "auto"):
        self.on_press = on_press
        self.on_release = on_release
        self.binding = binding
        self.backend_pref = backend
        self.pressed = False
        self.backend = "none"
        self._learn_future: asyncio.Future[Binding] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._sdl_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.devices: list[str] = []
        self.last_error: str | None = None

    # ----------------------------------------------------------- manuell
    def press(self, source: str = "button") -> None:
        if not self.pressed:
            self.pressed = True
            self.on_press(source)

    def release(self, source: str = "button") -> None:
        if self.pressed:
            self.pressed = False
            self.on_release(source)

    # ------------------------------------------------------------ Start
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.backend_pref in ("auto", "evdev") and sys.platform.startswith("linux") and await self._start_evdev():
            self.backend = "evdev"
            return
        if self.backend_pref in ("auto", "sdl") and self._start_sdl():
            self.backend = "sdl"
            return
        log.info("PTT: kein Hardware-Backend aktiv (Dashboard-Knopf/Tastatur funktionieren weiterhin)")

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()

    def _event(self, backend: str, device: str, code: int, value: int) -> None:
        """value: 1 = gedrückt, 0 = losgelassen."""
        if self._learn_future is not None and not self._learn_future.done() and value == 1:
            self._learn_future.set_result(Binding(backend, device, code))
            return
        b = self.binding
        if b is None or b.backend != backend or b.code != code:
            return
        if b.device and b.device not in (device,) and not device.endswith(b.device) and b.device not in device:
            return
        if value == 1:
            self.press(backend)
        elif value == 0:
            self.release(backend)

    async def learn(self, timeout: float = 15.0) -> Binding | None:
        loop = asyncio.get_running_loop()
        self._learn_future = loop.create_future()
        try:
            return await asyncio.wait_for(self._learn_future, timeout)
        except TimeoutError:
            return None
        finally:
            self._learn_future = None

    # ------------------------------------------------------------ evdev
    async def _start_evdev(self) -> bool:
        try:
            import evdev
        except ImportError:
            self.last_error = "python-evdev nicht installiert"
            return False
        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except (PermissionError, OSError):
                continue
            caps = dev.capabilities()
            if evdev.ecodes.EV_KEY in caps:
                devices.append(dev)
        if not devices:
            self.last_error = "keine lesbaren /dev/input-Geräte (Rechte?)"
            return False
        self.devices = [f"{d.name} ({d.path})" for d in devices]
        for dev in devices:
            self._tasks.append(asyncio.create_task(self._evdev_reader(dev), name=f"ptt-{dev.path}"))
        log.info("PTT evdev: %d Geräte", len(devices))
        return True

    async def _evdev_reader(self, dev: Any) -> None:
        import evdev

        try:
            async for ev in dev.async_read_loop():
                if ev.type == evdev.ecodes.EV_KEY and ev.value in (0, 1):
                    self._event("evdev", dev.name, ev.code, ev.value)
        except (OSError, asyncio.CancelledError):
            return

    # -------------------------------------------------------------- SDL
    def _start_sdl(self) -> bool:
        try:
            import os

            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
            import pygame
        except ImportError:
            self.last_error = (self.last_error or "") + "; pygame nicht installiert"
            return False
        try:
            pygame.init()
            pygame.joystick.init()
        except Exception as exc:
            self.last_error = f"SDL: {exc}"
            return False
        self._sdl_thread = threading.Thread(target=self._sdl_loop, daemon=True, name="ptt-sdl")
        self._sdl_thread.start()
        return True

    def _sdl_loop(self) -> None:
        import pygame

        joysticks: dict[int, Any] = {}
        while not self._stop.is_set():
            try:
                for event in pygame.event.get():
                    if event.type == pygame.JOYDEVICEADDED:
                        js = pygame.joystick.Joystick(event.device_index)
                        joysticks[js.get_instance_id()] = js
                        self.devices = [j.get_name() for j in joysticks.values()]
                    elif event.type == pygame.JOYDEVICEREMOVED:
                        joysticks.pop(event.instance_id, None)
                    elif event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                        js = joysticks.get(event.instance_id)
                        name = js.get_name() if js else ""
                        value = 1 if event.type == pygame.JOYBUTTONDOWN else 0
                        if self._loop:
                            self._loop.call_soon_threadsafe(self._event, "sdl", name, event.button, value)
            except Exception:
                pass
            time.sleep(0.005)

    def info(self) -> dict[str, Any]:
        return {"backend": self.backend, "binding": self.binding.to_dict() if self.binding else None,
                "pressed": self.pressed, "devices": self.devices[:20], "error": self.last_error,
                "learning": self._learn_future is not None}
