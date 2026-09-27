"""Transporte BLE contra dobles de bleak.

Esto comprueba la lógica propia (filtro de búsqueda, notificaciones,
reconexión) y que las llamadas encajan con las firmas del bleak instalado. No
prueba la radio: eso solo se puede hacer con el tag encendido.
"""

import asyncio
import inspect
import struct
from types import SimpleNamespace

import bleak
import pytest

from uwb_bridge.transports import ble_link
from uwb_bridge.transports.ble_link import BleTransport

NAME = "UWB-TAG"
SERVICE = "76360001-61ff-4c6a-8d40-75579102b404"
CHAR = "76360002-61ff-4c6a-8d40-75579102b404"


def payload(seq: int) -> bytearray:
    return bytearray(struct.pack("<HHHHBBBI", seq, 2940, 3120, 0xFFFF, 210, 198, 0, 1000 + seq * 100))


class FakeRadio:
    """Lo que hay en el aire: qué se anuncia y las conexiones que se han abierto."""

    def __init__(self):
        self.advertising = []  # pares (device, advertisement)
        self.clients = []
        self.scans = 0
        self.connected = asyncio.Event()


def install_fakes(monkeypatch, radio: FakeRadio):
    class FakeScanner:
        @classmethod
        async def find_device_by_filter(cls, filterfunc, timeout=10.0, **kwargs):
            radio.scans += 1
            await asyncio.sleep(0.01)
            for device, advertisement in radio.advertising:
                if filterfunc(device, advertisement):
                    return device
            return None

    class FakeClient:
        def __init__(self, address_or_ble_device, disconnected_callback=None, **kwargs):
            self.device = address_or_ble_device
            self.disconnected_callback = disconnected_callback
            self.notify = {}
            self.closed = False

        async def __aenter__(self):
            radio.clients.append(self)
            return self

        async def __aexit__(self, *exc):
            self.closed = True

        async def start_notify(self, char_specifier, callback, **kwargs):
            self.notify[char_specifier] = callback
            radio.connected.set()

        def drop(self):
            radio.connected.clear()
            self.disconnected_callback(self)

    monkeypatch.setattr(ble_link, "BleakScanner", FakeScanner)
    monkeypatch.setattr(ble_link, "BleakClient", FakeClient)


def advertise(radio, name=NAME, local_name=NAME, service_uuids=(SERVICE,)):
    device = SimpleNamespace(name=name, address="AA:BB")
    advertisement = SimpleNamespace(local_name=local_name, service_uuids=list(service_uuids))
    radio.advertising.append((device, advertisement))
    return device


async def run_transport(radio, scenario, **kwargs):
    transport = BleTransport(NAME, SERVICE, CHAR, retry_s=0.01, **kwargs)
    samples = []
    task = asyncio.ensure_future(transport.run(samples.append))
    try:
        await asyncio.wait_for(scenario(samples), timeout=5.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return transport, samples


@pytest.mark.asyncio
async def test_notifications_become_samples(monkeypatch):
    radio = FakeRadio()
    install_fakes(monkeypatch, radio)
    advertise(radio)

    async def scenario(samples):
        await radio.connected.wait()
        notify = radio.clients[0].notify[CHAR]
        for seq in (65534, 65535, 0):
            notify(None, payload(seq))
        notify(None, bytearray(b"corto"))
        notify(None, bytearray(20))
        notify(None, payload(1))

    transport, samples = await run_transport(radio, scenario)
    assert [s.seq for s in samples] == [65534, 65535, 0, 1]
    assert samples[0].distances_mm == {"a": 2940, "b": 3120, "c": -1}
    assert samples[0].qualities == {"a": 210, "b": 198, "c": 0}
    assert samples[0].t_ms == 1000 + 65534 * 100
    assert transport.discarded == 2
    assert transport.source == "ble"


@pytest.mark.asyncio
async def test_reconnects_after_the_tag_is_lost(monkeypatch, capsys):
    radio = FakeRadio()
    install_fakes(monkeypatch, radio)

    async def scenario(samples):
        await asyncio.sleep(0.1)  # todavía no hay tag: sigue buscando
        assert radio.clients == [] and radio.scans > 1

        advertise(radio)
        await radio.connected.wait()
        radio.clients[0].notify[CHAR](None, payload(1))
        radio.clients[0].drop()

        while len(radio.clients) < 2 or not radio.connected.is_set():
            await asyncio.sleep(0.01)
        radio.clients[1].notify[CHAR](None, payload(2))

    _, samples = await run_transport(radio, scenario)
    assert [s.seq for s in samples] == [1, 2]
    assert radio.clients[0].closed

    output = capsys.readouterr().out
    assert output.count("buscando") == 1  # no una por intento
    assert output.count("conectado a") == 2
    assert output.count("conexión perdida") == 1


@pytest.mark.asyncio
async def test_bluetooth_errors_are_retried(monkeypatch, capsys):
    radio = FakeRadio()
    install_fakes(monkeypatch, radio)
    advertise(radio)
    real_find = ble_link.BleakScanner.find_device_by_filter
    failures = []

    async def flaky_find(filterfunc, timeout=10.0, **kwargs):
        if len(failures) < 3:
            failures.append(1)
            raise bleak.exc.BleakError("Bluetooth apagado")
        return await real_find(filterfunc, timeout=timeout)

    monkeypatch.setattr(ble_link.BleakScanner, "find_device_by_filter", flaky_find)

    async def scenario(samples):
        await radio.connected.wait()
        radio.clients[0].notify[CHAR](None, payload(9))

    _, samples = await run_transport(radio, scenario)
    assert [s.seq for s in samples] == [9]
    assert capsys.readouterr().out.count("Bluetooth apagado") == 1


@pytest.mark.parametrize(
    "name, local_name, service_uuids, expected",
    [
        (NAME, NAME, [SERVICE], True),
        (None, NAME, [], True),
        ("Arduino", None, [SERVICE], True),  # nombre en caché de macOS, pero el servicio es el del tag
        ("Arduino", "Arduino", [SERVICE.upper()], True),
        (NAME, None, [], True),
        ("Otro", "Otro", ["0000180f-0000-1000-8000-00805f9b34fb"], False),
        (None, None, [], False),
    ],
)
def test_device_filter(name, local_name, service_uuids, expected):
    transport = BleTransport(NAME, SERVICE, CHAR)
    device = SimpleNamespace(name=name)
    advertisement = SimpleNamespace(local_name=local_name, service_uuids=service_uuids)
    assert transport._matches(device, advertisement) is expected


def test_calls_fit_the_installed_bleak_api():
    def callback(*args):
        pass

    scanner = inspect.signature(bleak.BleakScanner.find_device_by_filter)
    scanner.bind(callback, timeout=ble_link.SCAN_TIMEOUT_S)

    client = inspect.signature(bleak.BleakClient.__init__)
    bound = client.bind(None, "dispositivo", disconnected_callback=callback)
    assert "address_or_ble_device" in bound.arguments

    notify = inspect.signature(bleak.BleakClient.start_notify)
    bound = notify.bind(None, CHAR, callback)
    assert list(bound.arguments)[1:] == ["char_specifier", "callback"]

    assert hasattr(bleak.BleakClient, "__aenter__") and hasattr(bleak.BleakClient, "__aexit__")
    fields = bleak.backends.scanner.AdvertisementData._fields
    assert "local_name" in fields and "service_uuids" in fields
    assert issubclass(bleak.exc.BleakDeviceNotFoundError, bleak.exc.BleakError)
