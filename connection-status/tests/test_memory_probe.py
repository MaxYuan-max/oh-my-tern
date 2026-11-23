import importlib.util
import json
import struct
import subprocess
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import hosts_fixture  # noqa: F401 — writes the generic hosts.json and sets its env vars

spec = importlib.util.spec_from_file_location("memory_probe", Path(__file__).resolve().parents[1] / "memory_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
GiB = 1024 ** 3
MiB = 1024 ** 2


def components(sample):
    return {item["id"]: (item["bytes"], item["included"]) for item in sample["breakdown"]}


def vm_stats(anonymous, purgeable, wired, compressed, file_backed, page_size=16384):
    return ("Mach Virtual Memory Statistics: (page size of {} bytes)\n"
            "Pages active: 900000.\nPages inactive: 800000.\n"
            "Anonymous pages: {}.\nPages purgeable: {}.\nPages wired down: {}.\n"
            "Pages occupied by compressor: {}.\nPages stored in compressor: 999999.\n"
            "File-backed pages: {}.\n").format(page_size, anonymous, purgeable, wired, compressed, file_backed)


class MemoryAccountingTests(unittest.TestCase):
    def test_container_uses_charged_ram_and_scoped_hierarchical_components(self):
        sample = probe.container_memory(json.dumps({
            "cgroup_version": 1, "ram_used_bytes": 6 * GiB, "ram_limit_bytes": 8 * GiB,
            "cache_bytes": 2 * GiB, "swap_used_bytes": 77,
        }), "total_rss {}\ntotal_cache {}\ntotal_inactive_file {}\ntotal_swap 123\nhierarchical_memsw_limit {}\n".format(
            3 * GiB, 2 * GiB, GiB, 40 * GiB))
        self.assertEqual(sample["used_bytes"], 6 * GiB)
        self.assertEqual(sample["working_set_bytes"], 5 * GiB)
        self.assertEqual(sample["total_bytes"], 8 * GiB)
        self.assertEqual(sample["available_bytes"], 2 * GiB)
        self.assertEqual(sample["ratio"], 0.75)
        self.assertEqual(sample["scope"], "container-cache")
        self.assertEqual(sample["platform"], "linux")
        self.assertEqual(sample["pressure"], "unknown")
        self.assertEqual(components(sample), {
            "rss": (3 * GiB, True), "cache": (2 * GiB, True),
            "other": (GiB, True), "headroom": (2 * GiB, False),
        })
        self.assertEqual(sample["swap_used_bytes"], 123)
        self.assertNotIn("swap_total_bytes", sample)

    def test_missing_hierarchical_counters_are_unknown_not_helper_zero(self):
        text = json.dumps({"cgroup_version": 1, "ram_used_bytes": 6 * GiB, "ram_limit_bytes": 8 * GiB,
                           "cache_bytes": 2 * GiB, "swap_used_bytes": 0})
        for stat in (None, "rss 5\ncache 10\nswap 0\nhierarchical_memsw_limit 123\n",
                     "total_rss -1\ntotal_cache -1\ntotal_swap -1\n"):
            with self.subTest(stat=stat):
                sample = probe.container_memory(text, stat)
                self.assertEqual(components(sample), {"used": (6 * GiB, True), "headroom": (2 * GiB, False)})
                self.assertNotIn("working_set_bytes", sample)
                self.assertNotIn("swap_used_bytes", sample)
                self.assertNotIn("swap_total_bytes", sample)

    def test_working_set_uses_only_a_valid_hierarchical_inactive_file_counter(self):
        text = json.dumps({"cgroup_version": 1, "ram_used_bytes": 6 * GiB, "ram_limit_bytes": 8 * GiB})
        for stat in (f"inactive_file {GiB}\ntotal_cache {2 * GiB}\n", "total_inactive_file -1\n",
                     "total_inactive_file unknown\n", f"total_inactive_file {6 * GiB}\n",
                     f"total_inactive_file {7 * GiB}\n"):
            with self.subTest(stat=stat):
                sample = probe.container_memory(text, stat)
                self.assertNotIn("working_set_bytes", sample)
                self.assertEqual(sample["used_bytes"], 6 * GiB)
                self.assertEqual(sample["available_bytes"], 2 * GiB)
        self.assertEqual(probe.container_memory(text, "total_inactive_file 0\n")["working_set_bytes"], 6 * GiB)

    def test_inconsistent_container_snapshots_do_not_invent_a_negative_remainder(self):
        text = json.dumps({"cgroup_version": 1, "ram_used_bytes": 6 * GiB, "ram_limit_bytes": 8 * GiB})
        sample = probe.container_memory(text, "total_rss {}\ntotal_cache {}\ntotal_swap 0\n".format(5 * GiB, 2 * GiB))
        self.assertEqual(components(sample), {"used": (6 * GiB, True), "headroom": (2 * GiB, False)})
        self.assertEqual(sample["swap_used_bytes"], 0)

    def test_host_memory_uses_available_and_keeps_cache_out_of_stacked_categories(self):
        sample = probe.linux_memory(
            "MemTotal: 8388608 kB\nMemFree: 524288 kB\nMemAvailable: 7340032 kB\n"
            "AnonPages: 524288 kB\nCached: 4194304 kB\nBuffers: 1000 kB\n"
            "SwapTotal: 2097152 kB\nSwapFree: 786432 kB\n")
        self.assertEqual(sample["used_bytes"], GiB)
        self.assertEqual(sample["total_bytes"], 8 * GiB)
        self.assertEqual(sample["available_bytes"], 7 * GiB)
        self.assertEqual(sample["ratio"], 0.125)
        self.assertEqual(sample["platform"], "linux")
        self.assertEqual(sample["pressure"], "unknown")
        self.assertEqual(components(sample), {
            "anonymous": (512 * MiB, True), "other": (512 * MiB, True), "available": (7 * GiB, False),
        })
        self.assertEqual(sample["swap_total_bytes"], 2 * GiB)
        self.assertEqual(sample["swap_used_bytes"], 1280 * MiB)

    def test_missing_or_inconsistent_host_optional_counters_stay_unknown(self):
        sample = probe.linux_memory("MemTotal: 8 kB\nMemAvailable: 6 kB\n")
        self.assertEqual(components(sample), {"used": (2 * 1024, True), "available": (6 * 1024, False)})
        self.assertNotIn("swap_total_bytes", sample)
        self.assertNotIn("swap_used_bytes", sample)
        sample = probe.linux_memory("MemTotal: 8 kB\nMemAvailable: 6 kB\nAnonPages: 5 kB\n"
                                    "SwapTotal: 2 kB\nSwapFree: 3 kB\n")
        self.assertEqual(components(sample), {"used": (2 * 1024, True), "available": (6 * 1024, False)})
        self.assertEqual(sample["swap_total_bytes"], 2 * 1024)
        self.assertNotIn("swap_used_bytes", sample)
        with self.assertRaises(KeyError):
            probe.linux_memory("MemTotal: 8 kB\nMemFree: 2 kB\n")
        with self.assertRaises(ValueError):
            probe.linux_memory("MemTotal: 8 kB\nMemAvailable: 9 kB\n")

    def test_invalid_or_missing_limits_cannot_become_host_totals(self):
        for limit in (0, -1, True, "8589934592"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                probe.container_memory(json.dumps({"cgroup_version": 1, "ram_used_bytes": GiB, "ram_limit_bytes": limit}))
        with self.assertRaises(KeyError):
            probe.container_memory('{"cgroup_version":1,"ram_used_bytes":1024}')
        for version in (2, True):
            with self.subTest(version=version), self.assertRaises(ValueError):
                probe.container_memory(json.dumps({"cgroup_version": version, "ram_used_bytes": 1024,
                                                   "ram_limit_bytes": 8192}))

    def test_limit_reduction_keeps_real_over_limit_usage(self):
        sample = probe.container_memory(json.dumps({"cgroup_version": 1, "ram_used_bytes": 6 * GiB,
                                                    "ram_limit_bytes": 4 * GiB}))
        self.assertEqual(sample["used_bytes"], 6 * GiB)
        self.assertEqual(sample["ratio"], 1.5)
        self.assertEqual(sample["available_bytes"], 0)
        self.assertEqual(components(sample)["headroom"], (0, False))

    def test_macos_uses_physical_compressor_pages_and_excludes_reclaimable(self):
        sample = probe.macos_memory(str(8 * GiB), vm_stats(65536, 8192, 16384, 32768, 65536))
        self.assertEqual(sample["used_bytes"], 1664 * MiB)
        self.assertEqual(sample["available_bytes"], 8 * GiB - 1664 * MiB)
        self.assertEqual(sample["platform"], "darwin")
        self.assertEqual(components(sample), {
            "app": (896 * MiB, True), "wired": (256 * MiB, True),
            "compressed": (512 * MiB, True), "cached": (1152 * MiB, False),
        })
        self.assertEqual(sample["pressure"], "unknown")
        self.assertNotIn("swap_used_bytes", sample)

    def test_macos_rejects_missing_or_inconsistent_required_counters(self):
        text = vm_stats(5, 1, 2, 3, 1, page_size=4096)
        with self.assertRaises(KeyError):
            probe.macos_memory(str(GiB), text.replace("Pages occupied by compressor: 3.\n", ""))
        with self.assertRaises(ValueError):
            probe.macos_memory(str(GiB), vm_stats(1, 2, 1, 1, 1))
        with self.assertRaises(ValueError):
            probe.macos_memory(str(4096), text)
        with self.assertRaises(ValueError):
            probe.macos_memory(str(GiB), text.replace("page size of 4096", "page size of 0"))
        sample = probe.macos_memory(str(10 * 4096), vm_stats(5, 1, 2, 3, 10, page_size=4096))
        self.assertNotIn("cached", components(sample))
        sample = probe.macos_memory(str(GiB), text.replace("File-backed pages: 1.\n", ""))
        self.assertNotIn("cached", components(sample))

    def test_macos_pressure_is_kernel_level_not_usage_threshold(self):
        for raw, expected in (("1\n", "normal"), ("2", "warning"), ("4", "critical"),
                              ("0", "unknown"), ("3", "unknown"), ("100", "unknown"), ("", "unknown")):
            with self.subTest(raw=raw):
                self.assertEqual(probe.macos_pressure(raw), expected)

    def test_macos_swap_uses_exact_xsw_usage_bytes(self):
        data = struct.pack("=QQQII", 8 * GiB, 5 * GiB, 3 * GiB, 16384, 1)
        self.assertEqual(probe.macos_swap(data), {"swap_total_bytes": 8 * GiB, "swap_used_bytes": 3 * GiB})
        with self.assertRaises(ValueError):
            probe.macos_swap(data[:-1])
        with self.assertRaises(ValueError):
            probe.macos_swap(struct.pack("=QQQII", 1, 0, 2, 4096, 1))


class LinuxPressureTests(unittest.TestCase):
    def test_valid_psi_avg10_values_are_percentages(self):
        text = ("some avg10=0.00 avg60=1.00 avg300=2.00 total=123\n"
                "full avg10=99.75 avg60=3.00 avg300=4.00 total=456\n")
        self.assertEqual(probe.linux_psi(text), {"pressure_some_avg10": 0.0, "pressure_full_avg10": 99.75})
        self.assertEqual(probe.linux_psi("full avg10=0.00\nsome avg10=100.00\n"),
                         {"pressure_some_avg10": 100.0, "pressure_full_avg10": 0.0})

    def test_unavailable_psi_fields_are_omitted(self):
        self.assertEqual(probe.linux_psi(""), {})
        self.assertEqual(probe.linux_psi("some avg60=4.00\nfull total=99\n"), {})
        self.assertEqual(probe.linux_psi("some avg10=2.50\n"), {"pressure_some_avg10": 2.5})

    def test_malformed_and_out_of_range_psi_are_not_coerced(self):
        for value in ("NaN", "inf", "-0.01", "100.01", "+1", "1e1", "3.0.0", "", "1,2"):
            with self.subTest(value=value):
                parsed = probe.linux_psi("some avg10={} avg60=0.00\nfull avg10=1.50\n".format(value))
                self.assertEqual(parsed, {"pressure_full_avg10": 1.5})
        for text in ("some avg10=1.0 avg10=2.0\n", "some avg10=1.0\nsome avg10=2.0\n"):
            with self.subTest(text=text):
                self.assertEqual(probe.linux_psi(text), {})


class ProcessMemoryTests(unittest.TestCase):
    def test_macos_keeps_executable_paths_with_spaces(self):
        rows, count, apps = probe.macos_processes(
            "  81  2048 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
            "  93  8192 /usr/local/bin/node\n")
        self.assertEqual([(row["pid"], row["rss_bytes"]) for row in rows], [(93, 8 * MiB), (81, 2 * MiB)])
        self.assertEqual(rows[1]["name"], "Google Chrome")
        self.assertEqual(rows[1]["path"], "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        self.assertEqual(count, 2)
        self.assertEqual([app["name"] for app in apps], ["node", "Google Chrome"])

    def test_macos_groups_helpers_by_the_outermost_app_bundle(self):
        rows, count, apps = probe.macos_processes(
            "1 100 /Applications/Orb.app/Contents/MacOS/Orb\n"
            "2 200 /Applications/Orb.app/Contents/Helpers/Orb Helper.app/Contents/MacOS/Orb Helper\n"
            "3 50 /Applications/Other.app/Contents/MacOS/Other\n")
        self.assertEqual(count, 3)
        self.assertEqual(apps[0], {"name": "Orb", "rss_bytes": 300 * 1024, "count": 2})
        self.assertEqual(rows[0]["path"],
                         "/Applications/Orb.app/Contents/Helpers/Orb Helper.app/Contents/MacOS/Orb Helper")

    def test_same_named_macos_bundles_are_distinct_without_path_display_names(self):
        rows, count, apps = probe.macos_processes(
            "20 100 /Applications/Orb.app/Contents/MacOS/Orb\n"
            "5 90 /Applications/Orb.app/Contents/Helpers/Orb Helper.app/Contents/MacOS/Helper\n"
            "10 80 /Users/example/Applications/Orb.app/Contents/MacOS/Orb\n")
        self.assertEqual(count, 3)
        self.assertEqual(apps, [
            {"name": "Orb (PID 5)", "rss_bytes": 190 * 1024, "count": 2},
            {"name": "Orb (PID 10)", "rss_bytes": 80 * 1024, "count": 1},
        ])
        self.assertEqual(rows[2]["path"], "/Users/example/Applications/Orb.app/Contents/MacOS/Orb")

    def test_macos_groups_the_full_inventory_before_selecting_raw_top_twenty(self):
        large = ["{} 100 /opt/workers/job{}".format(pid, pid) for pid in range(1, 26)]
        helpers = ["{} 30 /Applications/Orb.app/Contents/Helpers/Helper{}.app/Contents/MacOS/Helper".format(
            pid, pid) for pid in range(101, 109)]
        rows, count, apps = probe.macos_processes("\n".join(large + helpers))
        self.assertEqual(count, 33)
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(row["pid"] <= 25 for row in rows))
        self.assertEqual(apps[0], {"name": "Orb", "rss_bytes": 240 * 1024, "count": 8})

    def test_top_processes_sort_by_bytes_then_pid_before_limiting(self):
        rows, count = probe.top_processes([
            {"pid": pid, "name": "node", "rss_bytes": (pid // 2) * 1024}
            for pid in range(1, 26)
        ])
        self.assertEqual([row["pid"] for row in rows], [24, 25, 22, 23, 20, 21, 18, 19, 16, 17,
                                                       14, 15, 12, 13, 10, 11, 8, 9, 6, 7])
        self.assertEqual(count, 25)

    def test_container_processes_include_children_not_neighboring_cgroups(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "self").mkdir()
            (root / "self/cgroup").write_text("6:memory:/docker/a\n")
            for pid, group, pages in [(11, "/docker/a", 3), (12, "/docker/a/child", 5),
                                      (13, "/docker/ab", 100), (14, "/docker/other", 200)]:
                process = root / str(pid)
                process.mkdir()
                (process / "cgroup").write_text("6:memory:" + group + "\n")
                (process / "statm").write_text("1000 " + str(pages) + " 1\n")
                (process / "comm").write_text("node\n")
                (process / "exe").symlink_to("/opt/a directory/node")
            (root / "15").mkdir()
            (root / "15/cgroup").write_text("6:memory:/docker/a\n")
            with patch.object(probe.os, "sysconf", return_value=16384):
                rows, count, apps = probe.linux_processes(root, container=True)
        self.assertEqual([(row["pid"], row["rss_bytes"]) for row in rows], [(12, 81920), (11, 49152)])
        self.assertEqual(rows[0]["path"], "/opt/a directory/node")
        self.assertEqual(count, 2)
        self.assertEqual(apps, [{"name": "node", "rss_bytes": 131072, "count": 2}])

    def test_linux_top_apps_group_by_reported_program_name_and_keep_raw_paths(self):
        rows = [
            {"pid": 2, "name": "node", "path": "/opt/a/node", "rss_bytes": 20},
            {"pid": 1, "name": "node", "path": "/opt/a/node", "rss_bytes": 10},
            {"pid": 3, "name": "node", "path": "/opt/b/node", "rss_bytes": 25},
            {"pid": 4, "name": "node", "path": "", "rss_bytes": 5},
            {"pid": 5, "name": "node", "path": "", "rss_bytes": 4},
        ]
        selected, count, apps = probe.summarize_processes(rows)
        self.assertEqual(apps, [{"name": "node", "rss_bytes": 64, "count": 5}])
        self.assertEqual(count, 5)
        self.assertEqual({row["pid"]: row["path"] for row in selected},
                         {row["pid"]: row["path"] for row in rows})

    def test_linux_reported_names_distinguish_pi_from_its_runtime(self):
        rows = [
            {"pid": 1, "name": "pi", "path": "/opt/bin/node", "rss_bytes": 300},
            {"pid": 2, "name": "pi", "path": "/opt/bin/node", "rss_bytes": 200},
            {"pid": 3, "name": "node", "path": "/opt/bin/node", "rss_bytes": 100},
            {"pid": 4, "name": "python3", "path": "/usr/bin/python3.12 (deleted)", "rss_bytes": 250},
            {"pid": 5, "name": "python", "path": "/usr/bin/python3.12 (deleted)", "rss_bytes": 200},
            {"pid": 6, "name": "python3", "path": "/usr/bin/python3.12", "rss_bytes": 100},
        ]
        selected, count, apps = probe.summarize_processes(rows)
        self.assertEqual(count, 6)
        self.assertEqual({app["name"]: (app["rss_bytes"], app["count"]) for app in apps}, {
            "pi": (500, 2), "node": (100, 1), "python3": (350, 2), "python": (200, 1),
        })
        self.assertIn("/usr/bin/python3.12 (deleted)", [row["path"] for row in selected])

    def test_linux_reads_group_identity_for_processes_below_raw_top_twenty(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [(pid, 100, "/opt/high/job{}".format(pid)) for pid in range(1, 22)]
            entries += [(pid, 25, "/opt/shared/worker") for pid in range(100, 106)]
            for pid, pages, executable in entries:
                process = root / str(pid)
                process.mkdir()
                (process / "statm").write_text("1000 {} 1\n".format(pages))
                (process / "comm").write_text(Path(executable).name + "\n")
                (process / "exe").symlink_to(executable)
            with patch.object(probe.os, "sysconf", return_value=4096):
                rows, count, apps = probe.linux_processes(root)
        self.assertEqual(count, 27)
        self.assertTrue(all(row["pid"] < 100 for row in rows))
        self.assertEqual(apps[0], {"name": "worker", "rss_bytes": 150 * 4096, "count": 6})

    def test_missing_container_scope_never_becomes_a_host_inventory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "self").mkdir()
            (root / "self/cgroup").write_text("5:cpu:/docker/a\n")
            with self.assertRaises(ValueError):
                probe.linux_processes(root, container=True)


class ProbeScopeTests(unittest.TestCase):
    def test_container_stat_uses_most_specific_bound_mount_and_decodes_spaces(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            (proc / "self").mkdir(parents=True)
            (proc / "self/cgroup").write_text("6:memory:/docker/a/child\n")
            bound = root / "container memory"
            broad = root / "broader"
            (bound / "child").mkdir(parents=True)
            (broad / "docker/a/child").mkdir(parents=True)
            (bound / "child/memory.stat").write_text("total_rss 123\n")
            (broad / "docker/a/child/memory.stat").write_text("total_rss 999\n")
            mount = str(bound).replace(" ", "\\040")
            (proc / "self/mountinfo").write_text(
                "11 10 0:2 / {} ro - cgroup cgroup rw,memory\n".format(broad) +
                "12 10 0:3 /docker/a {} ro - cgroup cgroup rw,memory\n".format(mount))
            self.assertEqual(probe.container_stat(proc), "total_rss 123\n")

    def test_container_stat_does_not_fall_back_to_a_neighbor_or_host(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "self").mkdir()
            (root / "self/cgroup").write_text("6:memory:/docker/a\n")
            (root / "self/mountinfo").write_text("12 10 0:3 /docker/ab /sys/fs/cgroup/memory ro - cgroup cgroup rw,memory\n")
            with self.assertRaises(ValueError):
                probe.container_stat(root)

    def test_container_probe_never_substitutes_host_swap_or_processes(self):
        usage = json.dumps({"cgroup_version": 1, "ram_used_bytes": 6 * GiB, "ram_limit_bytes": 8 * GiB,
                            "cache_bytes": GiB, "swap_used_bytes": 0})
        with patch.object(probe, "run", return_value=usage), \
             patch.object(probe, "container_stat", side_effect=OSError("unreadable")), \
             patch.object(probe, "linux_processes", return_value=([], 0, [])) as processes:
            sample = probe.probe("container")
        processes.assert_called_once_with(container=True)
        self.assertEqual(sample["scope"], "container-cache")
        self.assertEqual(sample["pressure"], "unknown")
        self.assertNotIn("swap_used_bytes", sample)
        self.assertNotIn("swap_total_bytes", sample)
        self.assertEqual(sample["apps"], [])

    def test_linux_host_reads_optional_psi_without_assigning_a_category(self):
        meminfo = "MemTotal: 8 kB\nMemAvailable: 6 kB\n"
        psi = "some avg10=12.34 avg60=5.00\nfull avg10=0.01 avg60=0.00\n"
        for psi_text in (psi, None):
            def read_file(path, *args, **kwargs):
                if str(path) == "/proc/meminfo":
                    return meminfo
                if str(path) == "/proc/pressure/memory":
                    if psi_text is None:
                        raise OSError("PSI unavailable")
                    return psi_text
                raise AssertionError(str(path))
            with self.subTest(available=psi_text is not None), \
                 patch.object(probe.sys, "platform", "linux"), \
                 patch.object(probe.Path, "read_text", autospec=True, side_effect=read_file), \
                 patch.object(probe, "linux_processes", return_value=([], 0, [])):
                sample = probe.probe("local")
            self.assertEqual(sample["pressure"], "unknown")
            if psi_text is None:
                self.assertNotIn("pressure_some_avg10", sample)
                self.assertNotIn("pressure_full_avg10", sample)
            else:
                self.assertEqual(sample["pressure_some_avg10"], 12.34)
                self.assertEqual(sample["pressure_full_avg10"], 0.01)

    def test_container_never_reads_or_reports_host_psi(self):
        usage = json.dumps({"cgroup_version": 1, "ram_used_bytes": GiB, "ram_limit_bytes": 2 * GiB})
        with patch.object(probe, "run", return_value=usage), \
             patch.object(probe, "container_stat", return_value="total_rss 5\ntotal_cache 2\ntotal_swap 0\n"), \
             patch.object(probe, "linux_processes", return_value=([], 0, [])), \
             patch.object(probe.Path, "read_text", autospec=True, side_effect=AssertionError("host file accessed")):
            sample = probe.probe("container")
        self.assertEqual(sample["pressure"], "unknown")
        self.assertNotIn("pressure_some_avg10", sample)
        self.assertNotIn("pressure_full_avg10", sample)

    def test_process_failure_does_not_discard_container_memory(self):
        usage = json.dumps({"cgroup_version": 1, "ram_used_bytes": GiB, "ram_limit_bytes": 2 * GiB})
        with patch.object(probe, "run", return_value=usage), \
             patch.object(probe, "container_stat", return_value="total_swap 17\n"), \
             patch.object(probe, "linux_processes", side_effect=ValueError("missing scope")):
            sample = probe.probe("container")
        self.assertEqual(sample["used_bytes"], GiB)
        self.assertEqual(sample["swap_used_bytes"], 17)
        self.assertIn("missing scope", sample["process_reason"])
        self.assertNotIn("processes", sample)
        self.assertNotIn("apps", sample)

    def test_macos_probe_uses_real_pressure_even_at_high_utilization(self):
        def fake_run(argv, source=None, binary=False):
            if argv[-1] == "hw.memsize":
                return str(10 * 4096)
            if argv == ["/usr/bin/vm_stat"]:
                return vm_stats(6, 0, 2, 1, 1, page_size=4096)
            if argv[-1] == "kern.memorystatus_vm_pressure_level":
                return "1\n"
            if argv[-1] == "vm.swapusage":
                self.assertTrue(binary)
                return struct.pack("=QQQII", 8, 5, 3, 4096, 1)
            if argv[0] == "/bin/ps":
                return "1 1 /Applications/Orb.app/Contents/MacOS/Orb\n"
            raise AssertionError(argv)
        with patch.object(probe.sys, "platform", "darwin"), patch.object(probe, "run", side_effect=fake_run):
            sample = probe.probe("local")
        self.assertEqual(sample["ratio"], 0.9)
        self.assertEqual(sample["pressure"], "normal")
        self.assertEqual(sample["swap_used_bytes"], 3)
        self.assertEqual(sample["swap_total_bytes"], 8)
        self.assertEqual(sample["apps"][0]["name"], "Orb")

    def test_macos_optional_failures_do_not_invalidate_memory(self):
        def fake_run(argv, source=None, binary=False):
            if argv[-1] == "hw.memsize":
                return str(10 * 4096)
            if argv == ["/usr/bin/vm_stat"]:
                return vm_stats(6, 0, 2, 1, 1, page_size=4096)
            if argv[-1] == "kern.memorystatus_vm_pressure_level":
                raise subprocess.CalledProcessError(1, argv)
            if argv[-1] == "vm.swapusage":
                return b"invalid"
            if argv[0] == "/bin/ps":
                return "1 1 /sbin/launchd\n"
            raise AssertionError(argv)
        with patch.object(probe.sys, "platform", "darwin"), patch.object(probe, "run", side_effect=fake_run):
            sample = probe.probe("local")
        self.assertEqual(sample["pressure"], "unknown")
        self.assertNotIn("swap_used_bytes", sample)
        self.assertEqual(sample["used_bytes"], 9 * 4096)

    def test_remote_targets_keep_existing_host_key_checks_and_scope(self):
        targets = (("build-a", "build-a", "container"),
                   ("linux-b", "ssh://user@linux-b.example:2222", "local"))
        for target, destination, collector in targets:
            with self.subTest(target=target), patch.object(probe, "run", return_value='{"scope":"sample"}') as run:
                self.assertEqual(probe.probe(target), {"scope": "sample"})
                argv, source = run.call_args[0]
                self.assertEqual(argv[-2:], [destination, "python3 - " + collector])
                self.assertIn("StrictHostKeyChecking=yes", argv)
                self.assertIn("def probe(target):", source)

    def test_a_host_missing_from_the_config_never_samples_this_machine(self):
        # Sampling locally under a remote host's name would report the wrong RAM with confidence.
        with patch.object(probe, "run") as run:
            with self.assertRaises(ValueError):
                probe.probe("removed-host")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
