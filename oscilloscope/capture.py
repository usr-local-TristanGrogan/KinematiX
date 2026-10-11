
import csv
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable

import numpy as np


# ---------------- Configuration ----------------

CHANNEL = "CHAN3"
OUTPUT_ROOT = Path(__file__).resolve().parent / "captures"
CAPTURE_DURATION_SECONDS = 2.4
HORIZONTAL_SCALE_SECONDS_PER_DIV = 0.1
ACQUISITION_MEMORY_DEPTH = "24M"
EXPECTED_POINT_COUNT = 24_000_000
TRIGGER_WAIT_TIMEOUT_SECONDS = 60
WAVEFORM_READY_TIMEOUT_SECONDS = 10
WAVEFORM_CHUNK_POINTS = 100_000
WAVEFORM_BLOCK_ATTEMPTS = 3
WAVEFORM_BLOCK_RETRY_DELAY_SECONDS = 0.25

# Set to a VISA resource string to skip interactive selection.
# Examples:
# USB:   USB0::0x1AB1::0x04CE::DS1ZA...::INSTR
# LAN:   TCPIP0::192.168.1.100::INSTR
VISA_RESOURCE = None

# Fixed hysteresis thresholds for a 3.3 V logic signal (about 30% / 70% VDD).
EDGE_LOW_THRESHOLD_VOLTS = 1.0
EDGE_HIGH_THRESHOLD_VOLTS = 2.3


# ---------------- Instrument connection ----------------

def available_scope_resources():
    try:
        import pyvisa
    except ImportError as exc:
        raise RuntimeError("PyVISA is not installed. Install the backend requirements.") from exc

    rm = pyvisa.ResourceManager()
    try:
        resources = tuple(rm.list_resources())
    finally:
        rm.close()
    if not resources:
        raise RuntimeError(
            "No VISA instruments found. Check the USB/LAN connection and VISA installation."
        )
    return resources


def connect_scope(resource_index=0, resource_name=None):
    try:
        import pyvisa
    except ImportError as exc:
        raise RuntimeError("PyVISA is not installed. Install the backend requirements.") from exc

    rm = pyvisa.ResourceManager()
    try:
        resources = tuple(rm.list_resources())
        if not resources:
            raise RuntimeError(
                "No VISA instruments found. Check the USB/LAN connection and VISA installation."
            )
        if resource_name is not None:
            if resource_name not in resources:
                raise RuntimeError(f"Configured VISA resource is not available: {resource_name}")
            selected_resource = resource_name
        else:
            if not isinstance(resource_index, int) or not 0 <= resource_index < len(resources):
                raise RuntimeError(
                    f"VISA resource index {resource_index!r} is unavailable; "
                    f"found {len(resources)} resource(s)."
                )
            selected_resource = resources[resource_index]
        scope = rm.open_resource(selected_resource)
    except Exception:
        rm.close()
        raise
    return rm, scope


def check_scope_identity(resource_index=0, resource_name=None):
    """Open the selected VISA instrument, verify its identity, then close it."""
    rm, scope = connect_scope(resource_index, resource_name)
    try:
        instrument_id = scope.query("*IDN?").strip()
        if not instrument_id:
            raise RuntimeError("The selected VISA resource did not return an instrument ID.")
        return instrument_id
    finally:
        scope.close()
        rm.close()


def query_optional(scope, command):
    """Read a setting without failing the entire capture."""
    try:
        return scope.query(command).strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def wait_for_single_acquisition(scope):
    deadline = time.monotonic() + TRIGGER_WAIT_TIMEOUT_SECONDS
    last_status = "unknown"
    saw_active_state = False

    while time.monotonic() < deadline:
        last_status = scope.query(":TRIG:STAT?").strip().upper()
        if last_status != "STOP":
            saw_active_state = True
        elif saw_active_state:
            return
        time.sleep(0.1)

    scope.write(":STOP")
    raise TimeoutError(
        "The scope did not complete a single acquisition within "
        f"{TRIGGER_WAIT_TIMEOUT_SECONDS} seconds. Last trigger status: "
        f"{last_status!r}; observed active state: {saw_active_state}. "
        "Check that the trigger level and edge match the input signal."
    )


