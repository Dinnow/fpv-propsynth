"""Betaflight-style stick (gimbal) overlay rendered from log rcCommand data.

Draws two square boxes with a crosshair and a moving dot, matching the look of
Betaflight Blackbox Explorer's stick display:

  * Mode 2 (default): left box  = yaw (x) + throttle (y),
                      right box = roll (x) + pitch (y).

The overlay is drawn directly onto BGR video frames (OpenCV) so it can be
composited and re-encoded together with the flight video.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Coral dot like Betaflight Blackbox Explorer (BGR for OpenCV).
_DOT_BGR = (107, 107, 255)
_LINE_BGR = (150, 150, 150)
_TEXT_BGR = (235, 235, 235)
_FILL_BGR = (20, 20, 20)

# Position presets -> (anchor_x, anchor_y) as fractions of frame size for the
# CENTER of the whole two-box group.
_POS_PRESETS = {
    "bottom-center": (0.50, 0.86),
    "bottom-left": (0.20, 0.86),
    "bottom-right": (0.80, 0.86),
    "top-center": (0.50, 0.16),
    "top-left": (0.20, 0.16),
    "top-right": (0.80, 0.16),
    "center": (0.50, 0.50),
}


@dataclass
class StickLayout:
    position: str = "bottom-center"  # preset key, or "" when using pos_frac
    pos_frac: tuple | None = None    # (x,y) 0..1 center override
    box_frac: float = 0.12           # box side as fraction of frame height
    opacity: float = 0.35            # box fill opacity (0..1)
    mode: int = 2                    # transmitter stick mode (1..4)
    show_labels: bool = True         # microsecond value labels + "Mode N"
    scale: float = 1.0               # extra size multiplier


def _stick_axes(mode, roll, pitch, yaw, thr_norm):
    """Return ((lx, ly), (rx, ry)) each in -1..1 (screen: +y = up)."""
    r = np.clip(roll / 500.0, -1, 1)
    p = np.clip(pitch / 500.0, -1, 1)
    y = np.clip(yaw / 500.0, -1, 1)
    t = np.clip(2.0 * thr_norm - 1.0, -1, 1)   # 0..1 -> -1..1
    # Mode 2 is by far the most common (throttle+yaw on the left stick).
    if mode == 1:
        return (y, p), (r, t)
    if mode == 3:
        return (r, t), (y, p)
    if mode == 4:
        return (r, p), (y, t)
    return (y, t), (r, p)  # Mode 2 (default)


def _geometry(fw, fh, layout: StickLayout):
    box = int(layout.box_frac * fh * layout.scale)
    gap = int(box * 0.14)
    if layout.pos_frac is not None:
        cx = int(layout.pos_frac[0] * fw)
        cy = int(layout.pos_frac[1] * fh)
    else:
        fx, fy = _POS_PRESETS.get(layout.position, _POS_PRESETS["bottom-center"])
        cx, cy = int(fx * fw), int(fy * fh)
    total_w = 2 * box + gap
    left_x = cx - total_w // 2
    top_y = cy - box // 2
    return box, gap, left_x, top_y


def draw_overlay(frame, roll, pitch, yaw, throttle, layout: StickLayout):
    """Draw the stick overlay onto ``frame`` (BGR, modified in place)."""
    import cv2

    fh, fw = frame.shape[:2]
    box, gap, left_x, top_y = _geometry(fw, fh, layout)
    thr_norm = (throttle - 1000.0) / 1000.0
    (lx, ly), (rx, ry) = _stick_axes(layout.mode, roll, pitch, yaw, thr_norm)

    boxes = [(left_x, top_y, lx, ly), (left_x + box + gap, top_y, rx, ry)]
    margin = int(box * 0.10)
    half = box / 2.0 - margin

    # Semi-transparent fill for both boxes in one overlay pass.
    ov = frame.copy()
    for bx, by, _, _ in boxes:
        cv2.rectangle(ov, (bx, by), (bx + box, by + box), _FILL_BGR, -1)
    cv2.addWeighted(ov, layout.opacity, frame, 1 - layout.opacity, 0, frame)

    for bx, by, nx, ny in boxes:
        ccx, ccy = bx + box // 2, by + box // 2
        cv2.rectangle(frame, (bx, by), (bx + box, by + box), _LINE_BGR, 1, cv2.LINE_AA)
        cv2.line(frame, (ccx, by), (ccx, by + box), _LINE_BGR, 1, cv2.LINE_AA)
        cv2.line(frame, (bx, ccy), (bx + box, ccy), _LINE_BGR, 1, cv2.LINE_AA)
        dx = int(ccx + nx * half)
        dy = int(ccy - ny * half)   # screen y is inverted
        rad = max(4, int(box * 0.06))
        cv2.circle(frame, (dx, dy), rad, _DOT_BGR, -1, cv2.LINE_AA)

    if layout.show_labels:
        fs = max(0.4, box / 320.0)
        us = lambda v: f"{int(round(1500 + v))} us"
        thr_us = lambda: f"{int(round(throttle))} us"
        lb, rb = boxes[0], boxes[1]
        # Left box: yaw (left side) + throttle (below); Mode label inside bottom.
        cv2.putText(frame, us(yaw), (lb[0] - int(box * 0.62), lb[1] + box // 2 + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, _TEXT_BGR, 1, cv2.LINE_AA)
        cv2.putText(frame, thr_us(), (lb[0] + int(box * 0.12), lb[1] + box + int(box * 0.22)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, _TEXT_BGR, 1, cv2.LINE_AA)
        cv2.putText(frame, f"Mode {layout.mode}",
                    (lb[0] + int(box * 0.18), lb[1] + box - int(box * 0.06)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs * 0.85, _LINE_BGR, 1, cv2.LINE_AA)
        # Right box: roll (right side) + pitch (below).
        cv2.putText(frame, us(roll), (rb[0] + box + int(box * 0.10), rb[1] + box // 2 + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, _TEXT_BGR, 1, cv2.LINE_AA)
        cv2.putText(frame, us(pitch), (rb[0] + int(box * 0.12), rb[1] + box + int(box * 0.22)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, _TEXT_BGR, 1, cv2.LINE_AA)
    return frame
