"""Estimate the board's 3D position and orientation relative to the camera.

Uses the pins found by pin_finder.py as 2D<->3D correspondences: the physical
pin positions (mm) are known from the board layout, so cv2.solvePnP recovers
the board pose from a single photo - no depth sensor needed.

Camera frame (OpenCV convention): X right, Y down, Z out of the lens.
Board frame: origin at the centre of the pin layout, X along the long edge
(towards the double-row header), Y towards the analog header, Z into the board.

Usage:
  python board_pose.py samples/IMG_4899.HEIC --out pose.jpg
  python board_pose.py photo.jpg --calib camera_calib.npz   # after calibrating
"""

from __future__ import annotations

import argparse
import json

import cv2
import numpy as np
from PIL import Image

import pin_finder as pf

SENSOR_DIAG_35MM = 43.27  # diagonal of a 36x24 mm frame


def camera_matrix_from_exif(path: str, img_shape) -> np.ndarray:
    """Approximate intrinsics from the 35mm-equivalent focal length in EXIF."""
    exif = Image.open(path).getexif().get_ifd(0x8769)
    f35 = exif.get(0xA405)  # FocalLengthIn35mmFilm
    if not f35:
        raise RuntimeError("No 35mm focal length in EXIF - pass --calib or --fx.")
    h, w = img_shape[:2]
    f_px = f35 * np.hypot(w, h) / SENSOR_DIAG_35MM
    return np.array([[f_px, 0, w / 2], [0, f_px, h / 2], [0, 0, 1]], np.float64)


def load_calib(path: str, img_shape):
    """Load a calibration npz and rescale it if the image size differs from calibration."""
    c = np.load(path)
    K, dist = c["camera_matrix"].copy(), c["dist_coeffs"]
    cw, ch = c["image_size"]
    h, w = img_shape[:2]
    if (w, h) != (cw, ch):
        if abs(w / h - cw / ch) > 0.01:
            raise RuntimeError(f"Calibrated at {cw}x{ch} but image is {w}x{h} (different aspect/orientation).")
        K[:2] *= w / cw
    return K, dist


def template_mm(board: str) -> np.ndarray:
    """Physical (x, y, 0) position in mm of each template pin, origin at layout centre."""
    prof = pf.BOARDS[board]
    sx, sy = prof["mm_per_ref"]
    xy = np.array([[t[1], t[2]] for t in prof["template"]()], np.float64)
    mm = xy * [sx, sy]
    mm -= (mm.min(0) + mm.max(0)) / 2
    return np.hstack([mm, np.zeros((len(mm), 1))])


def estimate_pose(img, K, dist=None, board="metro_grand_central"):
    pins, _ = pf.find_pins(img, board)
    obj = template_mm(board)
    ok = np.array([p.detected for p in pins])
    img_pts = np.array([[p.x, p.y] for p in pins], np.float64)
    if ok.sum() < 6:
        raise RuntimeError(f"Only {ok.sum()} pins detected; need at least 6 for a pose.")
    dist = np.zeros(5) if dist is None else dist

    good, rvec, tvec = cv2.solvePnP(obj[ok], img_pts[ok], K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not good:
        raise RuntimeError("solvePnP failed.")
    rvec, tvec = cv2.solvePnPRefineLM(obj[ok], img_pts[ok], K, dist, rvec, tvec)

    proj, _ = cv2.projectPoints(obj[ok], rvec, tvec, K, dist)
    reproj_err = float(np.linalg.norm(proj.reshape(-1, 2) - img_pts[ok], axis=1).mean())

    R, _ = cv2.Rodrigues(rvec)
    normal = R[:, 2]  # board Z axis in camera frame
    tilt = float(np.degrees(np.arccos(abs(normal[2]))))
    yaw = float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))

    t = tvec.ravel()
    pins_cam = obj @ R.T + t
    pins_3d = [{"label": p.label, "header": p.header, "detected": p.detected,
                "x": float(x), "y": float(y), "z": float(z)}
               for p, (x, y, z) in zip(pins, pins_cam)]
    return {
        "pins_3d": pins_3d,  # each pin's position in the camera frame, mm
        "position_mm": {"x": float(t[0]), "y": float(t[1]), "z": float(t[2])},
        "distance_mm": float(np.linalg.norm(t)),
        "tilt_deg": tilt,            # angle between board plane and image plane
        "yaw_deg": yaw,              # in-plane rotation of the board's long axis
        "rotation_matrix": R.tolist(),
        "rvec": rvec.ravel().tolist(),
        "tvec": t.tolist(),
        "reprojection_error_px": reproj_err,
        "pins_used": int(ok.sum()),
    }, pins


def draw_pose(img, pose, K, dist=None, axis_mm=40):
    out = img.copy()
    dist = np.zeros(5) if dist is None else dist
    rvec, tvec = np.array(pose["rvec"]), np.array(pose["tvec"])
    cv2.drawFrameAxes(out, K, dist, rvec, tvec, axis_mm, max(4, img.shape[1] // 300))
    p = pose["position_mm"]
    lines = [f"distance {pose['distance_mm']:.1f} mm",
             f"x {p['x']:.1f}  y {p['y']:.1f}  z {p['z']:.1f} mm",
             f"tilt {pose['tilt_deg']:.1f} deg  yaw {pose['yaw_deg']:.1f} deg"]
    s = img.shape[1] / 1500
    for i, line in enumerate(lines):
        org = (int(30 * s), int((60 + 55 * i) * s))
        cv2.putText(out, line, org, cv2.FONT_HERSHEY_SIMPLEX, 1.4 * s, (0, 0, 0), int(8 * s), cv2.LINE_AA)
        cv2.putText(out, line, org, cv2.FONT_HERSHEY_SIMPLEX, 1.4 * s, (255, 255, 255), int(3 * s), cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--board", default="metro_grand_central", choices=list(pf.BOARDS))
    ap.add_argument("--calib", help="npz with camera_matrix and dist_coeffs from a checkerboard calibration")
    ap.add_argument("--fx", type=float, help="override focal length in pixels")
    ap.add_argument("--out", default="pose.jpg")
    ap.add_argument("--json", help="write pose as JSON")
    ap.add_argument("--pin", nargs="*", default=[], help="print 3D position of these pins, e.g. D13 SDA")
    args = ap.parse_args()

    img = pf.load_image(args.image)
    dist = None
    if args.calib:
        K, dist = load_calib(args.calib, img.shape)
    else:
        K = camera_matrix_from_exif(args.image, img.shape)
    if args.fx:
        K[0, 0] = K[1, 1] = args.fx

    pose, _ = estimate_pose(img, K, dist, args.board)
    p = pose["position_mm"]
    print(f"Board centre relative to camera: x={p['x']:.1f} y={p['y']:.1f} z={p['z']:.1f} mm "
          f"(distance {pose['distance_mm']:.1f} mm)")
    print(f"Tilt {pose['tilt_deg']:.1f} deg, yaw {pose['yaw_deg']:.1f} deg, "
          f"reprojection error {pose['reprojection_error_px']:.2f} px over {pose['pins_used']} pins "
          f"(focal length {K[0, 0]:.0f} px)")
    for name in args.pin:
        for q in pose["pins_3d"]:
            if q["label"].upper() == name.upper():
                print(f"  {q['label']:6s} ({q['header']}): x={q['x']:.1f} y={q['y']:.1f} z={q['z']:.1f} mm")

    cv2.imwrite(args.out, draw_pose(img, pose, K, dist))
    print(f"Pose image -> {args.out}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(pose, f, indent=2)


if __name__ == "__main__":
    main()
