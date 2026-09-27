"""Tag simulado sobre un pseudo-terminal, para probar el transporte serie sin hardware."""

import os
import select
import threading
import time

TAG_CLOCK_OFFSET_MS = 1_000_000  # el millis() del tag no tiene nada que ver con la hora del Mac


class FakeTag:
    """Tag simulado: emite registros por un pty y responde a SYNC."""

    def __init__(self, link_path, first_seq=0, period_s=0.02, answers_sync=True):
        self.master, slave = os.openpty()
        self._slave = slave
        # El transporte abre siempre la misma ruta; el enlace permite cambiar el
        # pty que hay detrás, como cuando el tag se desenchufa y vuelve.
        if os.path.lexists(link_path):
            os.remove(link_path)
        os.symlink(os.ttyname(slave), link_path)
        self._seq = first_seq
        self._period_s = period_s
        self._stop = threading.Event()
        self._answers_sync = answers_sync
        self.sync_requests = 0
        self._threads = [
            threading.Thread(target=self._emit, daemon=True),
            threading.Thread(target=self._answer, daemon=True),
        ]
        self._boot = time.time()
        for thread in self._threads:
            thread.start()

    def millis(self) -> int:
        return TAG_CLOCK_OFFSET_MS + round((time.time() - self._boot) * 1000)

    def write(self, data: str | bytes) -> None:
        try:
            os.write(self.master, data if isinstance(data, bytes) else data.encode())
        except OSError:
            pass

    def _emit(self) -> None:
        while not self._stop.is_set():
            self.write(f"{self._seq},2900,3100,200,190,{self.millis()}\r\n")
            self._seq = (self._seq + 1) % 65536
            self._stop.wait(self._period_s)

    def _answer(self) -> None:
        pending = b""
        while not self._stop.is_set():
            # Con espera acotada: cerrar un pty con otro hilo bloqueado leyéndolo
            # deja colgado el close() en macOS.
            readable, _, _ = select.select([self.master], [], [], 0.02)
            if not readable:
                continue
            try:
                pending += os.read(self.master, 64)
            except OSError:
                return
            *commands, pending = pending.split(b"\n")
            for command in commands:
                if command.strip() == b"SYNC":
                    self.sync_requests += 1
                    if self._answers_sync:
                        self.write(f"# SYNC {self.millis()}\r\n")

    def unplug(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        os.close(self.master)
        os.close(self._slave)
