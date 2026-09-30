"""Procedural synthesis of quad-rotor sound from per-motor RPM tracks.

The signal for each motor is built by phase accumulation so pitch tracks RPM
continuously (no clicks). Each motor contributes a stack of rotation-order
harmonics (the multiples of blade count dominate -> blade-pass tone) plus a
thrust-modulated aerodynamic noise bed. Four motors at slightly different RPMs
sum into the characteristic beating.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# --- tunable timbre constants ------------------------------------------------
_MAX_ORDER = 16          # highest rotation-order harmonic considered
_ROLLOFF = 1.35          # harmonic amplitude ~ 1 / order**rolloff
_BLADE_BOOST = 2.6       # extra weight for blade-pass harmonics (order % blades == 0)
_ROT_WHINE = 0.25        # weight of the 1st rotation-order imbalance whine
_NOISE_LEVEL = 0.45      # aerodynamic broadband level relative to tone
_THRUST_EXP = 1.6        # loudness ~ (rpm / rpm_ref) ** thrust_exp
_NYQUIST_GUARD = 0.45    # drop partials above this fraction of sample rate

# Motor positions for stereo panning (x in [-1, 1]); FL, FR, RL, RR.
_PAN = np.array([-0.6, 0.6, -0.4, 0.4])

# --- physical-realism presets ------------------------------------------------
PROP_CONDITION_PRESETS = {"new": 0.0, "good": 0.15, "worn": 0.5, "damaged": 0.85}
WEIGHT_PRESETS = {"light": 0.3, "normal": 0.5, "heavy": 0.8}

# environment -> (T60 s, default wet, tail brightness Hz, air-absorption cutoff Hz,
#                 early-reflection taps as (delay_s, gain))
ENVIRONMENT_PRESETS = {
    "field":     (0.30, 0.06,  9000, 12000, [(0.012, 0.25)]),
    "room":      (0.50, 0.22, 12000, 14000, [(0.007, 0.5), (0.011, 0.4),
                                             (0.017, 0.3), (0.023, 0.25)]),
    "warehouse": (1.30, 0.33, 10000, 12000, [(0.013, 0.45), (0.029, 0.35),
                                             (0.047, 0.25)]),
    "bando":     (1.80, 0.40, 13000, 15000, [(0.009, 0.55), (0.019, 0.4),
                                             (0.037, 0.3), (0.061, 0.22)]),
    "forest":    (0.60, 0.12,  6000,  7000, [(0.021, 0.3), (0.043, 0.2)]),
}


@dataclass
class Timbre:
    """Physical-realism controls for the synthesizer."""
    prop_condition: float = 0.15    # 0 new .. 1 damaged
    weight_load: float = 0.5        # 0 light .. 1 heavy (disk loading)
    electrical_whine: float = 0.12  # 0..1 motor commutation "singing"
    imbalance: float = 0.25         # 0..1 per-motor beating amount
    environment: str = "field"      # key into ENVIRONMENT_PRESETS
    reverb_wet: float | None = None  # None -> preset default; 0 -> dry
    poles: int = 14                 # motor magnet poles (for whine frequency)


def resolve_condition(value) -> float:
    """Accept a preset name or a 0..1 number for prop condition."""
    if isinstance(value, str):
        return PROP_CONDITION_PRESETS.get(value.lower().strip(), 0.15)
    return float(np.clip(value, 0.0, 1.0))


def resolve_weight(value) -> float:
    """Accept a preset name or a 0..1 number for weight/disk-loading."""
    if isinstance(value, str):
        return WEIGHT_PRESETS.get(value.lower().strip(), 0.5)
    return float(np.clip(value, 0.0, 1.0))


def weight_to_load(weight_g: float, prop_in: float) -> float:
    """Map a real all-up weight (grams) to a 0..1 disk-loading factor.

    Disk loading = hover thrust per motor / disk area. Normalized to a rough
    FPV range (~20..200 N/m^2) to give a usable 0..1 control.
    """
    area = np.pi * (prop_in * 0.0254 / 2.0) ** 2          # m^2 per prop
    thrust_per_motor = (weight_g / 1000.0) * 9.81 / 4.0    # N (hover, quad)
    dl = thrust_per_motor / max(area, 1e-6)                # N/m^2
    return float(np.clip((dl - 20.0) / (200.0 - 20.0), 0.0, 1.0))


def _prop_rolloff(prop_in: float) -> float:
    """Larger props emphasise low harmonics (steeper roll-off, darker tone)."""
    return _ROLLOFF + 0.12 * (prop_in - 3.0)


def _onepole_lowpass(x: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    """Cheap one-pole low-pass for shaping the noise bed."""
    dt = 1.0 / sr
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    a = dt / (rc + dt)
    try:
        from scipy.signal import lfilter
        return lfilter([a], [1.0, -(1.0 - a)], x)
    except Exception:
        y = np.empty_like(x)
        acc = 0.0
        for i in range(x.size):
            acc += a * (x[i] - acc)
            y[i] = acc
        return y


def _motor_voice(
    rpm: np.ndarray,
    sr: int,
    blades: int,
    rolloff: float,
    rpm_ref: float,
    rng: np.random.Generator,
    tim: Timbre,
) -> np.ndarray:
    """Synthesise one motor's mono signal from its per-sample RPM track."""
    cond = tim.prop_condition
    load = tim.weight_load

    f_rot = rpm / 60.0                     # rotation frequency (Hz), per sample
    base_phase = (2.0 * np.pi / sr) * np.cumsum(f_rot)
    nyq = _NYQUIST_GUARD * sr

    # Weight/condition-modified timbre weights.
    blade_boost = _BLADE_BOOST * (1.0 + 0.4 * load)
    whine1 = _ROT_WHINE * (1.0 + 4.0 * cond)   # imbalance whine grows with wear

    tone = np.zeros(rpm.size, dtype=np.float64)
    for order in range(1, _MAX_ORDER + 1):
        freq = f_rot * order
        weight = 1.0 / (order ** rolloff)
        if order % blades == 0:
            weight *= blade_boost
        if order == 1:
            weight *= whine1
        # Heavier craft -> stronger low-order thrum (low-shelf emphasis).
        weight *= 1.0 + load * (2.0 / order)
        # Silence any partial that crosses Nyquist (per-sample anti-alias mask).
        mask = (freq < nyq).astype(np.float64)
        tone += weight * mask * np.sin(base_phase * order)

    # Motor electrical "singing": partials at the commutation frequency and its
    # octave (f_elec = f_rot * pole_pairs), rising with RPM.
    if tim.electrical_whine > 0:
        pole_pairs = max(tim.poles / 2.0, 1.0)
        rpm_norm = np.clip(rpm / rpm_ref, 0.0, 1.5)
        for mult, g in ((1.0, 1.0), (2.0, 0.4)):
            fe = f_rot * pole_pairs * mult
            mask = (fe < nyq).astype(np.float64)
            tone += (tim.electrical_whine * 0.25 * g) * rpm_norm * mask * \
                np.sin(base_phase * pole_pairs * mult)

    # Worn/damaged props add a rotation-rate "roughness" amplitude modulation.
    if cond > 0:
        rough = 1.0 + 0.3 * cond * np.sin(base_phase + rng.uniform(0, 2 * np.pi))
        tone *= rough

    # Thrust-driven loudness envelope (heavier -> steeper).
    env = np.clip(rpm / rpm_ref, 0.0, 1.5) ** (_THRUST_EXP + 0.2 * load)
    tone *= env

    # Aerodynamic noise: white noise, low-passed toward the blade-pass region,
    # amplitude-modulated by thrust. More with wear and with disk loading.
    noise = rng.standard_normal(rpm.size)
    mean_bp = float(np.nanmedian(f_rot) * blades) or 200.0
    noise = _onepole_lowpass(noise, min(mean_bp * 3.0, nyq), sr)
    noise_level = _NOISE_LEVEL * (1.0 + 1.2 * cond) * (1.0 + 0.5 * load)
    tone += noise_level * env * noise

    return tone


