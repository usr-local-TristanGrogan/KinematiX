"""Callable oscilloscope capture and protocol-analysis pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from oscilloscope import capture
from oscilloscope.scripts import detect_protocol


CAPTURES_ROOT = Path(__file__).resolve().parents[1] / "oscilloscope" / "captures"
_SAFE_PATH_PART = re.compile(r"[A-Za-z0-9_-]+\Z")


def run_capture_pipeline(
    job_id: str,
    pin_label: str,
    resource_index: int = 0,
    output_root: str | Path = CAPTURES_ROOT,
    pin_id: str | None = None,
    on_acquisition_complete: Callable[[], None] | None = None,
) -> dict:
    """Capture, extract edges, and classify one selected pin's signal."""
    _validate_path_part(job_id, "job ID")
    capture_id = pin_id or pin_label
    _validate_path_part(capture_id, "pin ID")
    output_dir = Path(output_root) / job_id / capture_id

    capture_result = capture.capture_once(
        output_dir=output_dir,
        resource_index=resource_index,
        extra_metadata={"job_id": job_id, "pin_id": capture_id, "pin_label": pin_label},
        on_acquisition_complete=on_acquisition_complete,
    )
    edge_file = Path(capture_result["edges_csv"])
    protocol_result = detect_protocol.analyze(edge_file)
    report_path = edge_file.parent / "protocol_detection.json"
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(protocol_result, file, indent=2)

    return {
        **capture_result,
        "protocol_detection_json": str(report_path.resolve()),
        "protocol_detection": protocol_result,
    }


def _validate_path_part(value: str, description: str) -> None:
    if not isinstance(value, str) or not _SAFE_PATH_PART.fullmatch(value):
        raise ValueError(f"{description} must contain only letters, numbers, '_' or '-'.")