"""Real-window behaviour of the memory card: pinning, anchoring, cleanup and width.

Pixel/CSS parity with the native card lives in test_memory_summary.py; this file
only asserts things that need a running window: the hover card is anchored above
its widget, a click keeps the same card open, obsolete canvases from older
versions disappear, and the card never widens past a 280pt window.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1]
APP_BINARY = Path("/Applications/Tern.app/Contents/MacOS/tern")
TERN = str(APP_BINARY) if APP_BINARY.is_file() else shutil.which("tern")


@unittest.skipUnless(sys.platform == "darwin" and TERN, "requires native macOS Tern")
class MemoryPanelLayoutTests(unittest.TestCase):
    def test_widget_anchored_card_pins_and_fits_a_narrow_window(self):
        with TemporaryDirectory(prefix="tern-memory-card-test-") as directory:
            root = Path(directory)
            (root / "crates/tern/goldens").mkdir(parents=True)
            fixture = root / "crates/plugins/fixtures/connection-status"
            fixture.mkdir(parents=True)
            shutil.copyfile(SOURCE / "plugin.toml", fixture / "plugin.toml")
            gib = 1024 ** 3
            sample = {
                "used_bytes": int(18.2 * gib), "total_bytes": 24 * gib,
                "available_bytes": int(5.8 * gib), "scope": "system", "platform": "darwin",
                "pressure": "normal", "swap_used_bytes": 430 * 1024 ** 2, "swap_total_bytes": 2 * gib,
                "process_count": 983, "processes": [],
                "breakdown": [
                    {"id": "app", "label": "App", "bytes": 6 * gib, "included": True},
                    {"id": "wired", "label": "Wired", "bytes": 3 * gib, "included": True},
                    {"id": "compressed", "label": "Compressed", "bytes": 7 * gib, "included": True},
                    {"id": "cached", "label": "Cached files", "bytes": 4 * gib, "included": False},
                ],
                "apps": [
                    {"name": "An application name much wider than the available cell",
                     "rss_bytes": 12 * gib, "count": 47},
                    {"name": "node", "rss_bytes": 896 * 1024 ** 2, "count": 6},
                ],
            }
            (fixture / "memory_probe.py").write_text(
                "print(" + repr(json.dumps(sample)) + ")\n")
            (root / "hosts.json").write_text(json.dumps({"hosts": []}))
            (fixture / "window.luau").write_text(
                'tern.command({id="legacy-open",title="Legacy fixture",run=function(cx)\n'
                'cx.canvas:open({title="Old memory details",about="内存详情：local",'
                'view=tern.ui.text("Old memory details"),how="beside",focus=false})\nend})\n')
            window_source = (SOURCE / "window.luau").read_text()
            self.assertTrue(window_source.startswith("--!strict"))
            window_source += '''
tern.command({id="fixture-step",title="Fixture sample",run=function(cx)
    refresh(cx)
end})
'''
            socket = root / "control.sock"
            control = root / "tern-control"
            control.write_text(
                '#!/bin/sh\nif [ "$1" = ctl ]; then\n  shift\n'
                + "  exec " + shlex.quote(TERN) + " ctl --control " + shlex.quote(str(socket)) + ' "$@"\nfi\n'
                + "exec " + shlex.quote(TERN) + ' "$@"\n')
            control.chmod(0o755)
            environment = {k: v for k, v in os.environ.items() if not k.startswith("TERN_")}
            environment.update(TERN_CONFIG_DIR=str(root / "config"), TERN_DAEMON_SOCKET=str(root / "daemon.sock"),
                               TERN_EXE=str(control), STENCIL_FIXTURE_ROOT=str(root / "crates/tern"),
                               STENCIL_LOG_DIR=str(root / "logs"),
                               CONNECTION_STATUS_CONFIG=str(root / "hosts.json"))
            log_path = root / "serve.log"
            with log_path.open("w") as log:
                process = subprocess.Popen([TERN, "serve", "--control", str(socket), "--out", str(root / "shots")],
                                           env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

                def ctl(*arguments):
                    result = subprocess.run([TERN, "ctl", "--control", str(socket), *arguments], env=environment,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                            timeout=12, check=True)
                    answer = json.loads(result.stdout)
                    self.assertTrue(answer.get("ok"), answer)
                    return answer

                def wait_for(predicate):
                    deadline = time.monotonic() + 15
                    while True:
                        answer = predicate()
                        if answer:
                            return answer
                        self.assertIsNone(process.poll(), log_path.read_text())
                        if time.monotonic() >= deadline:
                            self.fail("native card state did not arrive\n" + log_path.read_text())
                        if socket.exists():
                            ctl("wait", "50")
                        time.sleep(0.05)

                widget = ".wg[data-widget='connection-status/memory']"
                card = ".wgc[data-widget='connection-status/memory']"

                def texts():
                    nodes = []
                    for part in (".wgc-k", ".wgc-v", ".wgc-btn"):
                        nodes += ctl("tree", card + " " + part).get("nodes", [])
                    return [node.get("text") or "" for node in nodes]

                def command(name):
                    ctl("plugins", "run", "plugin.connection-status." + name)

                try:
                    wait_for(socket.exists)
                    ctl("size", "480", "850")
                    ctl("font", "19")
                    ctl("ui-font-size", "17")
                    ctl("reduce-motion", "on")
                    ctl("plugins", "fixtures")
                    original = {pane["id"] for pane in ctl("state")["panes"]}
                    command("legacy-open")
                    self.assertNotEqual({pane["id"] for pane in ctl("state")["panes"]}, original)
                    (fixture / "window.luau").write_text(window_source)
                    ctl("plugins", "fixtures")
                    wait_for(lambda: {pane["id"] for pane in ctl("state")["panes"]} == original)
                    ctl("widgets", "set", "connection-status/memory@ring")
                    command("fixture-step")
                    wait_for(lambda: "76%" in str(ctl("tree", widget)))

                    # Hovering shows the card just above the widget, with no extra pane or PiP.
                    ctl("hover", widget)
                    wait_for(lambda: ctl("tree", card)["count"] == 1)
                    self.assertEqual(ctl("state")["pips"], [])
                    popup = ctl("tree", card)["nodes"][0]["rect"]
                    button = ctl("tree", widget)["nodes"][0]["rect"]
                    self.assertLessEqual(popup[1] + popup[3], button[1] + 2)
                    self.assertLess(button[1] - popup[1] - popup[3], 24)

                    # A click pins the very same card; moving away leaves it open.
                    ctl("click", widget)
                    ctl("move", "450", "110")
                    ctl("wait", "500")
                    self.assertEqual(ctl("tree", card)["count"], 1)
                    pinned = ctl("tree", card)["nodes"][0]["rect"]
                    self.assertAlmostEqual(pinned[0], popup[0], delta=2)
                    self.assertAlmostEqual(pinned[1], popup[1], delta=2)

                    values = texts()
                    for text in ("GB of 24 GB", "5.8 GB available", "Top apps"):
                        self.assertTrue(any(text in value for value in values), text)
                    self.assertTrue(any(value.startswith("An application name much wider") for value in values))
                    self.assertFalse(any(value.startswith("PID ") for value in values))

                    # The card fits a 280pt window instead of forcing the popover wider.
                    ctl("size", "280", "850")
                    ctl("wait", "250")
                    narrow = ctl("tree", card)["nodes"][0]["rect"]
                    self.assertLessEqual(narrow[2], 257)

                    # A second click closes it instead of stacking another card.
                    ctl("size", "480", "850")
                    ctl("wait", "250")
                    ctl("click", widget)
                    ctl("wait", "500")
                    self.assertEqual(ctl("tree", ".wgc")["count"], 0)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    if os.environ.get("MEMORY_PANEL_LOG"):
                        print(log_path.read_text())


if __name__ == "__main__":
    unittest.main()
