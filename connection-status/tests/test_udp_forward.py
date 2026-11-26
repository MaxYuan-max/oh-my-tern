import os
from pathlib import Path
import queue
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_udp import authenticated
from udp_relay import relay


class DirectAuthenticationTests(unittest.TestCase):
    ADDRESS = '192.0.2.9:7709'
    IDENTITY = 'user@target'
    SUCCESS = ('ok   udp          192.0.2.9:7709 answers over UDP 7709 (QUIC)\n'
               'ok   host key     SHA256:example is the one pinned in /test/known_hosts\n'
               'ok   auth         accepted: SHA256:user via ssh key from 192.0.2.9 as user@target\n')

    def test_requires_the_direct_route_and_expected_authenticated_identity(self):
        self.assertTrue(authenticated(self.SUCCESS, self.ADDRESS, self.IDENTITY))
        for wrong in (self.SUCCESS.replace('from 192.0.2.9', 'from relay'),
                      self.SUCCESS.replace('as user@target', 'as user@other'),
                      self.SUCCESS.replace('is the one pinned', 'was not pinned'),
                      self.SUCCESS.replace('7709 answers', '8376 answers')):
            self.assertFalse(authenticated(wrong, self.ADDRESS, self.IDENTITY))


class RelayingTests(unittest.TestCase):
    def run_relay(self, allowed):
        backend = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(backend.close)
        backend.bind(('127.0.0.1', 0))
        backend.settimeout(1.2)
        lease, client = os.pipe()
        messages = queue.Queue()
        worker = threading.Thread(target=relay, args=(('127.0.0.1', 0), backend.getsockname(),
                                                       allowed, lease, messages.put), daemon=True)
        worker.start()

        def stop():
            os.close(client)
            worker.join(2)
            os.close(lease)
            self.assertFalse(worker.is_alive(), 'relay must stop when the control lease closes')

        self.addCleanup(stop)
        address = messages.get(timeout=2).split()[3]
        return backend, ('127.0.0.1', int(address.rsplit(':', 1)[1]))

    def test_each_client_has_its_own_reply_path(self):
        back, uri = self.run_relay('127.0.0.1')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as a, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as b:
            a.settimeout(1)
            b.settimeout(1)
            a.sendto(b'a', uri)
            b.sendto(b'b', uri)
            clients = dict(back.recvfrom(10) for _ in range(2))
            self.assertNotEqual(clients[b'a'], clients[b'b'])
            back.sendto(b'B', clients[b'b'])
            back.sendto(b'A', clients[b'a'])
            self.assertEqual(a.recvfrom(10), (b'A', uri))
            self.assertEqual(b.recvfrom(10), (b'B', uri))

    def test_refuses_an_ip_other_than_the_ssh_client(self):
        back, uri = self.run_relay('192.0.2.9')
        back.settimeout(.2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
            source.sendto(b'must not forward', uri)
            with self.assertRaises(socket.timeout):
                back.recvfrom(128)
