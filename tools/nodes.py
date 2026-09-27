#!/usr/bin/env python3
"""Asocia cada placa a su rol por el número de serie USB.

Con varias placas conectadas a la vez los puertos cambian de nombre, así que
el rol se guarda junto al número de serie en firmware/nodes.yaml.

    nodes.py list                  placas conectadas y su rol
    nodes.py register ROL [PUERTO] guarda la placa conectada como ROL
    nodes.py port ROL              imprime el puerto de la placa con ese rol
"""

import argparse
import sys
from pathlib import Path

import yaml
from serial.tools import list_ports

ROLES = ("anchor_a", "anchor_b", "anchor_c", "tag")
ARDUINO_VID = 0x2341
# Nano 33 BLE en modo normal y en modo bootloader.
NANO33BLE_PIDS = {0x005A, 0x805A, 0x015A, 0x025A}
NODES_FILE = Path(__file__).resolve().parent.parent / "firmware" / "nodes.yaml"


def connected_boards():
    boards = []
    for port in list_ports.comports():
        if port.vid == ARDUINO_VID and port.pid in NANO33BLE_PIDS:
            # En macOS cada placa aparece como tty.* y cu.*; se usa cu.*
            boards.append((port.device.replace("/dev/tty.", "/dev/cu."), port.serial_number))
    return sorted(set(boards))


def load_nodes():
    if not NODES_FILE.exists():
        return {}
    return yaml.safe_load(NODES_FILE.read_text()) or {}


def save_nodes(nodes):
    header = "# Número de serie USB de cada placa. Lo escribe tools/nodes.py.\n"
    NODES_FILE.write_text(header + yaml.safe_dump(nodes, sort_keys=True))


def cmd_list(_args):
    roles = {serial: role for role, serial in load_nodes().items()}
    boards = connected_boards()
    if not boards:
        print("No hay ninguna placa Nano 33 BLE conectada.")
        return 1
    for device, serial in boards:
        print(f"{device}  serie={serial}  rol={roles.get(serial, '(sin registrar)')}")
    return 0


def cmd_register(args):
    boards = connected_boards()
    if args.port:
        wanted = args.port.replace("/dev/tty.", "/dev/cu.")
        boards = [board for board in boards if board[0] == wanted]
    if len(boards) != 1:
        print(
            f"error: se esperaba una sola placa y hay {len(boards)}. "
            "Conecta solo la que quieres registrar o indica el puerto.",
            file=sys.stderr,
        )
        return 1
    device, serial = boards[0]
    if not serial:
        print(f"error: la placa en {device} no informa de su número de serie", file=sys.stderr)
        return 1
    nodes = {role: value for role, value in load_nodes().items() if value != serial}
    nodes[args.role] = serial
    save_nodes(nodes)
    print(f"{args.role}: {serial} ({device})")
    return 0


def cmd_port(args):
    serial = load_nodes().get(args.role)
    if not serial:
        print(
            f"error: no hay ninguna placa registrada como {args.role}. "
            f"Conéctala sola y ejecuta: tools/nodes.py register {args.role}",
            file=sys.stderr,
        )
        return 1
    for device, board_serial in connected_boards():
        if board_serial == serial:
            print(device)
            return 0
    print(f"error: la placa {args.role} ({serial}) no está conectada", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list").set_defaults(run=cmd_list)

    register = commands.add_parser("register")
    register.add_argument("role", choices=ROLES)
    register.add_argument("port", nargs="?")
    register.set_defaults(run=cmd_register)

    port = commands.add_parser("port")
    port.add_argument("role", choices=ROLES)
    port.set_defaults(run=cmd_port)

    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
