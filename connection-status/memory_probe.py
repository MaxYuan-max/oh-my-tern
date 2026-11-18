"""Read the selected host's RAM and top resident-memory processes without changing Tern panes."""
import argparse
import json
import heapq
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys

SSH = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=1"]


def host_config():
    """This file is also piped to `python3 -` over SSH, so it must stand alone.

    The remote side only receives a target of "local" or "container" and never
    needs this file; the local side reads the installation's hosts.json.
    """
    path = Path(os.environ.get("CONNECTION_STATUS_CONFIG") or
                Path.home() / "Library/Application Support/Tern/plugin-data/connection-status/hosts.json")
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


CONFIG = host_config()
# Names match TUNNEL_HOSTS in window.luau. Each entry names the SSH destination
# from the user's own SSH configuration and the memory accounting it uses:
# "container" reads cgroup-charged usage through the host helper, "linux" reads
# /proc/meminfo. Nothing is installed on the remote side.
TARGETS = {}
for _host in (CONFIG.get("hosts") or []):
    _memory = _host.get("memory") if isinstance(_host, dict) else None
    if isinstance(_memory, dict) and _memory.get("ssh") and _host.get("name"):
        TARGETS[str(_host["name"])] = (str(_memory["ssh"]), _memory.get("kind") or "linux")
CONTAINER_USAGE = os.path.expanduser(CONFIG.get("container_usage") or "~/bin/container-usage")


def memory(used, total, scope):
    if type(used) is not int or type(total) is not int or used < 0 or total <= 0:
        raise ValueError("Invalid memory counters")
    # A lowered cgroup limit can temporarily be below its charged usage.
    return {"used_bytes": used, "total_bytes": total, "ratio": used / total,
            "scope": scope, "pressure": "unknown"}


def component(identifier, label, value, included):
    return {"id": identifier, "label": label, "bytes": value, "included": included}


def container_memory(text, stat_text=None):
    """Use the helper's effective limit and charged usage, including cache."""
    reading = json.loads(text)
    if type(reading.get("cgroup_version")) is not int or reading["cgroup_version"] != 1:
        raise ValueError("Unsupported container-usage accounting version")
    sample = memory(reading["ram_used_bytes"], reading["ram_limit_bytes"], "container-cache")
    sample["platform"] = "linux"
    used, total = sample["used_bytes"], sample["total_bytes"]
    # This is remaining quota, not a claim about physically reclaimable memory.
    headroom = max(total - used, 0)
    sample["available_bytes"] = headroom

    counters = {}
    for line in (stat_text or "").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in ("total_rss", "total_cache", "total_swap") and fields[1].isdecimal():
            counters[fields[0]] = int(fields[1])
    parts = []
    for key, identifier, label in (("total_rss", "rss", "RSS"), ("total_cache", "cache", "Cache")):
        if key in counters:
            parts.append(component(identifier, label, counters[key], True))
    known = sum(part["bytes"] for part in parts)
    # The helper and memory.stat are separate snapshots; don't graph inconsistent totals.
    if parts and known <= used:
        parts.append(component("other", "Other", used - known, True))
    else:
        parts = [component("used", "Used", used, True)]
    sample["breakdown"] = parts + [component("headroom", "Headroom", headroom, False)]
    # The helper substitutes zero if total_swap is absent; only a present cgroup counter is evidence.
    if "total_swap" in counters:
        sample["swap_used_bytes"] = counters["total_swap"]
    # v1's memsw limit combines RAM and swap and is not a swap capacity.
    return sample


def linux_memory(text):
    counters = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(MemTotal|MemAvailable|AnonPages|SwapTotal|SwapFree):\s+(\d+)\s+kB\s*", line)
        if match:
            counters[match[1]] = int(match[2]) * 1024
    available = counters["MemAvailable"]
    sample = memory(counters["MemTotal"] - available, counters["MemTotal"], "system")
    sample["platform"] = "linux"
    sample["available_bytes"] = available
    used = sample["used_bytes"]
    anonymous = counters.get("AnonPages")
    if anonymous is not None and anonymous <= used:
        parts = [component("anonymous", "Anonymous", anonymous, True),
                 component("other", "Other", used - anonymous, True)]
    else:
        parts = [component("used", "Used", used, True)]
    # Linux's cache counters overlap its MemAvailable estimate and are not stackable here.
    sample["breakdown"] = parts + [component("available", "Available", available, False)]
    if "SwapTotal" in counters:
        sample["swap_total_bytes"] = counters["SwapTotal"]
        if "SwapFree" in counters and counters["SwapFree"] <= counters["SwapTotal"]:
            sample["swap_used_bytes"] = counters["SwapTotal"] - counters["SwapFree"]
    return sample


def linux_psi(text):
    """Return valid host PSI 10-second stall percentages without inventing a level."""
    result = {}
    seen = set()
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in ("some", "full"):
            continue
        kind = fields[0]
        key = "pressure_{}_avg10".format(kind)
        if kind in seen:
            result.pop(key, None)
            continue
        seen.add(kind)
        values = [field[6:] for field in fields[1:] if field.startswith("avg10=")]
        if len(values) != 1 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", values[0]):
            continue
        value = float(values[0])
        if value <= 100:
            result[key] = value
    return result


