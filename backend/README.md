# Backend Hardware Services

Hardware-facing code lives here; the FastAPI server remains in `website/` and
the standalone instrument implementation remains in `oscilloscope/`.

## STM32 Serial

`robot_serial.py` sends one newline-terminated `MOVE X Y` command and waits for
a complete response line. `READY` acknowledges a successful move; `ERROR` and
other responses fail the request. Configure `STM32_SERIAL_PORT`,
`STM32_BAUD_RATE`, and `STM32_RESPONSE_TIMEOUT_SECONDS` in `backend/config.py`.

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
the STM32, then processes each pin in order: move and await `READY`, capture
and classify, return to `0,0` and await `READY`. A failure stops the sequence
for manual correction. Poll `GET /api/jobs/{job_id}` for progress and results.