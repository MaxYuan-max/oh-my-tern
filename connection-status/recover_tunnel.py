"""Make sure the local SSH-framed UDP tunnel to a Tern host works: a healthy one is left alone, a broken one is rebuilt.

    recover_tunnel.py <name>   # a name from hosts.json

Exit status 0: Tern's doctor finds the tunnel answering and authenticates through it. Otherwise the reason is on
stderr and the exit status is 1. The plugin runs this before it connects a host from the palette, and for
"检查并恢复当前主机 SSH 隧道". SSH reaches these hosts only once the VPN is up, so before rebuilding anything this
checks that a fresh SSH login works, and changes nothing when it does not.
"""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import NamedTuple

import hosts_config

HOME = Path.home()
CONFIG = hosts_config.load()
STATE = hosts_config.data_dir()
SCRIPT = Path(os.path.expanduser(CONFIG.get("tunnel_script") or "~/src/tern-ssh-udp/tunnel.py"))
TERN = '/Applications/Tern.app/Contents/MacOS/tern'
# A fresh login the way the tunnel makes it: no connection sharing, nothing interactive.
SSH = ['/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ControlMaster=no',
       '-o', 'ControlPath=none', '-o', 'ControlPersist=no', '-o', 'ConnectTimeout=8']


class Tunnel(NamedTuple):
    host: str         # SSH destination the tunnel logs in to
    port: int         # local UDP port, on 127.0.0.1 and ::1
    target_host: str  # IPv4 host the SSH side sends the datagrams to (tunnel.py --target-host)
    files: str        # prefix of the log, pid and lock files in STATE
    doctor: str       # address `tern remote doctor` dials; pinned in Tern's known_hosts


# Keyed by the names of TUNNEL_HOSTS in window.luau. `host` is the SSH
# destination the tunnel logs in to; `files` prefixes its log, pid and lock
# files in STATE; `doctor` is the address `tern remote doctor` dials, pinned in
# Tern's known_hosts. All of it comes from the installation's hosts.json.
TUNNELS = {}
for _host in hosts_config.hosts(CONFIG):
    _tunnel = _host.get("tunnel")
    if not isinstance(_tunnel, dict):
        continue
    for _field in ('ssh', 'files', 'doctor'):
        if not isinstance(_tunnel.get(_field), str) or not _tunnel[_field]:
            raise ValueError('主机 %s 的 tunnel 缺少 %s' % (_host["name"], _field))
    TUNNELS[str(_host["name"])] = Tunnel(
        _tunnel['ssh'], int(_host["port"]), str(_tunnel.get('target_host') or '127.0.0.1'),
        _tunnel['files'], _tunnel['doctor'])


class Failure(Exception):
    """The tunnel cannot be made to work; the message says why."""


def parse_holders(output, port):
    """PIDs with a socket bound to UDP `port`, from `lsof -Fpn`; a client socket connected to it does not count."""
    pids, pid = set(), None
    for line in output.splitlines():
        if line.startswith('p'):
            pid = int(line[1:])
        elif line.startswith('n') and line[1:].split('->')[0].endswith(':%d' % port):
            pids.add(pid)
    return pids


def holders(port):
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-iUDP:%d' % port, '-Fpn'], capture_output=True, text=True)
    return parse_holders(result.stdout, port)


