import asyncio
import os
import time

import serial

from ..parsers import ParseError, is_comment, parse_serial_line, parse_sync_reply
from .base import SampleHandler, SyncHandler, Transport

READ_TIMEOUT_S = 0.2
MAX_LINE_BYTES = 256


class SerialTransport(Transport):
    source = "serial"

    def __init__(self, port: str, baud: int, retry_s: float = 2.0, sync_interval_s: float = 10.0) -> None:
        super().__init__()
        self._port_name = port
        self._baud = baud
        self._retry_s = retry_s
        self._sync_interval_s = sync_interval_s
        self._sync_t0_ms: float | None = None

    async def run(self, on_sample: SampleHandler, on_sync: SyncHandler | None = None) -> None:
        waiting = False
        while True:
            try:
                port = await asyncio.to_thread(self._open)
            except (serial.SerialException, OSError, ValueError):
                if not waiting:
                    print(f"Serie: no se puede abrir {self._port_name}; reintentando…")
                    waiting = True
                await asyncio.sleep(self._retry_s)
                continue

            waiting = False
            print(f"Serie: conectado a {self._port_name}")
            try:
                await self._session(port, on_sample, on_sync)
            finally:
                port.close()
            print("Serie: conexión perdida; reintentando…")
            waiting = True
            await asyncio.sleep(self._retry_s)

    def _open(self) -> serial.Serial:
        return serial.Serial(self._port_name, self._baud, timeout=READ_TIMEOUT_S, write_timeout=1.0)

    async def _session(self, port: serial.Serial, on_sample: SampleHandler, on_sync: SyncHandler | None) -> None:
        """Lee hasta que el puerto falla. Vuelve sin excepción cuando se pierde la conexión."""
        self._sync_t0_ms = None
        sync_task = asyncio.create_task(self._sync_loop(port)) if on_sync else None
        reading: asyncio.Future | None = None
        pending = b""
        # Lo primero que llega tras abrir puede ser una línea empezada; si le
        # faltan solo dígitos del seq pasaría por un registro válido.
        aligned = False
        try:
            while True:
                reading = asyncio.ensure_future(asyncio.to_thread(_read_chunk, port))
                data, received_ms = await asyncio.shield(reading)
                if data is None:
                    return
                if not data:
                    # Hay drivers que al desenchufar no dan error y solo dejan de
                    # entregar datos; el dispositivo sí desaparece de /dev.
                    if not os.path.exists(self._port_name):
                        return
                    continue
                pending += data
                *lines, pending = pending.split(b"\n")
                if len(pending) > MAX_LINE_BYTES:
                    pending = b""
                    self.discarded += 1
                for line in lines:
                    # Una línea `#` nunca se confunde con un registro: no se
                    # tira, que puede ser la respuesta al primer SYNC.
                    if aligned or is_comment(line):
                        self._handle_line(line, received_ms, on_sample, on_sync)
                    aligned = True
        finally:
            if sync_task is not None:
                sync_task.cancel()
            if reading is not None and not reading.done():
                # La lectura sigue en su hilo; se espera a que venza su timeout
                # para no cerrar el puerto mientras lo está usando.
                await asyncio.wait([reading])

    def _handle_line(self, line: bytes, received_ms: float, on_sample: SampleHandler, on_sync: SyncHandler | None) -> None:
        t_tag_ms = parse_sync_reply(line)
        if t_tag_ms is not None:
            t0_ms, self._sync_t0_ms = self._sync_t0_ms, None
            if t0_ms is not None and on_sync is not None:
                on_sync(t0_ms, received_ms, t_tag_ms)
            return
        try:
            sample = parse_serial_line(line, round(received_ms))
        except ParseError:
            self.discarded += 1
            return
        if sample is not None:
            on_sample(sample)

    async def _sync_loop(self, port: serial.Serial) -> None:
        while True:
            try:
                await asyncio.to_thread(self._send_sync, port)
            except (serial.SerialException, OSError):
                # Si el puerto ha caído lo detecta la lectura; aquí no hay nada que hacer.
                pass
            await asyncio.sleep(self._sync_interval_s)

    def _send_sync(self, port: serial.Serial) -> None:
        # t0 se anota antes de escribir y en este mismo hilo, para que ya esté
        # puesto cuando llegue la respuesta.
        self._sync_t0_ms = time.time() * 1000
        port.write(b"SYNC\n")


def _read_chunk(port: serial.Serial) -> tuple[bytes | None, float]:
    """Lee lo que haya, o espera hasta el timeout. Devuelve None si el puerto ha caído.

    La hora se toma aquí, en el hilo que lee, y sin redondear: la del SYNC se
    compara con la de envío y un redondeo puede dejarla por detrás.
    """
    try:
        data = port.read(max(port.in_waiting, 1))
    except (serial.SerialException, OSError):
        return None, 0.0
    return data, time.time() * 1000
