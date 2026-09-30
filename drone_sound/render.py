"""Orchestrate: decode -> RPM -> resample to audio rate -> synth -> WAV -> mp3."""

from __future__ import annotations

import os
import shutil
import subprocess
import wave
from dataclasses import dataclass

import numpy as np

from .decode import load_log
from .rpm import resolve_rpm
from .synth import synthesize, Timbre
from .sync import detect_log_events, pick_log_event
from . import videosync


@dataclass
class RenderResult:
    output_path: str
    duration_s: float
    sample_rate: int
    rpm_source: str
    craft_name: str
    rpm_peak: float


def _resample_to_audio(time_s, values, sr, t0, t1):
    """Linear-interpolate control ``values`` (N[,K]) onto an even audio grid."""
    n = int(round((t1 - t0) * sr))
    grid = t0 + np.arange(n) / sr
    if values.ndim == 1:
        return np.interp(grid, time_s, values), n
    out = np.empty((n, values.shape[1]), dtype=np.float64)
    for k in range(values.shape[1]):
        out[:, k] = np.interp(grid, time_s, values[:, k])
    return out, n


def _write_wav(path, audio, sr):
    """Write float audio in [-1, 1] as 16-bit PCM WAV (mono or stereo)."""
    audio = np.atleast_1d(audio)
    channels = 1 if audio.ndim == 1 else audio.shape[1]
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _to_mp3(wav_path, mp3_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found on PATH; cannot write mp3. "
            "Install ffmpeg or use a .wav output path."
        )
    subprocess.run(
        [ffmpeg, "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-q:a", "2", mp3_path],
        check=True,
        capture_output=True,
    )


def render(
    input_path: str,
    output_path: str,
    kv: float = 7200.0,
    prop_in: float = 2.5,
    blades: int = 2,
    poles: int = 14,
    rpm_source: str = "auto",
    sr: int = 48000,
    stereo: bool = False,
    start_s: float | None = None,
    end_s: float | None = None,
    timbre: Timbre | None = None,
    keep_wav: bool = False,
    progress=None,
) -> RenderResult:
    """Full pipeline. ``progress`` is an optional ``callable(str)`` for status."""

    def say(msg):
        if progress:
            progress(msg)

    say("Decoding blackbox log...")
    log = load_log(input_path)

    say(f"Resolving RPM (source={rpm_source})...")
    rpm, source_used = resolve_rpm(log, kv=kv, poles=poles, source=rpm_source)

    t0 = log.time_s[0] if start_s is None else max(log.time_s[0], start_s)
    t1 = log.time_s[-1] if end_s is None else min(log.time_s[-1], end_s)
    if t1 <= t0:
        raise ValueError("Selected time window is empty (check --start/--end).")

    say("Resampling to audio rate...")
    rpm_audio, _ = _resample_to_audio(log.time_s, rpm, sr, t0, t1)

    if timbre is not None:
        timbre.poles = poles
    say("Synthesising audio...")
    audio = synthesize(rpm_audio, sr=sr, blades=blades, prop_in=prop_in,
                       stereo=stereo, timbre=timbre)

    out_ext = os.path.splitext(output_path)[1].lower()
    if out_ext == ".wav":
        say("Writing WAV...")
        _write_wav(output_path, audio, sr)
        final = output_path
    else:
        wav_tmp = os.path.splitext(output_path)[0] + ".wav"
        say("Writing WAV...")
        _write_wav(wav_tmp, audio, sr)
        say("Encoding mp3 (ffmpeg)...")
        _to_mp3(wav_tmp, output_path)
        if not keep_wav:
            try:
                os.remove(wav_tmp)
            except OSError:
                pass
        final = output_path

    say("Done.")
    return RenderResult(
        output_path=final,
        duration_s=float(t1 - t0),
        sample_rate=sr,
        rpm_source=source_used,
        craft_name=log.craft_name,
        rpm_peak=float(np.nanmax(rpm)),
    )


def _synthesize_full_log(log, kv, prop_in, blades, poles, rpm_source, sr, stereo,
                         timbre=None):
    """Synthesize audio spanning the whole log; return (audio, source_used)."""
    rpm, source_used = resolve_rpm(log, kv=kv, poles=poles, source=rpm_source)
    t0, t1 = float(log.time_s[0]), float(log.time_s[-1])
    rpm_audio, _ = _resample_to_audio(log.time_s, rpm, sr, t0, t1)
    if timbre is not None:
        timbre.poles = poles
    audio = synthesize(rpm_audio, sr=sr, blades=blades, prop_in=prop_in,
                       stereo=stereo, timbre=timbre)
    return audio, source_used


@dataclass
class VideoRenderResult:
    video_path: str            # muxed output video
    audio_path: str            # standalone aligned audio (full, video timeline)
    trimmed_audio_path: str    # audio trimmed to start at the throttle onset
    delta_s: float             # log time that maps to video t=0
    video_onset_s: float       # video time where the anchor event occurs
    log_event: str
    log_event_time: float
    video_event_time: float
    rpm_source: str
    craft_name: str
    video_duration_s: float


