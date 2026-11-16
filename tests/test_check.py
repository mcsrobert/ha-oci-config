import contextlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import yaml

from ha_oci_config.__main__ import main
from ha_oci_config.check import PLACEHOLDER, check, parse_check_secrets, secret_names, write_placeholder_secrets
from ha_oci_config.errors import Error


class SecretsTest(unittest.TestCase):
    def setUp(self):
        self.config = Path(tempfile.mkdtemp()) / 'config'
        self.addCleanup(shutil.rmtree, self.config.parent)
        self.write('configuration.yaml', 'homeassistant:\n  latitude: !secret home_latitude\n'
                   'automation: !include automations.yaml\n'
                   'template: !include includes/template.yaml\n'
                   'packages: !include_dir_named packages\n')
        self.write('automations.yaml', '- id: a\n  action:\n    - action: notify.x\n      data:\n'
                   '        message: !secret "quoted name"\n')

    def write(self, name, text):
        path = self.config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_finds_secrets_in_the_configuration_and_every_included_file(self):
        self.write('includes/template.yaml', '- sensor:\n    - name: x\n      state: !secret template_state\n')
        self.write('packages/deep/nested.yml', 'mqtt:\n  password: !secret mqtt_password\n'
                   'flow: [!secret flow_one, {key: !secret flow_two}]\n---\nsecond: !secret second_document\n')
        self.write('blueprints/automation/b.yaml', 'blueprint:\n  input: {}\ntrigger: !input t\nkey: !secret blueprint_key\n')
        self.assertEqual(secret_names(self.config), {'home_latitude', 'quoted name', 'template_state',
                                                     'mqtt_password', 'flow_one', 'flow_two', 'second_document',
                                                     'blueprint_key'})

    def test_ignores_secret_text_that_is_not_a_tag(self):
        self.write('includes/template.yaml', '# password: !secret commented\nvalue: "!secret quoted_text"\n')
        self.assertEqual(secret_names(self.config), {'home_latitude', 'quoted name'})

    def test_follows_explicit_includes_recursively_without_requiring_yaml_extensions(self):
        self.write('includes/template.yaml', 'sensor: !include "nested/sensor.inc"\n')
        self.write('includes/nested/sensor.inc', 'url: !secret sensor_url\nvalue: !include ../value\n')
        self.write('includes/value', 'state: !secret sensor_state\n')
        self.write('includes/unreferenced.inc', 'ignored: !secret not_loaded\n')
        self.assertEqual(secret_names(self.config), {'home_latitude', 'quoted name', 'sensor_url', 'sensor_state'})

    def test_include_cycles_and_recursive_yaml_aliases_terminate(self):
        self.write('includes/template.yaml', 'a: !include loop.inc\n')
        self.write('includes/loop.inc', 'a: !include ./nested/../loop.inc\nb: &loop [*loop, !secret cycle_secret]\n')
        self.assertEqual(secret_names(self.config), {'home_latitude', 'quoted name', 'cycle_secret'})

    def test_ignores_non_tag_include_text_missing_files_and_paths_outside_the_config(self):
        self.write('includes/template.yaml', 'comment: "!include unused.inc"\n'
                   'missing: !include missing.inc\noutside: !include ../../outside.inc\n')
        (self.config.parent / 'outside.inc').write_text('x: !secret outside\n')
        self.write('includes/unused.inc', 'x: !secret unused\n')
        self.assertEqual(secret_names(self.config), {'home_latitude', 'quoted name'})

    def test_falls_back_to_text_for_files_yaml_cannot_parse(self):
        self.write('includes/template.yaml', 'a: !secret broken_one\n  b: [\nc: !secret "broken two" # x\n')
        self.assertEqual(secret_names(self.config), {'home_latitude', 'quoted name', 'broken_one', 'broken two'})

    def test_writes_a_placeholder_for_every_secret(self):
        self.write('includes/template.yaml', 'x: !secret home_latitude\n')
        self.assertEqual(write_placeholder_secrets(self.config), ['home_latitude', 'quoted name'])
        self.assertEqual(yaml.safe_load((self.config / 'secrets.yaml').read_text()),
                         {'home_latitude': PLACEHOLDER, 'quoted name': PLACEHOLDER})

    def test_writes_nothing_without_secrets(self):
        shutil.rmtree(self.config)
        self.write('configuration.yaml', 'homeassistant:\n')
        self.assertEqual(write_placeholder_secrets(self.config), [])
        self.assertFalse((self.config / 'secrets.yaml').exists())

    def test_rejects_invalid_placeholder_overrides(self):
        for text in ('broken: [', '[]', 'value', 'false', '1: value', "'': value", 'x: !secret real'):
            with self.subTest(text=text), self.assertRaisesRegex(Error, 'check-secrets must be'):
                parse_check_secrets(text)
        self.assertEqual(parse_check_secrets(''), {})


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.config = Path(tempfile.mkdtemp()) / 'config'
        self.addCleanup(shutil.rmtree, self.config.parent)
        self.config.mkdir()
        (self.config / 'configuration.yaml').write_text('http:\n  api_password: !secret api_password\n')
        self.seen = {}

    def fake_check_config(self, result):
        def check(path):
            secrets = Path(path) / 'secrets.yaml'
            self.seen['secrets'] = yaml.safe_load(secrets.read_text()) if secrets.exists() else None
            return result

        scripts = types.ModuleType('homeassistant.scripts')
        scripts.check_config = types.SimpleNamespace(check=check)
        homeassistant = types.ModuleType('homeassistant')
        homeassistant.scripts = scripts
        return mock.patch.dict(sys.modules, {'homeassistant': homeassistant, 'homeassistant.scripts': scripts})

    def test_checks_a_copy_with_placeholder_secrets_and_leaves_the_build_untouched(self):
        with self.fake_check_config({'except': {}, 'warn': {}}), contextlib.redirect_stdout(io.StringIO()) as log:
            check(self.config)
        self.assertEqual(self.seen['secrets'], {'api_password': PLACEHOLDER})
        self.assertFalse((self.config / 'secrets.yaml').exists())
        self.assertIn('passed', log.getvalue())

    def test_reports_problems(self):
        result = {'except': {'http': [f'{self.config}/x is bad']}, 'warn': {}}
        with self.fake_check_config(result), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(Error):
                check(self.config)
        self.assertFalse((self.config / 'secrets.yaml').exists())

    def test_cli_uses_typed_overrides_for_secrets_in_an_explicit_include_only_in_the_copy(self):
        config = self.config / 'configuration.yaml'
        config.write_text(config.read_text() + 'http: !include http.inc\n')
        (self.config / 'http.inc').write_text('trusted_proxies: [!secret reverse_proxy]\n'
                                             'url: !secret public_url\nport: !secret server_port\n'
                                             'enabled: !secret enabled\nvalues: !secret values\n')
        original = {path.name: path.read_bytes() for path in self.config.iterdir()}
        overrides = ('reverse_proxy: 192.0.2.0/24\npublic_url: https://example.com\n'
                     'server_port: 8123\nenabled: false\nvalues: [one, two]\nunused: dummy\n')
        with self.fake_check_config({'except': {}, 'warn': {}}), contextlib.redirect_stdout(io.StringIO()):
            main(['check', str(self.config), '--secrets', overrides])
        self.assertEqual(self.seen['secrets'], {'api_password': PLACEHOLDER, 'reverse_proxy': '192.0.2.0/24',
                                              'public_url': 'https://example.com', 'server_port': 8123,
                                              'enabled': False, 'values': ['one', 'two']})
        self.assertEqual({path.name: path.read_bytes() for path in self.config.iterdir()}, original)

    def test_cli_reports_invalid_overrides_before_running_the_check(self):
        with self.assertRaisesRegex(SystemExit, 'error: check-secrets must be valid YAML'):
            main(['check', str(self.config), '--secrets', 'broken: ['])
        self.assertEqual(self.seen, {})


if __name__ == '__main__':
    unittest.main()
