import asyncio
import webbrowser
from pathlib import Path

from .config import DASHBOARD_DIR, LOGS_DIR, Config, config_message
from .pipeline import Pipeline
from .sample import Sample
from .server import HOST, Hub, start_http_server
from .session_log import SessionLog
from .transports import Transport

STATUS_INTERVAL_S = 10.0
CONTRACT_WS_PORT = 8765


class StartupError(Exception):
    pass


async def run(
    config: Config,
    transport: Transport,
    *,
    write_log: bool = True,
    open_browser: bool = False,
    dashboard_dir: Path = DASHBOARD_DIR,
    logs_dir: Path = LOGS_DIR,
) -> None:
    """Arranca los servidores y procesa muestras hasta que el transporte termina."""
    pipeline = Pipeline(config)
    hub = Hub(config_message(config, transport.source))
    session_log = SessionLog(logs_dir) if write_log else None
    last_message: dict = {}

    def on_sample(sample: Sample) -> None:
        nonlocal last_message
        result = pipeline.process(sample)
        last_message = result.message
        hub.publish(result.message)
        if session_log is not None:
            session_log.write(sample, result)

    async def report_status() -> None:
        reported = None
        while True:
            await asyncio.sleep(STATUS_INTERVAL_S)
            if last_message is reported:
                print(f"Sin datos · clientes {hub.client_count}")
                continue
            reported = last_message
            discarded = f" · ilegibles {transport.discarded}" if transport.discarded else ""
            rate = "—" if reported["rate_hz"] is None else f"{reported['rate_hz']:.1f}"
            print(
                f"{rate} Hz · latencia {reported['latency_ms']} ms"
                f" · perdidos {reported['lost']}{discarded} · clientes {hub.client_count}"
            )

    try:
        http_server = start_http_server(dashboard_dir, config.http_port)
    except OSError as exc:
        raise StartupError(f"no se puede abrir el puerto HTTP {config.http_port}: {exc.strerror or exc}") from exc
    try:
        try:
            ws_server = await hub.serve(config.ws_port)
        except OSError as exc:
            raise StartupError(f"no se puede abrir el puerto WebSocket {config.ws_port}: {exc.strerror or exc}") from exc

        url = f"http://{HOST}:{config.http_port}/"
        print(f"Dashboard: {url} · WebSocket: ws://{HOST}:{config.ws_port}")
        if open_browser:
            # El dashboard supone el puerto del contrato salvo que se le indique otro.
            webbrowser.open(url if config.ws_port == CONTRACT_WS_PORT else f"{url}?ws={config.ws_port}")
        status_task = asyncio.create_task(report_status())
        try:
            await transport.run(on_sample, pipeline.add_sync)
        finally:
            status_task.cancel()
            ws_server.close()
            await ws_server.wait_closed()
    finally:
        http_server.shutdown()
        http_server.server_close()
        if session_log is not None:
            session_log.close()
