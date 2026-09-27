import csv
from datetime import datetime
from pathlib import Path

from .parsers import LOG_COLUMNS
from .pipeline import Result
from .sample import Sample


class SessionLog:
    """Escribe el log de una sesión con el formato del contrato.

    El fichero se crea al llegar la primera muestra, para no dejar logs vacíos
    cuando el tag no llega a conectar.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._file = None
        self._writer = None
        self.path: Path | None = None

    def write(self, sample: Sample, result: Result) -> None:
        if self._writer is None:
            self._open()
        has_position = result.x is not None and result.y is not None
        self._writer.writerow(
            [
                sample.t_host_ms,
                sample.seq,
                sample.d_a,
                sample.d_b,
                sample.q_a,
                sample.q_b,
                sample.t_ms,
                f"{result.x:.3f}" if has_position else "",
                f"{result.y:.3f}" if has_position else "",
                result.zone or "",
            ]
        )
        # Una sesión suele acabar con Ctrl+C o tirando del cable: cada fila va a
        # disco en cuanto se escribe.
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._writer = None

    def _open(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        attempt = 1
        while True:
            suffix = "" if attempt == 1 else f"-{attempt}"
            path = self._directory / f"{stamp}{suffix}.csv"
            try:
                # Modo "x": nunca se pisa el log de una sesión anterior.
                self._file = path.open("x", newline="", encoding="utf-8")
                break
            except FileExistsError:
                attempt += 1
        self.path = path
        self._writer = csv.writer(self._file, lineterminator="\n")
        self._writer.writerow(LOG_COLUMNS)
        print(f"Registro: {path}")
