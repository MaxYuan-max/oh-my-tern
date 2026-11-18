import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("path_probe", Path(__file__).resolve().parents[1] / "path_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

RAW = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MAXY = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PID = 39821


def line(thread, message, pid=PID, level="INFO"):
    return "2026-11-06T13:30:48.042+08:00 %d  %s %25s stencil_tern: %s\n" % (pid, level, thread, message)


def connected(thread, way, verb="connected"):
    return line(thread, "remote client %s relay=warm way=%s answered_ms=470 logged_in_ms=946" % (verb, way))


def relayed(endpoint):
    return "through the relay https://my.omp.sh/ to endpoint " + endpoint


def feed(labels, *lines, pid=PID):
    for text in lines:
        probe.consume(labels, text, pid)
    return labels


class ConnectionWayTests(unittest.TestCase):
    def test_loopback_entries_are_tunnels_and_other_udp_is_direct(self):
        labels = feed({},
                      connected("tern-remote-raw.localhost", "over UDP to 127.0.0.1:18376"),
                      connected("tern-remote-linux-b.localhost", "over UDP to [::1]:28377"),
                      connected("tern-remote-10.0.0.9", "over UDP to 10.0.0.9:8376"))
        self.assertEqual((labels["raw.localhost"]["mode"], labels["raw.localhost"]["port"]), ("loopback", "18376"))
        self.assertEqual((labels["linux-b.localhost"]["mode"], labels["linux-b.localhost"]["port"]), ("loopback", "28377"))
        self.assertEqual(labels["10.0.0.9"]["mode"], "direct")

    def test_relay_migration_follows_only_its_endpoint(self):
        labels = feed({},
                      connected("tern-remote-bbbbbbbbbb…", relayed(MAXY), "reconnected"),
                      connected("tern-remote-aaaaaaaaaa…", relayed(RAW), "reconnected"),
                      connected("tern-remote-raw.localhost", "over UDP to 127.0.0.1:18376"),
                      line("tern-relay", "remote connection now direct host=%s addr=[::ffff:10.0.0.9]:8376 "
                                         "rtt_ms=Some(213)" % MAXY))
        self.assertEqual((labels["bbbbbbbbbb…"]["mode"], labels["bbbbbbbbbb…"]["addr"]),
                         ("direct", "[::ffff:10.0.0.9]:8376"))
        self.assertEqual(labels["aaaaaaaaaa…"]["mode"], "relay")
        self.assertEqual(labels["raw.localhost"]["mode"], "loopback")
        feed(labels, line("tern-relay", "remote connection back on the relay host=" + MAXY))
        self.assertEqual(labels["bbbbbbbbbb…"]["mode"], "relay")
        self.assertNotIn("addr", labels["bbbbbbbbbb…"])

    def test_lost_connection_forgets_way_until_reconnected(self):
        thread = "tern-remote-aaaaaaaaaa…"
        labels = feed({}, connected(thread, "over UDP to 127.0.0.1:18376"),
                      line(thread, "remote connection lost", level="WARN"))
        self.assertNotIn("aaaaaaaaaa…", labels)
        feed(labels, connected(thread, relayed(RAW), "reconnected"),
             line(thread, 'remote session ended why="the client left" stats=rtt=467ms'))
        self.assertNotIn("aaaaaaaaaa…", labels)
        feed(labels, connected(thread, relayed(RAW), "reconnected"))
        self.assertEqual(labels["aaaaaaaaaa…"]["mode"], "relay")

    def test_other_tern_processes_do_not_override(self):
        labels = feed({}, connected("tern-remote-aaaaaaaaaa…", relayed(RAW)),
                      connected("tern-remote-aaaaaaaaaa…", "over UDP to 127.0.0.1:18376"), pid=PID)
        feed(labels, line("tern-remote-aaaaaaaaaa…", "remote connection lost", pid=1234))
        self.assertEqual(labels["aaaaaaaaaa…"]["mode"], "loopback")


class IncrementalReadTests(unittest.TestCase):
    def test_partial_line_waits_and_process_change_rereads(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "tern.log")
            with open(log, "w", encoding="utf-8") as stream:
                stream.write(connected("tern-remote-aaaaaaaaaa…", relayed(RAW)))
            answer = probe.probe({}, log=log, pid=PID)
            self.assertEqual(answer["labels"]["aaaaaaaaaa…"]["mode"], "relay")
            transition = connected("tern-remote-aaaaaaaaaa…", "over UDP to 127.0.0.1:18376", "reconnected")
            with open(log, "a", encoding="utf-8") as stream:
                stream.write(transition[:40])
            answer = probe.probe(answer, log=log, pid=PID)
            self.assertEqual(answer["labels"]["aaaaaaaaaa…"]["mode"], "relay")
            with open(log, "a", encoding="utf-8") as stream:
                stream.write(transition[40:])
            answer = probe.probe(answer, log=log, pid=PID)
            self.assertEqual(answer["labels"]["aaaaaaaaaa…"]["mode"], "loopback")
            # A restarted Tern logs under a new PID: the old process's ways no longer apply.
            self.assertEqual(probe.probe(answer, log=log, pid=PID + 1)["labels"], {})


if __name__ == "__main__":
    unittest.main()
