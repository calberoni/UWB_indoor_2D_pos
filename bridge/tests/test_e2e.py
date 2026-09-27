"""Extremo a extremo: el bridge como proceso, en modo replay, con un cliente WebSocket."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

import pytest
import yaml
from websockets.asyncio.client import connect

import sim

NUMBER = "número"
NULLABLE_NUMBER = "número o null"
SAMPLE_FIELDS = {
    "type": str,
    "seq": int,
    "x": NULLABLE_NUMBER,
    "y": NULLABLE_NUMBER,
    "err_m": NULLABLE_NUMBER,
    "anchors_used": int,
    "ranges": dict,
    "rate_hz": NULLABLE_NUMBER,
    "latency_ms": int,
    "lost": int,
    "zone": (str, type(None)),
    "valid": bool,
}


def has_type(value, kind) -> bool:
    if kind in (NUMBER, NULLABLE_NUMBER):
        if value is None:
            return kind == NULLABLE_NUMBER
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, kind) and not (kind is int and isinstance(value, bool))


@pytest.fixture
def workspace(tmp_path, config_path, free_port):
    """Configuración con puertos libres y un log simulado del paseo."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["server"] = {"ws_port": free_port(), "http_port": free_port()}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    log = tmp_path / "paseo.csv"
    sim.simulate(sim.Options(scenario="walk", duration_s=30.0), log, path)
    return path, log, raw["server"]["ws_port"], raw["server"]["http_port"]


