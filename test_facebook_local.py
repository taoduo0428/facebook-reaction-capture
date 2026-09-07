import json
import os
from pathlib import Path
import tempfile
import unittest
import http.client
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from facebook_local import configuration, environment, initialize


class LocalDeploymentTests(unittest.TestCase):
    def test_v2_serves_assets_but_never_parent_files(self):
        from start_ui_v2 import V2ProxyHandler
        server = ThreadingHTTPServer(('127.0.0.1', 0), V2ProxyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, expected in [('/', 200), ('/app.js', 200), ('/../README.md', 404), ('/%2e%2e/README.md', 404), ('/..\\README.md', 404)]:
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                connection.request('GET', path)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, expected, path)
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_initialize_preserves_user_registry_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('facebook.local', 'facebook_workers'):
                (root / (name + '.example.json')).write_text('{}', encoding='utf-8')
            initialize(root)
            registry = root / 'facebook_workers.json'
            registry.write_bytes(b'{"existing_account":true}')
            initialize(root)
            self.assertEqual(registry.read_bytes(), b'{"existing_account":true}')

    def test_explicit_paths_survive_spaces_and_non_ascii(self):
        with tempfile.TemporaryDirectory(prefix='fb deploy ') as directory:
            root = Path(directory)
            sdk, avd = root / 'SDK 路径', root / 'AVD 路径'
            (root / 'facebook.local.json').write_text(json.dumps({'sdk_root': str(sdk), 'avd_home': str(avd), 'port': 18765}), encoding='utf-8')
            with patch.dict(os.environ):
                settings = configuration(root)
                environment(settings, root)
                self.assertEqual(os.environ['FACEBOOK_SDK_ROOT'], str(sdk.resolve()))
                self.assertEqual(os.environ['FACEBOOK_AVD_HOME'], str(avd.resolve()))
                self.assertTrue(os.environ['FACEBOOK_ADB_EXE'].endswith('adb.exe'))
                self.assertEqual(settings['port'], 18765)

    def test_rejects_invalid_ports_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for port in (True, '8765', -1, 65536):
                (root / 'facebook.local.json').write_text(json.dumps({'port': port}), encoding='utf-8')
                with self.subTest(port=port), self.assertRaises(ValueError):
                    configuration(root)

    def test_ui_files_are_utf8_and_title_closes(self):
        root = Path(__file__).resolve().parent
        for folder in ('web', 'web_v2'):
            html = (root / folder / 'index.html').read_text(encoding='utf-8')
            self.assertNotIn('\ufffd', html)
            self.assertIn('</title>', html)
            self.assertNotIn('?/span>', html)


if __name__ == '__main__':
    unittest.main()
