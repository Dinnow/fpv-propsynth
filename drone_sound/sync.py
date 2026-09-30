"""Detect alignment anchor events in a blackbox log.

Without shared clocks or video audio, we align the synthesized sound to the
video by matching a physical event visible in both. In the log we can find:

  * ``arm``     -- motors first leave the disarmed/min state (props start).
  * ``spoolup`` -- average motor output first rises decisively above the
                   armed-idle baseline (throttle-up).
  * ``takeoff`` -- vertical acceleration first departs 1 g in a sustained way
                   after spool-up (leaving the ground) -- the natural match to
                   the video's first big frame motion.

All times are seconds from the start of the log.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .decode import LogData


@dataclass
class LogEvents:
    arm: float | None
    spoolup: float | None
    takeoff: float | None
    throttle: float | None = None

    def get(self, name: str) -> float | None:
        return {"arm": self.arm, "spoolup": self.spoolup,
                "takeoff": self.takeoff, "throttle": self.throttle}.get(name)


def _first_sustained(mask: np.ndarray, time_s: np.ndarray, hold_s: float) -> float | None:
    """Return the time of the first sample that starts a ``hold_s`` run of True."""
    if not mask.any():
        return None
    n = len(mask)
    i = 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j] and (time_s[j] - time_s[i]) < hold_s:
                j += 1
            if (time_s[min(j, n - 1)] - time_s[i]) >= hold_s or j >= n:
                return float(time_s[i])
            i = j
        else:
            i += 1
    return None


def detect_throttle_onset(
    log: LogData,
    margin_frac: float = 0.02,
    hold_s: float = 0.2,
) -> float | None:
    """Time when the throttle stick first leaves idle (rcCommand[3] rises).

    This is the anchor used to match the video's on-screen (OSD) throttle
    starting to increase. Idle baseline = median throttle over the first 2 s.
    """
    t = log.time_s
    thr = log.throttle
    if thr.size == 0 or float(np.nanmax(thr)) <= 0:
        return None
    idle = float(np.median(thr[t < t[0] + 2.0])) if (t < t[0] + 2.0).any() else float(thr[0])
    span = max(float(np.nanmax(thr)) - idle, 1.0)
    threshold = idle + margin_frac * span
    return _first_sustained(thr > threshold, t, hold_s=hold_s)


def detect_log_events(log: LogData) -> LogEvents:
    t = log.time_s
    motor_avg = log.motor.mean(axis=1)
    span = max(log.motor_max - log.motor_min, 1.0)

    # --- arm: motors first rise above the disarmed floor -----------------
    floor = log.motor_min + 0.05 * span
    arm_mask = motor_avg > floor
    arm = _first_sustained(arm_mask, t, hold_s=0.2)

    # --- spool-up: rise well above the armed-idle baseline ---------------
    # Baseline = median motor over the first second of armed flight.
    arm_t = arm if arm is not None else float(t[0])
    base_win = (t >= arm_t) & (t < arm_t + 1.0)
    baseline = float(np.median(motor_avg[base_win])) if base_win.any() else float(motor_avg[0])
    threshold = baseline + 0.12 * span
    spool_mask = (t > arm_t) & (motor_avg > threshold)
    spoolup = _first_sustained(spool_mask, t, hold_s=0.3)

    # --- takeoff: liftoff = first vertical-accel departure from 1 g shortly
    #     after spool-up (searched in a short window so it captures leaving the
    #     ground, not a later mid-flight punch). Falls back to spool-up.
    takeoff = None
    spool_t = spoolup if spoolup is not None else arm_t
    if np.any(log.accz != 0):
        dev = np.abs(log.accz - 1.0)
        window = (t >= spool_t) & (t <= spool_t + 4.0)
        to_mask = window & (dev > 0.18)
        takeoff = _first_sustained(to_mask, t, hold_s=0.12)
    if takeoff is None:
        takeoff = spoolup

    throttle = detect_throttle_onset(log)

    return LogEvents(arm=arm, spoolup=spoolup, takeoff=takeoff, throttle=throttle)


def pick_log_event(events: LogEvents, name: str) -> float:
    """Return the requested event time, falling back sensibly if missing."""
    order = {
        "throttle": ["throttle", "spoolup", "arm", "takeoff"],
        "takeoff": ["takeoff", "spoolup", "arm"],
        "spoolup": ["spoolup", "throttle", "takeoff", "arm"],
        "arm": ["arm", "throttle", "spoolup", "takeoff"],
    }.get(name, ["throttle", "spoolup", "arm", "takeoff"])
    for key in order:
        val = events.get(key)
        if val is not None:
            return val
    return 0.0
