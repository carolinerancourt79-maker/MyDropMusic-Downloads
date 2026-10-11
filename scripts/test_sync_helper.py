"""Exercise the real TLS helper, connection reuse, file integrity and pairing."""
import concurrent.futures
import http.client
import importlib.util
import json
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync_helper', ROOT / 'WindowsSync/MyDropMusicHelper.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class CountingServer(helper.MusicServer):
    accepts = 0
    def get_request(self):
        request = super().get_request()
        self.accepts += 1
        return request


class QuietHandler(helper.Handler):
    def log_message(self, *args):
        pass


class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name) / 'Music'
        cls.root.mkdir()
        helper.CERT_DIR = Path(cls.temp.name) / 'Certificate'
        tls, fingerprint = helper.tls_identity()
        _, second_fingerprint = helper.tls_identity()
        assert fingerprint == second_fingerprint
        cls.files = {'Rock/A + B.mp3': b'audio-plus' * 10000,
                     'Rock/Été 中文.mp3': b'audio-unicode' * 17000,
                     'Dance/B.mp3': b'audio-b' * 20000}
        for name, contents in cls.files.items():
            path = cls.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(contents)
        cls.server = CountingServer(('127.0.0.1', 0), QuietHandler, cls.root, 'test-pairing', tls)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.context = ssl.create_default_context(cafile=str(helper.CERT_DIR / 'sync-cert.pem'))
        # Test trusts this generated certificate, whose CN isn't the loopback IP.
        cls.context.check_hostname = False

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def connection(self):
        return http.client.HTTPSConnection('127.0.0.1', self.server.server_port,
                                          context=self.context, timeout=5)

    def request(self, conn, path, code='test-pairing'):
        conn.request('GET', path, headers={'X-MyDrop-Pairing': code})
        response = conn.getresponse()
        data = response.read()
        return response, data

    def test_keep_alive_and_exact_unicode_bytes(self):
        before = self.server.accepts
        conn = self.connection()
        try:
            for _ in range(2):
                for name, expected in self.files.items():
                    response, data = self.request(conn, '/file?path=' + quote(name, safe='/+'))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(data, expected)
                    self.assertEqual(int(response.getheader('Content-Length')), len(expected))
                    self.assertFalse(response.will_close)
            self.assertEqual(self.server.accepts - before, 1)
        finally:
            conn.close()

    def test_auth_checked_again_on_reused_connection(self):
        conn = self.connection()
        try:
            response, _ = self.request(conn, '/manifest', code='wrong')
            self.assertEqual(response.status, 401)
            response, data = self.request(conn, '/manifest')
            self.assertEqual(response.status, 200)
            self.assertEqual({f['path'] for f in json.loads(data)['files']}, set(self.files))
            response, _ = self.request(conn, '/file?path=Rock/A%20%2B%20B.mp3', code='wrong')
            self.assertEqual(response.status, 401)
        finally:
            conn.close()

    def test_missing_and_traversal_paths_are_rejected(self):
        for path, expected in [('/file?path=absent.mp3', 404), ('/file?path=../outside.mp3', 403)]:
            conn = self.connection()
            try:
                response, _ = self.request(conn, path)
                self.assertEqual(response.status, expected)
            finally:
                conn.close()

    def test_six_clients_with_repeated_downloads(self):
        barrier = threading.Barrier(6)
        def download(_):
            conn = self.connection()
            try:
                response, _ = self.request(conn, '/health')
                self.assertEqual(response.status, 200)
                barrier.wait(timeout=5)
                for name, expected in self.files.items():
                    response, data = self.request(conn, '/file?path=' + quote(name, safe='/'))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(data, expected)
            finally:
                conn.close()
        before = self.server.accepts
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(download, range(6)))
        self.assertEqual(self.server.accepts - before, 6)


if __name__ == '__main__':
    unittest.main()
