# Audit

Hackathon project: a robot arm with an oscilloscope probe moves to chosen pins
of a microcontroller to measure and decode their signals. A phone photo is used
to label every pin so the user can pick which ones to probe.

```bash
python run.py        # Windows: py run.py   - then scan the QR code with the phone
```

**How the website, phone pairing and pin detection work: [HOW_IT_WORKS.md](HOW_IT_WORKS.md)**

Work is split across branches by subsystem:

| Branch | Contents |
| --- | --- |
| `main` | Integrated, working version of the project |
| `robot-arm` | Robot arm control |
| `computer-vision` | Board / pin detection from phone photos (`computer-vision/`) |
| `website` | Web front end |
| `waveform` | Waveform capture and analysis |

Each subsystem lives in its own top-level folder so branches can be merged into `main` without conflicts.