def _fft_convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Fast linear convolution via numpy rFFT (no scipy). Returns len(x) samples."""
    n = len(x) + len(ir) - 1
    nfft = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft), nfft)
    return y[: len(x)]


def _fir_lowpass(x: np.ndarray, cutoff_hz: float, sr: int, taps: int = 63) -> np.ndarray:
    """Zero-ish-phase FIR low-pass (windowed sinc) applied with numpy convolve."""
    if cutoff_hz >= sr * 0.5:
        return x
    fc = cutoff_hz / sr
    m = (taps - 1) / 2.0
    k = np.arange(taps) - m
    h = np.sinc(2 * fc * k) * np.hamming(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same")


def _make_ir(sr: int, environment: str, seed: int) -> np.ndarray:
    """Build a room impulse response (early reflections + decaying tail)."""
    t60, _wet, bright, _air, taps = ENVIRONMENT_PRESETS.get(
        environment, ENVIRONMENT_PRESETS["field"])
    rng = np.random.default_rng(seed)
    length = max(int(t60 * sr), 1)
    t = np.arange(length) / sr
    tail = rng.standard_normal(length) * np.exp(-6.9 * t / t60)
    tail = _fir_lowpass(tail, bright, sr)          # darken the tail
    ir = tail
    for delay_s, gain in taps:                     # early reflections
        d = int(delay_s * sr)
        if d < length:
            ir[d] += gain
    ir[0] += 0.0
    rms = float(np.sqrt(np.mean(ir ** 2))) or 1.0
    return ir / (rms * np.sqrt(length))            # energy-normalize


def _apply_environment(audio: np.ndarray, sr: int, tim: Timbre) -> np.ndarray:
    """Apply air absorption + baked-in reverb per the environment preset."""
    t60, wet_default, _bright, air_cut, _taps = ENVIRONMENT_PRESETS.get(
        tim.environment, ENVIRONMENT_PRESETS["field"])
    wet = wet_default if tim.reverb_wet is None else float(tim.reverb_wet)
    stereo = audio.ndim == 2

    def process(chan, seed):
        dry = _fir_lowpass(chan, air_cut, sr) if air_cut < sr * 0.5 else chan
        if wet <= 0:
            return dry
        ir = _make_ir(sr, tim.environment, seed)
        w = _fft_convolve(dry, ir)
        return (1.0 - wet) * dry + wet * w

    if stereo:
        out = np.empty_like(audio)
        out[:, 0] = process(audio[:, 0], seed=101)   # decorrelated IRs for width
        out[:, 1] = process(audio[:, 1], seed=202)
        return out
    return process(audio, seed=101)


def synthesize(
    rpm_audio: np.ndarray,
    sr: int,
    blades: int = 2,
    prop_in: float = 2.5,
    stereo: bool = False,
    timbre: Timbre | None = None,
    seed: int = 12345,
) -> np.ndarray:
    """Render audio from RPM tracks already sampled at ``sr``.

    Parameters
    ----------
    rpm_audio : (M, K) float array -- per-motor RPM at the audio sample rate.
    sr        : sample rate (Hz).
    blades    : blades per propeller.
    prop_in   : propeller diameter in inches (shapes timbre).
    stereo    : if True, pan motors into a 2-channel image.

    Returns
    -------
    (M,) float32 for mono, or (M, 2) float32 for stereo, peak-normalised.
    """
    tim = timbre or Timbre()
    rpm_audio = np.atleast_2d(rpm_audio)
    if rpm_audio.shape[0] < rpm_audio.shape[1]:
        # Guard against a transposed (K, M) input.
        rpm_audio = rpm_audio.T
    n_samples, n_motors = rpm_audio.shape
    rolloff = _prop_rolloff(prop_in)
    rpm_ref = max(float(np.nanpercentile(rpm_audio, 95)), 1.0)
    rng = np.random.default_rng(seed)

    if stereo:
        out = np.zeros((n_samples, 2), dtype=np.float64)
    else:
        out = np.zeros(n_samples, dtype=np.float64)

    # Per-motor imbalance: constant detune + slow wobble so the four motors beat.
    detune_sd = tim.imbalance * (0.004 + 0.02 * tim.prop_condition)
    wobble_depth = tim.imbalance * (0.005 + 0.02 * tim.prop_condition)

    for k in range(n_motors):
        rpm_k = rpm_audio[:, k].astype(np.float64)
        if detune_sd > 0:
            rpm_k = rpm_k * (1.0 + rng.normal(0.0, detune_sd))
        if wobble_depth > 0:
            # Slow (~2-6 Hz) sinusoidal wander, unique per motor.
            wf = rng.uniform(2.0, 6.0)
            phase0 = rng.uniform(0, 2 * np.pi)
            t = np.arange(n_samples) / sr
            rpm_k = rpm_k * (1.0 + wobble_depth * np.sin(2 * np.pi * wf * t + phase0))

        voice = _motor_voice(rpm_k, sr, blades, rolloff, rpm_ref, rng, tim)
        if stereo:
            pan = _PAN[k % len(_PAN)]
            gl = np.sqrt(0.5 * (1.0 - pan))
            gr = np.sqrt(0.5 * (1.0 + pan))
            out[:, 0] += gl * voice
            out[:, 1] += gr * voice
        else:
            out += voice

    # Soft limit then peak-normalise to about -1 dBFS.
    out = np.tanh(out / (n_motors * 0.9))

    # Environment: air absorption + baked-in reverb.
    out = _apply_environment(out, sr, tim)

    peak = float(np.max(np.abs(out))) or 1.0
    out = out / peak * 0.89
    return out.astype(np.float32)
