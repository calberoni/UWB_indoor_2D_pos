from collections import deque

SEQ_MODULO = 0x10000
RATE_WINDOW_MS = 1000
OFFSET_WINDOW_MS = 30_000


class RateMeter:
    """Muestras por segundo, medidas sobre las recibidas en el último segundo."""

    def __init__(self, window_ms: int = RATE_WINDOW_MS) -> None:
        self._window_ms = window_ms
        self._times: deque[int] = deque()

    def reset(self) -> None:
        self._times.clear()

    def update(self, t_host_ms: int) -> float:
        times = self._times
        times.append(t_host_ms)
        while times[0] < t_host_ms - self._window_ms:
            times.popleft()
        span_ms = times[-1] - times[0]
        if len(times) < 2 or span_ms <= 0:
            return 0.0
        # Intervalos entre muestras y no un simple recuento: con el recuento, la
        # muestra que está justo en el borde de la ventana entra y sale por el
        # jitter y la tasa baila entre 10 y 11 con el tag a 10 Hz exactos.
        return (len(times) - 1) * 1000.0 / span_ms


class LossCounter:
    """Ciclos perdidos según los saltos de seq, con vuelta a 0 después de 65535."""

    def __init__(self) -> None:
        self.lost = 0
        self._last_seq: int | None = None

    def resync(self) -> None:
        """Olvida el último seq sin borrar la cuenta; se usa cuando el tag reinicia."""
        self._last_seq = None

    def update(self, seq: int) -> int:
        if self._last_seq is not None:
            step = (seq - self._last_seq) % SEQ_MODULO
            # Un paso de más de media vuelta es un seq repetido o atrasado, no una
            # pérdida de decenas de miles de ciclos.
            if step == 0 or step > SEQ_MODULO // 2:
                return self.lost
            self.lost += step - 1
        self._last_seq = seq
        return self.lost


class LatencyEstimator:
    """Latencia según la sección 4 del contrato.

    Con medidas SYNC (serie) usa el desfase de la de menor ida y vuelta. Sin
    ellas (BLE, replay, o serie antes de la primera respuesta) usa el mínimo de
    t_host − t_ms en los últimos 30 s.
    """

    def __init__(self, window_ms: int = OFFSET_WINDOW_MS) -> None:
        self._window_ms = window_ms
        self._diffs: deque[tuple[int, int]] = deque()  # (t_host_ms, t_host_ms − t_ms)
        self._sync_round_trip_ms: float | None = None
        self._sync_offset_ms: float | None = None

    def reset(self) -> None:
        self._diffs.clear()
        self._sync_round_trip_ms = None
        self._sync_offset_ms = None

    @property
    def sync_round_trip_ms(self) -> float | None:
        return self._sync_round_trip_ms

    def add_sync(self, t0_ms: float, t1_ms: float, t_tag_ms: int) -> None:
        round_trip = t1_ms - t0_ms
        if round_trip < 0:
            return
        if self._sync_round_trip_ms is None or round_trip < self._sync_round_trip_ms:
            self._sync_round_trip_ms = round_trip
            self._sync_offset_ms = (t0_ms + t1_ms) / 2 - t_tag_ms

    def update(self, t_host_ms: int, t_ms: int) -> int:
        diffs = self._diffs
        diffs.append((t_host_ms, t_host_ms - t_ms))
        while diffs[0][0] < t_host_ms - self._window_ms:
            diffs.popleft()
        offset = self._sync_offset_ms
        if offset is None:
            offset = min(diff for _, diff in diffs)
        # El desfase por SYNC tiene un error de hasta media ida y vuelta, así que
        # la resta puede salir ligeramente negativa.
        return max(0, round(t_host_ms - (t_ms + offset)))
