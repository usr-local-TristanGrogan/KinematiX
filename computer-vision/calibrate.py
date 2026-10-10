"""Camera calibration with a printed checkerboard.

Gives the exact focal length / principal point / lens distortion, which
board_pose.py needs for accurate distances (pass the result with --calib).

1. Make and print the board (print at 100% / "actual size", no scaling):
     python calibrate.py make-board --out checkerboard.png
   Measure one square with a ruler and use that value for --square-mm.

2. Capture 15-25 views of the board, tilted and moved around the frame,
   with the SAME camera, lens and resolution you'll use for the robot:
     python calibrate.py live --camera 0 --square-mm 20      # SPACE = capture, Q = finish
   or from photos you took on the phone:
     python calibrate.py photos calib_photos/*.HEIC --square-mm 20
"""

from __future__ import annotations

import argparse
import glob

import cv2
import numpy as np

import pin_finder as pf

PATTERN = (9, 6)  # inner corners (columns, rows)


def make_board(out: str, square_mm: float, dpi: int = 300):
    px = int(round(square_mm / 25.4 * dpi))
    cols, rows = PATTERN[0] + 1, PATTERN[1] + 1
    margin = px // 2
    img = np.full((rows * px + 2 * margin, cols * px + 2 * margin), 255, np.uint8)
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                y, x = margin + r * px, margin + c * px
                img[y:y + px, x:x + px] = 0
    cv2.putText(img, f"{PATTERN[0]}x{PATTERN[1]} inner corners, {square_mm} mm squares - print at 100%",
                (margin, img.shape[0] - margin // 4), cv2.FONT_HERSHEY_SIMPLEX, px / 150, 0, 2)
    cv2.imwrite(out, img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    print(f"Wrote {out} ({img.shape[1]}x{img.shape[0]} px at {dpi} dpi). "
          "Print at 100% scale and measure a square.")


def find_corners(gray):
    flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    ok, corners = cv2.findChessboardCornersSB(gray, PATTERN, flags)
    return corners if ok else None


def calibrate(views, image_size, square_mm, out):
    objp = np.zeros((PATTERN[0] * PATTERN[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2) * square_mm
    obj = [objp] * len(views)
    rms, K, dist, _, _ = cv2.calibrateCamera(obj, views, image_size, None, None)
    np.savez(out, camera_matrix=K, dist_coeffs=dist, image_size=np.array(image_size), rms=rms)
    print(f"Calibrated from {len(views)} views at {image_size[0]}x{image_size[1]}: "
          f"RMS error {rms:.3f} px (good: < 0.5), fx={K[0, 0]:.1f} fy={K[1, 1]:.1f} "
          f"cx={K[0, 2]:.1f} cy={K[1, 2]:.1f}")
    print(f"Saved {out}")


def run_photos(paths, square_mm, out):
    views, size = [], None
    for p in paths:
        img = pf.load_image(p)
        s = (img.shape[1], img.shape[0])
        if size and s != size:
            print(f"skip {p}: size {s} differs from {size}")
            continue
        corners = find_corners(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        print(f"{p}: {'ok' if corners is not None else 'board not found'}")
        if corners is not None:
            size = s
            views.append(corners)
    if len(views) < 8:
        raise SystemExit(f"Only {len(views)} usable views; need at least 8 (15+ recommended).")
    calibrate(views, size, square_mm, out)


def run_live(camera, square_mm, out, width, height):
    cap = open_camera(camera, width, height)
    views, size = [], None
    print("SPACE = capture view, Q = calibrate and quit. Vary angle, distance and position.")
    while True:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("Camera stopped delivering frames.")
        size = (frame.shape[1], frame.shape[0])
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, None, fx=0.5, fy=0.5)
        corners = find_corners(small)
        disp = frame.copy()
        if corners is not None:
            cv2.drawChessboardCorners(disp, PATTERN, corners * 2, True)
        cv2.putText(disp, f"views: {len(views)}  (SPACE capture, Q finish)", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.imshow("calibrate", disp)
        k = cv2.waitKey(1) & 0xFF
        if k == ord(" "):
            full = find_corners(gray)
            if full is not None:
                views.append(full)
                print(f"captured view {len(views)}")
            else:
                print("board not found in full-res frame, try again")
        elif k in (ord("q"), 27):
            break
    cap.release()
    cv2.destroyAllWindows()
    if len(views) < 8:
        raise SystemExit(f"Only {len(views)} views; need at least 8 (15+ recommended).")
    calibrate(views, size, square_mm, out)


def open_camera(index, width=None, height=None):
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened() or not cap.read()[0]:
        raise SystemExit(f"Could not open camera {index}. Check camera permission for your terminal/Cursor "
                         "in System Settings > Privacy & Security > Camera, and try other indexes.")
    return cap


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-board")
    m.add_argument("--out", default="checkerboard.png")
    m.add_argument("--square-mm", type=float, default=20.0)
    lv = sub.add_parser("live")
    lv.add_argument("--camera", type=int, default=0)
    lv.add_argument("--width", type=int, default=1920)
    lv.add_argument("--height", type=int, default=1080)
    ph = sub.add_parser("photos")
    ph.add_argument("paths", nargs="+")
    for p in (lv, ph):
        p.add_argument("--square-mm", type=float, required=True, help="measured size of one printed square")
        p.add_argument("--out", default="camera_calib.npz")
    args = ap.parse_args()

    if args.cmd == "make-board":
        make_board(args.out, args.square_mm)
    elif args.cmd == "live":
        run_live(args.camera, args.square_mm, args.out, args.width, args.height)
    else:
        paths = [q for p in args.paths for q in (glob.glob(p) or [p])]
        run_photos(paths, args.square_mm, args.out)


if __name__ == "__main__":
    main()
