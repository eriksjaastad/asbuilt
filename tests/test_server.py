import re
import tempfile
from pathlib import Path
from unittest import mock

from asbuilt.server import PAGE, Server
from server_case import ServerCase

JS, HTML = "text/javascript; charset=utf-8", "text/html; charset=utf-8"
SERVED = {"/?refresh=1": HTML, "/layout.js": JS, "/vendor/elk.js": JS}
NOT_LISTED = ["/nope", "/../x", "/%2e%2e/x", "//x", "/vendor/", "/vendor/manifest.json"]


class ServerTest(ServerCase):
    def test_it_serves_this_machine_only_and_the_page_scripts_and_libraries(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for path, kind in SERVED.items():
            resp = self.request(path)
            self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, kind))
            self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        rough = (PAGE / "vendor/rough.js").read_bytes()
        self.assertEqual(self.request("/vendor/rough.js").read(), rough)

    def test_every_script_the_page_names_is_served(self):
        named = re.findall(r'src="([^"]+)"', (PAGE / "index.html").read_text())
        self.assertGreaterEqual(len(named), 3)
        for path in named:
            self.assertEqual(self.request(path).status, 200, path)

    def test_only_the_listed_files_are_served(self):
        for path in NOT_LISTED:
            self.assertEqual(self.request(path).status, 404, path)

    def test_a_missing_library_file_stops_the_start_and_names_the_file(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "index.html").write_text("")
        self.enterContext(mock.patch("asbuilt.server.PAGE", tmp))
        with self.assertRaisesRegex(FileNotFoundError, r"elk\.js is missing"):
            Server(0)
