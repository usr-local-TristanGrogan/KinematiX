# Audit

Hackathon project: a robot arm with an oscilloscope probe moves to chosen pins
of a microcontroller to measure and decode their signals. A phone photo is used
to label every pin so the user can pick which ones to probe.

```bash
python run.py        # Windows: py run.py   - then scan the QR code with the phone
```

**How the website, phone pairing and pin detection work: [HOW_IT_WORKS.md](HOW_IT_WORKS.md)**

STM32 Nucleo F446RE firmware, serial tools, motor wiring, and calibration instructions
are in [`robot-arm/`](robot-arm/README.md).

```bash
make -C robot-arm         # build the firmware
make -C robot-arm test    # run laptop tests
make -C robot-arm flash   # program the connected Nucleo through ST-LINK
python3 robot-arm/tools/arrow_control.py  # motor jogging and speed control
```

Work is split across branches by subsystem:

| Branch | Contents |
| --- | --- |
| `main` | Integrated, working version of the project |
| `robot-arm` | Robot arm control (`robot-arm/`) |
| `computer-vision` | Board / pin detection from phone photos (`computer-vision/`) |
| `website` | Web front end |
| `waveform` | Waveform capture and analysis |

Each subsystem lives in its own top-level folder so branches can be merged into `main` without conflicts.