def option(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv[:-1] else default


def is_ours(command, tunnel):
    """Whether `command` is this script's client for exactly this tunnel; nothing else is ever stopped."""
    argv = command.split()
    if str(SCRIPT) not in argv:
        return False
    rest = argv[argv.index(str(SCRIPT)) + 1:]
    return (rest[:1] == ['client'] and option(rest, '--host') == tunnel.host
            and option(rest, '--port') == str(tunnel.port)
            and option(rest, '--target-host', '127.0.0.1') == tunnel.target_host)


def command_of(pid):
    result = subprocess.run(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
    return result.stdout.strip()


def problems(output):
    """The failing lines of `tern remote doctor`, or the end of its output when none is marked."""
    lines = output.strip().splitlines()
    return '\n'.join([line for line in lines if line.startswith(('FAIL', 'skip'))] or lines[-3:])


def doctor(tunnel):
    """(True, output) when the tunnel answers over UDP and Tern authenticates through it."""
    try:
        done = subprocess.run([TERN, 'remote', 'doctor', tunnel.doctor], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired:
        return False, 'tern remote doctor 20 秒内没有结束'
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def ssh_login(tunnel):
    """(True, '') when a fresh non-interactive SSH login to the tunnel's host can run python3, else (False, why)."""
    try:
        done = subprocess.run(SSH + [tunnel.host, 'python3', '-c', 'pass'], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired:
        return False, '20 秒内没有登录成功'
    return done.returncode == 0, '\n'.join(done.stderr.strip().splitlines()[-3:])


def stop(pids, port):
    """SIGTERM the tunnels (each ends its own SSH children) and wait for the port; False when it stays held."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while holders(port):
        if time.monotonic() > deadline:
            return False
        time.sleep(.2)
    return True


def start(tunnel):
    """A detached tunnel client with three spare SSH logins and a 5 s SSH keepalive."""
    argv = ['/usr/bin/python3', '-u', str(SCRIPT), 'client', '--host', tunnel.host, '--port', str(tunnel.port),
            '--target-host', tunnel.target_host, '--spares', '3', '--keepalive', '5']
    with open(STATE / (tunnel.files + '.log'), 'ab', buffering=0) as log:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, close_fds=True)
    (STATE / (tunnel.files + '.pid')).write_text('%d\n' % process.pid)
    return process


def log_tail(tunnel):
    with open(STATE / (tunnel.files + '.log'), 'rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 2000))
        return '\n'.join(stream.read().decode(errors='replace').strip().splitlines()[-3:])


def wait_until_answering(tunnel, process, seconds=45):
    """Polls the doctor: the spare SSH logins need a few seconds, so the first tries can fail. (ok, detail)."""
    deadline = time.monotonic() + seconds
    while True:
        if process.poll() is not None:
            return False, '隧道进程已退出（状态 %s）\n%s' % (process.returncode, log_tail(tunnel))
        ok, output = doctor(tunnel)
        if ok:
            return True, ''
        if time.monotonic() > deadline:
            return False, problems(output)
        time.sleep(2)


def ensure(name, tunnel):
    """What was done, as a message; raises Failure when the tunnel does not end up working."""
    if not SCRIPT.is_file():
        raise Failure('找不到已验证的隧道脚本：' + str(SCRIPT))
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / (tunnel.files + '-recovery.lock'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Failure('另一个 %s 隧道操作正在运行' % name)
    running = set()
    for pid in holders(tunnel.port):
        command = command_of(pid)
        if not command:
            continue  # exited meanwhile
        if not is_ours(command, tunnel):
            raise Failure('端口 %d 被其他程序占用（PID %d）；未停止任何程序' % (tunnel.port, pid))
        running.add(pid)
    if running and doctor(tunnel)[0]:
        return '%s 隧道正常，Tern 认证通过；未重启，也未中断现有连接。' % name
    reachable, why = ssh_login(tunnel)
    if not reachable:
        raise Failure('无法 SSH 登录 %s；请确认 VPN 已连接。没有改动现有隧道。\n%s' % (tunnel.host, why))
    if running and not stop(running, tunnel.port):
        raise Failure('旧隧道未释放端口；没有强制终止')
    process = start(tunnel)
    time.sleep(.5)
    answering, detail = wait_until_answering(tunnel, process)
    if not answering:
        raise Failure('隧道已启动（PID %d），但没有通过 Tern 认证；日志 %s\n%s'
                      % (process.pid, STATE / (tunnel.files + '.log'), detail))
    return '%s SSH 隧道已%s并通过 Tern 认证；PID %d' % (name, '重建' if running else '启动', process.pid)


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in TUNNELS:
        sys.exit('用法：recover_tunnel.py ' + ' | '.join(TUNNELS))
    try:
        print(ensure(sys.argv[1], TUNNELS[sys.argv[1]]))
    except Failure as failure:
        sys.exit(str(failure))
