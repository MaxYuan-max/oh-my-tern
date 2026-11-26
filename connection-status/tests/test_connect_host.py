"""Connection picker recovery for failed versus active host attempts."""
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
class ConnectHostTests(unittest.TestCase):
    def test_failed_host_is_dialed_again_without_interrupting_active_attempts(self):
        with TemporaryDirectory(prefix="tern-connect-test-") as directory:
            root = Path(directory)
            (root / "crates/tern/goldens").mkdir(parents=True)
            fixture = root / "crates/plugins/fixtures/connection-status"
            fixture.mkdir(parents=True)
            shutil.copyfile(SOURCE / "plugin.toml", fixture / "plugin.toml")
            for helper in ("memory_probe.py", "path_probe.py", "recover_tunnel.py"):
                shutil.copyfile(SOURCE / helper, fixture / helper)
            (root / "hosts.json").write_text(json.dumps({"hosts": []}))
            output = root / "result.json"
            window_source = (SOURCE / "window.luau").read_text()
            self.assertTrue(window_source.startswith("--!strict"))
            window_source += '''
local fixtureOutput = __OUTPUT__
local fixtureTarget: any = {
    name = "direct-a",
    address = "user@direct-a.example:7709",
    alias = "192.0.2.9",
    port = "7709",
    endpoint = "",
    detail = "UDP 7709 直连（跨 VPN）",
}
local fixtureHosts: { any } = {}
local fixtureOps: { string } = {}
dial = function(_cx: any, _target: any, address: string, _replace: { any })
    table.insert(fixtureOps, "dial:" .. address)
end
local fixtureCx: any = {
    hosts = {
        list = function(_self)
            return fixtureHosts
        end,
        switch = function(_self, id)
            table.insert(fixtureOps, "switch:" .. tostring(id))
        end,
        disconnect = function(_self, id)
            table.insert(fixtureOps, "disconnect:" .. tostring(id))
        end,
    },
    toast = function(_self, level, text, sub)
        table.insert(fixtureOps, "toast:" .. level .. ":" .. text)
    end,
}
local function runAttempt(state: string): string
    fixtureHosts = {{
        id = 41,
        kind = "remote",
        state = state,
        name = "direct-a.example",
        address = fixtureTarget.address,
        detail = { error = "fixture connection failure" },
        sessions = {},
        panes = {},
    }}
    fixtureOps = {}
    connectTarget(fixtureCx, fixtureTarget, true)
    return table.concat(fixtureOps, ",")
end
tern.command({id="fixture-connect-recovery",title="Connection recovery fixture",run=function()
    tern.fs.write(fixtureOutput, tern.json.encode({
        failed = runAttempt("failed"),
        reconnecting = runAttempt("reconnecting"),
        connecting = runAttempt("connecting"),
    }))
end})
'''.replace("__OUTPUT__", json.dumps(str(output)))
            (fixture / "window.luau").write_text(window_source)
            socket = root / "control.sock"
            environment = {k: v for k, v in os.environ.items() if not k.startswith("TERN_")}
            environment.update(
                TERN_CONFIG_DIR=str(root / "config"),
                TERN_DAEMON_SOCKET=str(root / "daemon.sock"),
                STENCIL_FIXTURE_ROOT=str(root / "crates/tern"),
                STENCIL_LOG_DIR=str(root / "logs"),
                CONNECTION_STATUS_CONFIG=str(root / "hosts.json"),
            )
            log_path = root / "serve.log"
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    [TERN, "serve", "--control", str(socket), "--out", str(root / "shots")],
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )

                def ctl(*arguments):
                    result = subprocess.run(
                        [TERN, "ctl", "--control", str(socket), *arguments],
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=12,
                        check=True,
                    )
                    answer = json.loads(result.stdout)
                    self.assertTrue(answer.get("ok"), answer)
                    return answer

                try:
                    deadline = time.monotonic() + 15
                    while not socket.exists():
                        self.assertIsNone(process.poll(), log_path.read_text())
                        if time.monotonic() >= deadline:
                            self.fail("Tern control socket did not arrive\n" + log_path.read_text())
                        time.sleep(0.05)
                    ctl("plugins", "fixtures")
                    ctl("plugins", "run", "plugin.connection-status.fixture-connect-recovery")
                    result = json.loads(output.read_text())
                    address = "user@direct-a.example:7709"
                    self.assertEqual(result["failed"], "disconnect:41,dial:" + address)
                    self.assertEqual(result["reconnecting"], "switch:41")
                    self.assertEqual(result["connecting"], "switch:41")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    if os.environ.get("CONNECT_HOST_LOG"):
                        print(log_path.read_text())


if __name__ == "__main__":
    unittest.main()
