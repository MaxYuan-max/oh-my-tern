"""Report each remote host's connection way from this Tern app's own log, without changing it."""
import ipaddress
import json
import os
import re
import sys

LOG = os.path.expanduser("~/Library/Logs/Tern/tern.log")
LINE = re.compile(r"^(\S+)\s+(\d+)\s+(?:TRACE|DEBUG|INFO|WARN|ERROR)\s+(\S+)\s+(.*)$")
CONNECTED = re.compile(r"stencil_tern: remote client (?:re)?connected\b(.*)$")
OVER_UDP = re.compile(r"\bway=over UDP to (\S+)")
RELAY = re.compile(r"\bway=through the relay (\S+) to endpoint ([0-9a-f]{64})\b")
NOW_DIRECT = re.compile(r"stencil_tern: remote connection now direct host=([0-9a-f]{64}) addr=(\S+)")
BACK_ON_RELAY = re.compile(r"stencil_tern: remote connection back on the relay host=([0-9a-f]{64})\b")
ENDED = ("stencil_tern: remote connection lost", "stencil_tern: remote client disconnected",
         "stencil_tern: remote session ended")
HOST_THREAD = "tern-remote-"


def loopback(address):
    """Whether `host:port` (IPv6 bracketed) names this machine."""
    host = address.rsplit(":", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or host.endswith(".localhost")
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def connected(details, time):
    state = {"since": time}
    over = OVER_UDP.search(details)
    relay = RELAY.search(details)
    if over:
        address = over.group(1)
        state.update(mode="loopback" if loopback(address) else "direct", addr=address,
                     port=address.rsplit(":", 1)[-1])
    elif relay:
        state.update(mode="relay", relay=relay.group(1), endpoint=relay.group(2))
    else:
        state["mode"] = "unknown"
    return state


def consume(labels, line, pid):
    """Apply one log line of process `pid` to the per-thread-label connection states."""
    match = LINE.match(line)
    if match is None or int(match.group(2)) != pid:
        return
    time, thread, rest = match.group(1), match.group(3), match.group(4)
    if thread.startswith(HOST_THREAD):
        label = thread[len(HOST_THREAD):]
        event = CONNECTED.search(rest)
        if event:
            labels[label] = connected(event.group(1), time)
        elif any(ended in rest for ended in ENDED):
            labels.pop(label, None)
        return
    # The relay reports migrations by endpoint; only relay-dialed connections know theirs.
    direct = NOW_DIRECT.search(rest)
    if direct:
        for state in labels.values():
            if state.get("endpoint") == direct.group(1):
                state.update(mode="direct", addr=direct.group(2), upgraded=True, since=time)
        return
    back = BACK_ON_RELAY.search(rest)
    if back:
        for state in labels.values():
            if state.get("endpoint") == back.group(1) and state.get("upgraded"):
                state.update(mode="relay", upgraded=False, since=time)
                state.pop("addr", None)


def probe(request, log=LOG, pid=None):
    """Read new complete lines since the cursor; restart when the process or log file changed."""
    pid = os.getppid() if pid is None else pid
    info = os.stat(log)
    identity = "%s:%s" % (info.st_dev, info.st_ino)
    cursor = request.get("cursor") or {}
    valid = (cursor.get("format") == 1 and cursor.get("pid") == pid and cursor.get("identity") == identity
             and int(cursor.get("offset", 0)) <= info.st_size)
    labels = (request.get("labels") or {}) if valid else {}
    offset = int(cursor.get("offset", 0)) if valid else 0
    with open(log, "rb") as stream:
        stream.seek(offset)
        for raw in stream:
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            consume(labels, raw.decode("utf-8", "replace"), pid)
    return {"pid": pid, "log": log, "labels": labels,
            "cursor": {"format": 1, "pid": pid, "identity": identity, "offset": offset}}


if __name__ == "__main__":
    try:
        print(json.dumps(probe(json.load(sys.stdin)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"reason": "读取 Tern 日志失败：%s" % error}, ensure_ascii=False))
        sys.exit(1)
