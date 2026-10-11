"""Load calibrated board-pin coordinates in the STM32's X/Y coordinate frame.

Expected JSON format::

    {
            "coordinate_units": "mm",
            "boards": {
                "metro_grand_central": {
                    "pin_004": {"label": "D13", "header": "digital_high", "x": 12.5, "y": 34.0}
                }
      }
    }

Pin IDs are based on the board template order. Coordinates must use the units
and origin expected by the STM32 firmware. Null coordinates are allowed while
the template is being filled in, but cannot be used for a move.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_COORDINATES_PATH = Path(__file__).with_name("pin_coordinates.json")


class PinCoordinateMapError(ValueError):
    """Raised for invalid or missing calibrated pin coordinates."""


@dataclass(frozen=True)
class PinCoordinate:
    x: float
    y: float
    label: str | None = None
    header: str | None = None


class PinCoordinateMap:
    def __init__(self, boards: Mapping[str, Mapping[str, Mapping[str, object]]]):
        if not isinstance(boards, Mapping) or not boards:
            raise PinCoordinateMapError("Coordinate map must contain at least one board.")

        self._boards: dict[str, dict[str, PinCoordinate | None]] = {}
        for board, pins in boards.items():
            if not isinstance(board, str) or not board.strip():
                raise PinCoordinateMapError("Board names must be non-empty strings.")
            if not isinstance(pins, Mapping):
                raise PinCoordinateMapError(f"Pin entries for board {board!r} must be an object.")

            normalized_pins: dict[str, PinCoordinate | None] = {}
            for pin_id, coordinate in pins.items():
                if not isinstance(pin_id, str) or not pin_id.strip():
                    raise PinCoordinateMapError(f"Pin IDs for board {board!r} must be non-empty strings.")
                normalized_id = pin_id.strip().upper()
                if normalized_id in normalized_pins:
                    raise PinCoordinateMapError(
                        f"Duplicate pin ID {normalized_id!r} for board {board!r}."
                    )
                if not isinstance(coordinate, Mapping):
                    raise PinCoordinateMapError(f"Coordinates for {board}/{pin_id} must contain x and y.")
                x, y = coordinate.get("x"), coordinate.get("y")
                if x is None or y is None:
                    normalized_pins[normalized_id] = None
                else:
                    normalized_pins[normalized_id] = PinCoordinate(
                        x=self._finite_coordinate(x, board, pin_id, "x"),
                        y=self._finite_coordinate(y, board, pin_id, "y"),
                        label=self._optional_text(coordinate.get("label"), board, pin_id, "label"),
                        header=self._optional_text(coordinate.get("header"), board, pin_id, "header"),
                    )
            self._boards[board] = normalized_pins

    @classmethod
    def from_json_file(cls, path: str | Path = DEFAULT_COORDINATES_PATH) -> PinCoordinateMap:
        coordinate_path = Path(path)
        try:
            with coordinate_path.open(encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError as exc:
            raise PinCoordinateMapError(
                f"Pin coordinate file not found: {coordinate_path}. "
                "Add calibrated STM32-frame coordinates before probing."
            ) from exc
        except json.JSONDecodeError as exc:
            raise PinCoordinateMapError(f"Invalid JSON in pin coordinate file: {exc}") from exc
        if not isinstance(data, Mapping) or not isinstance(data.get("boards"), Mapping):
            raise PinCoordinateMapError("Coordinate file must contain a 'boards' object keyed by board name.")
        return cls(data["boards"])

    def coordinate_for(self, board: str, pin_id: str) -> PinCoordinate:
        pins = self._boards.get(board)
        if pins is None:
            raise PinCoordinateMapError(f"No coordinates are configured for board {board!r}.")
        normalized_id = pin_id.strip().upper()
        if normalized_id not in pins:
            raise PinCoordinateMapError(
                f"No coordinate entry exists for pin ID {pin_id!r} on {board!r}."
            )
        coordinate = pins[normalized_id]
        if coordinate is None:
            raise PinCoordinateMapError(
                f"Coordinates for pin ID {pin_id!r} on {board!r} have not been filled in."
            )
        return coordinate

    @staticmethod
    def _finite_coordinate(value: object, board: str, label: str, axis: str) -> float:
        if isinstance(value, bool):
            raise PinCoordinateMapError(f"Coordinate {axis} for {board}/{label} must be a finite number.")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise PinCoordinateMapError(
                f"Coordinate {axis} for {board}/{label} must be a finite number."
            ) from exc
        if not math.isfinite(numeric):
            raise PinCoordinateMapError(f"Coordinate {axis} for {board}/{label} must be a finite number.")
        return numeric

    @staticmethod
    def _optional_text(value: object, board: str, pin_id: str, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise PinCoordinateMapError(f"{field} for {board}/{pin_id} must be a non-empty string.")
        return value.strip()


def load_pin_coordinate_map(path: str | Path = DEFAULT_COORDINATES_PATH) -> PinCoordinateMap:
    return PinCoordinateMap.from_json_file(path)