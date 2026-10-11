import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oscilloscope import capture


class FakeScope:
    def __init__(self, instrument_id="RIGOL,DS1Z,TEST,1.0"):
        self.instrument_id = instrument_id
        self.closed = False
        self.timeout = None
        self.chunk_size = None

    def query(self, command):
        if command == "*IDN?":
            return self.instrument_id
        raise AssertionError(f"Unexpected query: {command}")

    def close(self):
        self.closed = True


class FakeResourceManager:
    def __init__(self):
        self.resources = ("USB0::SCOPE0::INSTR", "TCPIP0::SCOPE1::INSTR")
        self.opened_resource = None
        self.closed = False
        self.scope = FakeScope()

    def list_resources(self):
        return self.resources

    def open_resource(self, resource):
        self.opened_resource = resource
        return self.scope

    def close(self):
        self.closed = True


class CaptureTests(unittest.TestCase):
    def test_connect_scope_selects_resource_zero(self):
        manager = FakeResourceManager()
        fake_pyvisa = SimpleNamespace(ResourceManager=lambda: manager)

        with patch.dict(sys.modules, {"pyvisa": fake_pyvisa}):
            returned_manager, scope = capture.connect_scope(resource_index=0)

        self.assertIs(returned_manager, manager)
        self.assertIs(scope, manager.scope)
        self.assertEqual(manager.opened_resource, manager.resources[0])

    def test_callable_capture_checks_identity_saves_outputs_and_closes_scope(self):
        manager = FakeResourceManager()
        scope = manager.scope
        raw, timestamps, volts = [1, 2, 3], [0.0, 0.1, 0.2], [0.1, 2.5, 4.0]
        metadata = {"channel": "CHAN3"}
        edges = [{"time_s": 0.1, "from_level": 0, "to_level": 1, "edge": "rising"}]
        thresholds = {"method": "test"}

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "capture"
            with patch("oscilloscope.capture.connect_scope", return_value=(manager, scope)):
                with patch(
                    "oscilloscope.capture.acquire_waveform",
                    return_value=(raw, timestamps, volts, metadata),
                ):
                    with patch(
                        "oscilloscope.capture.extract_edges",
                        return_value=(edges, thresholds),
                    ):
                        with patch("oscilloscope.capture.save_capture", return_value=output_dir) as save:
                            result = capture.capture_once(
                                output_dir=output_dir,
                                resource_index=0,
                                extra_metadata={"job_id": "job_1", "pin_label": "D13"},
                            )

        self.assertEqual(metadata["instrument_id"], "RIGOL,DS1Z,TEST,1.0")
        self.assertEqual(metadata["pin_label"], "D13")
        self.assertEqual(result["edge_count"], 1)
        self.assertEqual(Path(result["edges_csv"]), output_dir / "edges.csv")
        save.assert_called_once_with(
            raw, timestamps, volts, metadata, edges, thresholds, output_dir
        )
        self.assertTrue(scope.closed)
        self.assertTrue(manager.closed)

    def test_empty_instrument_identity_fails_and_closes_resources(self):
        manager = FakeResourceManager()
        scope = FakeScope(instrument_id="")

        with patch("oscilloscope.capture.connect_scope", return_value=(manager, scope)):
            with patch("oscilloscope.capture.acquire_waveform") as acquire:
                with self.assertRaisesRegex(RuntimeError, "instrument ID"):
                    capture.capture_once()

        acquire.assert_not_called()
        self.assertTrue(scope.closed)
        self.assertTrue(manager.closed)


if __name__ == "__main__":
    unittest.main()