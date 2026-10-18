import http.server
from pathlib import Path
from urllib.parse import urlsplit

PAGE = Path(__file__).with_name("page")
LIBRARIES = ("elk.js", "rough.js")
TYPES = {".html": "text/html", ".js": "text/javascript"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        file = self.server.files.get(path)
        if file is None:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", TYPES[file.suffix] + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(file.read_bytes())

    def log_message(self, *args):
        """Say nothing per request: the command line prints the address once."""


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port):
        """Serve the page folder and two libraries on 127.0.0.1:port, from a table fixed here."""
        scripts = {f"/{p.name}": p for p in PAGE.glob("*.js")}
        libraries = {f"/vendor/{n}": PAGE / "vendor" / n for n in LIBRARIES}
        self.files = {"/": PAGE / "index.html"} | scripts | libraries
        for file in self.files.values():
            if not file.is_file():
                raise FileNotFoundError(f"{file} is missing from the installed package")
        super().__init__(("127.0.0.1", port), Handler)
