"""Exercise the resolver before setup-python, including failed upstream requests."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


RESOLVER = Path(__file__).resolve().parents[1] / 'scripts' / 'resolve-python.sh'


class PythonVersionTest(unittest.TestCase):
    def resolve(self, release, response='3.14.9\n', exit_code=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            curl = root / 'curl'
            curl.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$CURL_ARGS"\n'
                            'printf "%s" "$CURL_RESPONSE"\nexit "$CURL_EXIT"\n')
            curl.chmod(0o755)
            args = root / 'args'
            result = subprocess.run(['bash', str(RESOLVER), release], capture_output=True, text=True,
                                    env={**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}',
                                         'CURL_ARGS': str(args), 'CURL_RESPONSE': response,
                                         'CURL_EXIT': str(exit_code)})
            return result, args.read_text().splitlines() if args.exists() else []

    def test_exact_release_tag_and_patch_version(self):
        result, args = self.resolve('2026.10.4')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '3.14.9\n')
        self.assertEqual(args[-1],
                         'https://raw.githubusercontent.com/home-assistant/core/2026.10.4/.python-version')

    def test_beta_release(self):
        result, args = self.resolve('2026.12.0b1', '3.14.10\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('/2026.12.0b1/', args[-1])
        self.assertEqual(result.stdout, '3.14.10\n')

    def test_invalid_release_is_rejected_before_fetch(self):
        for release in ('dev', 'latest', '2026.10', '../dev', '2026.10.4\n'):
            with self.subTest(release=release):
                result, args = self.resolve(release)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertEqual(args, [])

    def test_invalid_pin_is_not_passed_to_setup_python(self):
        for response in ('', '3.14', '<html>Not found</html>', '3.14.9\n3.15.0', '3.15.0rc1'):
            with self.subTest(response=response):
                result, _ = self.resolve('2026.10.4', response)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_failed_download_is_not_silently_replaced(self):
        result, _ = self.resolve('2026.10.4', '3.14.9', exit_code=22)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
