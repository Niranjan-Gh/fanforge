import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fanforge import App, Hardware, curve_value
from fanforge_safety import ARMED_KEY, Header, fresh_temperature, validate_config, write_config


class SafetyTests(unittest.TestCase):
    def test_invalid_or_late_samples(self):
        for value in (None, float("nan"), float("inf"), -1, 0, 151):
            with self.subTest(value=value):
                self.assertIsNone(fresh_temperature(value, 1, 2, None))
        self.assertIsNone(fresh_temperature(42, 1, 5, None))
        self.assertIsNone(fresh_temperature(42, 5, 6, 1))
        self.assertEqual(fresh_temperature(42, 5, 6, 4), 42)

    def test_missing_package_sensor_cannot_mask_overheat(self):
        hw = object.__new__(Hardware)
        hw.cpu_temps = [SimpleNamespace(Name="CPU Package", Value=None),
                        SimpleNamespace(Name="Core", Value=42)]
        self.assertIsNone(hw.cpu_temp())
        hw.cpu_temps[0].Value = 45
        hw.cpu_temps[1].Value = 86
        self.assertEqual(hw.cpu_temp(), 86)

    def test_emergency_skips_curves_and_disarmed_headers(self):
        writes = []
        safe = Header(writes.append, Mock())
        failing = Header(Mock(side_effect=OSError()), Mock())
        inactive = Header(Mock(), Mock())
        app = SimpleNamespace(
            auto=Mock(), smooth=45, rows=[
                {"enabled": Mock(get=lambda: True), "header": failing},
                {"enabled": Mock(get=lambda: True), "header": safe},
                {"enabled": Mock(get=lambda: False), "header": inactive},
            ])
        App._drive(app, None)
        self.assertEqual(writes, [100])
        failing.release.assert_called_once()
        inactive.write.assert_not_called()
        inactive.release.assert_not_called()

    def test_poll_exception_requests_full_speed(self):
        app = SimpleNamespace(
            hw=Mock(), after=Mock(), last_poll=None, closing=False, rows=[],
            master_editor=None, temp_var=Mock(), status=Mock(), config_error="", _drive=Mock(), _tick=Mock(),
        )
        app.hw.update.side_effect = OSError("sensor driver failed")
        App._tick(app)
        app._drive.assert_called_once_with(None)
        app.after.assert_called_once()

    def test_failed_restore_is_retried_and_write_fault_latches(self):
        write = Mock(side_effect=OSError("write"))
        restore = Mock(side_effect=[OSError("restore"), None])
        header = Header(write, restore)
        header.apply(35)
        self.assertTrue(header.owned)
        self.assertIsNone(header.current)
        header.apply(100, emergency=True)
        self.assertFalse(header.owned)
        self.assertTrue(header.fault)
        write.assert_called_once_with(35)
        self.assertEqual(restore.call_count, 2)

    def test_release_only_touched_headers(self):
        for touched in (False, True):
            with self.subTest(touched=touched):
                release = Mock()
                header = Header(Mock(), release)
                if touched:
                    header.apply(40)
                self.assertTrue(header.restore())
                self.assertTrue(header.restore())
                self.assertEqual(release.call_count, int(touched))

    def test_ramp_down_and_emergency_bypass(self):
        writes = []
        header = Header(writes.append, Mock())
        for target, emergency in ((80, False), (80, False), (20, False), (100, True)):
            header.apply(target, emergency)
        self.assertEqual(writes, [80, 78, 100])
        self.assertEqual(curve_value([[30, 25], [50, 45]], 40), 35)


class ConfigTests(unittest.TestCase):
    def test_legacy_curves_without_opt_ins(self):
        config = {"/header/0": [[20, 25], [80, 100]], "__linked__": True}
        self.assertEqual(validate_config(config), config)
        self.assertNotIn(ARMED_KEY, validate_config(config))

    def test_reject_invalid_config(self):
        for config in ([], {"__linked__": "false"}, {ARMED_KEY: ["fan"]},
                       {"curve": [[20, 30], [20, 40]]},
                       {"curve": [[20, 0], [80, 100]]},
                       {"curve": [[20, float("nan")], [80, 100]]},
                       {"curve": [[20, True], [80, 100]]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)

    def test_failed_replace_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            write_config(path, {"__linked__": False})
            with patch("fanforge_safety.os.replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    write_config(path, {"__linked__": True})
            self.assertEqual(json.loads(path.read_text()), {"__linked__": False})
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