def start_bridge(repo_root, *args):
    env = dict(os.environ, PYTHONPATH=str(repo_root / "bridge"), PYTHONUNBUFFERED="1")
    return subprocess.Popen(
        [sys.executable, "-m", "uwb_bridge", *args],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stop_bridge(process) -> str:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    return output


async def open_client(port, process, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return await connect(f"ws://localhost:{port}")
        except OSError:
            assert process.poll() is None, "el bridge terminó antes de aceptar conexiones"
            assert time.monotonic() < deadline, "el bridge no aceptó conexiones a tiempo"
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_replay_publishes_config_and_samples(repo_root, workspace):
    config_file, log, ws_port, http_port = workspace
    process = start_bridge(repo_root, "--replay", str(log), "--config", str(config_file), "--speed", "2")
    try:
        client = await open_client(ws_port, process)
        async with client:
            messages = [json.loads(await asyncio.wait_for(client.recv(), 5.0)) for _ in range(41)]

        config, samples = messages[0], messages[1:]
        assert config["type"] == "config"
        assert config["source"] == "replay"
        assert set(config) == {"type", "source", "anchors", "tag", "room", "zones", "display"}
        assert config["anchors"] == [
            {"id": "a", "x": 0.0, "y": 0.0, "z": 1.8},
            {"id": "b", "x": 4.0, "y": 0.0, "z": 1.8},
            {"id": "c", "x": 2.0, "y": 4.0, "z": 1.8},
        ]
        assert config["tag"] == {"height_m": 1.2}
        assert config["room"] == {"x_min_m": -0.5, "x_max_m": 4.5, "y_min_m": 0.0, "y_max_m": 4.0}
        assert [z["name"] for z in config["zones"]] == ["mesa", "estantería", "puerta"]
        assert all(set(z) == {"name", "x_m", "y_m", "width_m", "height_m"} for z in config["zones"])
        assert config["display"] == {"uncertainty_m": 0.2, "wifi_uncertainty_m": 3.0, "trail_s": 3.0}

        for sample in samples:
            assert set(sample) == set(SAMPLE_FIELDS)
            assert sample["type"] == "sample"
            for field, kind in SAMPLE_FIELDS.items():
                assert has_type(sample[field], kind), field
            assert list(sample["ranges"]) == ["a", "b", "c"]
            for entry in sample["ranges"].values():
                assert set(entry) == {"d", "r", "q", "ok"}
                assert isinstance(entry["q"], int) and isinstance(entry["ok"], bool)
                assert (entry["d"] is None) == (entry["r"] is None)
                if entry["d"] is not None:
                    assert 0 <= entry["r"] <= entry["d"]
            assert sample["anchors_used"] in (0, 2, 3)
            assert (sample["x"] is None) == (sample["err_m"] is None) == (sample["anchors_used"] == 0)

        seqs = [s["seq"] for s in samples]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        last = samples[-1]
        assert 9.0 <= last["rate_hz"] <= 11.0
        assert all(s["rate_hz"] is not None for s in samples[1:])
        # El paseo simulado tiene posición en cuanto se calientan los filtros.
        assert all(s["x"] is not None for s in samples if s["seq"] >= 3)
        assert 0 <= last["latency_ms"] < 100
        assert all(-0.5 <= s["x"] <= 4.5 and 0.0 <= s["y"] <= 4.0 for s in samples if s["x"] is not None)
        assert sum(s["anchors_used"] == 3 for s in samples) > 0.9 * len(samples)

        # El servidor HTTP sirve la carpeta dashboard/ del repo, esté como esté.
        with urllib.request.urlopen(f"http://localhost:{http_port}/", timeout=3) as response:
            assert response.status == 200
    finally:
        output = stop_bridge(process)

    assert process.returncode == 0, output
    assert f"ws://localhost:{ws_port}" in output
    assert "Traceback" not in output


@pytest.mark.asyncio
async def test_replay_does_not_write_a_session_log(repo_root, workspace):
    config_file, log, ws_port, _ = workspace
    logs_before = set((repo_root / "logs").glob("*.csv"))
    process = start_bridge(repo_root, "--replay", str(log), "--config", str(config_file), "--speed", "20")
    try:
        client = await open_client(ws_port, process)
        async with client:
            for _ in range(20):
                await asyncio.wait_for(client.recv(), 5.0)
    finally:
        output = stop_bridge(process)
    assert set((repo_root / "logs").glob("*.csv")) == logs_before
    assert "Registro:" not in output


def test_replay_ends_with_the_log(repo_root, tmp_path, workspace):
    config_file, _, _, _ = workspace
    short = tmp_path / "corto.csv"
    sim.simulate(sim.Options(scenario="static", duration_s=3.0), short, config_file)
    process = start_bridge(repo_root, "--replay", str(short), "--config", str(config_file), "--speed", "10")
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        raise
    assert process.returncode == 0, output
    assert "Fin del replay." in output


def test_port_in_use_is_reported(repo_root, workspace):
    config_file, log, ws_port, http_port = workspace
    first = start_bridge(repo_root, "--replay", str(log), "--config", str(config_file))
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                urllib.request.urlopen(f"http://localhost:{http_port}/", timeout=1).close()
                break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        second = start_bridge(repo_root, "--replay", str(log), "--config", str(config_file))
        output, _ = second.communicate(timeout=10)
        assert second.returncode == 1
        assert "Error: no se puede abrir el puerto" in output
        assert "Traceback" not in output
    finally:
        stop_bridge(first)


@pytest.mark.parametrize(
    "args, fragment",
    [
        ([], "one of the arguments --serial --ble --replay is required"),
        (["--serial", "/dev/null", "--replay", "x.csv"], "not allowed with argument"),
        (["--ble", "--replay", "x.csv"], "not allowed with argument"),
        (["--replay", "x.csv", "--speed", "0"], "--speed debe ser mayor que 0"),
        (["--serial", "/dev/null", "--speed", "2"], "solo se usan con --replay"),
    ],
)
def test_cli_rejects_invalid_arguments(repo_root, args, fragment):
    process = start_bridge(repo_root, *args)
    output, _ = process.communicate(timeout=10)
    assert process.returncode == 2
    assert fragment in output


def test_cli_reports_missing_log(repo_root, workspace, tmp_path):
    config_file, _, _, _ = workspace
    process = start_bridge(repo_root, "--replay", str(tmp_path / "no-existe.csv"), "--config", str(config_file))
    output, _ = process.communicate(timeout=10)
    assert process.returncode == 1
    assert "no se puede leer" in output
    assert "Traceback" not in output
