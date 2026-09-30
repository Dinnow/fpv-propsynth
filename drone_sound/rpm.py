"""Convert blackbox data to per-motor mechanical RPM.

Two sources:
  * ``erpm``     -- real telemetry: RPM = eRPM_raw * 100 / (poles / 2)
  * ``estimate`` -- physics estimate from motor command, KV and pack voltage
  * ``auto``     -- use eRPM when the log carries it, else estimate
"""

from __future__ import annotations

import numpy as np

from .decode import LogData

# Fraction of the no-load KV*V speed a motor actually reaches under aero load.
_LOAD_FACTOR = 0.72


def erpm_to_rpm(erpm_raw: np.ndarray, poles: int) -> np.ndarray:
    """Betaflight eRPM field -> mechanical RPM. ``poles`` is motor magnet count."""
    return erpm_raw * 100.0 / (poles / 2.0)


def estimate_rpm(
    motor: np.ndarray,
    vbat: np.ndarray,
    kv: float,
    motor_min: float,
    motor_max: float,
) -> np.ndarray:
    """Estimate RPM from motor output, KV and voltage when no eRPM is logged.

    Throttle fraction drives a near-linear speed curve; the no-load speed is
    ``kv * voltage`` scaled by an empirical load factor.
    """
    span = max(motor_max - motor_min, 1.0)
    thr = np.clip((motor - motor_min) / span, 0.0, 1.0)
    v = vbat.copy()
    # If voltage looks unusable, assume a nominal 3S pack.
    if v.size == 0 or float(np.nanmedian(v)) < 3.0:
        v = np.full_like(motor, 11.4)
    if v.ndim == 1:
        v = v[:, None]
    no_load = kv * v  # RPM at full throttle, unloaded
    # Slight curve: idle spins a little even at zero stick.
    return no_load * _LOAD_FACTOR * (0.06 + 0.94 * thr ** 0.9)


def resolve_rpm(
    log: LogData,
    kv: float,
    poles: int,
    source: str = "auto",
) -> tuple[np.ndarray, str]:
    """Return ``(rpm[N,K], source_used)``."""
    source = source.lower()
    if source not in ("auto", "erpm", "estimate"):
        raise ValueError(f"Unknown rpm source: {source!r}")

    use_erpm = source == "erpm" or (source == "auto" and log.has_erpm)
    if use_erpm:
        if not log.has_erpm:
            raise ValueError("rpm-source 'erpm' requested but log has no eRPM data.")
        return erpm_to_rpm(log.eRPM, poles), "erpm"

    rpm = estimate_rpm(log.motor, log.vbat, kv, log.motor_min, log.motor_max)
    return rpm, "estimate"
