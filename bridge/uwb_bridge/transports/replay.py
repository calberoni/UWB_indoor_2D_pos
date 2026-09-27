import asyncio
import csv
from dataclasses import replace
from pathlib import Path

from ..parsers import LOG_COLUMNS, ParseError, parse_log_row
from ..sample import Sample
from .base import SampleHandler, SyncHandler, Transport, TransportError

NOMINAL_PERIOD_MS = 100


class ReplayTransport(Transport):
    source = "replay"

    def __init__(self, path: Path | str, speed: float = 1.0, repeat: bool = False) -> None:
        super().__init__()
        if speed <= 0:
            raise ValueError("speed debe ser mayor que 0")
        self._path = Path(path)
        self._speed = speed
        self._repeat = repeat
        # Se lee al construir: un fichero que no vale debe fallar antes de
        # arrancar los servidores, no después.
        self._samples = self._load()

    def _load(self) -> list[Sample]:
        try:
            with self._path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                missing = [c for c in LOG_COLUMNS[:7] if c not in (reader.fieldnames or [])]
                if missing:
                    raise TransportError(f"{self._path} no tiene la cabecera de un log: faltan {', '.join(missing)}")
                samples = []
                for row in reader:
                    try:
                        samples.append(parse_log_row(row))
                    except ParseError:
                        self.discarded += 1
        except OSError as exc:
            raise TransportError(f"no se puede leer {self._path}: {exc.strerror}") from exc
        except UnicodeDecodeError as exc:
            raise TransportError(f"{self._path} no es un log de texto") from exc
        if not samples:
            raise TransportError(f"{self._path} no contiene muestras")
        return samples

    async def run(self, on_sample: SampleHandler, on_sync: SyncHandler | None = None) -> None:
        samples = self._samples
        clock = asyncio.get_running_loop()
        first_ms = samples[0].t_host_ms
        lap_ms = samples[-1].t_host_ms - first_ms + NOMINAL_PERIOD_MS
        shift_ms = 0
        while True:
            start = clock.time()
            for sample in samples:
                # Se espera hasta una hora absoluta, no un intervalo: así los
                # retrasos de cada espera no se acumulan a lo largo del log.
                due = start + (sample.t_host_ms - first_ms) / 1000.0 / self._speed
                await asyncio.sleep(max(due - clock.time(), 0.0))
                on_sample(replace(sample, t_host_ms=sample.t_host_ms + shift_ms))
            if not self._repeat:
                return
            # Al repetir, la hora del Mac sigue avanzando como en una sesión
            # real; t_ms y seq vuelven a empezar, como si el tag reiniciara.
            shift_ms += lap_ms
            await asyncio.sleep(NOMINAL_PERIOD_MS / 1000.0 / self._speed)