def wait_for_full_record_preamble(scope):
    deadline = time.monotonic() + WAVEFORM_READY_TIMEOUT_SECONDS
    preamble_response = "not queried"

    while time.monotonic() < deadline:
        preamble_response = scope.query(":WAV:PRE?").strip()
        try:
            preamble = [float(value) for value in preamble_response.split(",")]
        except ValueError:
            preamble = []

        if len(preamble) >= 10 and int(preamble[2]) == EXPECTED_POINT_COUNT:
            return preamble
        time.sleep(0.1)

    raise RuntimeError(
        "The single acquisition stopped, but the waveform preamble did "
        f"not report {EXPECTED_POINT_COUNT:,} points within "
        f"{WAVEFORM_READY_TIMEOUT_SECONDS} seconds. Last preamble: "
        f"{preamble_response!r}."
    )


# ---------------- Waveform acquisition ----------------

def acquire_waveform(scope, on_acquisition_complete=None):
    scope.write(":STOP")
    scope.write(f":ACQ:MDEP {ACQUISITION_MEMORY_DEPTH}")
    scope.write(f":WAV:SOUR {CHANNEL}")
    scope.write(f":TRIG:EDGE:SOUR {CHANNEL}")
    scope.write(":WAV:MODE RAW")
    scope.write(":WAV:FORM BYTE")

    # Keep 100 ms/div; 24M points at 10 MSa/s span 2.4 seconds.
    scope.write(f":TIM:SCAL {HORIZONTAL_SCALE_SECONDS_PER_DIV}")
    scope.write(":SING")
    wait_for_single_acquisition(scope)
    if on_acquisition_complete is not None:
        on_acquisition_complete()

    # Preamble fields:
    # format, type, points, count, xinc, xorig, xref,
    # yinc, yorig, yref
    preamble = wait_for_full_record_preamble(scope)

    (
        waveform_format,
        waveform_type,
        reported_points,
        average_count,
        x_increment,
        x_origin,
        x_reference,
        y_increment,
        y_origin,
        y_reference,
    ) = preamble[:10]

    point_count = int(reported_points)
    point_count_source = "waveform preamble"

    if point_count != EXPECTED_POINT_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_POINT_COUNT:,} waveform points, but the "
            f"preamble reports {point_count:,}. Check RAW waveform mode "
            "and the selected memory depth."
        )

    # Read the full acquisition in bounded blocks. A single WAV:DATA? query
    # can return only the first 1200 displayed points on this scope family.
    chunks = []
    for first_point in range(1, point_count + 1, WAVEFORM_CHUNK_POINTS):
        last_point = min(
            first_point + WAVEFORM_CHUNK_POINTS - 1, point_count
        )
        expected_chunk_size = last_point - first_point + 1
        for attempt in range(WAVEFORM_BLOCK_ATTEMPTS):
            scope.write(f":WAV:STAR {first_point}")
            scope.write(f":WAV:STOP {last_point}")
            chunk = scope.query_binary_values(
                ":WAV:DATA?",
                datatype="B",
                is_big_endian=False,
                container=np.array,
                header_fmt="ieee",
            )
            if chunk.size == expected_chunk_size:
                break
            if attempt + 1 < WAVEFORM_BLOCK_ATTEMPTS:
                time.sleep(WAVEFORM_BLOCK_RETRY_DELAY_SECONDS)

        if chunk.size != expected_chunk_size:
            raise RuntimeError(
                "Incomplete RAW waveform block: requested points "
                f"{first_point}-{last_point} "
                "(" 
                f"{expected_chunk_size} points, received {chunk.size} "
                f"after {WAVEFORM_BLOCK_ATTEMPTS} attempts). Preamble "
                f"reported {int(reported_points)} points; download count "
                f"came from {point_count_source}. Acquisition memory "
                f"query: {query_optional(scope, ':ACQ:MDEP?')}."
            )
        chunks.append(chunk)

    raw = np.concatenate(chunks)

    if raw.size == 0:
        raise RuntimeError("The scope returned no waveform samples.")

    actual_duration = (raw.size - 1) * x_increment
    if not np.isclose(
        actual_duration,
        CAPTURE_DURATION_SECONDS,
        rtol=0,
        atol=2 * x_increment,
    ):
        raise RuntimeError(
            f"The {raw.size:,}-point waveform spans {actual_duration:.9f} "
            f"seconds, not the requested {CAPTURE_DURATION_SECONDS:.1f}. "
            "Check the scope's actual sample rate and timebase."
        )

    # Convert sample indices into time and ADC values into volts.
    sample_index = np.arange(raw.size, dtype=np.float64)

    timestamps = (
        (sample_index - x_reference) * x_increment + x_origin
    )

    volts = (
        raw.astype(np.float64) - y_origin - y_reference
    ) * y_increment

    metadata = {
        "instrument_id": query_optional(scope, "*IDN?"),
        "channel": CHANNEL,
        "capture_time_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "acquisition_state_before_download": "stopped after single trigger",
        "waveform_format": waveform_format,
        "waveform_type": waveform_type,
        "reported_points": int(reported_points),
        "point_count_used_for_download": point_count,
        "point_count_source": point_count_source,
        "downloaded_points": int(raw.size),
        "average_count": average_count,
        "x_increment_seconds": x_increment,
        "x_origin_seconds": x_origin,
        "x_reference": x_reference,
        "y_increment_volts": y_increment,
        "y_origin": y_origin,
        "y_reference": y_reference,
        "sample_rate_reported_by_scope": query_optional(
            scope, ":ACQ:SRAT?"
        ),
        "memory_depth_reported_by_scope": query_optional(
            scope, ":ACQ:MDEP?"
        ),
        "timebase_seconds_per_div": query_optional(
            scope, ":TIM:SCAL?"
        ),
        "channel_scale_volts_per_div": query_optional(
            scope, f":{CHANNEL}:SCAL?"
        ),
        "channel_offset": query_optional(
            scope, f":{CHANNEL}:OFFS?"
        ),
        "channel_coupling": query_optional(
            scope, f":{CHANNEL}:COUP?"
        ),
        "requested_capture_duration_seconds": CAPTURE_DURATION_SECONDS,
        "horizontal_scale_seconds_per_div": query_optional(
            scope, ":TIM:SCAL?"
        ),
        "waveform_download_chunk_points": WAVEFORM_CHUNK_POINTS,
        "threshold_method": "fixed-voltage hysteresis",
        "protocol_decoding_performed": False,
    }

    return raw, timestamps, volts, metadata


