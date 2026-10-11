"""Single-job-at-a-time coordinator for serial motion and scope capture."""

from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend.pin_coordinates import PinCoordinate, PinCoordinateMapError, load_pin_coordinate_map
from backend.robot_serial import SerialSettings, STM32SerialClient
from backend.scope_pipeline import run_capture_pipeline
from oscilloscope.capture import check_scope_identity


class JobBusyError(RuntimeError):
    """Raised when another probe job is already using the hardware."""


class ManualJobStateError(RuntimeError):
    """Raised when a manual capture confirmation is not currently expected."""


class ProbeJobManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="probe-job")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._manual_ready_events: dict[str, threading.Event] = {}
        self._active_job_id: str | None = None

    def start(
        self,
        board: str,
        pin_ids: list[str],
        manual_mode: bool = False,
        pin_metadata: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if not pin_ids:
            raise ValueError("Select at least one pin.")
        normalized_ids = [pin_id.strip().lower() for pin_id in pin_ids if isinstance(pin_id, str)]
        if len(normalized_ids) != len(pin_ids) or any(not pin_id for pin_id in normalized_ids):
            raise ValueError("Every selected pin must have a valid pin ID.")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("The selected pin list contains duplicate pin IDs.")

        if manual_mode:
            if pin_metadata is None or any(pin_id not in pin_metadata for pin_id in normalized_ids):
                raise ValueError("Pin display information is missing for the manual job.")
            targets = [
                (pin_id, None, pin_metadata[pin_id])
                for pin_id in normalized_ids
            ]
            settings = None
        else:
            coordinates = load_pin_coordinate_map()
            targets = [
                (pin_id, coordinates.coordinate_for(board, pin_id), None)
                for pin_id in normalized_ids
            ]
            settings = SerialSettings.from_config()
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "board": board,
            "manual_mode": manual_mode,
            "status": "queued",
            "stage": "waiting for hardware",
            "total_pins": len(targets),
            "completed_pins": 0,
            "current_pin": None,
            "results": [],
            "error": None,
        }

        with self._lock:
            if self._active_job_id is not None:
                raise JobBusyError("A probe job is already using the hardware.")
            self._active_job_id = job_id
            self._jobs[job_id] = job
            try:
                self._executor.submit(self._run, job_id, targets, settings)
            except Exception:
                self._active_job_id = None
                self._jobs.pop(job_id, None)
                raise
        return copy.deepcopy(job)

    def confirm_manual_ready(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            event = self._manual_ready_events.get(job_id)
            if job is None or not job["manual_mode"] or job["stage"] != "manual_positioning" or event is None:
                raise ManualJobStateError("The job is not waiting for a manual probe confirmation.")
            job["stage"] = "capturing and analyzing"
            event.set()
            return copy.deepcopy(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    def _run(self, job_id: str, targets, settings: SerialSettings | None) -> None:
        current_pin = None
        stage = "checking oscilloscope"
        robot = None
        try:
            self._update(job_id, status="running", stage=stage)
            instrument_id = check_scope_identity(resource_index=0)
            self._update(job_id, scope_instrument=instrument_id)
            if not self.get(job_id)["manual_mode"]:
                stage = "connecting to STM32"
                self._update(job_id, stage=stage)
                robot = STM32SerialClient(settings)
                robot.connect()

            for pin_id, coordinate, metadata in targets:
                current_pin = metadata if metadata is not None else self._pin_details(pin_id, coordinate)
                if self.get(job_id)["manual_mode"]:
                    ready_event = threading.Event()
                    with self._lock:
                        self._manual_ready_events[job_id] = ready_event
                        self._jobs[job_id].update(
                            stage="manual_positioning",
                            current_pin=current_pin,
                        )
                    if not ready_event.wait(timeout=300):
                        raise TimeoutError("Timed out waiting for manual probe positioning confirmation.")
                    with self._lock:
                        self._manual_ready_events.pop(job_id, None)
                else:
                    stage = "moving to pin"
                    self._update(job_id, stage=stage, current_pin=current_pin)
                    robot.move(coordinate.x, coordinate.y)

                stage = "capturing signal"
                self._update(job_id, stage=stage, current_pin=current_pin)

                def acquisition_complete():
                    nonlocal stage
                    stage = "processing signal"
                    self._update(
                        job_id,
                        stage=stage,
                        current_pin=current_pin,
                    )

                capture_result = run_capture_pipeline(
                    job_id,
                    current_pin["label"],
                    resource_index=0,
                    pin_id=pin_id,
                    on_acquisition_complete=acquisition_complete,
                )

                self._append_result(job_id, {
                    **current_pin,
                    "capture": capture_result,
                })
                if robot is not None:
                    stage = "returning home"
                    self._update(job_id, stage=stage, current_pin=current_pin)
                    robot.home()

                self._increment_completed(job_id)
                current_pin = None

            self._update(
                job_id,
                status="complete",
                stage="complete",
                current_pin=None,
            )
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="failed",
                failed_stage=stage,
                current_pin=current_pin,
                error=str(exc),
            )
        finally:
            if robot is not None:
                robot.close()
            with self._lock:
                self._manual_ready_events.pop(job_id, None)
                if self._active_job_id == job_id:
                    self._active_job_id = None

    @staticmethod
    def _pin_details(pin_id: str, coordinate: PinCoordinate) -> dict[str, str]:
        return {
            "pin_id": pin_id,
            "label": coordinate.label or pin_id,
            "header": coordinate.header or "",
        }

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)

    def _append_result(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job_id]["results"].append(result)

    def _increment_completed(self, job_id: str) -> None:
        with self._lock:
            self._jobs[job_id]["completed_pins"] += 1


probe_jobs = ProbeJobManager()
