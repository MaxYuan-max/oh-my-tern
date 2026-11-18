"""A generic hosts.json for tests, set up before the plugin modules are imported.

The plugin reads host names, SSH destinations and tunnels from the installation
(`hosts.json`); the repository ships only an example. Tests use these neutral
names so nothing about a real machine is baked into the suite.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

PLUGIN = Path(__file__).resolve().parents[1]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

_DIRECTORY = tempfile.TemporaryDirectory(prefix="connection-status-hosts-")
CONFIG = Path(_DIRECTORY.name) / "hosts.json"
DATA = Path(_DIRECTORY.name) / "data"
DATA.mkdir()

CONFIG.write_text(json.dumps({
    "version": 1,
    "tunnel_script": "~/src/tern-ssh-udp/tunnel.py",
    "container_usage": "~/bin/container-usage",
    "hosts": [
        {
            "name": "build-a",
            "address": "user@build-a.localhost:18376",
            "fallback": "user@127.0.0.1:18376",
            "alias": "build-a.localhost",
            "port": "18376",
            "endpoint": "",
            "detail": "SSH 隧道 → build-a",
            "tunnel": {
                "summary": "本地 UDP 18376 经 SSH/TCP 转发至 build-a UDP 8376。",
                "ssh": "build-a",
                "target_host": "127.0.0.1",
                "target_port": 8376,
                "files": "a-tunnel",
                "doctor": "user@build-a.localhost:18376",
            },
            "memory": {"ssh": "build-a", "kind": "container"},
        },
        {
            "name": "linux-b",
            "address": "user@linux-b.localhost:28377",
            "fallback": "user@127.0.0.1:28377",
            "alias": "linux-b.localhost",
            "port": "28377",
            "endpoint": "",
            "detail": "SSH 隧道 → linux-b",
            "tunnel": {
                "summary": "本地 UDP 28377 经 build-a 的 SSH/TCP 转发至 linux-b（10.0.0.9）UDP 8376。",
                "ssh": "build-a",
                "target_host": "10.0.0.9",
                "target_port": 8376,
                "files": "b-tunnel",
                "doctor": "user@linux-b.localhost:28377",
            },
            "memory": {"ssh": "ssh://user@linux-b.example:2222", "kind": "linux"},
        },
    ],
}))

os.environ["CONNECTION_STATUS_CONFIG"] = str(CONFIG)
os.environ["CONNECTION_STATUS_DATA"] = str(DATA)
