import contextlib
import http.client
import threading
import unittest

from asbuilt.server import Server


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.chdir("/"))
        self.server = Server(0)
        self.port = self.server.server_port
        serving = threading.Thread(target=self.server.serve_forever, args=(0.01,))
        serving.daemon = True
        serving.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", path)
        return conn.getresponse()
