import tempfile
import unittest
from pathlib import Path

from backend.pin_coordinates import PinCoordinateMap, PinCoordinateMapError


class PinCoordinateMapTests(unittest.TestCase):
    def test_lookup_is_case_insensitive_for_pin_labels(self):
        coordinates = PinCoordinateMap({"metro_grand_central": {"D13": {"x": 12, "y": -4.5}}})

        self.assertEqual(
            coordinates.coordinate_for("metro_grand_central", "d13").x,
            12.0,
        )
        self.assertEqual(
            coordinates.coordinate_for("metro_grand_central", "d13").y,
            -4.5,
        )

    def test_missing_pin_or_board_is_rejected(self):
        coordinates = PinCoordinateMap({"board": {"D1": {"x": 1, "y": 2}}})

        with self.assertRaises(PinCoordinateMapError):
            coordinates.coordinate_for("other", "D1")
        with self.assertRaises(PinCoordinateMapError):
            coordinates.coordinate_for("board", "D2")

    def test_non_finite_coordinate_is_rejected(self):
        with self.assertRaises(PinCoordinateMapError):
            PinCoordinateMap({"board": {"D1": {"x": float("nan"), "y": 2}}})

    def test_duplicate_case_insensitive_labels_are_rejected(self):
        with self.assertRaises(PinCoordinateMapError):
            PinCoordinateMap({"board": {"D1": {"x": 1, "y": 2}, "d1": {"x": 3, "y": 4}}})

    def test_reads_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coordinates.json"
            path.write_text(
                '{"coordinate_units":"mm","boards":{"board":{"pin_001":'
                '{"label":"D1","header":"digital","x":1.25,"y":2.5}}}}',
                encoding="utf-8",
            )
            coordinates = PinCoordinateMap.from_json_file(path)

        self.assertEqual(coordinates.coordinate_for("board", "pin_001").x, 1.25)

    def test_unfilled_template_coordinate_is_rejected_at_lookup(self):
        coordinates = PinCoordinateMap({"board": {"pin_001": {"label": "D1", "x": None, "y": None}}})

        with self.assertRaisesRegex(PinCoordinateMapError, "not been filled in"):
            coordinates.coordinate_for("board", "pin_001")


if __name__ == "__main__":
    unittest.main()