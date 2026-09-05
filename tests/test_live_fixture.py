import unittest

from benchmarks.fixtures import packet
from integration.live import packet_ids
from integration.proxy import start


class LiveFixtureTests(unittest.TestCase):
    def test_boundary_evidence_keeps_actual_packet_order(self):
        self.assertEqual(packet_ids(packet(65, b"fixture") + packet(66, b"fixture")), [65, 66])
        with self.assertRaises(RuntimeError):
            packet_ids(b"\x41")

    def test_proxy_cannot_target_arbitrary_hosts(self):
        with self.assertRaises(ValueError):
            start(80, 443, "kai.ovh")


if __name__ == "__main__":
    unittest.main()
