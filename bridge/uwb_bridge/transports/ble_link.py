import asyncio
import time

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from ..parsers import ParseError, parse_ble_payload
from .base import SampleHandler, SyncHandler, Transport

SCAN_TIMEOUT_S = 10.0


class BleTransport(Transport):
    source = "ble"

    def __init__(self, name: str, service_uuid: str, char_uuid: str, retry_s: float = 2.0) -> None:
        super().__init__()
        self._name = name
        self._service_uuid = service_uuid.lower()
        self._char_uuid = char_uuid
        self._retry_s = retry_s

    async def run(self, on_sample: SampleHandler, on_sync: SyncHandler | None = None) -> None:
        print(f"BLE: buscando {self._name}…")
        last_error = None
        while True:
            try:
                device = await BleakScanner.find_device_by_filter(self._matches, timeout=SCAN_TIMEOUT_S)
                if device is None:
                    continue
                last_error = None
                await self._session(device, on_sample)
                print("BLE: conexión perdida; reintentando…")
            except (BleakError, OSError, asyncio.TimeoutError) as exc:
                # Con el Bluetooth apagado el mismo error se repite en cada
                # intento; se avisa una vez.
                error = str(exc) or type(exc).__name__
                if error != last_error:
                    print(f"BLE: {error}; reintentando…")
                    last_error = error
            await asyncio.sleep(self._retry_s)

    def _matches(self, device, advertisement) -> bool:
        # macOS guarda en caché el nombre del periférico y a veces anuncia uno
        # antiguo; el UUID del servicio identifica al tag igual de bien.
        if advertisement.local_name == self._name or device.name == self._name:
            return True
        return self._service_uuid in (uuid.lower() for uuid in advertisement.service_uuids)

    async def _session(self, device, on_sample: SampleHandler) -> None:
        disconnected = asyncio.Event()

        def on_notify(_characteristic, payload: bytearray) -> None:
            t_host_ms = round(time.time() * 1000)
            try:
                sample = parse_ble_payload(bytes(payload), t_host_ms)
            except ParseError:
                self.discarded += 1
                return
            on_sample(sample)

        async with BleakClient(device, disconnected_callback=lambda _client: disconnected.set()) as client:
            await client.start_notify(self._char_uuid, on_notify)
            print(f"BLE: conectado a {device.name or self._name}")
            await disconnected.wait()
