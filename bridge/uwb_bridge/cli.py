import argparse
import asyncio
import sys

from . import app
from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config
from .transports import Transport, TransportError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uwb_bridge",
        description="Recibe las distancias del tag UWB, calcula la posición y la publica por WebSocket.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--serial", metavar="PUERTO", help="recibir por USB serie, p. ej. /dev/tty.usbmodem1101")
    source.add_argument(
        "--ble",
        metavar="NOMBRE",
        nargs="?",
        const="",
        help="recibir por BLE; sin nombre se usa el de la configuración",
    )
    source.add_argument("--replay", metavar="FICHERO", help="reproducir un log de sesión")
    parser.add_argument("--config", metavar="FICHERO", default=DEFAULT_CONFIG_PATH, help="por defecto, config.yaml del repo")
    parser.add_argument("--speed", type=float, default=1.0, metavar="FACTOR", help="velocidad del replay (por defecto 1)")
    parser.add_argument("--loop", action="store_true", help="repetir el replay hasta que se interrumpa")
    parser.add_argument("--no-log", action="store_true", help="no escribir el log de la sesión")
    parser.add_argument("--open", action="store_true", help="abrir el dashboard en el navegador")
    return parser


def build_transport(args: argparse.Namespace, config: Config) -> Transport:
    # Cada transporte se importa solo si se usa: un replay no necesita Bluetooth.
    if args.serial is not None:
        from .transports.serial_link import SerialTransport

        return SerialTransport(args.serial, config.serial_baud)
    if args.ble is not None:
        from .transports.ble_link import BleTransport

        return BleTransport(args.ble or config.ble_name, config.ble_service_uuid, config.ble_char_uuid)

    from .transports.replay import ReplayTransport

    return ReplayTransport(args.replay, speed=args.speed, repeat=args.loop)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.speed <= 0:
        parser.error("--speed debe ser mayor que 0")
    if args.replay is None and (args.speed != 1.0 or args.loop):
        parser.error("--speed y --loop solo se usan con --replay")

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2

    # Con la salida redirigida (make, tests) los mensajes deben salir al momento.
    sys.stdout.reconfigure(line_buffering=True)

    is_replay = args.replay is not None
    try:
        transport = build_transport(args, config)
        if is_replay:
            print(f"Fuente: replay de {args.replay} (x{args.speed:g})")
        asyncio.run(
            app.run(
                config,
                transport,
                write_log=not (is_replay or args.no_log),
                open_browser=args.open,
            )
        )
    except KeyboardInterrupt:
        print("\nDetenido.")
        return 0
    except (TransportError, app.StartupError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if is_replay:
        print("Fin del replay.")
    return 0
