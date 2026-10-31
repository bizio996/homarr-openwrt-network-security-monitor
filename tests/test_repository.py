"""Static release checks; no OpenWrt or Homarr access required."""
import json
import pathlib
import py_compile
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class RepositoryChecks(unittest.TestCase):
    def test_python_compiles(self):
        py_compile.compile(str(ROOT / 'collector' / 'app.py'), doraise=True)

    def test_all_widgets_have_sanitized_http_source(self):
        widgets = sorted((ROOT / 'widgets').glob('*.json'))
        self.assertEqual(len(widgets), 8)
        for path in widgets:
            data = json.loads(path.read_text())
            self.assertIn('requests', data, path.name)
            for src in data.get('sources', {}).values():
                self.assertEqual(src.get('baseUrl'), 'http://192.0.2.10:8788', path.name)
                self.assertNotIn('token', json.dumps(src).lower())

    def test_no_private_ip_or_credentials_in_exported_text(self):
        # Public sources can legitimately contain RFC5737 examples and public ping targets.
        bad = re.compile(r'192\.168\.(?:1|50)\.(?:1|24|60|224)\b|AGZHM_1\.4\.4|homarr-monitor\.json.*password', re.I)
        for path in ROOT.rglob('*'):
            if not path.is_file() or '.git' in path.parts or '__pycache__' in path.parts:
                continue
            if path.suffix.lower() in ('.md', '.yaml', '.json', '.py', '.example', '.yml', '.txt', '.sh', ''):
                self.assertIsNone(bad.search(path.read_text()), str(path))

    def test_protected_secrets_not_in_repo(self):
        for path in ROOT.rglob('*'):
            if path.is_file() and '.git' not in path.parts:
                self.assertNotEqual(path.name, '.env')
                self.assertNotIn(path.suffix, ('.sqlite3', '.db'))

if __name__ == '__main__':
    unittest.main()
