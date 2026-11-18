"""Render the installed card in an isolated Tern, never in a user's window."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


SOURCE = Path(__file__).resolve().parents[1]
APP = Path("/Applications/Tern.app/Contents/MacOS/tern")
TERN = str(APP) if APP.is_file() else shutil.which("tern")
TEMP_ROOT = Path("/private/var/folders/ls/t88tf_1d0td1vmtfyc5jntt80000gn/T/opencode")


@unittest.skipUnless(sys.platform == "darwin" and TERN, "requires macOS Tern")
class MemorySummaryTests(unittest.TestCase):
    def test_mixed_size_summary_and_no_history(self):
        parent = TEMP_ROOT if TEMP_ROOT.is_dir() else None
        with TemporaryDirectory(prefix="memory-summary-test-", dir=parent) as directory:
            root = Path(directory)
            fixture = root / "crates/plugins/fixtures/connection-status"
            fixture.mkdir(parents=True)
            (root / "crates/tern/goldens").mkdir(parents=True)
            shutil.copyfile(SOURCE / "plugin.toml", fixture / "plugin.toml")
            gib = 1024 ** 3
            sample = {
                "used_bytes": int(18.2 * gib), "total_bytes": 24 * gib,
                "available_bytes": int(5.8 * gib), "scope": "system", "platform": "darwin", "pressure": "normal",
                "swap_used_bytes": int(4.4 * gib), "swap_total_bytes": 5 * gib,
                "process_count": 612, "processes": [],
                "breakdown": [
                    {"id": "app", "label": "App", "bytes": int(9.5 * gib), "included": True},
                    {"id": "wired", "label": "Wired", "bytes": int(3.7 * gib), "included": True},
                    {"id": "compressed", "label": "Compressed", "bytes": int(5.0 * gib), "included": True},
                    {"id": "cached", "label": "Cached files", "bytes": int(4.8 * gib), "included": False},
                ],
                "apps": [
                    # These match Tern's `widgets fixture` memory apps, so the
                    # same names/counts can be compared without live sampling.
                    {"name": "Google Chrome", "rss_bytes": int(4.6 * gib), "count": 31},
                    {"name": "Xcode", "rss_bytes": int(2.9 * gib), "count": 4},
                    {"name": "rust-analyzer", "rss_bytes": int(1.2 * gib), "count": 1},
                    {"name": "Tern", "rss_bytes": 860 * 1024 ** 2, "count": 3},
                    {"name": "Slack", "rss_bytes": 640 * 1024 ** 2, "count": 6},
                ],
            }
            (fixture / "sample.json").write_text(json.dumps(sample))
            # If a real sampler callback fires it still only reads this fixture;
            # no SSH, user settings, terminal input or real memory probes are used.
            (fixture / "memory_probe.py").write_text(
                "from pathlib import Path\nprint(Path(__file__).with_name('sample.json').read_text())\n"
            )
            command = '''
tern.command({id="fixture-summary",title="Summary fixture",run=function(cx)
    local sample = tern.json.decode(tern.fs.read("sample.json"))
    sample.checked = tern.now()
    sample.text = "76%%"
    sample.ratio = sample.used_bytes / sample.total_bytes
    sample.title, sample.css = memoryCard("local", sample)
    memoryReadings["local"] = sample
    updateHosts(cx)
    syncMemoryStyle(cx)
    tern.chrome.refresh()
    tern.fs.write(%s, tern.json.encode({title=sample.title, css=sample.css}))
end})
''' % json.dumps(str(root / "rendered.json"))
            remote_commands = r'''
local __fixture_host = nil
local __original_update_hosts = updateHosts
updateHosts = function(cx)
    __original_update_hosts(cx)
    if __fixture_host then paneHosts[cx.session:focused()] = __fixture_host end
end
local function __remote_summary(cx, key, address, container)
    local sample = tern.json.decode(tern.fs.read("sample.json"))
    sample.checked = tern.now()
    sample.pressure = "unknown"
    sample.platform = "linux"
    local gib = 1073741824
    if container then
        sample.used_bytes, sample.total_bytes, sample.available_bytes = 6*gib, 8*gib, 2*gib
        sample.scope = "container-cache"
        sample.swap_total_bytes = nil
        sample.breakdown = {
            {id="rss", label="RSS", bytes=2*gib, included=true},
            {id="cache", label="Cache", bytes=gib, included=true},
            {id="other", label="Other", bytes=3*gib, included=true},
            {id="headroom", label="Headroom", bytes=2*gib, included=false},
        }
    else
        sample.pressure_some_avg10, sample.pressure_full_avg10 = 0.12, 0
        sample.breakdown = {
            {id="anonymous", label="Anonymous", bytes=10*gib, included=true},
            {id="other", label="Other", bytes=sample.used_bytes-10*gib, included=true},
            {id="available", label="Available", bytes=sample.available_bytes, included=false},
        }
    end
    sample.process_count = if container then 109 else 351
    sample.processes = {
        {pid=3401179, name="node-MainThread", rss_bytes=432*1048576, path="/opt/node"},
        {pid=2439787, name="python", rss_bytes=362*1048576, path="/usr/bin/python"},
        {pid=3645398, name="pi", rss_bytes=361*1048576, path="/home/user/bin/pi"},
    }
    sample.ratio = sample.used_bytes / sample.total_bytes
    sample.text = string.format("%.0f%%", sample.ratio * 100)
    sample.title, sample.css = memoryCard(key, sample)
    memoryReadings[key] = sample
    __fixture_host = {kind="remote", state="connected", name=key, address=address}
    updateHosts(cx)
    syncMemoryStyle(cx)
    tern.chrome.refresh()
    tern.fs.write(__OUTPUT__ .. "/" .. key .. ".json", tern.json.encode({title=sample.title, css=sample.css}))
end
tern.command({id="fixture-linux",title="Linux summary fixture",run=function(cx)
    __remote_summary(cx, "linux-host", "user@linux-host.localhost:28377", false)
end})
tern.command({id="fixture-container",title="Container summary fixture",run=function(cx)
    __remote_summary(cx, "container-host", "user@container-host.localhost:18376", true)
end})
for _, name in {"expanded", "collapsed", "container-expanded", "container-collapsed"} do
    tern.command({id="fixture-capture-" .. name,title="Capture " .. name,run=function()
        local samples = {}
        for key, sample in memoryReadings do
            samples[key] = {title=sample.title, css=sample.css}
        end
        tern.fs.write(__OUTPUT__ .. "/" .. name .. ".json", tern.json.encode({pins=__pin_requests, samples=samples}))
    end})
end
'''.replace("__OUTPUT__", json.dumps(str(root)))
            prelude = r'''
local __native = tern
local __pin_requests = 0
local tern = setmetatable({process=setmetatable({run=function(argv, opts, callback)
    if argv[2] == "ctl" and argv[3] == "widgets" and argv[4] == "card" then
        __pin_requests += 1
        callback({status=0, stdout='{"ok":true}', stderr="", timed_out=false}, nil)
        return
    end
    return __native.process.run(argv, opts, callback)
end}, {__index=__native.process})}, {__index=__native})
'''
            window_source = (SOURCE / "window.luau").read_text().replace("--!strict", "--!strict\n" + prelude, 1)
            (fixture / "window.luau").write_text(window_source + command + remote_commands)
            scenario = root / "summary.txt"
            scenario.write_text(
                "plugins fixtures\n"
                "widgets fixture\n"
                "widgets set memory connection-status/memory@ring\n"
                "plugins run plugin.connection-status.fixture-summary\n"
                "widgets card memory\n"
                "shot native\n"
                "widgets card\n"
                "widgets card connection-status/memory\n"
                "shot plugin\n"
                "size 480 850\n"
                "ui-font-size 17\n"
                "widgets card memory\n"
                "shot native-zoom\n"
                "widgets card connection-status/memory\n"
                "shot zoom\n"
                "ui-font-size 13\n"
                "size 280 700\n"
                "shot narrow\n"
                "size 800 700\n"
                "widgets card\n"
                "plugins run plugin.connection-status.fixture-linux\n"
                "widgets card connection-status/memory\n"
                "shot linux\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "widgets card connection-status/memory\n"
                "plugins run plugin.connection-status.fixture-capture-expanded\n"
                "shot linux-expanded\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "widgets card connection-status/memory\n"
                "plugins run plugin.connection-status.fixture-capture-collapsed\n"
                "shot linux-collapsed\n"
                "widgets card\n"
                "plugins run plugin.connection-status.fixture-container\n"
                "widgets card connection-status/memory\n"
                "shot container\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "widgets card connection-status/memory\n"
                "plugins run plugin.connection-status.fixture-capture-container-expanded\n"
                "shot container-expanded\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "widgets card connection-status/memory\n"
                "plugins run plugin.connection-status.fixture-capture-container-collapsed\n"
                "shot container-collapsed\n"
            )
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("TERN_", "STENCIL_"))}
            (root / "hosts.json").write_text(json.dumps({"hosts": [
                {"name": "linux-host", "address": "user@linux-host.localhost:28377", "alias": "linux-host.localhost",
                 "port": "28377", "memory": {"ssh": "linux-host", "kind": "linux"}},
                {"name": "container-host", "address": "user@container-host.localhost:18376",
                 "alias": "container-host.localhost", "port": "18376",
                 "memory": {"ssh": "container-host", "kind": "container"}},
            ]}))
            env.update(TERN_CONFIG_DIR=str(root / "config"),
                       STENCIL_FIXTURE_ROOT=str(root / "crates/tern"),
                       STENCIL_LOG_DIR=str(root / "logs"),
                       CONNECTION_STATUS_CONFIG=str(root / "hosts.json"))
            result = subprocess.run(
                [TERN, "shot", str(scenario), "--out", str(root / "shots"),
                 "--size", "800x700", "--theme", "dark"],
                env=env, cwd=root, capture_output=True, text=True, timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = os.environ.get("MEMORY_SUMMARY_SHOTS")
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                for image in (root / "shots").rglob("*"):
                    if image.is_file():
                        shutil.copyfile(image, Path(output) / image.name)
            rendered = json.loads((root / "rendered.json").read_text())
            self.assertIn("18.2: GB of 24 GB", rendered["title"])
            self.assertIn("5.8 GB available", rendered["title"])
            self.assertIn("Swap used", rendered["title"])
            self.assertNotIn("本机 · 已用", rendered["title"])
            for text in ("Last ", "Peak ", "历史"):
                self.assertNotIn(text, rendered["title"] + rendered["css"])

            def layout(name):
                files = list((root / "shots").rglob(name + "-dark.layout.json"))
                self.assertEqual(len(files), 1, result.stdout)
                return json.loads(files[0].read_text())["elements"]

            def first(elements, suffix):
                matches = [e for e in elements if e.get("visible")
                           and e["path"].endswith(suffix)]
                self.assertTrue(matches, "missing element: " + suffix)
                return matches[0]

            def field(elements, row, kind):
                matches = [e for e in elements if e.get("visible")
                           and e["path"].endswith(">div.wgc-row>span.wgc-" + kind)
                           and re.search(r">div:nth-of-type\(" + str(row)
                                         + r"\)>span:nth-of-type\(\d+\)$", e["nth"])]
                self.assertEqual(len(matches), 1, (row, kind, matches))
                return matches[0]

            title_lines = rendered["title"].splitlines()

            def row_number(prefix):
                matches = [i + 1 for i, text in enumerate(title_lines) if text.startswith(prefix)]
                self.assertEqual(len(matches), 1, prefix)
                return matches[0]

            def row_box(elements, row):
                matches = [e for e in elements if e.get("visible")
                           and e["path"].endswith(">div.wgc-sec>div.wgc-row")
                           and re.search(r">div:nth-of-type\(" + str(row) + r"\)$", e["nth"])]
                self.assertEqual(len(matches), 1, row)
                return matches[0]

            native, plugin = layout("native"), layout("plugin")
            pressure_row = row_number("● ")
            used_row, available_row = row_number("18.2:"), row_number("5.8 GB available")
            field(plugin, pressure_row, "k")  # The pressure badge remains visible.
            large, small, available = field(plugin, used_row, "k"), field(plugin, used_row, "v"), field(plugin, available_row, "k")
            native_large = first(native, ">span.wgc-num")
            native_small = first(native, ">span.wgc-unit")
            for actual, expected in ((large, native_large), (small, native_small)):
                for prop in ("fontSize", "fontFamily", "fontWeight", "color"):
                    self.assertEqual(actual[prop], expected[prop], prop)
            self.assertGreater(float(large["fontSize"][:-2]), float(small["fontSize"][:-2]) * 1.8)
            self.assertAlmostEqual(large["rect"][0] + large["rect"][2] + 6,
                                   small["rect"][0], delta=1)
            self.assertAlmostEqual(small["rect"][1] + small["rect"][3],
                                   available["rect"][1] + available["rect"][3], delta=2)
            self.assertLessEqual(small["rect"][0] + small["rect"][2] + 4, available["rect"][0])

            def check_header_spacing(elements, reference):
                number = field(elements, used_row, "k")["rect"]
                bar = row_box(elements, row_number("内存分布"))["rect"]
                native_number = first(reference, ">span.wgc-num")["rect"]
                native_bar = first(reference, ">div.wgc-memory-bar")["rect"]
                self.assertAlmostEqual(bar[1] - number[1] - number[3],
                                       native_bar[1] - native_number[1] - native_number[3], delta=1)
                self.assertAlmostEqual(bar[3], native_bar[3], delta=1)
                header = first(elements, ">div.wgc-head")["rect"]
                pressure = row_box(elements, pressure_row)["rect"]
                # No empty subtitle row remains. Pressure is in the existing
                # title band rather than consuming space above the number.
                self.assertAlmostEqual(number[1], header[1] + header[3], delta=1)
                self.assertGreaterEqual(pressure[1], header[1])
                self.assertLessEqual(pressure[1] + pressure[3], header[1] + header[3])

            check_header_spacing(plugin, native)

            # Compare the app section to the built-in card, using its fixture's
            # exact names/counts. No live process changes can hide layout bugs.
            def native_apps(suffix):
                return [e for e in native if e.get("visible")
                        and "wgc-memory-apps>" in e["path"] and e["path"].endswith(suffix)]

            native_names = native_apps(">span.wgc-memory-name")
            native_gauges = native_apps(">span.wgc-memory-gauge")
            native_values = native_apps(">span.wgc-v")
            self.assertEqual(len(native_names), len(sample["apps"]))
            native_card, plugin_card = first(native, ".wgc"), first(plugin, ".wgc")
            native_right = native_card["rect"][0] + native_card["rect"][2]
            plugin_right = plugin_card["rect"][0] + plugin_card["rect"][2]

            def same_font(actual, expected):
                for prop in ("fontSize", "fontFamily", "fontWeight", "color"):
                    self.assertEqual(actual[prop], expected[prop], (prop, actual, expected))

            native_legend_labels = [e for e in native if e.get("visible")
                                    and e["path"].endswith(">span.wgc-memory-label")]
            native_legend_values = [e for e in native if e.get("visible")
                                    and e["path"].endswith(">span.wgc-memory-value")]
            for i, label in enumerate(("App:", "Wired:", "Compressed:", "Cached files:")):
                legend_row = row_number(label)
                legend_label, legend_value = field(plugin, legend_row, "k"), field(plugin, legend_row, "v")
                same_font(legend_label, native_legend_labels[i])
                same_font(legend_value, native_legend_values[i])
                self.assertAlmostEqual(plugin_right - legend_value["rect"][0] - legend_value["rect"][2],
                                       native_right - native_legend_values[i]["rect"][0]
                                       - native_legend_values[i]["rect"][2], delta=1)

            heading_row = row_number("Top apps:")
            heading, inventory = field(plugin, heading_row, "k"), field(plugin, heading_row, "v")
            same_font(heading, first(native, "div.wgc-sec.wgc-memory-apps>div.wgc-sh"))
            same_font(inventory, first(native, ">span.wgc-memory-procs"))
            self.assertAlmostEqual(plugin_right - inventory["rect"][0] - inventory["rect"][2], 12, delta=1)
            previous_name = None
            for i, app in enumerate(sample["apps"]):
                row = row_number(app["name"])
                name, value = field(plugin, row, "k"), field(plugin, row + 2, "k")
                same_font(name, native_names[i])
                same_font(value, native_values[i])
                if app["count"] > 1:
                    counter = field(plugin, row, "v")
                    same_font(counter, first(native, ">span.wgc-memory-count"))
                    self.assertAlmostEqual(counter["rect"][0] - name["rect"][0] - name["rect"][2], 6, delta=1)
                if previous_name:
                    self.assertAlmostEqual(name["rect"][1] - previous_name["rect"][1], 22, delta=1)
                else:
                    self.assertAlmostEqual(name["rect"][1] - heading["rect"][1], 18, delta=1)
                previous_name = name
                # Gauge and RSS have the same fixed right-side columns as the
                # built-in, independent of the longest application name.
                gauge_nth = re.sub(r">span:nth-of-type\(\d+\)$", "", value["nth"])
                gauge_nth = re.sub(r">div:nth-of-type\(\d+\)$", ">div:nth-of-type(" + str(row + 1) + ")", gauge_nth)
                gauge = next(e for e in plugin if e.get("visible") and e["nth"] == gauge_nth)
                for actual, expected in ((gauge, native_gauges[i]), (value, native_values[i])):
                    self.assertAlmostEqual(plugin_right - actual["rect"][0] - actual["rect"][2],
                                           native_right - expected["rect"][0] - expected["rect"][2], delta=1)
                self.assertAlmostEqual(gauge["rect"][2], 48, delta=1)
                self.assertAlmostEqual(gauge["rect"][1] + gauge["rect"][3] / 2,
                                       name["rect"][1] + name["rect"][3] / 2, delta=1)

            footer_row = row_number("Updated every")
            hint, task = field(plugin, footer_row, "k"), field(plugin, footer_row, "v")
            same_font(hint, first(native, ">span.wgc-hint"))
            same_font(task, first(native, ">button.wgc-btn"))
            self.assertAlmostEqual(hint["rect"][0] - plugin_card["rect"][0], 12, delta=1)
            self.assertAlmostEqual(plugin_right - task["rect"][0] - task["rect"][2], 14, delta=1)
            self.assertIn("Updated every 5 s: Task manager", rendered["title"])
            footer_nth = re.sub(r">span:nth-of-type\(\d+\)$", "", hint["nth"])
            footer_box = next(e for e in plugin if e.get("visible") and e["nth"] == footer_nth)
            self.assertAlmostEqual(footer_box["rect"][2], plugin_card["rect"][2], delta=1)
            self.assertAlmostEqual(footer_box["rect"][3], first(native, ">div.wgc-foot")["rect"][3], delta=1)
            self.assertAlmostEqual(footer_box["rect"][1] - previous_name["rect"][1] - previous_name["rect"][3], 8, delta=1)
            zoom = layout("zoom")
            native_zoom = layout("native-zoom")
            zoom_small, zoom_available = field(zoom, used_row, "v"), field(zoom, available_row, "k")
            self.assertAlmostEqual(zoom_small["rect"][1] + zoom_small["rect"][3],
                                   zoom_available["rect"][1] + zoom_available["rect"][3], delta=2)
            self.assertLessEqual(zoom_small["rect"][0] + zoom_small["rect"][2] + 4,
                                 zoom_available["rect"][0])
            check_header_spacing(zoom, native_zoom)
            zoom_names = [e for e in native_zoom if e.get("visible")
                          and e["path"].endswith(">span.wgc-memory-name")]
            previous = None
            for i, app in enumerate(sample["apps"]):
                actual = field(zoom, row_number(app["name"]), "k")
                same_font(actual, zoom_names[i])
                if previous:
                    self.assertAlmostEqual(actual["rect"][1] - previous["rect"][1],
                                           zoom_names[i]["rect"][1] - zoom_names[i - 1]["rect"][1], delta=1)
                previous = actual
            narrow = layout("narrow")
            narrow_large, narrow_available = field(narrow, used_row, "k"), field(narrow, available_row, "k")
            self.assertGreaterEqual(narrow_available["rect"][1],
                                    narrow_large["rect"][1] + narrow_large["rect"][3])

            for name, key, container in (("linux", "linux-host", False), ("container", "container-host", True)):
                remote = json.loads((root / (key + ".json")).read_text())
                self.assertNotIn("已用", remote["title"])
                self.assertIn(key + (" · 含缓存" if container else ""), remote["title"])
                self.assertIn("▸ 进程 · RSS:", remote["title"])
                self.assertNotIn("node-MainThread", remote["title"])
                self.assertNotIn("PID 3401179", remote["title"])
                self.assertTrue(remote["title"].splitlines()[-1].startswith("Updated every"))
                remote_lines = remote["title"].splitlines()

                def remote_row(prefix):
                    return next(i + 1 for i, text in enumerate(remote_lines) if text.startswith(prefix))

                elements = layout(name)
                pressure_prefix = "● 未知" if container else "PSI 0.12%"
                badge = row_box(elements, remote_row(pressure_prefix))["rect"]
                header = first(elements, ">div.wgc-head")["rect"]
                self.assertGreaterEqual(badge[1], header[1])
                self.assertLessEqual(badge[1] + badge[3], header[1] + header[3])
                number = field(elements, remote_row("6.0:" if container else "18.2:"), "k")
                same_font(number, native_large)
                bar = row_box(elements, remote_row("内存分布"))["rect"]
                native_bar = first(native, ">div.wgc-memory-bar")["rect"]
                self.assertAlmostEqual(bar[1] - number["rect"][1] - number["rect"][3], 10, delta=1)
                self.assertAlmostEqual(bar[3], native_bar[3], delta=1)
                if container:
                    self.assertIn("2.0 GB headroom", remote["title"])
                    self.assertNotIn("2.0 GB available", remote["title"])
                    self.assertTrue(any(text.startswith("RSS:") for text in remote_lines))
                    self.assertTrue(any(text.startswith("Cache:") for text in remote_lines))
                    self.assertTrue(any(text.startswith("Headroom:") for text in remote_lines))
                    free_row = remote_row("Headroom")
                    self.assertIn("var(--l1) 75.0000%", remote["css"])
                else:
                    self.assertTrue(any(text.startswith("Used:") for text in remote_lines))
                    self.assertTrue(any(text.startswith("Free:") for text in remote_lines))
                    self.assertFalse(any(text.startswith(("Anonymous:", "Other:")) for text in remote_lines))
                    used_entry = row_box(elements, remote_row("Used:"))["rect"]
                    free_entry = row_box(elements, remote_row("Free:"))["rect"]
                    self.assertAlmostEqual(used_entry[2], free_entry[2], delta=1)
                    self.assertAlmostEqual(free_entry[0] - used_entry[0] - used_entry[2], 16, delta=1)
                    free_row = remote_row("Free")
                    self.assertIn("var(--l1) 75.8333%", remote["css"])
                self.assertRegex(remote["css"], r":nth-child\(" + str(free_row)
                                 + r"\)::before \{[^}]*background: var\(--l1\);")

            expanded = json.loads((root / "expanded.json").read_text())
            collapsed = json.loads((root / "collapsed.json").read_text())
            self.assertEqual(expanded["pins"], 1)
            self.assertEqual(collapsed["pins"], 2)
            expanded_lines = expanded["samples"]["linux-host"]["title"].splitlines()
            self.assertTrue(any(text.startswith("▾ 进程 · RSS:") for text in expanded_lines))
            self.assertIn("node-MainThread", expanded_lines)
            self.assertIn("python", expanded_lines)
            self.assertIn("pi", expanded_lines)
            self.assertTrue(expanded_lines[-1].startswith("Updated every"))
            expanded_layout = layout("linux-expanded")
            last_pid = next(i + 1 for i, text in enumerate(expanded_lines) if text.startswith("PID 3645398"))
            last_number = row_box(expanded_layout, last_pid)["rect"]
            last_footer = row_box(expanded_layout, len(expanded_lines))["rect"]
            self.assertGreaterEqual(last_footer[1], last_number[1] + last_number[3])
            collapsed_title = collapsed["samples"]["linux-host"]["title"]
            self.assertIn("▸ 进程 · RSS:", collapsed_title)
            self.assertNotIn("node-MainThread", collapsed_title)
            self.assertNotIn("PID 3401179", collapsed_title)
            layout("linux-collapsed")
            container_expanded = json.loads((root / "container-expanded.json").read_text())
            container_collapsed = json.loads((root / "container-collapsed.json").read_text())
            self.assertEqual(container_expanded["pins"], 3)
            self.assertEqual(container_collapsed["pins"], 4)
            self.assertIn("node-MainThread", container_expanded["samples"]["container-host"]["title"])
            self.assertIn("▾ 进程 · RSS:", container_expanded["samples"]["container-host"]["title"])
            self.assertNotIn("node-MainThread", container_collapsed["samples"]["container-host"]["title"])
            self.assertIn("▸ 进程 · RSS:", container_collapsed["samples"]["container-host"]["title"])
            self.assertNotIn("node-MainThread", container_expanded["samples"]["linux-host"]["title"])
            layout("container-expanded")
            layout("container-collapsed")
            logs = result.stderr + "\n".join(f.read_text() for f in (root / "logs").glob("*.log"))
            for text in ("plugin handler failed", "plugin style sheet has errors", "exceeded its budget"):
                self.assertNotIn(text, logs)


if __name__ == "__main__":
    unittest.main()