# ---------------- Edge-event extraction ----------------

def extract_edges(timestamps, volts):
    """
    Extract candidate transitions using fixed logic-level thresholds.

    This is only signal preprocessing, not UART decoding.
    Original samples are preserved separately.
    """
    if len(volts) < 3:
        return [], None

    low_threshold = EDGE_LOW_THRESHOLD_VOLTS
    high_threshold = EDGE_HIGH_THRESHOLD_VOLTS
    if low_threshold >= high_threshold:
        raise ValueError(
            "EDGE_LOW_THRESHOLD_VOLTS must be below "
            "EDGE_HIGH_THRESHOLD_VOLTS."
        )

    state = None
    edges = []

    for i, voltage in enumerate(volts):
        if state is None:
            if voltage <= low_threshold:
                state = 0
            elif voltage >= high_threshold:
                state = 1
            continue

        if state == 0 and voltage >= high_threshold:
            edges.append({
                "time_s": float(timestamps[i]),
                "from_level": 0,
                "to_level": 1,
                "edge": "rising",
                "sample_index": i,
                "voltage_v": float(voltage),
            })
            state = 1

        elif state == 1 and voltage <= low_threshold:
            edges.append({
                "time_s": float(timestamps[i]),
                "from_level": 1,
                "to_level": 0,
                "edge": "falling",
                "sample_index": i,
                "voltage_v": float(voltage),
            })
            state = 0

    thresholds = {
        "low_threshold_volts": low_threshold,
        "high_threshold_volts": high_threshold,
        "method": "fixed-voltage hysteresis",
    }

    return edges, thresholds


# ---------------- File export ----------------

