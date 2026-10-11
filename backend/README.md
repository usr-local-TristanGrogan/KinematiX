# Backend Hardware Services

Hardware-facing code lives here; the FastAPI server remains in `website/` and
the standalone instrument implementation remains in `oscilloscope/`.

## STM32 Serial

The STM32 firmware and standalone motor tools live in [`../robot-arm/`](../robot-arm/README.md).
`robot_serial.py` sends a newline-terminated `MOVE X Y` command and waits for
`OK MOVING ...` followed by `EVENT DONE`, or immediate `OK DONE ...` if no pulses
are needed. `ERR ...` and `EVENT STOPPED ...` fail the request. The acceptance
reply alone never starts signal capture. Older firmware's standalone `READY`
completion reply is also accepted. Configure `STM32_SERIAL_PORT`,
`STM32_BAUD_RATE`, and `STM32_RESPONSE_TIMEOUT_SECONDS` in `backend/config.py`.

Before an automatic job, configure the arm's measured lengths and pulse settings,
physically place it in the known starting pose, then send `POSE 0 90` and `ARM`.
The STM32 boots disarmed and the backend does not invent calibration values or
automatically declare a physical pose. Close the standalone arrow controller
before the backend opens the same serial port. XY coordinates are millimeters
relative to the shoulder shaft. See the firmware README for the setup commands.

Run a no-motion connection check with:

```powershell
python -m backend.robot_serial
```

## Pin Coordinates

Fill in `backend/pin_coordinates.json` with one calibrated X/Y entry per
selected pin. Replace `SET_TO_STM32_UNITS` and enter coordinates in the STM32
firmware's coordinate frame and origin. The template has one unique ID per
physical pin so repeated labels remain distinct. The map loader rejects
unfilled coordinates rather than guessing a target.

## Scope Pipeline

`scope_pipeline.run_capture_pipeline(job_id, pin_label)` selects VISA resource
0 by default, checks that the device answers `*IDN?`, captures the waveform,
extracts edges, and runs the protocol detector on that capture's `edges.csv`.
Outputs are stored under `oscilloscope/captures/<job_id>/<pin_label>/`.

## Probe Jobs

The local-only `POST /api/jobs/start` endpoint accepts a board ID and selected
pin IDs. It validates all coordinates, verifies the oscilloscope, connects to
the STM32, then processes each pin in order: move and await completion, capture
and classify, send `HOME` and await completion. `HOME` returns to the pose declared
by `POSE`; XY `0,0` may be unreachable for a two-link arm. A failure stops the sequence
for manual correction. Poll `GET /api/jobs/{job_id}` for progress and results.
