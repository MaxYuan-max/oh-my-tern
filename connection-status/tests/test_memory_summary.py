"""The memory card is a structured widget card, rendered in an isolated Tern.

Assertions use the card rows (ids/labels/texts/CSS) plus the shot's layout JSON,
so the contract is the card API rather than the old `title` character grid.
"""
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


@unittest.skipUnless(sys.platform == "darwin" and TERN, "requires macOS Tern")
class MemorySummaryTests(unittest.TestCase):
    def test_card_rows_match_the_native_memory_panel(self):
        with TemporaryDirectory(prefix="memory-summary-test-") as directory:
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
                    {"name": "Google Chrome", "rss_bytes": int(4.6 * gib), "count": 31},
                    {"name": "Xcode", "rss_bytes": int(2.9 * gib), "count": 4},
                    {"name": "rust-analyzer", "rss_bytes": int(1.2 * gib), "count": 1},
                    {"name": "Tern", "rss_bytes": 860 * 1024 ** 2, "count": 3},
                    {"name": "Slack", "rss_bytes": 640 * 1024 ** 2, "count": 6},
                ],
            }
            (fixture / "sample.json").write_text(json.dumps(sample))
            # A real sampler callback still only reads this fixture; no SSH,
            # user settings, terminal input or real memory probes are used.
            (fixture / "memory_probe.py").write_text(
                "from pathlib import Path\nprint(Path(__file__).with_name('sample.json').read_text())\n"
            )
            output = json.dumps(str(root))
            fixture_lua = r'''
local __fixture_host = nil
local __fixture_keys = {}
local __original_update_hosts = updateHosts
updateHosts = function(cx)
    __original_update_hosts(cx)
    if __fixture_host then paneHosts[cx.session:focused()] = __fixture_host end
end
-- Freeze fixture samples: a stray sampler tick must not replace the test data.
local __original_sample_memory = sampleMemory
sampleMemory = function(host)
    local key = memoryKey(host)
    if key and __fixture_keys[key] then return end
    __original_sample_memory(host)
end

local function __load()
    return tern.json.decode(tern.fs.read("sample.json"))
end

local function __write_sample(name, sample)
    tern.fs.write(__OUTPUT__ .. "/" .. name .. ".json",
        tern.json.encode({card = sample.card, css = sample.css, text = sample.text, ratio = sample.ratio}))
end

local function __write(key)
    local sample = memoryReadings[key]
    if not sample or not sample.card then return end
    local state = if memoryProcessesExpanded[key] then "expanded" else "collapsed"
    __write_sample(key .. "-" .. state, sample)
end

tern.command({id="fixture-write",title="Write fixture cards",run=function()
    for key in memoryReadings do __write(key) end
end})

tern.command({id="fixture-local",title="Local summary fixture",run=function(cx)
    local sample = __load()
    sample.processes = {}
    for i = 1, 25 do
        sample.processes[i] = {
            pid = 400000 + i,
            name = "worker-" .. tostring(i),
            rss_bytes = (400 - i * 10) * 1048576,
        }
    end
    sample.process_count = 612
    memoryReadings["local"] = memorySample("local", sample)
    __fixture_keys["local"] = true
    __fixture_host = nil
    updateHosts(cx)
    syncMemoryStyle(cx)
    tern.chrome.refresh()
    __write("local")
end})

local gib = 1073741824
local function __remote_summary(cx, key, address, container)
    local sample = __load()
    sample.pressure = "unknown"
    sample.platform = "linux"
    if container then
        sample.used_bytes, sample.total_bytes, sample.available_bytes = 6*gib, 8*gib, 2*gib
        sample.scope = "container-cache"
        sample.swap_total_bytes = nil
        sample.breakdown = {
            {id="rss", label="RSS", bytes=2*gib, included=true},
            {id="cache", label="Cache", bytes=3*gib, included=true},
            {id="other", label="Other", bytes=gib, included=true},
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
    if container then
        for i = 4, 20 do
            sample.processes[i] = {pid=400000 + i, name="worker-" .. tostring(i), rss_bytes=(21 - i)*1048576}
        end
        __write_sample("container-fallback", memorySample(key, sample))
        sample.working_set_bytes = 4 * gib
    end
    memoryReadings[key] = memorySample(key, sample)
    __fixture_keys[key] = true
    __fixture_host = {kind="remote", state="connected", name=key, address=address}
    updateHosts(cx)
    syncMemoryStyle(cx)
    tern.chrome.refresh()
    __write(key)
end
tern.command({id="fixture-linux",title="Linux summary fixture",run=function(cx)
    __remote_summary(cx, "linux-host", "user@linux-host.localhost:28377", false)
end})
tern.command({id="fixture-container",title="Container summary fixture",run=function(cx)
    __remote_summary(cx, "container-host", "user@container-host.localhost:18376", true)
end})
'''.replace("__OUTPUT__", output)
            prelude = r'''
local __native = tern
local __pin_requests = 0
local tern = setmetatable({process=setmetatable({run=function(argv, opts, callback)
    if argv[2] == "ctl" and argv[3] == "widgets" and argv[4] == "card" then
        __pin_requests += 1
        callback({status=0, stdout='{"ok":true}', stderr="", timed_out=false}, nil)
        return
    end
    -- Deterministic local sample: the real probe would race the injected fixture.
    if type(argv) == "table" and type(argv[2]) == "string"
        and string.match(argv[2], "memory_probe%.py$") and argv[3] == "local" then
        local answer = __native.json.decode(__native.fs.read(__native.plugin.dir .. "/sample.json"))
        answer.processes = {}
        for i = 1, 25 do
            answer.processes[i] = {
                pid = 400000 + i,
                name = "worker-" .. tostring(i),
                rss_bytes = (400 - i * 10) * 1048576,
            }
        end
        answer.process_count = 612
        callback({status=0, stdout=__native.json.encode(answer), stderr="", timed_out=false}, nil)
        return
    end
    return __native.process.run(argv, opts, callback)
end}, {__index=__native.process})}, {__index=__native})
'''
            window_source = (SOURCE / "window.luau").read_text().replace("--!strict", "--!strict\n" + prelude, 1)
            (fixture / "window.luau").write_text(window_source + fixture_lua)
            scenario = root / "summary.txt"
            scenario.write_text(
                "plugins fixtures\n"
                "widgets fixture\n"
                "widgets set memory\n"
                "widgets card memory\n"
                "shot native\n"
                "widgets card\n"
                "widgets set memory connection-status/memory@ring\n"
                "plugins run plugin.connection-status.fixture-local\n"
                "widgets card connection-status/memory\n"
                "shot local\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "plugins run plugin.connection-status.fixture-write\n"
                "shot local-expanded\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "widgets card\n"
                "plugins run plugin.connection-status.fixture-linux\n"
                "widgets card connection-status/memory\n"
                "shot linux\n"
                "plugins run plugin.connection-status.fixture-linux\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "plugins run plugin.connection-status.fixture-write\n"
                "shot linux-expanded\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "widgets card\n"
                "plugins run plugin.connection-status.fixture-container\n"
                "widgets card connection-status/memory\n"
                "shot container\n"
                "plugins run plugin.connection-status.fixture-container\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "plugins run plugin.connection-status.fixture-write\n"
                "shot container-expanded\n"
                "plugins run plugin.connection-status.memory-processes\n"
                "widgets card\n"
                "size 280 700\n"
                "plugins run plugin.connection-status.fixture-local\n"
                "widgets card connection-status/memory\n"
                "shot narrow\n"
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
                env=env, cwd=root, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            captured = os.environ.get("MEMORY_SUMMARY_SHOTS")
            if captured:
                Path(captured).mkdir(parents=True, exist_ok=True)
                for image in (root / "shots").rglob("*"):
                    if image.is_file():
                        shutil.copyfile(image, Path(captured) / image.name)

            def card(key, state):
                path = root / (key + "-" + state + ".json")
                self.assertTrue(path.is_file(), path)
                return json.loads(path.read_text())

            local = card("local", "collapsed")
            local_expanded = card("local", "expanded")
            linux = card("linux-host", "collapsed")
            linux_expanded = card("linux-host", "expanded")
            container = card("container-host", "collapsed")
            container_expanded = card("container-host", "expanded")

            def rows(document):
                return {row["id"]: row for row in document["card"]}

            local_rows = rows(local)
            self.assertEqual(local["text"], "76%")
            self.assertEqual(local_rows["summary"]["label"], "18.2")
            self.assertEqual(local_rows["summary"]["text"], "GB of 24 GB")
            self.assertEqual(local_rows["summary"]["actions"], [{"id": "noop", "label": "5.8 GB available"}])
            self.assertEqual([local_rows["legend-" + str(i)]["label"] for i in (1, 2, 3, 4)],
                             ["App", "Wired", "Compressed", "Cached files"])
            self.assertEqual(local_rows["swap"]["text"], "4.4 GB of 5.0 GB")
            self.assertNotIn("processes", local_rows)
            self.assertEqual(local["card"][-1]["id"], "footer")
            self.assertEqual(local["card"][-1]["actions"], [{"id": "task-manager", "label": "Task manager"}])
            self.assertNotIn("PID", json.dumps(local["card"]))
            self.assertNotIn("本机", json.dumps(local["card"], ensure_ascii=False))

            # Expansion spends the 32-row budget on 18 one-line processes; Top
            # apps is replaced, and the footer remains the last row.
            self.assertEqual(len(local_expanded["card"]), 32, [row["id"] for row in local_expanded["card"]])
            expanded_rows = [row["id"] for row in local_expanded["card"]]
            self.assertNotIn("apps", expanded_rows)
            self.assertNotIn("app-1", expanded_rows)
            self.assertEqual(expanded_rows[-1], "footer")
            self.assertEqual(expanded_rows[-2], "more")
            self.assertEqual(expanded_rows[-3], "process-18")
            self.assertEqual(expanded_rows.count("process-" + str(18)), 1)
            self.assertFalse(any(name.startswith("process-19") for name in expanded_rows))
            self.assertEqual(rows(local_expanded)["more"]["label"], "还有 594 个进程")
            self.assertEqual(rows(local_expanded)["processes"]["actions"], [{"id": "toggle-processes", "label": "收起"}])
            self.assertEqual(rows(local_expanded)["process-18"]["label"], "worker-18")

            self.assertIn('content: " ×31"', local["css"])
            self.assertIn("width: 48px", local["css"])
            self.assertIn("right: 56px", local["css"])
            self.assertIn("data:image/svg+xml;base64,", local_expanded["css"])
            self.assertIn("var(--l1) 75.8333%", linux["css"])
            self.assertIn("var(--l1) 75.0000%", container["css"])
            for document in (local, local_expanded, linux, linux_expanded, container, container_expanded):
                text = json.dumps(document, ensure_ascii=False)
                for history in ("Last ", "Peak ", "历史", "wgc-memory-spark"):
                    self.assertNotIn(history, text)

            # Remote cards carry the host subtitle and a folded process heading.
            linux_rows = rows(linux)
            self.assertEqual(linux["text"], "76%")
            self.assertNotIn("working-set", linux_rows)
            self.assertEqual(linux_rows["subtitle"]["label"], "linux-host")
            self.assertEqual([linux_rows["legend-" + str(i)]["label"] for i in (1, 2)],
                             ["Used", "Free"])
            self.assertEqual(linux_rows["legend-3"]["label"], "")
            self.assertEqual(linux_rows["processes"]["text"], "3 / 351")
            self.assertEqual(linux_rows["processes"]["actions"], [{"id": "toggle-processes", "label": "展开"}])
            self.assertNotIn("node-MainThread", json.dumps(linux["card"]))
            self.assertEqual(linux["card"][-1]["id"], "footer")

            linux_expanded_rows = rows(linux_expanded)
            self.assertNotIn("apps", linux_expanded_rows)
            self.assertEqual(linux_expanded_rows["process-1"]["label"], "node-MainThread")
            self.assertEqual(linux_expanded_rows["process-1"]["text"], "PID 3401179 · 432 MB")
            self.assertEqual(linux_expanded_rows["more"]["label"], "还有 348 个进程")
            self.assertEqual(linux_expanded["card"][-1]["id"], "footer")

            container_rows = rows(container)
            self.assertEqual(container_rows["subtitle"]["label"], "container-host · 含缓存")
            self.assertEqual(container_rows["summary"]["label"], "6.0")
            self.assertEqual(container_rows["summary"]["text"], "GB of 8 GB")
            self.assertEqual(container_rows["summary"]["actions"], [{"id": "noop", "label": "2.0 GB headroom"}])
            self.assertEqual([container_rows["legend-" + str(i)]["label"] for i in (1, 2, 3, 4)],
                             ["RSS", "Cache", "Other", "Headroom"])
            self.assertEqual(container["text"], "50%")
            self.assertEqual(container["ratio"], 0.5)
            self.assertEqual(container_rows["working-set"], {
                "id": "working-set", "label": "工作集（估算）", "text": "4.0 GB · 4.0 GB 可用",
            })
            self.assertEqual(len(container_expanded["card"]), 32)
            self.assertEqual([row["id"] for row in container_expanded["card"][-3:]],
                             ["process-18", "more", "footer"])
            self.assertEqual(rows(container_expanded)["more"]["label"], "还有 91 个进程")

            container_fallback = json.loads((root / "container-fallback.json").read_text())
            self.assertEqual(container_fallback["text"], "75%")
            self.assertEqual(container_fallback["ratio"], 0.75)
            self.assertEqual(rows(container_fallback)["working-set"]["text"], "—")
            self.assertEqual(rows(container_fallback)["summary"], container_rows["summary"])

            def layout(name):
                files = list((root / "shots").rglob(name + "-dark.layout.json"))
                self.assertEqual(len(files), 1, result.stdout)
                return json.loads(files[0].read_text())["elements"]

            def visible(elements):
                return [e for e in elements if e.get("visible")]

            def first(elements, suffix):
                matches = [e for e in elements if e.get("visible") and e["path"].endswith(suffix)]
                self.assertTrue(matches, "missing element: " + suffix)
                return matches[0]

            def index_of(document, row_id):
                ids = [row["id"] for row in document["card"]]
                self.assertIn(row_id, ids)
                return ids.index(row_id) + 1

            def row_box(elements, index):
                matches = [e for e in elements if e.get("visible")
                           and "wgc-plugin-row" in e["path"]
                           and re.search(r">div:nth-of-type\(" + str(index) + r"\)$", e["nth"])]
                self.assertEqual(len(matches), 1, (index, [m["nth"] for m in matches]))
                return matches[0]

            def cell(elements, index, kind):
                suffix = "span.wgc-k" if kind == "k" else "span.wgc-v.wgc-plugin-v"
                matches = [e for e in elements if e.get("visible") and e["path"].endswith(suffix)
                           and re.search(r">div:nth-of-type\(" + str(index)
                                         + r"\)>span:nth-of-type\(\d+\)$", e["nth"])]
                self.assertEqual(len(matches), 1, (index, kind, [m["nth"] for m in matches]))
                return matches[0]

            def button(elements, index):
                matches = [e for e in elements if e.get("visible")
                           and e["path"].endswith("button.wgc-btn.wgc-plugin-act")
                           and re.search(r">div:nth-of-type\(" + str(index)
                                         + r"\)>button:nth-of-type\(\d+\)$", e["nth"])]
                self.assertEqual(len(matches), 1, (index, [m["nth"] for m in matches]))
                return matches[0]

            def card_rect(elements):
                return first(elements, "div.kit-popover.wgc")["rect"]

            def same_font(actual, expected):
                for prop in ("fontSize", "fontFamily", "fontWeight", "color"):
                    self.assertEqual(actual[prop], expected[prop], (prop, actual, expected))

            native = layout("native")
            local_shot = layout("local")
            plugin_card = card_rect(local_shot)
            self.assertAlmostEqual(plugin_card[2], 320, delta=1)
            header = first(local_shot, ">div.wgc-head")["rect"]
            self.assertAlmostEqual(header[3], 38, delta=1)

            def local_row(row_id):
                return row_box(local_shot, index_of(local, row_id))

            def local_cell(row_id, kind):
                return cell(local_shot, index_of(local, row_id), kind)

            pressure = local_row("pressure")["rect"]
            self.assertGreaterEqual(pressure[1], header[1])
            self.assertLessEqual(pressure[1] + pressure[3], header[1] + header[3])

            large = local_cell("summary", "k")
            small = local_cell("summary", "v")
            available = button(local_shot, index_of(local, "summary"))
            same_font(large, first(native, ">span.wgc-num"))
            same_font(small, first(native, ">span.wgc-unit"))
            same_font(available, first(native, ">span.wgc-cap"))
            self.assertGreater(float(large["fontSize"][:-2]), float(small["fontSize"][:-2]) * 1.8)
            self.assertAlmostEqual(large["rect"][0] + large["rect"][2] + 6, small["rect"][0], delta=1)
            self.assertAlmostEqual(small["rect"][1] + small["rect"][3],
                                   available["rect"][1] + available["rect"][3], delta=2)
            self.assertAlmostEqual(plugin_card[0] + plugin_card[2] - available["rect"][0] - available["rect"][2],
                                   12, delta=1)

            summary = local_row("summary")["rect"]
            bar = local_row("bar")["rect"]
            self.assertAlmostEqual(bar[1] - summary[1] - summary[3], 10, delta=1)
            native_bar = first(native, ">div.wgc-memory-bar")["rect"]
            self.assertAlmostEqual(bar[3], native_bar[3], delta=1)

            # Legends use the native two-column split with the value at the
            # column edge, not a gap after the label.
            native_labels = [e for e in native if e.get("visible")
                             and e["path"].endswith(">span.wgc-memory-label")]
            native_values = [e for e in native if e.get("visible")
                             and e["path"].endswith(">span.wgc-memory-value")]
            for index in range(4):
                same_font(local_cell("legend-" + str(index + 1), "k"), native_labels[index])
                same_font(local_cell("legend-" + str(index + 1), "v"), native_values[index])
            left_value = local_cell("legend-1", "v")["rect"]
            right_value = local_cell("legend-2", "v")["rect"]
            self.assertAlmostEqual(plugin_card[0] + plugin_card[2] - left_value[0] - left_value[2], 168, delta=1)
            self.assertAlmostEqual(plugin_card[0] + plugin_card[2] - right_value[0] - right_value[2], 12, delta=1)

            # App rows: 22px high, 22px apart, RSS at the card's right edge.
            native_names = [e for e in native if e.get("visible")
                            and e["path"].endswith(">span.wgc-memory-name")]
            native_app_values = [e for e in native if e.get("visible")
                                 and "wgc-memory-apps>" in e["path"] and e["path"].endswith(">span.wgc-v")]
            previous = None
            for index in range(5):
                row_id = "app-" + str(index + 1)
                name_node = local_cell(row_id, "k")
                value_node = local_cell(row_id, "v")
                name_row = local_row(row_id)["rect"]
                self.assertAlmostEqual(name_row[3], 22, delta=1)
                same_font(name_node, native_names[index])
                same_font(value_node, native_app_values[index])
                self.assertAlmostEqual(plugin_card[0] + plugin_card[2] - value_node["rect"][0] - value_node["rect"][2],
                                       12, delta=1)
                if previous:
                    self.assertAlmostEqual(name_row[1] - previous[1], 22, delta=1)
                previous = name_row

            # Footer: full-width 40px band, hint at 12px, button at 14px.
            hint = local_cell("footer", "k")
            task = button(local_shot, index_of(local, "footer"))
            footer = local_row("footer")["rect"]
            same_font(hint, first(native, ">span.wgc-hint"))
            same_font(task, first(native, ">button.wgc-btn"))
            self.assertAlmostEqual(footer[2], plugin_card[2], delta=1)
            self.assertAlmostEqual(footer[3], 40, delta=1)
            self.assertAlmostEqual(task["rect"][0] + task["rect"][2],
                                   plugin_card[0] + plugin_card[2] - 14, delta=1)
            self.assertGreaterEqual(footer[1], previous[1] + previous[3])

            # Remote cards keep the badge inside the 38px header.
            for name, document in (("linux", linux), ("container", container)):
                elements = layout(name)
                header = first(elements, ">div.wgc-head")["rect"]
                badge = row_box(elements, index_of(document, "pressure"))["rect"]
                self.assertGreaterEqual(badge[1], header[1])
                self.assertLessEqual(badge[1] + badge[3], header[1] + header[3])

            working_set = row_box(layout("container"), index_of(container, "working-set"))["rect"]
            self.assertAlmostEqual(working_set[3], 22, delta=1)

            # Expanded remote cards are one line per process, and the footer
            # follows the list (and the "more" row when one is present).
            linux_expanded_shot = layout("linux-expanded")
            process_index = index_of(linux_expanded, "process-1")
            first_process_row = row_box(linux_expanded_shot, process_index)["rect"]
            self.assertAlmostEqual(first_process_row[3], 22, delta=1)
            process_value = cell(linux_expanded_shot, process_index, "v")
            self.assertAlmostEqual(process_value["rect"][1] + process_value["rect"][3],
                                   first_process_row[1] + first_process_row[3], delta=1)
            more = row_box(linux_expanded_shot, index_of(linux_expanded, "more"))["rect"]
            footer = row_box(linux_expanded_shot, index_of(linux_expanded, "footer"))["rect"]
            self.assertGreaterEqual(footer[1], more[1] + more[3])

            # A 280pt window narrows the popover instead of overflowing it, and
            # the available figure drops below the large number.
            narrow = layout("narrow")
            narrow_card = card_rect(narrow)
            self.assertLessEqual(narrow_card[2], 257)
            summary_index = index_of(local, "summary")
            narrow_large = cell(narrow, summary_index, "k")
            narrow_available = button(narrow, summary_index)
            self.assertGreaterEqual(narrow_available["rect"][1],
                                    narrow_large["rect"][1] + narrow_large["rect"][3])

            logs = result.stderr + "\n".join(f.read_text() for f in (root / "logs").glob("*.log"))
            for text in ("plugin handler failed", "plugin style sheet has errors", "exceeded its budget",
                         "the card row", "dropped card row"):
                self.assertNotIn(text, logs)


if __name__ == "__main__":
    unittest.main()
