"""Locate and label the pins of a microcontroller board in a phone photo.

Pipeline:
  1. Load the photo (HEIC/JPEG/PNG), honouring EXIF rotation.
  2. Find the board by its solder-mask colour and rectify it to a top-down view.
  3. Overlay a pin template for the board (known header layout), trying both
     180-degree orientations and keeping the one that best matches real pads.
  4. Snap each template pin to the nearest detected pad, then fit a homography
     from template -> detected pads, so every pin (detected or not) lands on
     the board's actual geometry.
  5. Map everything back to the original photo coordinates.

Usage:
  python pin_finder.py IMG_4899.HEIC --out annotated.jpg --json pins.json
  python pin_finder.py IMG_4899.HEIC --highlight SDA SCL D13
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass


# --------------------------------------------------------------------------
# Board profiles
# --------------------------------------------------------------------------
# Template coordinates are in a reference frame where the rectified board is
# REF_W x REF_H pixels, laid out in the standard Arduino orientation:
# USB on the left, digital headers along the top, power/analog along the bottom.

REF_W, REF_H = 2000, 1050


def _row(labels, x0, y0, dx, dy, header):
    return [(lab, x0 + i * dx, y0 + i * dy, header) for i, lab in enumerate(labels)]


def metro_grand_central_template():
    p = 50.2  # 0.1" pitch in reference pixels
    pins = []
    pins += _row(["SCL", "SDA", "AREF", "GND"] + [f"D{n}" for n in range(13, 7, -1)],
                 368, 75, p, 0, "digital_high")
    pins += _row([f"D{n}" for n in range(7, -1, -1)], 901, 75, p, 0, "digital_low")
    pins += _row([f"D{n}" for n in range(14, 22)], 1355, 75, p, 0, "comms")

    vp = 48.9
    pins += _row(["5V"] + [f"D{n}" for n in range(22, 53, 2)] + ["GND"],
                 1855, 77, 0, vp, "double_row_even")
    pins += _row(["5V"] + [f"D{n}" for n in range(23, 54, 2)] + ["GND"],
                 1906, 77, 0, vp, "double_row_odd")

    bp = 49.4
    pins += _row(["NC", "IOREF", "RESET", "3V3", "5V", "GND", "GND", "VIN"],
                 555, 1000, bp, 0, "power")
    pins += _row([f"A{n}" for n in range(0, 8)], 1000, 1000, bp, 0, "analog_low")
    pins += _row([f"A{n}" for n in range(8, 16)], 1445, 1000, bp, 0, "analog_high")

    pins += [("MISO", 1250, 469, "icsp"), ("5V", 1303, 469, "icsp"),
             ("SCK", 1250, 518, "icsp"), ("MOSI", 1303, 518, "icsp"),
             ("RESET", 1250, 566, "icsp"), ("GND", 1303, 566, "icsp")]
    return pins


BOARDS = {
    "metro_grand_central": {
        "template": metro_grand_central_template,
        # HSV range (OpenCV: H 0-180) of the solder mask in well-lit photos;
        # an adaptive colour mask covers webcam streams and odd white balance.
        "mask_hsv": ((115, 60, 40), (165, 255, 255)),
        "pitch": 50.0,
        # mm per reference pixel: x from the 0.1" header pitch, y from the
        # 1.9" spacing between the top and bottom header rows
        "mm_per_ref": (2.54 / 50.2, 48.26 / 925),
    },
}


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------

def load_image(path) -> np.ndarray:
    """Load from a path or file-like object (JPEG/PNG/HEIC), applying EXIF rotation."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def white_balance(img: np.ndarray) -> np.ndarray:
    """White-patch balance using the brightest unsaturated pixels (silkscreen, labels)."""
    f = img.astype(np.float32)
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1]
    v = f.max(2)
    cand = (sat < 60) & (v < 254)
    if cand.sum() < 500:
        return img
    sel = cand & (v >= np.percentile(v[cand], 99))
    ref = f[sel].mean(0)
    gain = np.clip(ref.mean() / np.maximum(ref, 1), 0.6, 1.6)
    return np.clip(f * gain, 0, 255).astype(np.uint8)


def gold_mask(hsv: np.ndarray) -> np.ndarray:
    return cv2.inRange(hsv, (14, 90, 120), (35, 255, 255))


