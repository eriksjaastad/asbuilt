import http.server
from pathlib import Path

PAGE = Path(__file__).with_name("page")
FILES = (  # exactly what the page loads, as paths under the page folder
    "index.html main.js render.js latest.js prepare.js layout.js shapes.js draw.js "
    "arrows.js vendor/elk.js vendor/rough.js"
).split()
TYPES = {".html": "text/html", ".js": "text/javascript"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # The request line as sent: self.path has leading slashes folded away.
        path = self.requestline.split()[1].partition("?")[0]
        file = self.server.files.get(path)
        if file is None:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", TYPES[file.suffix] + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(file.read_bytes())

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port):
        self.files = {"/" if n == "index.html" else f"/{n}": PAGE / n for n in FILES}
        for file in self.files.values():
            if not file.is_file():
                raise FileNotFoundError(f"{file} is missing from the installed package")
        super().__init__(("127.0.0.1", port), Handler)
