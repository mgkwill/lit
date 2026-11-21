"""A broker listener must not depend on reverse DNS being responsive."""
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from task_broker import Server


class ListenerTests(unittest.TestCase):
    def test_listener_binds_without_reverse_dns(self):
        with patch('socket.getfqdn', side_effect=AssertionError('reverse DNS unavailable')):
            with Server(('127.0.0.1', 0), BaseHTTPRequestHandler) as server:
                self.assertGreater(server.server_port, 0)
                self.assertEqual(server.server_address, server.socket.getsockname())
                self.assertEqual(server.server_name, '127.0.0.1')


if __name__ == '__main__':
    unittest.main()
