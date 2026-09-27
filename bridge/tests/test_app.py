"""La aplicación completa por serie: tag simulado en un pty, cliente WebSocket y log de sesión."""

import asyncio
import csv
import json
from dataclasses import replace

import pytest
from websockets.asyncio.client import connect

from fake_tag import DISTANCES_MM, QUALITIES, TAG_POSITION, FakeTag
from uwb_bridge import app
from uwb_bridge.transports.replay import ReplayTransport
from uwb_bridge.transports.serial_link import SerialTransport


async def open_client(port):
    for _ in range(200):
        try:
            return await connect(f"ws://localhost:{port}")
        except OSError:
            await asyncio.sleep(0.02)
    raise AssertionError("el servidor WebSocket no arrancó")


@pytest.mark.asyncio
async def test_serial_session_is_published_and_logged(tmp_path, config, free_port):
    config = replace(config, ws_port=free_port(), http_port=free_port())
    link = str(tmp_path / "tty.tag")
    logs_dir = tmp_path / "logs"
    tag = FakeTag(link, first_seq=65500, period_s=0.02)
    transport = SerialTransport(link, config.serial_baud, retry_s=0.05, sync_interval_s=0.3)
    running = asyncio.ensure_future(
        app.run(config, transport, write_log=True, dashboard_dir=tmp_path, logs_dir=logs_dir)
    )
    try:
        client = await open_client(config.ws_port)
        async with client:
            first = json.loads(await asyncio.wait_for(client.recv(), 3.0))
            samples = [json.loads(await asyncio.wait_for(client.recv(), 3.0)) for _ in range(100)]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        tag.unplug()

    assert first["type"] == "config"
    assert first["source"] == "serial"

    seqs = [s["seq"] for s in samples]
    assert seqs == [(seqs[0] + i) % 65536 for i in range(100)]
    assert 0 in seqs  # la vuelta de seq ha pasado por el medio
    last = samples[-1]
    assert last["lost"] == 0
    assert last["valid"] is True
    assert {i: last["ranges"][i]["d"] for i in "abc"} == pytest.approx({i: d / 1000 for i, d in DISTANCES_MM.items()})
    assert (last["x"], last["y"]) == pytest.approx(TAG_POSITION, abs=0.002)
    assert last["anchors_used"] == 3
    assert 20 <= last["rate_hz"] <= 60  # el tag simulado emite a unos 50 Hz, según la carga de la máquina
    # Con el desfase medido por SYNC la latencia sale de restar relojes que no
    # tienen nada que ver; en local deben quedar unos pocos milisegundos.
    assert all(0 <= s["latency_ms"] < 50 for s in samples[20:])

    logs = list(logs_dir.glob("*.csv"))
    assert len(logs) == 1
    with logs[0].open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert list(rows[0]) == ["t_host_ms", "seq", "d_a", "d_b", "d_c", "q_a", "q_b", "q_c", "t_ms", "x", "y", "zone"]
    assert len(rows) >= 100
    logged = {int(row["seq"]): row for row in rows}
    for sample in samples:
        row = logged[sample["seq"]]
        assert {i: int(row[f"d_{i}"]) for i in "abc"} == DISTANCES_MM
        assert {i: int(row[f"q_{i}"]) for i in "abc"} == QUALITIES
        if sample["x"] is None:  # las primeras, mientras se calientan los filtros
            assert row["x"] == row["y"] == ""
        else:
            assert float(row["x"]) == pytest.approx(sample["x"], abs=0.001)
            assert float(row["y"]) == pytest.approx(sample["y"], abs=0.001)

    # Y el log grabado se puede reproducir.
    replayed = []
    await ReplayTransport(logs[0], speed=500).run(replayed.append)
    assert [s.seq for s in replayed] == [int(row["seq"]) for row in rows]


@pytest.mark.asyncio
async def test_no_log_option(tmp_path, config, free_port):
    config = replace(config, ws_port=free_port(), http_port=free_port())
    link = str(tmp_path / "tty.tag")
    logs_dir = tmp_path / "logs"
    tag = FakeTag(link)
    transport = SerialTransport(link, config.serial_baud, retry_s=0.05)
    running = asyncio.ensure_future(
        app.run(config, transport, write_log=False, dashboard_dir=tmp_path, logs_dir=logs_dir)
    )
    try:
        client = await open_client(config.ws_port)
        async with client:
            for _ in range(10):
                await asyncio.wait_for(client.recv(), 3.0)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        tag.unplug()
    assert not logs_dir.exists()


@pytest.mark.asyncio
async def test_bridge_keeps_serving_while_the_tag_is_away(tmp_path, config, free_port):
    config = replace(config, ws_port=free_port(), http_port=free_port())
    link = str(tmp_path / "tty.tag")
    transport = SerialTransport(link, config.serial_baud, retry_s=0.05)
    running = asyncio.ensure_future(
        app.run(config, transport, write_log=False, dashboard_dir=tmp_path, logs_dir=tmp_path)
    )
    try:
        client = await open_client(config.ws_port)
        async with client:
            # Sin tag: el cliente conecta y recibe la configuración igualmente.
            assert json.loads(await asyncio.wait_for(client.recv(), 3.0))["type"] == "config"
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(client.recv(), 0.3)

            tag = FakeTag(link, first_seq=10)
            try:
                sample = json.loads(await asyncio.wait_for(client.recv(), 3.0))
                assert sample["type"] == "sample"
            finally:
                tag.unplug()
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
