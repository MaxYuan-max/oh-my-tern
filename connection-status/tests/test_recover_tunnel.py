import importlib.util
from pathlib import Path
import unittest

import hosts_fixture  # noqa: F401 — writes the generic hosts.json and sets its env vars

spec = importlib.util.spec_from_file_location("recover_tunnel", Path(__file__).resolve().parents[1] / "recover_tunnel.py")
recover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recover)

RAW = recover.TUNNELS["build-a"]
MAXY = recover.TUNNELS["linux-b"]
PYTHON = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"


def client(*arguments):
    return " ".join([PYTHON, "-u", str(recover.SCRIPT), "client", *arguments])


class OwnTunnelTests(unittest.TestCase):
    def test_config_builds_one_tunnel_per_entry(self):
        self.assertEqual(RAW, recover.Tunnel("build-a", 18376, "127.0.0.1", "a-tunnel", "user@build-a.localhost:18376"))
        self.assertEqual(MAXY, recover.Tunnel("build-a", 28377, "10.0.0.9", "b-tunnel", "user@linux-b.localhost:28377"))

    def test_a_tunnel_started_without_target_host_reaches_loopback(self):
        self.assertTrue(recover.is_ours(client("--host", "build-a", "--port", "18376", "--spares", "3", "--keepalive", "5"), RAW))

    def test_the_second_tunnel_is_told_apart_by_its_target_host(self):
        other = client("--host", "build-a", "--port", "28377", "--target-host", "10.0.0.9", "--spares", "3")
        self.assertTrue(recover.is_ours(other, MAXY))
        self.assertFalse(recover.is_ours(other.replace("10.0.0.9", "10.0.0.10"), MAXY))
        self.assertFalse(recover.is_ours(client("--host", "build-a", "--port", "28377"), MAXY))

    def test_nothing_else_is_taken_for_the_tunnel(self):
        ours = client("--host", "build-a", "--port", "18376")
        for other in (
            ours.replace("18376", "183760"),
            ours.replace("--host build-a", "--host build-a2"),
            ours.replace(" client ", " remote "),
            ours.replace(str(recover.SCRIPT), "/tmp/tunnel.py"),
            "/usr/bin/ssh -N -L 18376:127.0.0.1:8376 build-a",
        ):
            self.assertFalse(recover.is_ours(other, RAW), other)


class HolderTests(unittest.TestCase):
    def test_only_sockets_bound_to_the_port_hold_it(self):
        listing = "\n".join([
            "p2494", "f9", "n127.0.0.1:62486->127.0.0.1:18376",  # Tern's own client socket, connected to the tunnel
            "p31954", "f3", "n127.0.0.1:18376", "f4", "n[::1]:18376",
            "p777", "f5", "n*:18376",
            "p888", "f6", "n127.0.0.1:18377",
        ])
        self.assertEqual(recover.parse_holders(listing, 18376), {31954, 777})

    def test_a_port_nobody_uses_has_no_holder(self):
        self.assertEqual(recover.parse_holders("", 18376), set())


if __name__ == "__main__":
    unittest.main()
