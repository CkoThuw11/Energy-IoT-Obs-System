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
        actions = self.feed(Detector(PARAMS), [80, 91, 101, 92, 84, 83])
        self.assertEqual([a["severity"] for a in actions], ["YELLOW", "RED", "RED", "RED", "RED"])
        self.assertEqual(actions[-1]["status"], "RESOLVED")
        self.assertEqual(actions[-1]["escalated_ts"], "2018-01-01 00:30:00")

    def test_ttb_escalates_before_threshold(self):
        # tải 90% nhưng tăng 20%/giờ → còn 30 phút đến 100% → Đỏ theo TTB, không chỉ Vàng theo ngưỡng
        alert = self.feed(Detector(PARAMS), [70, 72, 74, 76, 90])[0]
        self.assertEqual((alert["severity"], alert["trigger_rule"]), ("RED", "TTB"))
        self.assertEqual(alert["ttb_min"], 30)

    def test_received_load_recorded_on_open_only(self):
        detector = Detector(PARAMS)
        opened = detector.process("X", START, 91, 91, received=5.4)[0]
        self.assertEqual(opened["received_load"], 5.4)
        later = detector.process("X", START + timedelta(minutes=15), 92, 92, received=0)[0]
        self.assertEqual(later["received_load"], 5.4)               # giữ giá trị lúc mở
        self.assertIsNone(Detector(PARAMS).process("X", START, 91, 91)[0]["received_load"])

    def test_sensor_priority_and_recovery(self):
        actions = self.feed(Detector(PARAMS), [80, 80, 80, 80, 81])
        self.assertEqual([(a["alert_type"], a["status"]) for a in actions],
                         [("SENSOR", "OPEN"), ("SENSOR", "RESOLVED")])

    def test_gap_resets_history(self):
        detector = Detector(PARAMS)
        for index in (0, 1, 2, 4):
            self.assertEqual(detector.process("X", START + timedelta(minutes=15 * index), 95, 95)[-1]["alert_type"], "OVERLOAD")
        self.assertNotIn("SENSOR", detector.state["X"]["active"])

    def test_ttb_null_when_overloaded_or_not_rising(self):
        actions = self.feed(Detector(PARAMS), [90, 92, 94, 96, 101, 101, 101, 101, 95])
        self.assertIsNone(actions[0]["ttb_min"])          # chưa đủ 4 bản ghi
        self.assertIsNone(actions[4]["ttb_min"])          # load ≥ 100
        self.assertIsNone(actions[-1]["ttb_min"])         # tốc độ ≤ 0

    def test_became_red_only_on_transition(self):
        actions = self.feed(Detector(PARAMS), [80, 91, 101, 102, 92])
        self.assertEqual([a["became_red"] for a in actions], [False, True, False, False])
        opened_red = self.feed(Detector(PARAMS), [105, 106])
        self.assertEqual([a["became_red"] for a in opened_red], [True, False])

    def test_muted_alert_silent_until_closed(self):
        detector = Detector(PARAMS)
        self.feed(detector, [91])
        detector.mute("X", "OVERLOAD")
        silent = [detector.process("X", START + timedelta(minutes=15 * i), v, v)
                  for i, v in enumerate([95, 101, 84, 83], start=1)]
        self.assertEqual(silent, [[], [], [], []])
        self.assertNotIn("OVERLOAD", detector.state["X"]["active"])
        reopened = detector.process("X", START + timedelta(minutes=75), 92, 92)
        self.assertEqual(reopened[0]["opened_ts"], "2018-01-01 01:15:00")

    def test_transfer_offset_is_not_a_trend(self):
        # tải đo tăng đều 2%/bản ghi; từ bản ghi thứ 5 chuyển tải làm tải vận hành thấp hơn 30 điểm
        detector, actions = Detector(PARAMS), []
        for index, measured in enumerate([91, 93, 95, 97, 99, 101, 103]):
            shown = measured - 30 if index >= 4 else measured
            actions += detector.process("X", START + timedelta(minutes=15 * index), measured, shown, measured)
        overload = [a for a in actions if a["alert_type"] == "OVERLOAD"]
        self.assertEqual([a["speed_pct_h"] for a in overload], [None, None, None, None, 8, 8])
        self.assertEqual(overload[-1]["status"], "RESOLVED")   # đóng vì tải sau chuyển < 85% hai lần

    def test_duplicate_rejected_and_restart_state(self):
        detector = Detector(PARAMS)
        self.feed(detector, [91, 84])
        restored = Detector(PARAMS, detector.state)
        alert = restored.process("X", START + timedelta(minutes=30), 83, 83)[0]
        self.assertEqual(alert["status"], "RESOLVED")
        with self.assertRaises(ValueError):
            restored.process("X", START + timedelta(minutes=30), 83, 83)


if __name__ == "__main__":
    unittest.main()
