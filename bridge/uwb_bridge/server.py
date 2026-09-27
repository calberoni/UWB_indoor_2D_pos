import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.exceptions import ConnectionClosed

HOST = "localhost"


class Hub:
    """Clientes WebSocket conectados y envío de mensajes a todos ellos."""

    def __init__(self, config_message: dict) -> None:
        self._config_text = json.dumps(config_message, ensure_ascii=False)
        self._clients: set[ServerConnection] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def serve(self, port: int):
        # close_timeout corto: al parar el bridge no se espera 10 s a un cliente
        # que no contesta.
        return serve(self.handle, HOST, port, close_timeout=1.0)

    async def handle(self, connection: ServerConnection) -> None:
        try:
            # El cliente entra en la lista después de recibir `config`, para que
            # nunca le llegue un `sample` antes.
            await connection.send(self._config_text)
            self._clients.add(connection)
            async for _ in connection:
                pass  # el dashboard no envía nada; lo que llegue se descarta
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(connection)

    def publish(self, message: dict) -> None:
        # broadcast() escribe sin esperar a nadie: un cliente lento acumula en su
        # propio búfer hasta que vence su ping y se cierra, sin frenar al resto.
        broadcast(self._clients, json.dumps(message, ensure_ascii=False))


class _DashboardHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # Sin caché: el dashboard se edita mientras el bridge está en marcha.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args) -> None:
        pass


def start_http_server(directory: Path, port: int) -> ThreadingHTTPServer:
    """Sirve la carpeta del dashboard en un hilo aparte. Se detiene con shutdown()."""
    handler = partial(_DashboardHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    return server
