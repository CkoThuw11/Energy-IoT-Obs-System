import unittest
from datetime import datetime, timedelta

from detector import Detector

PARAMS = {"X": dict(rated_load=100, yellow_pct=90, red_pct=100,
                    yellow_ttb_min=60, red_ttb_min=30, close_pct=85,
                    close_records=2, stuck_records=4)}
START = datetime(2018, 1, 1)


class DetectionTests(unittest.TestCase):
    def feed(self, detector, values, station="X"):
        actions = []
        for index, value in enumerate(values):
            actions.extend(detector.process(station, START + timedelta(minutes=15 * index), value, value))
        return actions

    def test_threshold_lifecycle_and_red_retained(self):
        actions = self.feed(Detector(PARAMS, "A"), [80, 91, 101, 92, 84, 83])
        self.assertEqual([a["severity"] for a in actions], ["YELLOW", "RED", "RED", "RED", "RED"])
        self.assertEqual(actions[-1]["status"], "RESOLVED")
        self.assertEqual(actions[-1]["escalated_ts"], "2018-01-01 00:30:00")

    def test_ttb_only_in_b(self):
        values = [70, 72, 74, 76, 90]
        self.assertEqual(self.feed(Detector(PARAMS, "A"), values)[0]["severity"], "YELLOW")
        alert = self.feed(Detector(PARAMS, "B"), values)[0]
        self.assertEqual((alert["severity"], alert["trigger_rule"]), ("RED", "TTB"))
        self.assertEqual(alert["ttb_min"], 30)

    def test_sensor_priority_and_recovery(self):
        actions = self.feed(Detector(PARAMS, "B"), [80, 80, 80, 80, 81])
        self.assertEqual([(a["alert_type"], a["status"]) for a in actions],
                         [("SENSOR", "OPEN"), ("SENSOR", "RESOLVED")])

    def test_gap_resets_history(self):
        detector = Detector(PARAMS, "B")
        for index in (0, 1, 2, 4):
            self.assertEqual(detector.process("X", START + timedelta(minutes=15 * index), 95, 95)[-1]["alert_type"], "OVERLOAD")
        self.assertNotIn("SENSOR", detector.state["X"]["active"])

    def test_duplicate_rejected_and_restart_state(self):
        detector = Detector(PARAMS, "A")
        self.feed(detector, [91, 84])
        restored = Detector(PARAMS, "A", detector.state)
        alert = restored.process("X", START + timedelta(minutes=30), 83, 83)[0]
        self.assertEqual(alert["status"], "RESOLVED")
        with self.assertRaises(ValueError):
            restored.process("X", START + timedelta(minutes=30), 83, 83)


if __name__ == "__main__":
    unittest.main()
