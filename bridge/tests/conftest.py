import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Los tests funcionan igual con o sin PYTHONPATH=bridge.
for folder in (REPO_ROOT / "bridge", REPO_ROOT / "tools", Path(__file__).parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import sim  # noqa: E402
from uwb_bridge.config import load_config  # noqa: E402

# Sala de referencia de los tests. No se usa el config.yaml del repo porque ese
# cambia con cada montaje: D y las alturas se miden con cinta en la sala real.
REFERENCE_CONFIG = """\
anchors:
  - {id: a, x_m: 0.00, y_m: 0.00, z_m: 1.80, offset_cm: 0.0}
  - {id: b, x_m: 4.00, y_m: 0.00, z_m: 1.80, offset_cm: 0.0}
  - {id: c, x_m: 2.00, y_m: 4.00, z_m: 1.80, offset_cm: 0.0}
tag:
  height_m: 1.20
room:
  x_min_m: -0.50
  x_max_m: 4.50
  y_min_m: 0.00
  y_max_m: 4.00
zones:
  - {name: mesa, x_m: 0.30, y_m: 1.00, width_m: 1.20, height_m: 0.80}
  - {name: estantería, x_m: 3.00, y_m: 0.60, width_m: 1.00, height_m: 0.60}
  - {name: puerta, x_m: 3.20, y_m: 3.00, width_m: 1.00, height_m: 0.80}
filter:
  median_window: 5
  ema_alpha: 0.4
  min_quality: 40
  max_jump_m: 1.5
  max_jump_rejects: 5
positioning:
  range_sigma_m: 0.05
  max_residual_m: 0.30
  max_age_s: 0.5
  room_margin_m: 0.30
display:
  uncertainty_m: 0.20
  wifi_uncertainty_m: 3.00
  trail_s: 3.0
server:
  ws_port: 8765
  http_port: 8000
transport:
  serial_baud: 115200
  ble_name: UWB-TAG
  ble_service_uuid: 76360001-61ff-4c6a-8d40-75579102b404
  ble_char_uuid: 76360002-61ff-4c6a-8d40-75579102b404
"""


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def config_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("config") / "config.yaml"
    path.write_text(REFERENCE_CONFIG, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def config(config_path):
    return load_config(config_path)


@pytest.fixture(scope="session")
def geometry(config_path):
    return sim.load_geometry(config_path)


@pytest.fixture
def free_port():
    def pick() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    return pick


@pytest.fixture
def raw_mm(geometry):
    """Distancias 3D sin ruido, en mm y por ancla, que mediría el tag en (x, y)."""

    def measure(x: float, y: float) -> dict[str, int]:
        return {i: round(d * 1000) for i, d in sim.true_distances(geometry, x, y).items()}

    return measure
