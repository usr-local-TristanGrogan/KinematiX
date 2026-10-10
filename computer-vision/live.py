"""Live pin detection + board pose from a camera (e.g. iPhone via Continuity Camera).

  python live.py --camera 0 --calib camera_calib.npz --pin D13 SDA

Keys: S = save the current frame to frames/, Q = quit.
"""

from __future__ import annotations

import argparse
import os
import time

import cv2
import numpy as np

import board_pose as bp
import pin_finder as pf
from calibrate import open_camera


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--calib", help="camera_calib.npz from calibrate.py (strongly recommended)")
    ap.add_argument("--fx", type=float, help="focal length in px, if not calibrated")
    ap.add_argument("--board", default="metro_grand_central", choices=list(pf.BOARDS))
    ap.add_argument("--pin", nargs="*", default=[], help="pins to highlight and report in 3D")
    args = ap.parse_args()

    cap = open_camera(args.camera, args.width, args.height)
    ok, frame = cap.read()
    h, w = frame.shape[:2]
    dist = None
    if args.calib:
        K, dist = bp.load_calib(args.calib, frame.shape)
    else:
        fx = args.fx or w  # rough guess (~53 deg horizontal FOV); distances will be off until calibrated
        K = np.array([[fx, 0, w / 2], [0, fx, h / 2], [0, 0, 1]], np.float64)
        print(f"WARNING: no --calib, using guessed focal length {fx:.0f} px; distances are approximate.")

    os.makedirs("frames", exist_ok=True)
    wanted = {p.upper() for p in args.pin}
    t_last = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        disp = frame
        try:
            pose, pins = bp.estimate_pose(frame, K, dist, args.board)
            disp = pf.annotate(frame, pins, args.pin)
            disp = bp.draw_pose(disp, pose, K, dist, axis_mm=30)
            y = int(h * 0.25)
            for q in pose["pins_3d"]:
                if q["label"].upper() in wanted:
                    txt = f"{q['label']}: x={q['x']:.1f} y={q['y']:.1f} z={q['z']:.1f} mm"
                    cv2.putText(disp, txt, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5, cv2.LINE_AA)
                    cv2.putText(disp, txt, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)
                    y += 35
        except RuntimeError as e:
            disp = frame.copy()
            cv2.putText(disp, str(e)[:90], (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        now = time.time()
        fps, t_last = 1 / max(now - t_last, 1e-6), now
        cv2.putText(disp, f"{fps:.1f} fps", (w - 160, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.imshow("pins", disp)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("s"):
            path = time.strftime("frames/%Y%m%d-%H%M%S.png")
            cv2.imwrite(path, frame)
            print(f"saved {path}")
        elif k in (ord("q"), 27):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