def _mux(video_in, audio_in, video_out):
    """Mux audio into the video (video copied). Audio codec by container:

    ``.mov``/``.mkv`` use uncompressed PCM (best compatibility with editors such
    as DaVinci Resolve, which often will not decode AAC in MP4); ``.mp4`` uses
    AAC with faststart for broad player compatibility.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; cannot mux video.")
    ext = os.path.splitext(video_out)[1].lower()
    cmd = [ffmpeg, "-y", "-i", video_in, "-i", audio_in,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"]
    if ext in (".mov", ".mkv"):
        cmd += ["-c:a", "pcm_s16le"]
    else:  # .mp4 / .m4v and others
        cmd += ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"]
    cmd += ["-ar", "48000", "-ac", "2", "-shortest", video_out]
    subprocess.run(cmd, check=True, capture_output=True)


def render_video(
    input_path: str,
    video_path: str,
    output_path: str,
    kv: float = 7200.0,
    prop_in: float = 2.5,
    blades: int = 2,
    poles: int = 14,
    rpm_source: str = "auto",
    sr: int = 48000,
    stereo: bool = True,
    video_offset: float | None = None,
    log_event: str = "throttle",
    video_event_time: float | None = None,
    auto_video_sync: bool = False,
    osd_roi=None,
    trim_to_onset: bool = True,
    timbre: Timbre | None = None,
    keep_wav: bool = False,
    progress=None,
) -> VideoRenderResult:
    """Synthesize, align to the video, write standalone audio, and mux to video.

    Alignment precedence (``delta`` = log time that maps to video ``t=0``):
      1. ``video_offset``     -> delta = video_offset directly.
      2. ``auto_video_sync``  -> detect the OSD throttle onset in the video and
                                 pair it with the log's ``log_event`` (throttle).
      3. ``video_event_time`` -> pair the log event with that manual timecode.
      4. otherwise            -> assume the video starts at the log event.

    With ``trim_to_onset`` (default) the standalone audio begins exactly at the
    log anchor (first sound = throttle rise) and the muxed audio is silent before
    the video onset, so the first sound spike lands where the OSD throttle rises.
    """
    def say(msg):
        if progress:
            progress(msg)

    say("Decoding blackbox log...")
    log = load_log(input_path)

    say("Probing video...")
    video_dur = videosync.probe_duration(video_path)

    events = detect_log_events(log)
    ev_time = pick_log_event(events, log_event)

    if video_offset is not None:
        delta = float(video_offset)
        v_event = ev_time - delta
    elif auto_video_sync:
        say("Detecting OSD throttle onset in video (OpenCV)...")
        v_event = videosync.detect_osd_throttle_onset(video_path, roi=osd_roi)
        delta = ev_time - v_event
    elif video_event_time is not None:
        v_event = float(video_event_time)
        delta = ev_time - v_event
    else:
        v_event = 0.0
        delta = ev_time

    video_onset = max(0.0, float(v_event))

    say(f"Synthesizing audio (delta={delta:.2f}s)...")
    audio, source_used = _synthesize_full_log(
        log, kv, prop_in, blades, poles, rpm_source, sr, stereo, timbre=timbre)

    say("Aligning audio to video timeline...")
    aligned = videosync.align_audio(audio, sr, delta, video_dur)
    if trim_to_onset:
        # Silence anything before the video onset so the first sound = throttle rise.
        cut = int(round(video_onset * sr))
        cut = max(0, min(cut, aligned.shape[0]))
        aligned[:cut] = 0

    base = os.path.splitext(output_path)[0]
    wav_path = base + "_audio.wav"
    say("Writing standalone aligned audio (WAV)...")
    _write_wav(wav_path, aligned, sr)

    # Trimmed clip: synth from the log anchor onward, so the clip START is the
    # throttle-rise instant. Drop this at the video onset frame in an editor.
    onset_sample = int(round(ev_time * sr))
    onset_sample = max(0, min(onset_sample, audio.shape[0]))
    trimmed = audio[onset_sample:]
    trim_wav = base + "_onset.wav"
    say("Writing throttle-onset-trimmed audio (WAV)...")
    _write_wav(trim_wav, trimmed, sr)

    say("Muxing audio into video (ffmpeg)...")
    _mux(video_path, wav_path, output_path)

    # Convert both audio files to mp3 for convenience.
    def maybe_mp3(wav):
        try:
            mp3 = os.path.splitext(wav)[0] + ".mp3"
            _to_mp3(wav, mp3)
            if not keep_wav:
                os.remove(wav)
            return mp3
        except Exception:
            return wav

    audio_out = maybe_mp3(wav_path)
    trim_out = maybe_mp3(trim_wav)

    say("Done.")
    return VideoRenderResult(
        video_path=output_path,
        audio_path=audio_out,
        trimmed_audio_path=trim_out,
        delta_s=delta,
        video_onset_s=video_onset,
        log_event=log_event,
        log_event_time=ev_time,
        video_event_time=v_event,
        rpm_source=source_used,
        craft_name=log.craft_name,
        video_duration_s=video_dur,
    )
