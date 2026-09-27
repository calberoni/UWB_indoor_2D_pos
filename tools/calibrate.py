#!/usr/bin/env python3
"""Mide el error de distancia de un par tag-ancla y calibra el antenna delay.

Se conecta por USB a un nodo (tag o ancla), con el otro a una distancia medida
con cinta, y toma muestras. Sin --apply solo mide. Con --apply ajusta el
antenna delay del nodo conectado hasta que el error medio queda dentro de la
tolerancia.

    calibrate.py --port /dev/cu.usbmodem1101 --anchor a --distance 2.00
    calibrate.py --port /dev/cu.usbmodem1101 --anchor a --distance 2.00 --apply

El valor que se fija con --apply se pierde al reiniciar el nodo: hay que
copiarlo a firmware/uwb_node/config.h y volver a grabar.
"""

import argparse
import statistics
import sys
import time

import serial

# Unidades de tiempo del DW3000 (15.65 ps) por milímetro de recorrido en el aire.
DTU_PER_MM = (499.2e6 * 128.0) / 299702547.0 / 1000.0


class Node:
    def __init__(self, port, baud=115200):
        self.serial = serial.Serial(port, baud, timeout=0.2)
        self.role = None
        self.antenna_delay = None

    def close(self):
        self.serial.close()

    def lines(self, seconds):
        """Líneas recibidas durante un tiempo; las ilegibles se descartan."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw or not raw.endswith(b"\n"):
                continue
            try:
                yield raw.decode("ascii").strip()
            except UnicodeDecodeError:
                continue

    def command(self, text, prefix, seconds=2.0):
        """Envía un comando y devuelve la primera respuesta que empieza por prefix."""
        self.serial.reset_input_buffer()
        self.serial.write((text + "\n").encode("ascii"))
        for line in self.lines(seconds):
            if line.startswith(prefix):
                return line
        raise TimeoutError(f"el nodo no responde a {text!r}")

    def identify(self):
        info = self.command("INFO", "# INFO")
        # El estado de la radio va al final y puede llevar espacios.
        info, _, radio = info.partition(" radio=")
        fields = dict(item.split("=", 1) for item in info.split()[2:] if "=" in item)
        self.role = fields.get("rol")
        self.antenna_delay = int(fields["ant"])
        if radio != "OK":
            raise RuntimeError(f"la radio del nodo no está lista: {radio}")

    def set_antenna_delay(self, value):
        self.command(f"ANT {value}", "# ANT")
        self.antenna_delay = value

    def sample(self, anchor, count, timeout):
        """Distancias en mm del par elegido; los rangings fallidos no cuentan."""
        distances = []
        failed = 0
        if self.role != "TAG":
            self.command("RAW 1", "# RAW")
        self.serial.reset_input_buffer()
        for line in self.lines(timeout):
            value = self._distance(line, anchor)
            if value is None:
                continue
            if value < 0:
                failed += 1
            else:
                distances.append(value)
            if len(distances) >= count:
                break
        if self.role != "TAG":
            self.command("RAW 0", "# RAW")
        return distances, failed

    def _distance(self, line, anchor):
        if self.role == "TAG":
            if line.startswith("#"):
                return None
            fields = line.split(",")
            if len(fields) != 6:
                return None
            try:
                return int(fields[1 if anchor == "a" else 2])
            except ValueError:
                return None
        # Ancla: "# RAW" seguido de seis timestamps y la distancia
        fields = line.split()
        if len(fields) != 9 or fields[:2] != ["#", "RAW"]:
            return None
        try:
            return int(fields[8])
        except ValueError:
            return None


def measure(node, args):
    timeout = max(10.0, args.samples * 0.3)
    distances, failed = node.sample(args.anchor, args.samples, timeout)
    if len(distances) < max(10, args.samples // 2):
        raise RuntimeError(
            f"solo han llegado {len(distances)} muestras válidas de {args.samples} "
            f"({failed} rangings fallidos). Revisa que el otro nodo esté encendido y a la vista."
        )
    mean = statistics.fmean(distances)
    deviation = statistics.pstdev(distances)
    return mean, deviation, len(distances), failed


def report(measurement, true_mm, antenna_delay):
    mean, deviation, count, failed = measurement
    error = mean - true_mm
    print(
        f"  ant={antenna_delay}  muestras={count}  fallidos={failed}  "
        f"media={mean:.0f} mm  error={error:+.0f} mm  sigma={deviation:.0f} mm"
    )
    return error


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="puerto serie del nodo conectado")
    parser.add_argument("--anchor", choices=("a", "b"), default="a", help="ancla del par que se mide")
    parser.add_argument("--distance", type=float, required=True, help="distancia real, en metros")
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--tolerance", type=float, default=20.0, help="error admitido, en mm")
    parser.add_argument("--apply", action="store_true", help="ajusta el antenna delay del nodo")
    parser.add_argument("--max-steps", type=int, default=6)
    args = parser.parse_args()

    true_mm = args.distance * 1000.0
    node = None
    try:
        node = Node(args.port)
        time.sleep(0.5)
        node.identify()
        expected_anchor = {"ANCLA_A": "a", "ANCLA_B": "b"}.get(node.role)
        if expected_anchor and expected_anchor != args.anchor:
            parser.error(f"el nodo conectado es {node.role}; usa --anchor {expected_anchor}")
        print(f"Nodo {node.role}, par tag-ancla {args.anchor.upper()}, distancia real {true_mm:.0f} mm")

        measurement = measure(node, args)
        error = report(measurement, true_mm, node.antenna_delay)
        if args.apply:
            for _ in range(args.max_steps):
                if abs(error) < args.tolerance:
                    break
                # Solo se ajusta este nodo: cada unidad de delay en TX y en RX
                # acorta la distancia medida en una unidad de tiempo de vuelo.
                node.set_antenna_delay(node.antenna_delay + round(error * DTU_PER_MM))
                measurement = measure(node, args)
                error = report(measurement, true_mm, node.antenna_delay)

        print()
        if abs(error) < args.tolerance:
            print(f"Dentro de tolerancia (|error| < {args.tolerance:.0f} mm).")
        else:
            print(f"Fuera de tolerancia: error {error:+.0f} mm.")
        if args.apply:
            print(f"Antenna delay de {node.role}: {node.antenna_delay}")
            print("Cópialo a ANTENNA_DELAY en firmware/uwb_node/config.h y vuelve a grabar el nodo.")
        else:
            print(
                f"Corrección equivalente en config.yaml: "
                f"anchors.offset_cm.{args.anchor} = {-error / 10.0:+.1f}"
            )
        if measurement[1] > 50.0:
            print("Aviso: sigma mayor de 50 mm; revisa la línea de visión y los reflejos.")
        return 0 if abs(error) < args.tolerance else 1
    except (RuntimeError, TimeoutError, serial.SerialException) as problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    finally:
        if node is not None:
            node.close()


if __name__ == "__main__":
    sys.exit(main())
