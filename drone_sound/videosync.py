"""Video-side helpers: duration probe, first-motion detection, audio alignment."""

from __future__ import annotations

import json
import shutil
import subprocess

import numpy as np


def probe_duration(path: str) -> float:
    """Return the video duration in seconds via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found on PATH (install ffmpeg).")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        check=True, capture_output=True, text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def detect_first_motion(
    path: str,
    sample_fps: float = 10.0,
    downscale_width: int = 160,
    min_time: float = 0.0,
) -> float:
    """Return the time (s) of the first significant motion in the video.

    Uses OpenCV frame-differencing on downscaled, grayscale frames sampled at
    ``sample_fps``. "Motion" = mean absolute frame difference rising well above
    the early-baseline (static camera on the ground) level. Raises a clear error
    if OpenCV is unavailable.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "opencv-python is required for automatic video motion detection. "
            "Install it (`pip install opencv-python`) or align manually with "
            "--video-offset / --video-event-time."
        ) from exc

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(int(round(fps / sample_fps)), 1)

    times, diffs = [], []
    prev = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            h, w = frame.shape[:2]
            scale = downscale_width / max(w, 1)
            small = cv2.resize(frame, (downscale_width, max(int(h * scale), 1)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                diffs.append(float(np.mean(np.abs(gray - prev))))
                times.append(idx / fps)
            prev = gray
        idx += 1
    cap.release()

    if len(diffs) < 3:
        raise RuntimeError("Video too short or unreadable for motion detection.")

    d = np.asarray(diffs)
    ts = np.asarray(times)
    # Baseline from the calmest 20% of early samples.
    early = d[: max(len(d) // 4, 3)]
    base = float(np.median(early))
    mad = float(np.median(np.abs(early - base))) or 1e-3
    threshold = base + 6.0 * mad
    for i, (tt, dv) in enumerate(zip(ts, d)):
        if tt >= min_time and dv > threshold:
            return float(tt)
    # Nothing crossed: return the single largest jump.
    return float(ts[int(np.argmax(d))])


# Default throttle-number ROI as fractions of frame size, validated on a DJI
# 1080p OSD (center throttle value). Override with an absolute (x,y,w,h).
_DEFAULT_ROI_FRAC = (0.500, 0.532, 0.104, 0.102)


def detect_osd_throttle_onset(
    path: str,
    roi=None,
    sample_fps: float = 10.0,
    baseline_s: float = 1.5,
    factor: float = 2.5,
    hold_s: float = 0.3,
    verbose: bool = False,
) -> float:
    """Return the time (s) the on-screen OSD throttle value first starts rising.

    Watches a region around the OSD throttle number: builds a white-text mask
    (bright, low-saturation pixels) and measures its frame-to-frame change. While
    throttle is 0 the digit is static (small change); when it starts increasing
    the change jumps. Onset = first sustained jump above a baseline threshold.

    ``roi`` is an absolute ``(x, y, w, h)`` in pixels; if None a resolution-scaled
    default tuned for the DJI center-throttle OSD is used.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "opencv-python is required for OSD throttle detection. Install it or "
            "align manually with --video-offset / --video-event-time."
        ) from exc

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if roi is None:
        fx, fy, fw, fh = _DEFAULT_ROI_FRAC
        roi = (int(fx * w), int(fy * h), int(fw * w), int(fh * h))
    x, y, rw, rh = (int(v) for v in roi)
    step = max(int(round(fps / sample_fps)), 1)

    times, diffs = [], []
    prev = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            crop = frame[y:y + rh, x:x + rw]
            if crop.size:
                hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                white = ((hsv[:, :, 2] > 190) & (hsv[:, :, 1] < 70)).astype(np.uint8)
                if prev is not None:
                    diffs.append(int(np.count_nonzero(white != prev)))
                    times.append(idx / fps)
                prev = white
        idx += 1
    cap.release()

    if len(diffs) < 5:
        raise RuntimeError("Could not sample enough frames for OSD detection.")

    d = np.asarray(diffs, dtype=np.float64)
    ts = np.asarray(times)
    base = d[ts < (ts[0] + baseline_s)]
    if base.size < 2:
        base = d[: max(len(d) // 5, 2)]
    b_mean = float(np.mean(base))
    b_std = float(np.std(base))
    threshold = max(b_mean * factor, b_mean + 5.0 * b_std)

    if verbose:
        print(f"[osd] roi={roi} baseline_mean={b_mean:.0f} std={b_std:.0f} "
              f"threshold={threshold:.0f}")

    above = d > threshold
    # First index that begins a sustained (hold_s) run above threshold.
    n = len(above)
    i = 0
    while i < n:
        if above[i]:
            j = i
            while j < n and above[j] and (ts[j] - ts[i]) < hold_s:
                j += 1
            if j >= n or (ts[min(j, n - 1)] - ts[i]) >= hold_s:
                return float(ts[i])
            i = j
        else:
            i += 1
    # Nothing sustained: fall back to the single largest jump.
    return float(ts[int(np.argmax(d))])


def align_audio(
    audio: np.ndarray,
    sr: int,
    delta: float,
    video_duration: float,
) -> np.ndarray:
    """Map synthesized ``audio`` (log timeline) onto the video timeline.

    Video time ``v`` corresponds to log time ``v + delta``. Output covers
    ``[0, video_duration]`` at ``sr``; regions where ``v + delta`` falls outside
    the synthesized audio are silent.
    """
    audio = np.asarray(audio)
    stereo = audio.ndim == 2
    n_out = int(round(video_duration * sr))
    shape = (n_out, audio.shape[1]) if stereo else (n_out,)
    out = np.zeros(shape, dtype=audio.dtype)

    src_start = int(round(delta * sr))           # log sample index at video t=0
    n_src = audio.shape[0]
    lo = max(0, -src_start)                       # first output index with valid src
    hi = min(n_out, n_src - src_start)            # one past last valid output index
    if hi > lo:
        out[lo:hi] = audio[src_start + lo: src_start + hi]
    return out
