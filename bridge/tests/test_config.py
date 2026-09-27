import json

import pytest
import yaml

from uwb_bridge.config import DEFAULT_CONFIG_PATH, ConfigError, PositioningParams, Zone, config_message, load_config
from uwb_bridge.zones import find_zone


def write_config(tmp_path, raw) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def raw(config_path):
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def test_default_path_is_the_repo_config(repo_root):
    assert DEFAULT_CONFIG_PATH == repo_root / "config.yaml"


def test_loads_repo_config_by_default(repo_root):
    # Sin valores fijos: el config.yaml del repo se edita en cada montaje.
    config = load_config()
    raw = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    assert [a.id for a in config.anchors] == [a["id"] for a in raw["anchors"]]
    for anchor, entry in zip(config.anchors, raw["anchors"]):
        assert (anchor.x_m, anchor.y_m, anchor.z_m) == (entry["x_m"], entry["y_m"], entry["z_m"])
        assert anchor.offset_m == pytest.approx(entry["offset_cm"] / 100)
    assert config.tag_height_m == raw["tag"]["height_m"]
    assert [z.name for z in config.zones] == [z["name"] for z in raw["zones"]]
    assert config.filter.max_jump_m == raw["filter"]["max_jump_m"]
    assert config.positioning.max_residual_m == raw["positioning"]["max_residual_m"]
    assert config.positioning.max_age_s == raw["positioning"]["max_age_s"]
    assert config.ws_port == raw["server"]["ws_port"]
    assert config.ble_char_uuid == raw["transport"]["ble_char_uuid"]


def test_offsets_are_converted_from_cm(tmp_path, raw):
    raw["anchors"][0]["offset_cm"] = 12.5
    raw["anchors"][2]["offset_cm"] = -3.0
    config = load_config(write_config(tmp_path, raw))
    assert [a.offset_m for a in config.anchors] == pytest.approx([0.125, 0.0, -0.03])


def test_positioning_section_is_optional(tmp_path, raw):
    del raw["positioning"]
    assert load_config(write_config(tmp_path, raw)).positioning == PositioningParams()


@pytest.mark.parametrize("content, fragment", [("", "vacío"), ("anchors: [", "YAML")])
def test_unreadable_config_reports_the_problem(tmp_path, content, fragment):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=fragment):
        load_config(path)


@pytest.mark.parametrize(
    "section, key",
    [("tag", "height_m"), ("filter", "max_jump_m"), ("server", "ws_port"), ("room", "x_min_m")],
)
def test_missing_key_is_named(tmp_path, raw, section, key):
    del raw[section][key]
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("key", ["id", "x_m", "y_m", "z_m"])
def test_missing_anchor_key_is_named(tmp_path, raw, key):
    del raw["anchors"][1][key]
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path, raw))


def test_old_anchor_format_is_rejected(tmp_path, raw):
    raw["anchors"] = {"distance_m": 4.0, "height_m": 1.8}
    with pytest.raises(ConfigError, match="lista"):
        load_config(write_config(tmp_path, raw))


def test_repeated_anchor_ids(tmp_path, raw):
    raw["anchors"][2]["id"] = "a"
    with pytest.raises(ConfigError, match="repetidas: a"):
        load_config(write_config(tmp_path, raw))


def test_fewer_than_three_anchors(tmp_path, raw):
    raw["anchors"] = raw["anchors"][:2]
    with pytest.raises(ConfigError, match="al menos 3 anclas"):
        load_config(write_config(tmp_path, raw))


def test_anchor_ids_must_match_the_protocol(tmp_path, raw):
    raw["anchors"][2]["id"] = "z"
    with pytest.raises(ConfigError, match="deben ser a, b, c"):
        load_config(write_config(tmp_path, raw))


def test_more_anchors_are_accepted_without_the_protocol_check(tmp_path, raw):
    raw["anchors"].append({"id": "d", "x_m": 4.0, "y_m": 4.0, "z_m": 2.0})
    with pytest.raises(ConfigError, match="deben ser a, b, c"):
        load_config(write_config(tmp_path, raw))
    config = load_config(write_config(tmp_path, raw), protocol_ids=None)
    assert [a.id for a in config.anchors] == ["a", "b", "c", "d"]


@pytest.mark.parametrize(
    "c",
    [
        (2.0, 0.0),  # en la recta de A y B
        (8.0, 0.0),  # en la recta, fuera del segmento
        (2.0, 0.3),  # casi en la recta: 7.5 % del lado
        (0.0, 0.0),  # encima de A
    ],
)
def test_aligned_anchors_are_rejected(tmp_path, raw, c):
    raw["anchors"][2]["x_m"], raw["anchors"][2]["y_m"] = c
    with pytest.raises(ConfigError, match="casi alineadas"):
        load_config(write_config(tmp_path, raw))


def test_narrow_but_valid_triangle_is_accepted(tmp_path, raw):
    raw["anchors"][2]["x_m"], raw["anchors"][2]["y_m"] = 2.0, 0.5  # 12.5 % del lado
    assert load_config(write_config(tmp_path, raw)).anchors[2].y_m == 0.5


def test_heights_do_not_rescue_aligned_anchors(tmp_path, raw):
    # La geometría que cuenta es la del plano: alturas distintas no bastan.
    raw["anchors"][2].update({"x_m": 2.0, "y_m": 0.0, "z_m": 0.3})
    with pytest.raises(ConfigError, match="casi alineadas"):
        load_config(write_config(tmp_path, raw))


def test_invalid_values_are_rejected(tmp_path, raw):
    raw["positioning"]["max_age_s"] = 0
    with pytest.raises(ConfigError, match="positioning"):
        load_config(write_config(tmp_path, raw))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "no-existe.yaml")


def test_config_message_matches_the_contract(config):
    message = config_message(config, "replay")
    assert message == {
        "type": "config",
        "source": "replay",
        "anchors": [
            {"id": "a", "x": 0.0, "y": 0.0, "z": 1.8},
            {"id": "b", "x": 4.0, "y": 0.0, "z": 1.8},
            {"id": "c", "x": 2.0, "y": 4.0, "z": 1.8},
        ],
        "tag": {"height_m": 1.2},
        "room": {"x_min_m": -0.5, "x_max_m": 4.5, "y_min_m": 0.0, "y_max_m": 4.0},
        "zones": [
            {"name": "mesa", "x_m": 0.3, "y_m": 1.0, "width_m": 1.2, "height_m": 0.8},
            {"name": "estantería", "x_m": 3.0, "y_m": 0.6, "width_m": 1.0, "height_m": 0.6},
            {"name": "puerta", "x_m": 3.2, "y_m": 3.0, "width_m": 1.0, "height_m": 0.8},
        ],
        "display": {"uncertainty_m": 0.2, "wifi_uncertainty_m": 3.0, "trail_s": 3.0},
    }
    json.dumps(message)


def test_config_message_keeps_anchor_order(tmp_path, raw):
    raw["anchors"] = [raw["anchors"][2], raw["anchors"][0], raw["anchors"][1]]
    message = config_message(load_config(write_config(tmp_path, raw)), "ble")
    assert [a["id"] for a in message["anchors"]] == ["c", "a", "b"]


# --- Zonas ------------------------------------------------------------------


@pytest.mark.parametrize(
    "x, y, expected",
    [
        (0.9, 1.4, "mesa"),
        (3.5, 0.9, "estantería"),
        (3.7, 3.4, "puerta"),
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
