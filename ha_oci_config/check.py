"""Run Home Assistant's check_config on a built configuration."""
import json
from pathlib import Path
import re
import shutil
import tempfile

import yaml

from .errors import Error

PLACEHOLDER = 'ha-oci-config-placeholder'
# Fallback for files PyYAML cannot parse; check_config reports those files itself.
SECRET = re.compile(r'''!secret[ \t]+(?:"([^"\n]+)"|'([^'\n]+)'|([^\s#,\]}]+))''')


def check(config, placeholders=None):
    from homeassistant.scripts import check_config

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / 'config'
        shutil.copytree(config, copy)
        write_placeholder_secrets(copy, placeholders)
        result = check_config.check(str(copy))
    problems = {}
    for kind, key in (('errors', 'except'), ('warnings', 'warn')):
        problems[kind] = {domain: [message.replace(f'{copy}/', '') for message in messages if isinstance(message, str)]
                          for domain, messages in result[key].items()}
    print(json.dumps(problems, indent=2))
    if any(problems.values()):
        raise Error('Home Assistant check_config reported problems')
    print('Home Assistant check_config passed')


def parse_check_secrets(text):
    """Read synthetic validation values supplied through the action or CLI."""
    try:
        values = yaml.safe_load(text)
    except yaml.YAMLError:
        raise Error('check-secrets must be valid YAML') from None
    if values is None:
        return {}
    if not isinstance(values, dict) or any(not isinstance(name, str) or not name for name in values):
        raise Error('check-secrets must be a YAML mapping with nonempty string keys')
    return values


def write_placeholder_secrets(config, placeholders=None):
    """Write secrets.yaml with a placeholder for every secret the configuration references.

    The real secrets.yaml lives on the persistent volume and never reaches Git or the image,
    so check_config would otherwise fail on every !secret. Only call this on a throwaway copy.
    """
    config = Path(config)
    names = sorted(secret_names(config))
    if names:
        placeholders = placeholders or {}
        values = {name: placeholders.get(name, PLACEHOLDER) for name in names}
        (config / 'secrets.yaml').write_text(yaml.safe_dump(values, sort_keys=True))
    return names


def secret_names(config):
    """Find !secret references in YAML files and their explicit includes.

    Home Assistant treats an explicit !include as YAML regardless of its extension.
    """
    config = Path(config).resolve()
    names = set()
    pending = [path for path in sorted(config.rglob('*')) if path.suffix in ('.yaml', '.yml')]
    seen = set()
    while pending:
        path = pending.pop().resolve()
        if path in seen or not path.is_relative_to(config) or path.name == 'secrets.yaml' or not path.is_file():
            continue
        seen.add(path)
        secrets, includes = file_references(path.read_text(errors='replace'))
        names |= secrets
        pending.extend(path.parent / include for include in includes)
    return names


def file_references(text):
    """Return secret names and explicit include paths without constructing YAML tags."""
    try:
        nodes = [node for node in yaml.compose_all(text, Loader=yaml.SafeLoader) if node is not None]
    except yaml.YAMLError:
        return {next(group for group in match.groups() if group) for match in SECRET.finditer(text)}, set()
    names = set()
    includes = set()
    seen = set()
    while nodes:
        node = nodes.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            if node.tag == '!secret' and node.value:
                names.add(node.value)
            elif node.tag == '!include' and node.value:
                includes.add(node.value)
        elif isinstance(node, yaml.SequenceNode):
            nodes.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                nodes.extend((key, value))
    return names, includes