def macos_memory(total_text, vm_text):
    """Estimate used as app+wired+physical compressed; available is its complement."""
    page = re.search(r"page size of (\d+) bytes", vm_text)
    if not page or int(page[1]) <= 0:
        raise ValueError("vm_stat did not report its page size")
    page_size = int(page[1])
    counters = {key: int(value) for key, value in re.findall(
        r"^(Anonymous pages|Pages purgeable|Pages wired down|Pages occupied by compressor|File-backed pages):\s+(\d+)\.",
        vm_text, re.MULTILINE)}
    anonymous = counters["Anonymous pages"]
    purgeable = counters["Pages purgeable"]
    if purgeable > anonymous:
        raise ValueError("Inconsistent macOS purgeable pages")
    app = (anonymous - purgeable) * page_size
    wired = counters["Pages wired down"] * page_size
    compressed = counters["Pages occupied by compressor"] * page_size
    total = int(total_text.strip())
    used = app + wired + compressed
    if used > total:
        raise ValueError("Inconsistent macOS memory counters")
    sample = memory(used, total, "system")
    sample["platform"] = "darwin"
    available = total - used
    sample["available_bytes"] = available
    parts = [component("app", "App", app, True), component("wired", "Wired", wired, True),
             component("compressed", "Compressed", compressed, True)]
    if "File-backed pages" in counters:
        cached = (counters["File-backed pages"] + purgeable) * page_size
        # Cached is part of available, not another contribution to used or total.
        if cached <= available:
            parts.append(component("cached", "Cached", cached, False))
    sample["breakdown"] = parts
    return sample


def macos_pressure(text):
    # kern.memorystatus_vm_pressure_level uses Darwin's dispatch pressure flags.
    return {"1": "normal", "2": "warning", "4": "critical"}.get(text.strip(), "unknown")


def macos_swap(data):
    # sysctl -b vm.swapusage exposes xsw_usage in bytes, without display rounding.
    layout = "=QQQII"
    if len(data) != struct.calcsize(layout):
        raise ValueError("Invalid macOS swap counters")
    total, _available, used, _page_size, _encrypted = struct.unpack(layout, data)
    if used > total:
        raise ValueError("Invalid macOS swap counters")
    return {"swap_used_bytes": used, "swap_total_bytes": total}


def run(argv, source=None, binary=False):
    # The configured Linux hosts include Python 3.6; keep the streamed collector compatible.
    return subprocess.run(argv, input=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=not binary, check=True, timeout=7).stdout


def memory_cgroup(text):
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and "memory" in fields[1].split(",") and fields[2].startswith("/"):
            return fields[2].rstrip("/") or "/"
    raise ValueError("Memory cgroup is not readable")


def in_cgroup(path, parent):
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def container_stat(root=Path("/proc")):
    """Locate this process's cgroup-v1 memory.stat, including bind-mounted roots."""
    group = memory_cgroup((root / "self/cgroup").read_text())
    candidates = []
    for line in (root / "self/mountinfo").read_text().splitlines():
        before, separator, after = line.partition(" - ")
        left, right = before.split(), after.split()
        if not separator or len(left) < 5 or len(right) < 3 or right[0] != "cgroup":
            continue
        if "memory" not in right[2].split(","):
            continue
        unescape = lambda value: re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)
        mount_root = unescape(left[3]).rstrip("/") or "/"
        mount_point = unescape(left[4])
        if not mount_root.startswith("/") or not mount_point.startswith("/") or not in_cgroup(group, mount_root):
            continue
        relative = group[len(mount_root.rstrip("/")):].lstrip("/")
        candidates.append((len(mount_root), Path(mount_point) / relative / "memory.stat"))
    if not candidates:
        raise ValueError("Memory cgroup mount is not readable")
    error = None
    for _, path in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
        try:
            return path.read_text()
        except OSError as failure:
            error = failure
    raise error


def top_processes(rows):
    return heapq.nlargest(20, rows, key=lambda row: (row["rss_bytes"], -row["pid"])), len(rows)


def process_identity(row, macos):
    """Return a display-aggregation key, not proof of executable identity."""
    path = row.get("path", "")
    if not macos:
        # Top apps is a program-name view, not an executable-identity index.
        # In particular, pi and node can use the very same executable, while
        # /proc/<pid>/comm still distinguishes them. Keep exe (including a
        # " (deleted)" suffix) in the raw process record, not the app label.
        name = row.get("name", "")
        if name:
            return ("program", name), name
        return ("pid", row["pid"]), Path(path).name or str(row["pid"])
    if macos and path.startswith("/"):
        parts = Path(path).parts
        for index, part in enumerate(parts):
            if part.lower().endswith(".app") and len(part) > 4:
                bundle = str(Path(*parts[:index + 1]))
                return ("bundle", bundle), part[:-4]
        # CLI tools like opencode: the same program name may live in several
        # paths (app bundle, ~/.opencode/bin, …). Group by program name, not
        # by the literal executable path.
        name = Path(path).name
        if name:
            return ("program", name), name
    # `ps comm` without a path (e.g. `opencode` vs `/path/to/opencode`): group
    # by the bare program name so a main process and its children merge.
    if path:
        return ("program", Path(path).name), Path(path).name
    name = row.get("name", "")
    if name:
        return ("program", name), name
    # A process name alone cannot establish that two executables are the same.
    return ("pid", row["pid"]), Path(row["name"]).name or row["name"]