def pad_cluster_hull(img: np.ndarray, gold: np.ndarray):
    """Convex hull of the largest cluster of pad-sized gold blobs, or None."""
    n, _, stats, cent = cv2.connectedComponentsWithStats(gold)
    area = img.shape[0] * img.shape[1]
    idx = [i for i in range(1, n) if 5e-6 * area < stats[i, cv2.CC_STAT_AREA] < 1.5e-3 * area]
    if len(idx) < 8:
        return None
    pts = cent[idx].astype(np.float32)
    near = np.linalg.norm(pts[:, None] - pts[None], axis=2) < 0.06 * min(img.shape[:2])
    label = -np.ones(len(pts), int)
    for seed in range(len(pts)):
        if label[seed] >= 0:
            continue
        label[seed], stack = seed, [seed]
        while stack:
            j = stack.pop()
            for k in np.where(near[j] & (label < 0))[0]:
                label[k] = seed
                stack.append(k)
    biggest = np.bincount(label).argmax()
    return cv2.convexHull(pts[label == biggest])


def adaptive_board_mask(img: np.ndarray, hsv: np.ndarray, gold: np.ndarray):
    """Mask of pixels matching the board colour, learned from inside the pad cluster.

    Handles white-balance shifts and other board colours that a fixed HSV range misses.
    """
    hull = pad_cluster_hull(img, gold)
    if hull is None:
        return None
    region = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(region, hull.astype(np.int32), 255)
    silkscreen = (hsv[..., 1] < 50) & (hsv[..., 2] > 150)
    sample = (region > 0) & (gold == 0) & ~silkscreen & (hsv[..., 2] > 40)
    if sample.sum() < 500:
        return None
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    ref = np.median(lab[sample], 0)
    d_ab = np.hypot(lab[..., 1] - ref[1], lab[..., 2] - ref[2])
    return ((d_ab < 12) & (np.abs(lab[..., 0] - ref[0]) < 50)).astype(np.uint8) * 255


