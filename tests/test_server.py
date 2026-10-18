import contextlib
import http.client
import re
import socket
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from asbuilt.server import FILES, PAGE, Server

JS, HTML = "text/javascript; charset=utf-8", "text/html; charset=utf-8"
NOT_LISTED = ["/nope", "/../x", "/%2e%2e/x", "//x", "/vendor/", "/vendor/manifest.json"]
ALIASES = ["//main.js", "///vendor/rough.js", "/./main.js", "/main.js/", "/index.html"]


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.chdir("/"))
        self.server = Server(0)
        serving = threading.Thread(target=self.server.serve_forever, args=(0.01,))
        serving.daemon = True
        serving.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, host=None, method="GET"):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        self.addCleanup(conn.close)
        conn.request(method, path, headers={"Host": host} if host else {})
        return conn.getresponse()


class ServerTest(ServerCase):
    def test_it_serves_this_machine_only_with_the_right_types_and_no_store(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for path, kind in {"/?refresh=1": HTML, "/layout.js": JS}.items():
            resp = self.request(path)
            self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, kind))
            self.assertEqual(resp.getheader("Cache-Control"), "no-store")

    def test_the_files_served_are_exactly_the_files_the_page_loads(self):
        code = "".join((PAGE / n).read_text() for n in FILES if n[:6] != "vendor")
        loaded = set(re.findall(r'(?:src="/|from "\./)([^"]+)"', code))
        self.assertEqual(loaded, set(FILES) - {"index.html"})
        for name in loaded:
            body = self.request(f"/{name}").read()
            self.assertEqual(body, (PAGE / name).read_bytes(), name)

    def test_nothing_else_is_served_and_every_answer_says_no_store(self):
        for path in NOT_LISTED + ALIASES:
            resp = self.request(path)
            got = (resp.status, resp.getheader("Cache-Control"))
            self.assertEqual(got, (404, "no-store"), path)

    def test_a_missing_file_stops_the_start_and_names_the_file(self):
        for name in FILES:
            tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "page"
            shutil.copytree(PAGE, tmp, ignore=shutil.ignore_patterns(Path(name).name))
            self.enterContext(mock.patch("asbuilt.server.PAGE", tmp))
            with self.assertRaisesRegex(FileNotFoundError, re.escape(name)):
                Server(0)


class HostTest(ServerCase):
    def test_a_request_naming_another_host_is_refused_whatever_it_asks_for(self):
        port = self.server.server_port
        for host in ["evil.example", "127.0.0.1", "localhost", f"evil:{port}", "x:1"]:
            for method, path in [
                ("GET", "/"),
                ("GET", "/main.js"),
                ("GET", "/nope"),
                ("POST", "/"),
            ]:
                self.assertEqual(
                    self.request(path, host, method).status, 403, (host, path)
                )
        self.assertEqual(
            self.request("/", f"127.0.0.1.evil.example:{port}").status, 403
        )

    def test_this_machines_own_names_are_let_in(self):
        for host in ["127.0.0.1", "localhost"]:
            self.assertEqual(
                self.request("/", f"{host}:{self.server.server_port}").status, 200
            )

    def raw(self, data):
        with socket.create_connection(self.server.server_address, timeout=5) as conn:
            conn.sendall(data)
            return b"".join(iter(lambda: conn.recv(4096), b""))

    def test_a_request_with_no_host_is_refused_and_gets_nothing_else(self):
        everything = self.raw(b"GET / HTTP/1.0\r\n\r\n")
        self.assertTrue(everything.startswith(b"HTTP/1.0 403"))
        self.assertNotIn(b"As-Built", everything)

    def test_a_malformed_request_gets_its_400_and_no_handler_error(self):
        with mock.patch.object(self.server, "handle_error") as failed:
            everything = self.raw(b"GET / / / HTTP/1.0\r\n\r\n")
        self.assertTrue(everything.startswith(b"HTTP/1.0 400"))
        failed.assert_not_called()