def grouped_processes(rows, macos=False):
    groups = {}
    for row in rows:
        key, name = process_identity(row, macos)
        if key not in groups:
            groups[key] = {"name": name, "rss_bytes": 0, "count": 0, "pid": row["pid"]}
        group = groups[key]
        group["rss_bytes"] += row["rss_bytes"]
        group["count"] += 1
        group["pid"] = min(group["pid"], row["pid"])
    names = {}
    for group in groups.values():
        names[group["name"]] = names.get(group["name"], 0) + 1
    apps = []
    for group in groups.values():
        pid = group.pop("pid")
        # Only disambiguate when two different identities share a display name.
        if names[group["name"]] > 1:
            group["name"] = "{} (PID {})".format(group["name"], pid)
        apps.append(group)
    # RSS can include shared pages in several processes; it is not a partition of used RAM.
    return sorted(apps, key=lambda group: (-group["rss_bytes"], group["name"]))[:5]


def summarize_processes(rows, macos=False):
    apps = grouped_processes(rows, macos)
    selected, count = top_processes(rows)
    return selected, count, apps


def linux_processes(root=Path("/proc"), container=False):
    group = memory_cgroup((root / "self/cgroup").read_text()) if container else None
    page_size = os.sysconf("SC_PAGE_SIZE")
    rows = []
    for entry in root.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if group is not None and not in_cgroup(memory_cgroup((entry / "cgroup").read_text()), group):
                continue
            rss = int((entry / "statm").read_text().split()[1]) * page_size
            name = (entry / "comm").read_text().strip()
            if not name or rss < 0:
                continue
            try:
                path = os.readlink(str(entry / "exe"))
            except OSError:
                path = ""
            rows.append({"pid": int(entry.name), "name": name, "rss_bytes": rss, "path": path})
        except (OSError, ValueError, IndexError):
            # A process may exit or be unreadable during the snapshot; never substitute host-wide data.
            continue
    return summarize_processes(rows)


def macos_processes(text):
    rows = []
    for line in text.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        try:
            pid, rss = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        executable = fields[2]
        if pid > 0 and rss >= 0:
            rows.append({"pid": pid, "name": Path(executable).name, "rss_bytes": rss * 1024,
                         "path": executable if executable.startswith("/") else ""})
    return summarize_processes(rows, macos=True)


def probe(target):
    if target in TARGETS:
        destination, kind = TARGETS[target]
        # One SSH request collects totals and processes on the same machine, with no remote installation.
        collector = "container" if kind == "container" else "local"
        command = "python3 - " + shlex.quote(collector)
        return json.loads(run(SSH + [destination, command], Path(__file__).read_text()))
    if target not in ("local", "container"):
        # A host that vanished from the config must not silently sample this
        # machine and report its memory under the other host's name.
        raise ValueError("主机配置中没有 " + target)
    if target == "container":
        # Do not substitute host MemTotal or a working-set estimate if this helper fails.
        usage = run([CONTAINER_USAGE, "--json", "--interval", "0.1"])
        try:
            stat = container_stat()
        except (OSError, ValueError):
            stat = None
        sample = container_memory(usage, stat)
    elif sys.platform == "darwin":
        sample = macos_memory(run(["/usr/sbin/sysctl", "-n", "hw.memsize"]), run(["/usr/bin/vm_stat"]))
        try:
            sample["pressure"] = macos_pressure(run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]))
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            sample.update(macos_swap(run(["/usr/sbin/sysctl", "-b", "vm.swapusage"], binary=True)))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    else:
        sample = linux_memory(Path("/proc/meminfo").read_text())
        try:
            sample.update(linux_psi(Path("/proc/pressure/memory").read_text()))
        except (OSError, UnicodeError):
            # PSI may be unavailable; host pressure categories remain unknown.
            pass
    try:
        if target != "container" and sys.platform == "darwin":
            rows, count, apps = macos_processes(run(["/bin/ps", "-ww", "-axo", "pid=,rss=,comm="]))
        else:
            rows, count, apps = linux_processes(container=target == "container")
        sample.update(processes=rows, process_count=count, apps=apps)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # A process-list failure does not invalidate independently measured RAM totals.
        sample["process_reason"] = str(error)[:300]
    return sample


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["local", "container", *TARGETS])
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.target)))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        reason = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        reason = (reason or "Memory sampling failed")[:300]
        print(json.dumps({"reason": reason}))
        print(reason, file=sys.stderr)
        sys.exit(1)