def board_rect(mask: np.ndarray, gold: np.ndarray):
    """Corners of the mask blob containing the most gold pads (TL/TR/BR/BL, long side horizontal)."""
    mask = mask | gold
    k = max(5, int(min(mask.shape) * 0.01) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k // 2 | 1,) * 2))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if 0.02 * mask.size <= cv2.contourArea(c) <= 0.9 * mask.size]
    if not cnts:
        return None

    def gold_inside(c):
        region = np.zeros_like(mask)
        cv2.drawContours(region, [c], -1, 255, -1)
        return cv2.countNonZero(gold & region)

    box = cv2.boxPoints(cv2.minAreaRect(max(cnts, key=gold_inside)))
    s, d = box.sum(1), np.diff(box, axis=1).ravel()
    tl, br, tr, bl = box[np.argmin(s)], box[np.argmax(s)], box[np.argmin(d)], box[np.argmax(d)]
    if np.linalg.norm(tr - tl) < np.linalg.norm(bl - tl):
        return np.float32([bl, tl, tr, br])
    return np.float32([tl, tr, br, bl])


def pad_rect_candidates(img: np.ndarray, gold: np.ndarray, txy: np.ndarray) -> list:
    """Board corners extrapolated from the rectangle around the pad cluster.

    Works when the board colour is too dim or washed out to segment, since the
    headers sit along the board edges. Returns both 180-degree interpretations.
    """
    hull = pad_cluster_hull(img, gold)
    if hull is None or len(hull) < 4:
        return []
    box = cv2.boxPoints(cv2.minAreaRect(hull))
    s, d = box.sum(1), np.diff(box, axis=1).ravel()
    tl, br, tr, bl = box[np.argmin(s)], box[np.argmax(s)], box[np.argmin(d)], box[np.argmax(d)]
    rect = np.float32([bl, tl, tr, br] if np.linalg.norm(tr - tl) < np.linalg.norm(bl - tl) else [tl, tr, br, bl])
    (x0, y0), (x1, y1) = txy.min(0), txy.max(0)
    pads_ref = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    board_ref = np.float32([[0, 0], [REF_W, 0], [REF_W, REF_H], [0, REF_H]])
    cands = []
    for rot in (0, 2):
        H = cv2.getPerspectiveTransform(pads_ref, np.roll(rect, -rot, axis=0))
        cands.append(np.roll(cv2.perspectiveTransform(board_ref[None], H)[0], rot, axis=0))
    return cands


def find_board_candidates(img: np.ndarray, mask_hsv, txy: np.ndarray | None = None) -> list:
    """Candidate board corner sets: fixed colour range, adaptive colour mask, pad-cluster rectangle."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gold = gold_mask(hsv)
    masks = [cv2.inRange(hsv, *mask_hsv)]
    adaptive = adaptive_board_mask(img, hsv, gold)
    if adaptive is not None:
        masks.append(adaptive)
    cands = [r for r in (board_rect(m, gold) for m in masks) if r is not None]
    if txy is not None:
        cands += pad_rect_candidates(img, gold, txy)
    if not cands:
        raise RuntimeError("No board found - move the phone closer or improve lighting.")
    return cands


def pad_mask(warp: np.ndarray) -> np.ndarray:
    """Binary mask of likely pads: gold/copper, or bright unsaturated (glare on tin)."""
    hsv = cv2.cvtColor(warp, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (10, 40, 110), (40, 255, 255))
    shiny = cv2.inRange(hsv, (0, 0, 200), (180, 70, 255))
    return cv2.bitwise_or(gold, shiny)


def pad_centroids(mask: np.ndarray, pitch: float) -> np.ndarray:
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
    lo, hi = (0.06 * pitch) ** 2, (1.2 * pitch) ** 2
    keep = [i for i in range(1, n) if lo < stats[i, cv2.CC_STAT_AREA] < hi]
    return cent[keep].astype(np.float32) if keep else np.zeros((0, 2), np.float32)


def snap(template_xy: np.ndarray, pads: np.ndarray, radius: float):
    """For each template point, index of nearest pad within radius (or -1)."""
    if len(pads) == 0:
        return np.full(len(template_xy), -1), np.full(len(template_xy), np.inf)
    d = np.linalg.norm(template_xy[:, None, :] - pads[None, :, :], axis=2)
    idx, dist = d.argmin(1), d.min(1)
    idx[dist > radius] = -1
    return idx, dist


def fit_template(txy: np.ndarray, pads: np.ndarray, pitch: float):
    """Align template pins to detected pads; returns (fitted positions, pad index per pin or -1).

    Starts with a similarity transform (rotation/scale/shift) so a partial set of
    matches can't swing unmatched headers off the board, then loosens to an
    affine, and only uses a full homography once matches cover most pins.
    """
    xy = txy.copy()
    idx, _ = snap(xy, pads, 0.45 * pitch)
    for step, radius in (("similarity", 0.45), ("affine", 0.4), ("homography", 0.3)):
        ok = idx >= 0
        if ok.sum() < 6:
            break
        src, dst = xy[ok], pads[idx[ok]]
        if step == "homography":
            if ok.sum() < 0.6 * len(xy):
                break
            M, _ = cv2.findHomography(src, dst, cv2.RANSAC, 0.2 * pitch)
            if M is not None:
                xy = cv2.perspectiveTransform(xy[None], M)[0]
        else:
            est = cv2.estimateAffinePartial2D if step == "similarity" else cv2.estimateAffine2D
            M, _ = est(src, dst, method=cv2.RANSAC, ransacReprojThreshold=0.2 * pitch)
            if M is not None:
                xy = cv2.transform(xy[None], M)[0]
        idx, _ = snap(xy, pads, radius * pitch)
    return xy.astype(np.float32), idx


# --------------------------------------------------------------------------
# Main detection
# --------------------------------------------------------------------------

@dataclass
class Pin:
    label: str
    header: str
    x: float          # pixel coords in the original photo
    y: float
    detected: bool    # True if a real pad was found here (vs. inferred from layout)
    box: list         # 4 [x, y] corners of the pin's bounding box in the photo


def find_pins(img: np.ndarray, board: str = "metro_grand_central"):
    prof = BOARDS[board]
    template = prof["template"]()
    labels = [t[0] for t in template]
    headers = [t[3] for t in template]
    txy = np.float32([[t[1], t[2]] for t in template])
    pitch = prof["pitch"]

    img = white_balance(img)
    dst = np.float32([[0, 0], [REF_W, 0], [REF_W, REF_H], [0, REF_H]])

    # Every board outline candidate x both 180-degree orientations; keep the
    # fit that lands on the most pads.
    best = None
    for corners in find_board_candidates(img, prof["mask_hsv"], txy):
        for rot in (0, 2):
            H = cv2.getPerspectiveTransform(np.roll(corners, -rot, axis=0), dst)
            warp = cv2.warpPerspective(img, H, (REF_W, REF_H))
            pads = pad_centroids(pad_mask(warp), pitch)
            fitted, idx = fit_template(txy, pads, pitch)
            score = int((idx >= 0).sum())
            if best is None or score > best[0]:
                best = (score, H, warp, fitted, idx, corners)
    _, H, warp, warp_xy, idx, corners = best

    # Report the fitted layout position: glare can split a pad into fragments,
    # so individual blob centroids are noisier than the global fit.
    ok = idx >= 0

    Hinv = np.linalg.inv(H)
    photo_xy = cv2.perspectiveTransform(warp_xy[None], Hinv)[0]
    half = 0.4 * pitch
    square = np.float32([[-half, -half], [half, -half], [half, half], [-half, half]])
    boxes = cv2.perspectiveTransform((warp_xy[:, None, :] + square[None]).reshape(1, -1, 2), Hinv)
    boxes = boxes.reshape(-1, 4, 2)
    pins = [Pin(l, h, float(x), float(y), bool(o), b.round(1).tolist())
            for l, h, (x, y), o, b in zip(labels, headers, photo_xy, ok, boxes)]
    return pins, {"warp": warp, "warp_xy": warp_xy, "corners": corners, "H": H}


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

HEADER_COLOURS = {
    "digital_high": (255, 200, 0), "digital_low": (255, 120, 0), "comms": (0, 200, 255),
    "double_row_even": (255, 0, 200), "double_row_odd": (200, 80, 255), "power": (0, 0, 255), "analog_low": (0, 255, 0),
    "analog_high": (0, 180, 90), "icsp": (255, 255, 255),
}


def annotate(img, pins, highlight=()):
    out = img.copy()
    r = max(4, int(min(img.shape[:2]) * 0.004))
    font = 0.0005 * min(img.shape[:2])
    hl = {h.upper() for h in highlight}
    for p in pins:
        c = HEADER_COLOURS.get(p.header, (255, 255, 255))
        pt = (int(p.x), int(p.y))
        thick = -1 if p.detected else max(2, r // 3)
        if hl and p.label.upper() not in hl:
            cv2.circle(out, pt, r // 2, (90, 90, 90), -1)
            continue
        cv2.circle(out, pt, r * (2 if hl else 1), c, thick)
        cv2.putText(out, p.label, (pt[0] + r + 2, pt[1] - r), cv2.FONT_HERSHEY_SIMPLEX,
                    font * (1.6 if hl else 1), (0, 0, 0), max(3, r // 2), cv2.LINE_AA)
        cv2.putText(out, p.label, (pt[0] + r + 2, pt[1] - r), cv2.FONT_HERSHEY_SIMPLEX,
                    font * (1.6 if hl else 1), c, max(1, r // 5), cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--board", default="metro_grand_central", choices=list(BOARDS))
    ap.add_argument("--out", default="annotated.jpg")
    ap.add_argument("--json", help="write pin list as JSON")
    ap.add_argument("--warp-out", help="also save the rectified top-down board image")
    ap.add_argument("--highlight", nargs="*", default=[], help="only label these pins, e.g. SDA SCL D13")
    args = ap.parse_args()

    img = load_image(args.image)
    pins, dbg = find_pins(img, args.board)

    n_det = sum(p.detected for p in pins)
    print(f"Found board; {n_det}/{len(pins)} pins matched to real pads "
          f"({len(pins) - n_det} inferred from layout).")
    for h in args.highlight:
        for p in pins:
            if p.label.upper() == h.upper():
                print(f"  {p.label:6s} ({p.header}) at x={p.x:.0f}, y={p.y:.0f}")

    cv2.imwrite(args.out, annotate(img, pins, args.highlight))
    print(f"Annotated image -> {args.out}")
    if args.warp_out:
        w = dbg["warp"].copy()
        for p, (x, y) in zip(pins, dbg["warp_xy"]):
            cv2.circle(w, (int(x), int(y)), 10, HEADER_COLOURS[p.header], -1 if p.detected else 2)
        cv2.imwrite(args.warp_out, w)
    if args.json:
        with open(args.json, "w") as f:
            json.dump([asdict(p) for p in pins], f, indent=2)
        print(f"Pin list -> {args.json}")


if __name__ == "__main__":
    main()
