"""Newline-delimited serial client for the STM32 probe arm."""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from typing import Callable

from backend import config


class SerialConfigurationError(ValueError):
    """Raised when required STM32 serial settings are missing or invalid."""


class SerialCommunicationError(RuntimeError):
    """Raised when the STM32 cannot complete or acknowledge a command."""


class SerialResponseTimeout(SerialCommunicationError):
    """Raised when the STM32 does not respond before the configured deadline."""


class SerialProtocolError(SerialCommunicationError):
    """Raised when the STM32 returns a response outside the agreed protocol."""


@dataclass(frozen=True)
class SerialSettings:
    port: str
    baudrate: int
    response_timeout_seconds: float = 30.0

    @classmethod
    def from_config(cls) -> SerialSettings:
        port = config.STM32_SERIAL_PORT
        baudrate = config.STM32_BAUD_RATE
        timeout = config.STM32_RESPONSE_TIMEOUT_SECONDS

        if not isinstance(port, str) or not port.strip():
            raise SerialConfigurationError(
                "Set STM32_SERIAL_PORT in backend/config.py, for example 'COM4'."
            )
        if isinstance(baudrate, bool) or not isinstance(baudrate, int) or baudrate <= 0:
            raise SerialConfigurationError("STM32_BAUD_RATE must be greater than zero.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise SerialConfigurationError("STM32_RESPONSE_TIMEOUT_SECONDS must be a positive number.")
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise SerialConfigurationError("STM32_RESPONSE_TIMEOUT_SECONDS must be a positive number.")

        return cls(port=port.strip(), baudrate=baudrate, response_timeout_seconds=timeout)


SerialFactory = Callable[..., object]


class STM32SerialClient:
    def __init__(self, settings: SerialSettings, serial_factory: SerialFactory | None = None):
        self.settings = settings
        self._serial_factory = serial_factory
        self._connection = None

    def connect(self) -> None:
        if self._connection is not None:
            return
        factory = self._serial_factory
        if factory is None:
            try:
                import serial
            except ImportError as exc:
                raise SerialCommunicationError(
                    "PySerial is not installed. Install the backend requirements."
                ) from exc
            factory = serial.Serial

        try:
            self._connection = factory(
                self.settings.port,
                self.settings.baudrate,
                timeout=0.1,
                write_timeout=2.0,
            )
        except Exception as exc:
            raise SerialCommunicationError(
                f"Could not open STM32 serial port {self.settings.port}: {exc}"
            ) from exc

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> STM32SerialClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def check_connection(self) -> None:
        """Open and close the configured port without sending a motion command."""
        self.connect()
        self.close()

    def move(self, x: float, y: float) -> str:
        x_text = self._format_coordinate(x)
        y_text = self._format_coordinate(y)
        return self._motion_command(f"MOVE {x_text} {y_text}")

    def home(self) -> str:
        """Return to the pose declared by POSE, without assuming XY 0,0 is reachable."""
        return self._motion_command("HOME")

    def _motion_command(self, command: str) -> str:
        if self._connection is None:
            raise SerialCommunicationError("STM32 serial client is not connected.")
        data = (command + "\n").encode("ascii")

        try:
            self._connection.reset_input_buffer()
            written = self._connection.write(data)
            if written != len(data):
                raise SerialCommunicationError("Incomplete STM32 command write.")
            self._connection.flush()
        except Exception as exc:
            raise SerialCommunicationError(f"Failed to send STM32 command: {exc}") from exc

        deadline = time.monotonic() + self.settings.response_timeout_seconds
        moving = False
        while True:
            response = self._read_response(deadline)
            keyword = response.upper()
            if not keyword or keyword.startswith("READY "):
                continue  # Ignore empty lines and the startup banner.
            if keyword == "READY":
                return response  # Compatibility with older firmware.
            if keyword == "OK DONE" or keyword.startswith("OK DONE "):
                return response  # A target requiring no pulses is already complete.
            if keyword == "OK MOVING" or keyword.startswith("OK MOVING "):
                moving = True
                continue  # An accepted command has not yet finished moving.
            if keyword == "EVENT DONE" and moving:
                return response
            if (keyword in ("ERR", "ERROR") or keyword.startswith(("ERR ", "ERROR ", "EVENT STOPPED"))):
                raise SerialCommunicationError(f"STM32 reported {response}.")
            raise SerialProtocolError(f"Unexpected STM32 response: {response!r}.")

    def _read_response(self, deadline: float) -> str:
        response = bytearray()
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            self._connection.timeout = min(0.1, max(remaining, 0.001))
            try:
                char = self._connection.read(1)
            except Exception as exc:
                raise SerialCommunicationError(f"Failed reading STM32 response: {exc}") from exc
            if not char:
                continue
            if char == b"\n":
                try:
                    return response.decode("ascii").strip()
                except UnicodeDecodeError as exc:
                    raise SerialProtocolError("STM32 response must be ASCII.") from exc
            response.extend(char)
            if len(response) > 256:
                raise SerialProtocolError("STM32 response exceeded 256 bytes without a newline.")

        raise SerialResponseTimeout(
            f"STM32 did not complete the command within {self.settings.response_timeout_seconds:g} seconds."
        )

    @staticmethod
    def _format_coordinate(value: float) -> str:
        if isinstance(value, bool):
            raise ValueError("Coordinates must be finite numbers.")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Coordinates must be finite numbers.") from exc
        if not math.isfinite(numeric):
            raise ValueError("Coordinates must be finite numbers.")
        text = f"{numeric:.6f}".rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text


def main() -> int:
    try:
        settings = SerialSettings.from_config()
        with STM32SerialClient(settings) as client:
            print(f"STM32 serial port is available: {settings.port} at {settings.baudrate} baud.")
    except (SerialConfigurationError, SerialCommunicationError) as exc:
        print(f"STM32 serial check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
