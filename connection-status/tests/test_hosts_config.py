import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hosts_config


class HostsConfigTests(unittest.TestCase):
    def test_a_missing_file_is_an_empty_installation_not_an_error(self):
        self.assertEqual(hosts_config.load("/nonexistent/hosts.json"), {})
        self.assertEqual(hosts_config.hosts({}), [])

    def test_an_unreadable_file_is_reported_with_its_path(self):
        with self.subTest("invalid json"):
            with patch.object(Path, "read_text", side_effect=ValueError("bad json")):
                with self.assertRaises(ValueError):
                    hosts_config.load("/tmp/hosts.json")
        with self.subTest("hosts is not a list"):
            with patch.object(Path, "read_text", return_value=json.dumps({"hosts": {}})):
                with self.assertRaises(ValueError):
                    hosts_config.hosts(hosts_config.load("/tmp/hosts.json"))

    def test_every_host_needs_a_name_address_and_port(self):
        for broken in ({"address": "a", "port": "1"}, {"name": "a", "port": "1"},
                       {"name": "a", "address": "a"}, {"name": "", "address": "a", "port": "1"}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                hosts_config.hosts({"hosts": [broken]})

    def test_env_overrides_point_at_a_test_installation(self):
        with patch.dict(os.environ, {"CONNECTION_STATUS_DATA": "/tmp/data", "CONNECTION_STATUS_CONFIG": "/tmp/data/h.json"}):
            self.assertEqual(hosts_config.data_dir(), Path("/tmp/data"))
            self.assertEqual(hosts_config.config_path(), Path("/tmp/data/h.json"))


if __name__ == "__main__":
    unittest.main()
