"""Transporte serie contra un pseudo-terminal que hace de tag."""

import asyncio
import os
import time

import pytest

from fake_tag import DISTANCES_MM, QUALITIES, TAG_CLOCK_OFFSET_MS, FakeTag
from uwb_bridge.transports.serial_link import SerialTransport


async def collect(transport, samples, syncs, until, timeout_s=5.0, once_connected=None):
    task = asyncio.ensure_future(transport.run(samples.append, lambda *measure: syncs.append(measure)))
    try:
        deadline = time.monotonic() + timeout_s
        while not until() and time.monotonic() < deadline:
            if once_connected is not None and samples:
                once_connected()
                once_connected = None
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert until(), "el transporte no entregó lo esperado a tiempo"


@pytest.fixture
def link(tmp_path):
    return str(tmp_path / "tty.tag")


def test_delivers_samples_and_ignores_the_rest(link):
    noise = (
        b"# arranque: DEV_ID 0xDECA0302\r\n"
        b"# RAW 1 2 3\r\n"
        b"esto no es un registro\r\n"
        b"1,2,3\r\n"
        b"\xff\xfe\x00\r\n"
        b"\r\n"
    )
    tag = FakeTag(link, first_seq=65530)
    transport = SerialTransport(link, 115200, retry_s=0.05)
    samples, syncs = [], []
    try:
        asyncio.run(
            collect(transport, samples, syncs, lambda: len(samples) >= 30, once_connected=lambda: tag.write(noise))
        )
    finally:
        tag.unplug()

    seqs = [s.seq for s in samples]
    assert seqs == [(seqs[0] + i) % 65536 for i in range(len(seqs))]  # seguidos, con la vuelta a 0
    assert 0 in seqs
    assert all(s.distances_mm == DISTANCES_MM and s.qualities == QUALITIES for s in samples)
    now_ms = time.time() * 1000
    assert all(now_ms - 10_000 < s.t_host_ms <= now_ms for s in samples)
    assert transport.discarded == 3


def test_sync_exchange_measures_clock_offset(link):
    tag = FakeTag(link)
    transport = SerialTransport(link, 115200, retry_s=0.05, sync_interval_s=0.2)
    samples, syncs = [], []
    try:
        asyncio.run(collect(transport, samples, syncs, lambda: len(syncs) >= 3))
    finally:
        tag.unplug()

    assert tag.sync_requests >= 3
    true_offset_ms = tag._boot * 1000 - TAG_CLOCK_OFFSET_MS
    for t0_ms, t1_ms, t_tag_ms in syncs:
        assert 0 <= t1_ms - t0_ms < 200
        assert (t0_ms + t1_ms) / 2 - t_tag_ms == pytest.approx(true_offset_ms, abs=100)


def test_sync_replies_are_not_delivered_as_samples(link):
    tag = FakeTag(link)
    transport = SerialTransport(link, 115200, retry_s=0.05, sync_interval_s=0.05)
    samples, syncs = [], []
    try:
        asyncio.run(collect(transport, samples, syncs, lambda: len(syncs) >= 5 and len(samples) >= 10))
    finally:
        tag.unplug()
    assert transport.discarded == 0


def test_reconnects_when_the_tag_comes_back(link, capsys):
    transport = SerialTransport(link, 115200, retry_s=0.05)
    samples, syncs = [], []

    async def scenario():
        task = asyncio.ensure_future(transport.run(samples.append, lambda *m: syncs.append(m)))
        try:
            await asyncio.sleep(0.3)  # arranca sin tag: debe seguir intentándolo
            assert not task.done()
            assert samples == []

            first = FakeTag(link, first_seq=100)
            await wait_for(lambda: len(samples) >= 10)
            first.unplug()
            os.remove(link)
            count = len(samples)
            await asyncio.sleep(0.5)
            assert not task.done()

            second = FakeTag(link, first_seq=5000)
            await wait_for(lambda: len(samples) >= count + 10)
            second.unplug()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def wait_for(condition, timeout_s=5.0):
        deadline = time.monotonic() + timeout_s
        while not condition():
            assert time.monotonic() < deadline, "sin muestras tras reconectar"
            await asyncio.sleep(0.01)

    asyncio.run(scenario())

    seqs = [s.seq for s in samples]
    assert any(100 <= seq < 5000 for seq in seqs)
    assert any(seq >= 5000 for seq in seqs)
    assert len(syncs) >= 2  # un SYNC por cada conexión

    output = capsys.readouterr().out
    assert output.count("no se puede abrir") == 1  # avisa una vez, no en cada intento
    assert output.count("conectado a") == 2
    assert "conexión perdida" in output


def test_partial_first_line_is_not_taken_as_a_record(link):
    # El tag ya estaba emitiendo: lo primero que lee el bridge es media línea
    # que, sin la cabeza, parece un registro válido con otro seq.
    tag = FakeTag(link, period_s=3600, answers_sync=False)  # calla: solo llega lo que escribe el test
    transport = SerialTransport(link, 115200, retry_s=0.05)
    samples, syncs = [], []

    async def scenario():
        task = asyncio.ensure_future(transport.run(samples.append, lambda *m: syncs.append(m)))
        try:
            while not tag.sync_requests:  # al llegar el SYNC ya se sabe que el puerto está abierto
                await asyncio.sleep(0.01)
            tag.write(b"34,2900,3100,2700,200,190,180,5000\r\n1235,2900,3100,2700,200,190,180,5100\r\n")
            while not samples:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=5.0))
    finally:
        tag.unplug()
    assert [s.seq for s in samples] == [1235]


def test_cancelling_closes_the_port_cleanly(link):
    tag = FakeTag(link)
    transport = SerialTransport(link, 115200, retry_s=0.05)
    samples, syncs = [], []
    try:
        started = time.monotonic()
        asyncio.run(collect(transport, samples, syncs, lambda: len(samples) >= 5))
        assert time.monotonic() - started < 3.0
        # El puerto queda libre: otra sesión puede abrirlo a continuación.
        more = []
        asyncio.run(collect(SerialTransport(link, 115200), more, [], lambda: len(more) >= 5))
    finally:
        tag.unplug()
