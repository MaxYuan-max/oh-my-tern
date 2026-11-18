"""Locate and read the per-installation host list shared by the plugin's helpers.

Host names, SSH addresses, tunnels and memory scope belong to the machine the
plugin runs on, not to the plugin: the repository ships only
`hosts.example.json`, and each installation keeps `hosts.json` in its plugin
data directory. `CONNECTION_STATUS_CONFIG` overrides the file (tests use it).
"""
import json
import os
from pathlib import Path

DEFAULT_NAME = "hosts.json"


def config_path():
    override = os.environ.get("CONNECTION_STATUS_CONFIG")
    if override:
        return Path(override).expanduser()
    return data_dir() / DEFAULT_NAME


def data_dir():
    override = os.environ.get("CONNECTION_STATUS_DATA")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/Tern/plugin-data/connection-status"


def load(path=None):
    """Return the parsed config, or `{}` when the installation has none yet."""
    source = Path(path).expanduser() if path else config_path()
    try:
        return json.loads(source.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError("无法读取主机配置 %s：%s" % (source, error))


def hosts(config):
    """The host entries, with the fields every consumer relies on validated."""
    entries = config.get("hosts") if isinstance(config, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError("主机配置的 hosts 必须是数组")
    result = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("hosts[%d] 必须是对象" % index)
        for field in ("name", "address", "port"):
            if not isinstance(entry.get(field), (str, int)) or str(entry[field]) == "":
                raise ValueError("hosts[%d] 缺少 %s" % (index, field))
        result.append(entry)
    return result
