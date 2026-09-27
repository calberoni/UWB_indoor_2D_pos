import json

import pytest
import yaml

from uwb_bridge.config import DEFAULT_CONFIG_PATH, ConfigError, Zone, config_message, load_config
from uwb_bridge.zones import find_zone


def test_default_path_is_the_repo_config(repo_root):
    assert DEFAULT_CONFIG_PATH == repo_root / "config.yaml"


def test_loads_repo_config_by_default(repo_root):
    # Sin valores fijos: el config.yaml del repo se edita en cada montaje.
    config = load_config()
    raw = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    assert config.anchor_distance_m == raw["anchors"]["distance_m"]
    assert config.anchor_height_m == raw["anchors"]["height_m"]
    assert config.tag_height_m == raw["tag"]["height_m"]
    assert config.height_diff_m == pytest.approx(raw["anchors"]["height_m"] - raw["tag"]["height_m"])
    assert [z.name for z in config.zones] == [z["name"] for z in raw["zones"]]
    assert config.filter.median_window == raw["filter"]["median_window"]
    assert config.filter.ema_alpha == raw["filter"]["ema_alpha"]
    assert config.filter.min_quality == raw["filter"]["min_quality"]
    assert config.filter.max_jump_m == raw["filter"]["max_jump_m"]
    assert config.ws_port == raw["server"]["ws_port"]
    assert config.http_port == raw["server"]["http_port"]
    assert config.serial_baud == raw["transport"]["serial_baud"]
    assert config.ble_char_uuid == raw["transport"]["ble_char_uuid"]


def test_offsets_are_converted_from_cm(tmp_path, config_path):
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["anchors"]["offset_cm"] = {"a": 12.5, "b": -3.0}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    config = load_config(path)
    assert config.offset_a_m == pytest.approx(0.125)
    assert config.offset_b_m == pytest.approx(-0.03)


@pytest.mark.parametrize("content, fragment", [("", "vacío"), ("anchors: [", "YAML")])
def test_unreadable_config_reports_the_problem(tmp_path, content, fragment):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=fragment):
        load_config(path)


@pytest.mark.parametrize(
    "section, key",
    [("anchors", "distance_m"), ("anchors", "height_m"), ("tag", "height_m"), ("filter", "max_jump_m"), ("server", "ws_port")],
)
def test_missing_key_is_named(tmp_path, config_path, section, key):
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    del raw[section][key]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match=key):
        load_config(path)


def test_invalid_values_are_rejected(tmp_path, config_path):
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["anchors"]["distance_m"] = 0
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="distance_m"):
        load_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "no-existe.yaml")


def test_config_message_matches_the_contract(config):
    message = config_message(config, "replay")
    assert message == {
        "type": "config",
        "source": "replay",
        "anchors": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 4.0, "y": 0.0}, "height_m": 1.8},
        "tag": {"height_m": 1.2},
        "room": {"x_min_m": -0.5, "x_max_m": 4.5, "y_min_m": 0.0, "y_max_m": 4.0},
        "zones": [
            {"name": "mesa", "x_m": 0.3, "y_m": 1.0, "width_m": 1.2, "height_m": 0.8},
            {"name": "estantería", "x_m": 3.0, "y_m": 0.6, "width_m": 1.0, "height_m": 0.6},
            {"name": "puerta", "x_m": 1.6, "y_m": 3.2, "width_m": 1.0, "height_m": 0.8},
        ],
        "display": {"uncertainty_m": 0.2, "wifi_uncertainty_m": 3.0, "trail_s": 3.0},
    }
    json.dumps(message)


# --- Zonas ------------------------------------------------------------------


@pytest.mark.parametrize(
    "x, y, expected",
    [
        (0.9, 1.4, "mesa"),
        (3.5, 0.9, "estantería"),
        (2.1, 3.6, "puerta"),
        (2.0, 2.0, None),
        (0.3, 1.0, "mesa"),  # la esquina cuenta como dentro
        (1.5, 1.8, "mesa"),
        (1.51, 1.8, None),
        (0.29, 1.4, None),
        (0.9, 0.99, None),
        (-3.0, 9.0, None),
    ],
)
def test_find_zone(config, x, y, expected):
    assert find_zone(config.zones, x, y) == expected


def test_first_zone_wins_when_two_overlap():
    zones = [Zone("grande", 0, 0, 4, 4), Zone("pequeña", 1, 1, 1, 1)]
    assert find_zone(zones, 1.5, 1.5) == "grande"


def test_no_zones():
    assert find_zone([], 1.0, 1.0) is None
