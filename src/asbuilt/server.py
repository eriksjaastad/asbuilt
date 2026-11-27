import http.server
import json
import sys
from pathlib import Path

PAGE = Path(__file__).with_name("page")
FILES = (  # exactly what the page loads, as paths under the page folder
    "index.html main.js pipeline.js marks.js code.js render.js latest.js prepare.js layout.js lanes.js shapes.js draw.js "
    "arrows.js vendor/elk.js vendor/rough.js"
).split()
OVERVIEW_FILES = ("overview.html", "overview.js")
TYPES = {".html": "text/html", ".js": "text/javascript"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # The request line as sent: self.path has leading slashes folded away.
        path = self.requestline.split()[1].partition("?")[0]
        if path == "/overview.json":
            if self.server.overview_json is None:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.server.overview_json)
            return
        if path == "/events":
            if self.server.hub is None:
                return self.send_error(404)
            return self.events()
        file = self.server.files.get(path)
        if file is None:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", TYPES[file.suffix] + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(file.read_bytes())

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        version = -1
        while True:
            hub = self.server.hub
            version, message = hub.newer_than(version, self.server.keep_alive)
            line = ": keep-alive" if message is None else f"data: {message}"
            self.wfile.write(f"{line}\n\n".encode())

    def parse_request(self):
        """Refuse any request, of any kind, whose Host is not this server's own address."""
        if not super().parse_request():
            return False
        if self.headers.get("Host") not in self.server.hosts:
            self.send_error(403)
            return False
        return True

    def log_message(self, *args):
        """Say nothing per request: the command line prints the address once."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, hub, keep_alive=15):
        self.files = {"/" if n == "index.html" else f"/{n}": PAGE / n for n in FILES}
        self._start(port)
        self.hub, self.keep_alive = hub, keep_alive
        self.overview_json = None

    def _start(self, port):
        for file in self.files.values():
            if not file.is_file():
                raise FileNotFoundError(f"{file} is missing from the installed package")
        # For http:// a browser drops only the default port, 80, from Host; the check below needs it.
        if port == 80:
            raise OSError(
                "browsers leave port 80 out of the address, which this server checks"
            )
        super().__init__(("127.0.0.1", port), Handler)
        self.hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    def handle_error(self, request, client_address):
        """A page that closes its tab ends its stream with a broken pipe or reset: not an error to print."""
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class OverviewServer(Server):
    """Serve the component overview page and its /overview.json payload on localhost only."""

    def __init__(self, port, payload):
        self.files = {
            "/" if n == "overview.html" else f"/{n}": PAGE / n for n in OVERVIEW_FILES
        }
        self._start(port)
        self.hub, self.keep_alive = None, 15
        self.overview_json = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
