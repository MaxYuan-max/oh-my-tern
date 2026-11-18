"""The RTT widget's host-latency behaviour, in an isolated Tern window.

Covers the pane→host resolver, local-pane hiding, tone thresholds, RTT history,
the structured Refresh card and its button.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1]
APP = Path("/Applications/Tern.app/Contents/MacOS/tern")
TERN = str(APP) if APP.is_file() else shutil.which("tern")


@unittest.skipUnless(sys.platform == "darwin" and TERN, "requires macOS Tern")
class RttWidgetTests(unittest.TestCase):
    def test_remote_rtt_card_history_and_refresh(self):
        with TemporaryDirectory(prefix="tern-rtt-widget-test-") as directory:
            root = Path(directory)
            (root / "crates/tern/goldens").mkdir(parents=True)
            fixture = root / "crates/plugins/fixtures/connection-status"
            fixture.mkdir(parents=True)
            shutil.copyfile(SOURCE / "plugin.toml", fixture / "plugin.toml")
            shutil.copyfile(SOURCE / "memory_probe.py", fixture / "memory_probe.py")
            (root / "hosts.json").write_text(json.dumps({"hosts": []}))
            window_source = (SOURCE / "window.luau").read_text()
            self.assertTrue(window_source.startswith("--!strict"))
            window_source += '''
local __rtt_host = nil
local __rtt_record = nil
local __original_update_hosts = updateHosts
updateHosts = function(cx)
    __original_update_hosts(cx)
    if __rtt_host then
        table.insert(hosts, __rtt_host)
        paneHosts[cx.session:focused()] = __rtt_host
        local key = rttKey(__rtt_host)
        if key then
            if not __rtt_record then
                __rtt_record = rttSamples[key] or { address = __rtt_host.address, samples = {}, at = 0 }
            end
            rttSamples[key] = __rtt_record
        end
    end
end
probe = function() end
tern.command({id="fixture-rtt",title="Remote RTT fixture",run=function(cx)
    __rtt_host = { id=4242, kind="remote", state="connected", name="fixture-remote",
                   address="user@fixture-remote.localhost:28377", rtt_ms=95 }
    __rtt_record = nil
    refresh(cx)
end})
tern.command({id="fixture-rtt-bump",title="Change RTT",run=function()
    if __rtt_host then __rtt_host.rtt_ms = 250 end
end})
tern.command({id="fixture-rtt-bump-error",title="Change RTT past warning",run=function()
    if __rtt_host then __rtt_host.rtt_ms = 350 end
end})
tern.command({id="fixture-rtt-local",title="Local RTT fixture",run=function(cx)
    __rtt_host = { id=4243, kind="local", state="connected", name="fixture-local", address="" }
    __rtt_record = nil
    refresh(cx)
end})
tern.command({id="fixture-rtt-sample",title="Add a history point",run=function()
    sampleRtt(tern.now() + RTT_INTERVAL_MS + 1)
end})
tern.command({id="fixture-rtt-stats",title="Write RTT stats",run=function()
    local key = if __rtt_host then rttKey(__rtt_host) else nil
    local record = if key then rttSamples[key] else nil
    local stats = {
        count = if record then #record.samples else 0,
        series = if record then record.samples else {},
        rtt = if __rtt_host then __rtt_host.rtt_ms else nil,
    }
    tern.fs.write(__OUTPUT__ .. "/stats.json", tern.json.encode(stats))
end})
'''.replace("__OUTPUT__", json.dumps(str(root)))
            (fixture / "window.luau").write_text(window_source)
            socket = root / "control.sock"
            environment = {k: v for k, v in os.environ.items() if not k.startswith("TERN_")}
            environment.update(TERN_CONFIG_DIR=str(root / "config"), TERN_DAEMON_SOCKET=str(root / "daemon.sock"),
                               TERN_EXE=TERN, STENCIL_FIXTURE_ROOT=str(root / "crates/tern"),
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
                            self.fail("RTT widget state did not arrive\n" + log_path.read_text())
                        if socket.exists():
                            ctl("wait", "50")
                        time.sleep(0.05)

                widget = ".wg[data-widget='connection-status/rtt']"
                card = ".wgc[data-widget='connection-status/rtt']"

                def texts(selector):
                    return [node.get("text") or "" for node in ctl("tree", selector).get("nodes", [])]

                def command(name):
                    ctl("plugins", "run", "plugin.connection-status." + name)

                try:
                    wait_for(socket.exists)
                    ctl("size", "480", "850")
                    ctl("plugins", "fixtures")
                    ctl("widgets", "set", "connection-status/rtt@graph")
                    command("fixture-rtt")
                    wait_for(lambda: "95 ms" in str(ctl("tree", widget)))
                    self.assertEqual(ctl("tree", widget + "[data-tone='success']")["count"], 1)

                    # A local pane hides the widget, matching the standalone one.
                    command("fixture-rtt-local")
                    wait_for(lambda: ctl("tree", widget)["count"] == 0)
                    command("fixture-rtt")
                    wait_for(lambda: ctl("tree", widget)["count"] == 1)

                    # Card rows: Host / RTT / refresh hint + button.
                    ctl("hover", widget)
                    wait_for(lambda: ctl("tree", card)["count"] == 1)
                    self.assertEqual(ctl("tree", card + " .wgc-plugin-row")["count"], 3)
                    keys = texts(card + " .wgc-k")
                    values = texts(card + " .wgc-v")
                    buttons = texts(card + " .wgc-btn")
                    for expected in ("Host", "RTT"):
                        self.assertTrue(any(expected in value for value in keys), (expected, keys))
                    self.assertIn("fixture-remote", values)
                    self.assertIn("95 ms", values)
                    self.assertTrue(any("Tern RTT · 10 s refresh" in value for value in values), values)
                    self.assertEqual(buttons, ["Refresh"])
                    self.assertEqual(ctl("tree", card + " .wgc-plugin-row[data-tone='ok']")["count"], 1)

                    # History: the first sample is 95/300; forcing the 10 s
                    # interval adds a second one.
                    command("fixture-rtt-stats")
                    stats = json.loads((root / "stats.json").read_text())
                    self.assertEqual(stats["count"], 1)
                    self.assertAlmostEqual(stats["series"][0], 95 / 300, places=3)
                    command("fixture-rtt-sample")
                    command("fixture-rtt-stats")
                    stats = json.loads((root / "stats.json").read_text())
                    self.assertEqual(stats["count"], 2)

                    # The card's Refresh re-reads hosts and redraws in place;
                    # 250 ms is the warn band and 350 ms crosses into error.
                    command("fixture-rtt-bump")
                    ctl("click", card + " .wgc-plugin-row:nth-child(3) .wgc-btn")
                    wait_for(lambda: "250 ms" in str(ctl("tree", card)))
                    self.assertEqual(ctl("tree", widget + "[data-tone='warn']")["count"], 1)
                    self.assertEqual(ctl("tree", card + " .wgc-plugin-row[data-tone='warn']")["count"], 1)
                    command("fixture-rtt-bump-error")
                    ctl("click", card + " .wgc-plugin-row:nth-child(3) .wgc-btn")
                    wait_for(lambda: "350 ms" in str(ctl("tree", card)))
                    self.assertEqual(ctl("tree", widget + "[data-tone='error']")["count"], 1)
                    self.assertEqual(ctl("tree", card)["count"], 1)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    if os.environ.get("RTT_WIDGET_LOG"):
                        print(log_path.read_text())


if __name__ == "__main__":
    unittest.main()
