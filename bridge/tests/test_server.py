import asyncio
import json
import time
import urllib.error
import urllib.request

import pytest
from websockets.asyncio.client import connect

from uwb_bridge.config import config_message
from uwb_bridge.server import Hub, start_http_server


def sample_message(seq: int, padding: int = 0) -> dict:
    return {"type": "sample", "seq": seq, "padding": "x" * padding}


async def wait_until(condition, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "la condición no se cumplió a tiempo"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_each_client_gets_config_first_and_then_samples(config, free_port):
    port = free_port()
    hub = Hub(config_message(config, "replay"))
    async with hub.serve(port):
        async with connect(f"ws://localhost:{port}") as first, connect(f"ws://localhost:{port}") as second:
            await wait_until(lambda: hub.client_count == 2)
            hub.publish(sample_message(1))
            hub.publish(sample_message(2))
            for client in (first, second):
                received = [json.loads(await asyncio.wait_for(client.recv(), 2.0)) for _ in range(3)]
                assert received[0] == config_message(config, "replay")
                assert [m["seq"] for m in received[1:]] == [1, 2]
        await wait_until(lambda: hub.client_count == 0)


@pytest.mark.asyncio
async def test_config_keeps_accents_readable(config, free_port):
    port = free_port()
    hub = Hub(config_message(config, "serial"))
    async with hub.serve(port):
        async with connect(f"ws://localhost:{port}") as client:
            text = await asyncio.wait_for(client.recv(), 2.0)
    assert "estantería" in text
    assert json.loads(text)["source"] == "serial"


@pytest.mark.asyncio
async def test_late_client_gets_config_and_only_new_samples(config, free_port):
    port = free_port()
    hub = Hub(config_message(config, "replay"))
    async with hub.serve(port):
        hub.publish(sample_message(1))  # sin clientes: no pasa nada
        async with connect(f"ws://localhost:{port}") as client:
            await wait_until(lambda: hub.client_count == 1)
            hub.publish(sample_message(2))
            assert json.loads(await asyncio.wait_for(client.recv(), 2.0))["type"] == "config"
            assert json.loads(await asyncio.wait_for(client.recv(), 2.0))["seq"] == 2


@pytest.mark.asyncio
async def test_slow_client_does_not_hold_back_the_others(config, free_port):
    port = free_port()
    hub = Hub(config_message(config, "replay"))
    total = 3000
    async with hub.serve(port):
        # El cliente lento conecta y no lee nunca: su cola se llena y deja de
        # aceptar datos.
        slow = await connect(f"ws://localhost:{port}", max_queue=1)
        async with connect(f"ws://localhost:{port}") as fast:
            await wait_until(lambda: hub.client_count == 2)
            assert json.loads(await fast.recv())["type"] == "config"

            received = []

            async def read_all():
                while len(received) < total:
                    received.append(json.loads(await fast.recv())["seq"])

            reader = asyncio.ensure_future(read_all())
            slowest_publish = 0.0
            for seq in range(total):
                started = time.perf_counter()
                hub.publish(sample_message(seq, padding=2000))  # 6 MB en total
                slowest_publish = max(slowest_publish, time.perf_counter() - started)
                if seq % 50 == 0:
                    await asyncio.sleep(0)
            await asyncio.wait_for(reader, 10.0)

            assert received == list(range(total))
            assert slowest_publish < 0.05  # publicar nunca espera a nadie
            assert slow.protocol.state.name == "OPEN"
            slow.transport.abort()


@pytest.mark.asyncio
async def test_client_that_vanishes_does_not_break_publishing(config, free_port):
    port = free_port()
    hub = Hub(config_message(config, "replay"))
    async with hub.serve(port):
        async with connect(f"ws://localhost:{port}") as stays:
            gone = await connect(f"ws://localhost:{port}")
            await wait_until(lambda: hub.client_count == 2)
            await stays.recv()

            gone.transport.abort()  # corte brusco, sin cierre de WebSocket
            for seq in range(50):
                hub.publish(sample_message(seq))
                await asyncio.sleep(0.002)

            received = [json.loads(await asyncio.wait_for(stays.recv(), 2.0))["seq"] for _ in range(50)]
            assert received == list(range(50))
            await wait_until(lambda: hub.client_count == 1)


@pytest.mark.asyncio
async def test_messages_from_clients_are_ignored(config, free_port):
    port = free_port()
    hub = Hub(config_message(config, "replay"))
    async with hub.serve(port):
        async with connect(f"ws://localhost:{port}") as client:
            await client.recv()
            for _ in range(100):
                await client.send("hola")
            await wait_until(lambda: hub.client_count == 1)
            hub.publish(sample_message(7))
            assert json.loads(await asyncio.wait_for(client.recv(), 2.0))["seq"] == 7


# --- HTTP -------------------------------------------------------------------


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.status, response.headers, response.read()


def test_http_serves_the_dashboard_folder(tmp_path, free_port):
    (tmp_path / "index.html").write_text("<h1>plano</h1>", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log(1)", encoding="utf-8")
    port = free_port()
    server = start_http_server(tmp_path, port)
    try:
        status, headers, body = fetch(f"http://localhost:{port}/")
        assert status == 200
        assert body == b"<h1>plano</h1>"
        assert headers["Content-Type"].startswith("text/html")
        assert headers["Cache-Control"] == "no-store"

        status, headers, body = fetch(f"http://127.0.0.1:{port}/app.js")
        assert (status, body) == (200, b"console.log(1)")

        with pytest.raises(urllib.error.HTTPError) as error:
            fetch(f"http://localhost:{port}/no-existe.html")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_http_works_with_an_empty_folder(tmp_path, free_port):
    port = free_port()
    server = start_http_server(tmp_path, port)
    try:
        status, _, _ = fetch(f"http://localhost:{port}/")
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_http_does_not_serve_files_outside_the_folder(tmp_path, free_port):
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (tmp_path / "secreto.txt").write_text("no", encoding="utf-8")
    port = free_port()
    server = start_http_server(dashboard, port)
    try:
        for path in ("/../secreto.txt", "/%2e%2e/secreto.txt", "/..%2fsecreto.txt"):
            with pytest.raises(urllib.error.HTTPError) as error:
                fetch(f"http://localhost:{port}{path}")
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_http_port_in_use_raises(tmp_path, free_port):
    port = free_port()
    server = start_http_server(tmp_path, port)
    try:
        with pytest.raises(OSError):
            start_http_server(tmp_path, port)
    finally:
        server.shutdown()
        server.server_close()
