"""Forward one client's UDP to one fixed backend while the SSH control lease lives.

Sent to the jump host as Python source. No data datagrams use the SSH stream.
"""
import ipaddress
import os
import selectors
import socket
import sys
import time


def relay(listen, target, allowed, control, announce=print):
    flows = {}
    rounds = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ingress, selectors.DefaultSelector() as sel:
        ingress.bind(listen)  # No reuse: never take a port away from another service.
        ingress.setblocking(False)
        sel.register(ingress, selectors.EVENT_READ, 'client')
        sel.register(control, selectors.EVENT_READ, 'lease')
        now = time.monotonic()
        lease, idle = now + 35, now + 600
        announce('UDP ready on %s:%s for %s' % (ingress.getsockname() + (allowed,)))
        try:
            while True:
                now = time.monotonic()
                if now >= min(lease, idle):
                    return
                for peer, (backend, touched) in list(flows.items()):
                    if now - touched >= 120:
                        sel.unregister(backend)
                        backend.close()
                        del flows[peer]
                for key, _ in sel.select(min(lease, idle) - now):
                    if key.data == 'lease':
                        if not os.read(control, 4096):
                            return
                        lease = time.monotonic() + 35
                    elif key.data == 'client':
                        try:
                            packet, peer = ingress.recvfrom(65535)
                        except BlockingIOError:
                            continue
                        if peer[0] != allowed:
                            continue
                        now = time.monotonic()
                        if peer not in flows:
                            if len(flows) >= 16:
                                continue
                            backend = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            try:
                                backend.connect(target)
                                backend.setblocking(False)
                                sel.register(backend, selectors.EVENT_READ, peer)
                            except OSError:
                                backend.close()
                                continue
                        else:
                            backend, _ = flows[peer]
                        flows[peer] = (backend, now)
                        try:
                            backend.send(packet)
                        except OSError:
                            pass
                    else:
                        try:
                            packet = key.fileobj.recv(65535)
                            ingress.sendto(packet, key.data)
                            now = time.monotonic()
                            flows[key.data] = (key.fileobj, now)
                            idle = now + 600  # Only real backend traffic keeps an idle relay alive.
                            rounds += 1
                        except OSError:
                            pass
        finally:
            for backend, _ in flows.values():
                backend.close()
            announce('UDP relay closed (%d return datagrams)' % rounds)


if __name__ == '__main__':
    machine, listen_ip, listen_port, target_ip, target_port = sys.argv[1:]
    if socket.gethostname().split('.')[0] != machine:
        sys.exit('Wrong jump host; no UDP socket opened')
    listen = (str(ipaddress.IPv4Address(listen_ip)), int(listen_port))
    target = (str(ipaddress.IPv4Address(target_ip)), int(target_port))
    if listen == target:
        sys.exit('Relay cannot forward to itself')
    allowed = str(ipaddress.IPv4Address(os.environ['SSH_CONNECTION'].split()[0]))
    relay(listen, target, allowed, sys.stdin.fileno(), lambda line: print(line, flush=True))
