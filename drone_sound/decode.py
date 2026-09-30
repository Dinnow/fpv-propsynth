"""Decode a Betaflight/INAV blackbox (.bbl/.bfl) log into numpy arrays.

Primary path is the pure-Python ``orangebox`` parser so no external binary is
required. Only the fields we need for sound synthesis are collected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np


@dataclass
class LogData:
    """Decoded, per-frame flight data (all arrays share the same length)."""

    time_s: np.ndarray            # (N,) seconds, starting at 0
    motor: np.ndarray             # (N, 4) raw motor output (motorOutput min..max)
    eRPM: np.ndarray              # (N, 4) raw eRPM field (0 if not logged)
    throttle: np.ndarray          # (N,) rcCommand[3], typically ~1000..2000
    rc_roll: np.ndarray           # (N,) rcCommand[0], ~ -500..500
    rc_pitch: np.ndarray          # (N,) rcCommand[1], ~ -500..500
    rc_yaw: np.ndarray            # (N,) rcCommand[2], ~ -500..500
    vbat: np.ndarray              # (N,) pack voltage in volts (0 if unavailable)
    accz: np.ndarray              # (N,) accSmooth[2] in g (0 if unavailable)
    has_erpm: bool                # True if any eRPM field carries signal
    motor_min: float              # motorOutput lower bound (from header)
    motor_max: float              # motorOutput upper bound (from header)
    craft_name: str
    firmware: str

    @property
    def duration_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0]) if len(self.time_s) else 0.0

    @property
    def num_motors(self) -> int:
        return self.motor.shape[1]


def _find_indices(field_names):
    idx = {n: i for i, n in enumerate(field_names)}

    def motor_idxs(prefix):
        out = []
        for m in range(8):
            key = f"{prefix}[{m}]"
            if key in idx:
                out.append(idx[key])
        return out

    return idx, motor_idxs("motor"), motor_idxs("eRPM")


def _parse_header_meta(parser):
    """Best-effort extraction of motorOutput range, craft name, firmware."""
    motor_min, motor_max = 1000.0, 2000.0
    craft = ""
    firmware = ""
    headers = getattr(parser, "headers", None) or {}
    # orangebox exposes headers as a dict of str->value
    try:
        items = headers.items()
    except AttributeError:
        items = []
    for key, val in items:
        sval = str(val)
        if key == "motorOutput":
            m = re.findall(r"-?\d+", sval)
            if len(m) >= 2:
                motor_min, motor_max = float(m[0]), float(m[1])
        elif key == "Craft name":
            craft = sval
        elif key in ("Firmware revision", "Firmware type"):
            firmware = (firmware + " " + sval).strip()
    return motor_min, motor_max, craft, firmware


def load_log(path: str) -> LogData:
    """Decode ``path`` and return a :class:`LogData`.

    Uses the first flight session in the file if several are concatenated.
    """
    from orangebox import Parser

    parser = Parser.load(path)
    names = parser.field_names
    idx, motor_i, erpm_i = _find_indices(names)

    if "time" not in idx:
        raise ValueError("Log has no 'time' field; unsupported blackbox format.")
    if not motor_i:
        raise ValueError("Log has no motor[] fields; nothing to synthesize.")

    ti = idx["time"]
    thr_i = idx.get("rcCommand[3]")
    roll_i = idx.get("rcCommand[0]")
    pitch_i = idx.get("rcCommand[1]")
    yaw_i = idx.get("rcCommand[2]")
    vbat_i = idx.get("vbatLatest")
    accz_i = idx.get("accSmooth[2]")

    times, motors, erpms, thr, vbat, accz = [], [], [], [], [], []
    roll, pitch, yaw = [], [], []
    for frame in parser.frames():
        d = frame.data
        times.append(d[ti])
        motors.append([d[i] for i in motor_i])
        erpms.append([d[i] for i in erpm_i] if erpm_i else [0] * len(motor_i))
        thr.append(d[thr_i] if thr_i is not None else 0)
        roll.append(d[roll_i] if roll_i is not None else 0)
        pitch.append(d[pitch_i] if pitch_i is not None else 0)
        yaw.append(d[yaw_i] if yaw_i is not None else 0)
        vbat.append(d[vbat_i] if vbat_i is not None else 0)
        accz.append(d[accz_i] if accz_i is not None else 0)

    time_us = np.asarray(times, dtype=np.float64)
    time_s = (time_us - time_us[0]) / 1e6
    motor = np.asarray(motors, dtype=np.float64)
    eRPM = np.asarray(erpms, dtype=np.float64)
    throttle = np.asarray(thr, dtype=np.float64)
    rc_roll = np.asarray(roll, dtype=np.float64)
    rc_pitch = np.asarray(pitch, dtype=np.float64)
    rc_yaw = np.asarray(yaw, dtype=np.float64)

    motor_min, motor_max, craft, firmware = _parse_header_meta(parser)

    # vbatLatest is stored in centivolts-ish raw units; convert using vbat_scale
    # when available, else assume the value is already in 0.01 V units.
    vbat_arr = np.asarray(vbat, dtype=np.float64)
    if vbat_arr.max() > 100:  # looks like raw ADC/centivolt units
        vbat_v = vbat_arr / 100.0
    else:
        vbat_v = vbat_arr

    has_erpm = bool(eRPM.size) and float(np.nanmax(np.abs(eRPM))) > 1.0

    # accSmooth[2] -> g using the header's acc_1G (default 2048).
    acc_1g = 2048.0
    try:
        acc_1g = float(parser.headers.get("acc_1G", 2048) or 2048)
    except Exception:
        pass
    accz_g = np.asarray(accz, dtype=np.float64) / (acc_1g or 2048.0)

    # Guard against non-monotonic / duplicate timestamps.
    order = np.argsort(time_s, kind="stable")
    time_s = time_s[order]
    motor = motor[order]
    eRPM = eRPM[order]
    throttle = throttle[order]
    rc_roll = rc_roll[order]
    rc_pitch = rc_pitch[order]
    rc_yaw = rc_yaw[order]
    vbat_v = vbat_v[order]
    accz_g = accz_g[order]

    return LogData(
        time_s=time_s,
        motor=motor,
        eRPM=eRPM,
        throttle=throttle,
        rc_roll=rc_roll,
        rc_pitch=rc_pitch,
        rc_yaw=rc_yaw,
        vbat=vbat_v,
        accz=accz_g,
        has_erpm=has_erpm,
        motor_min=motor_min,
        motor_max=motor_max,
        craft_name=craft,
        firmware=firmware,
    )