def save_capture(raw, timestamps, volts, metadata, edges, thresholds, output_dir=None):
    if output_dir is None:
        capture_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = OUTPUT_ROOT / capture_name
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)

    # Compact numerical archive. Includes the original ADC codes.
    np.savez_compressed(
        output_dir / "waveform.npz",
        timestamps_s=timestamps,
        voltage_v=volts,
        raw_adc_codes=raw,
    )

    # Human-readable waveform.
    with open(
        output_dir / "waveform.csv", "w", newline=""
    ) as file:
        writer = csv.writer(file)
        writer.writerow(["time_s", "voltage_v", "raw_adc_code"])
        writer.writerows(
            (
                f"{timestamp:.7f}",
                f"{voltage:.7f}",
                int(adc_code),
            )
            for timestamp, voltage, adc_code in zip(timestamps, volts, raw)
        )

    # Edge list; no bit or protocol interpretation.
    edge_fields = [
        "time_s", "from_level", "to_level",
        "edge", "sample_index", "voltage_v",
        "time_since_previous_edge_s",
    ]

    previous_edge_time = None
    for edge in edges:
        edge["time_since_previous_edge_s"] = (
            "" if previous_edge_time is None
            else edge["time_s"] - previous_edge_time
        )
        previous_edge_time = edge["time_s"]

    with open(
        output_dir / "edges.csv", "w", newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=edge_fields)
        writer.writeheader()
        writer.writerows(
            {
                **edge,
                "time_s": f"{edge['time_s']:.7f}",
                "voltage_v": f"{edge['voltage_v']:.7f}",
                "time_since_previous_edge_s": (
                    ""
                    if edge["time_since_previous_edge_s"] == ""
                    else f"{edge['time_since_previous_edge_s']:.7f}"
                ),
            }
            for edge in edges
        )

    metadata["sample_count"] = int(len(raw))
    metadata["capture_duration_seconds"] = (
        float(timestamps[-1] - timestamps[0])
        if len(timestamps) > 1 else 0.0
    )
    metadata["voltage_min"] = float(np.min(volts))
    metadata["voltage_max"] = float(np.max(volts))
    metadata["voltage_mean"] = float(np.mean(volts))
    metadata["edge_count"] = len(edges)
    metadata["edge_thresholds"] = thresholds
    metadata["csv_time_decimal_places"] = 7

    with open(output_dir / "metadata.json", "w") as file:
        json.dump(metadata, file, indent=2)

    return output_dir


def capture_once(
    output_dir=None,
    resource_index=0,
    resource_name=None,
    extra_metadata=None,
    on_acquisition_complete: Callable[[], None] | None = None,
):
    """Acquire one waveform and save its raw data and extracted edges."""
    rm, scope = connect_scope(resource_index, resource_name)

    try:
        scope.timeout = 30000
        scope.chunk_size = 1024 * 1024

        instrument_id = scope.query("*IDN?").strip()
        if not instrument_id:
            raise RuntimeError("The selected VISA resource did not return an instrument ID.")

        raw, timestamps, volts, metadata = acquire_waveform(
            scope,
            on_acquisition_complete=on_acquisition_complete,
        )
        metadata["instrument_id"] = instrument_id
        if extra_metadata:
            metadata.update(extra_metadata)

        edges, thresholds = extract_edges(timestamps, volts)

        output_dir = save_capture(
            raw, timestamps, volts, metadata, edges, thresholds, output_dir
        )
        return {
            "output_dir": str(output_dir.resolve()),
            "waveform_npz": str((output_dir / "waveform.npz").resolve()),
            "waveform_csv": str((output_dir / "waveform.csv").resolve()),
            "edges_csv": str((output_dir / "edges.csv").resolve()),
            "metadata_json": str((output_dir / "metadata.json").resolve()),
            "sample_count": int(len(raw)),
            "edge_count": len(edges),
        }

    finally:
        scope.close()
        rm.close()


# ---------------- Main ----------------

def main():
    resources = available_scope_resources()
    print("Available VISA resources:")
    for index, resource in enumerate(resources):
        print(f"  {index}: {resource}")
    selection = int(input("Select your RIGOL resource number: "))
    if not 0 <= selection < len(resources):
        raise SystemExit(f"VISA resource index {selection} is unavailable.")
    print("Prepare the signal and trigger on the scope.")
    input("Press Enter to arm the single 24M-point acquisition...")

    result = capture_once(resource_name=resources[selection])
    print(f"\nCapture saved to: {result['output_dir']}")
    print(f"Samples: {result['sample_count']}")
    print(f"Transitions detected: {result['edge_count']}")
    print("No protocol decoding was performed.")


if __name__ == "__main__":
    main()
