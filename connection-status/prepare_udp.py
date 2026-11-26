"""Prepare and authenticate an optional, source-limited UDP route for a host."""
import fcntl
import ipaddress
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time

import hosts_config

TERN = '/Applications/Tern.app/Contents/MacOS/tern'
SSH = ['/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'UpdateHostKeys=no', '-o', 'ClearAllForwardings=yes', '-o', 'ControlMaster=no',
       '-o', 'ControlPath=none', '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5',
       '-o', 'ServerAliveCountMax=3']


def lookup(name):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name):
        raise ValueError('无效的主机名')
    entry = next((h for h in hosts_config.hosts(hosts_config.load()) if h['name'] == name), {})
    route = entry.get('udp_forward')
    if not isinstance(route, dict) or not isinstance(entry.get('tunnel'), dict):
        raise ValueError('没有配置独立 UDP 路径和备用隧道')
    endpoint = route['address'].rsplit('@', 1)[-1]
    ip, port = endpoint.split(':')
    target = entry['tunnel']
    relay_args = [route['host'], str(ipaddress.IPv4Address(ip)), str(int(port)),
                  str(ipaddress.IPv4Address(target['target_host'])), str(int(target['target_port']))]
    if not route['ssh'] or route['ssh'].startswith('-') or not route['identity'] or not target['doctor']:
        raise ValueError('UDP 路径配置不完整')
    if not all(0 < int(p) < 65536 for p in (relay_args[2], relay_args[4])) or relay_args[1:3] == relay_args[3:5]:
        raise ValueError('无效的 UDP 端口或目标')
    return route, endpoint, relay_args, target['doctor'].rsplit('@', 1)[-1]


def keep_running(route, relay_args):
    source = Path(__file__).with_name('udp_relay.py').read_text()
    command = ' '.join(shlex.quote(word) for word in ['python3', '-u', '-c', source] + relay_args)
    with subprocess.Popen(SSH + [route['ssh'], command], stdin=subprocess.PIPE) as proc:
        def stop(_signum, _frame):
            proc.terminate()
        signal.signal(signal.SIGTERM, stop)
        try:
            while proc.poll() is None:
                proc.stdin.write(b'\n')
                proc.stdin.flush()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    continue
        except BrokenPipeError:
            pass
        finally:
            if proc.poll() is None:
                proc.terminate()
        return proc.wait()


def read_pins(path, address):
    if not path.is_file():
        return set()
    return {words[1] for line in path.read_text().splitlines()
            if len(words := line.split()) >= 2 and words[0] == address}


def authenticated(output, endpoint, identity):
    source = endpoint.split(':', 1)[0]
    return (re.search(r'^ok\s+udp\s+' + re.escape(endpoint) + r' answers over UDP', output, re.M)
            and re.search(r'^ok\s+host key\s+.* is the one pinned', output, re.M)
            and re.search(r'^ok\s+auth\s+.* from ' + re.escape(source) + ' as ' + re.escape(identity) + r'\s*$', output, re.M))


def probe(route, endpoint, env):
    try:
        result = subprocess.run([TERN, 'remote', 'doctor', route['address']], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, env=env, timeout=10)
    except subprocess.TimeoutExpired:
        return False, 'Tern UDP 验证超时'
    output = result.stdout + result.stderr
    status = '\n'.join(line for line in output.splitlines() if line.startswith('FAIL'))
    return bool(authenticated(output, endpoint, route['identity'])), status or '未验证到指定跳板的 UDP 认证'


def prepare(name, route, endpoint, reference):
    config = Path(os.environ.get('TERN_CONFIG_DIR') or Path.home() / 'Library/Application Support/Tern')
    known = config / 'known_hosts'
    pins = read_pins(known, reference)
    if len(pins) != 1 or not next(iter(pins)).startswith('SHA256:'):
        raise ValueError('找不到备用 Tern 地址已固定的唯一指纹')
    pin = next(iter(pins))
    actual = read_pins(known, endpoint)
    if actual and actual != {pin}:
        raise ValueError('UDP 地址的已保存指纹不同，未更改信任')
    state = hosts_config.data_dir()
    state.mkdir(parents=True, exist_ok=True)
    with (state / (name + '-udp.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='udp-check-', dir=str(state)) as scratch:
            trusted = Path(scratch) / 'known_hosts'
            trusted.write_text('%s %s\n' % (endpoint, pin))
            trusted.chmod(0o600)
            env = dict(os.environ, TERN_CONFIG_DIR=scratch)  # No relay endpoint pin: require the direct path.
            ready, detail = probe(route, endpoint, env)
            worker = None
            try:
                if not ready:
                    with (state / (name + '-udp.log')).open('w') as log:
                        worker = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), name, '--worker'],
                                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                    for _ in range(2):
                        time.sleep(.7)
                        if worker.poll() is not None:
                            raise ValueError('UDP 转发未启动；请查看 ' + log.name)
                        ready, detail = probe(route, endpoint, env)
                        if ready:
                            break
                if not ready:
                    raise ValueError(detail)
                latest = read_pins(known, endpoint)
                if latest and latest != {pin}:
                    raise ValueError('UDP 地址的指纹在验证期间改变，未覆盖')
                if not latest:
                    subprocess.run([TERN, 'remote', 'trust', endpoint, pin], stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=5, check=True)
                return '经 %s 的 UDP 已通过 Tern 认证' % endpoint
            except Exception:
                if worker is not None and worker.poll() is None:
                    worker.terminate()
                raise


if __name__ == '__main__':
    try:
        if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != '--worker'):
            sys.exit('用法：prepare_udp.py HOST')
        name = sys.argv[1]
        route, endpoint, relay_args, reference = lookup(name)
        if len(sys.argv) == 3:
            sys.exit(keep_running(route, relay_args))
        print(prepare(name, route, endpoint, reference))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        sys.exit(str(e))
