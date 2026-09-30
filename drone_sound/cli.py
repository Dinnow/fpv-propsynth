"""Command-line interface for the drone sound generator."""

from __future__ import annotations

import argparse
import os
import sys

from .render import render, render_video
from .synth import Timbre, resolve_condition, resolve_weight, weight_to_load


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drone_sound",
        description="Generate a drone sound (.mp3/.wav) from a Betaflight blackbox log.",
    )
    p.add_argument("input", help="Path to the .bbl / .bfl blackbox log.")
    p.add_argument("-o", "--output", help="Output file (.mp3 or .wav). "
                   "Default: <input>.mp3")
    p.add_argument("--kv", type=float, default=7200.0,
                   help="Motor KV (used only when estimating RPM). Default 7200.")
    p.add_argument("--prop", type=float, default=2.5,
                   help="Propeller diameter in inches. Default 2.5.")
    p.add_argument("--blades", type=int, default=2,
                   help="Blades per propeller. Default 2.")
    p.add_argument("--poles", type=int, default=14,
                   help="Motor magnet poles (for eRPM->RPM). Default 14.")
    p.add_argument("--rpm-source", choices=["auto", "erpm", "estimate"],
                   default="auto",
                   help="RPM source. auto=eRPM if logged else estimate. Default auto.")
    p.add_argument("--sr", type=int, default=48000, help="Sample rate. Default 48000.")
    p.add_argument("--stereo", action="store_true", help="Render 2-channel stereo.")
    p.add_argument("--start", type=float, default=None, help="Start time (s).")
    p.add_argument("--end", type=float, default=None, help="End time (s).")
    p.add_argument("--keep-wav", action="store_true",
                   help="Keep the intermediate .wav when writing mp3.")

    # --- sound character (physical realism) ----------------------------------
    s = p.add_argument_group("sound character")
    s.add_argument("--prop-condition", default="good",
                   help="Prop wear: new|good|worn|damaged or 0..1. Default good.")
    s.add_argument("--weight", default="normal",
                   help="Disk loading: light|normal|heavy or 0..1. Default normal.")
    s.add_argument("--weight-grams", type=float, default=None,
                   help="All-up weight in grams (overrides --weight via disk loading).")
    s.add_argument("--environment", default="field",
                   choices=["field", "room", "warehouse", "bando", "forest"],
                   help="Acoustic environment (baked-in reverb). Default field.")
    s.add_argument("--reverb", default=None,
                   help="Reverb wet amount 0..1, or 'dry'. Default: per-environment.")
    s.add_argument("--whine", type=float, default=0.12,
                   help="Motor electrical whine 0..1. Default 0.12.")
    s.add_argument("--imbalance", type=float, default=0.25,
                   help="Per-motor imbalance beating 0..1. Default 0.25.")

    # --- log-event inspection ------------------------------------------------
    p.add_argument("--events", action="store_true",
                   help="Print detected log events (arm/spool-up/takeoff) and exit.")

    # --- video sync / muxing -------------------------------------------------
    g = p.add_argument_group("video sync")
    g.add_argument("--video", help="Video file to align the sound to and mux into.")
    g.add_argument("--video-offset", type=float, default=None,
                   help="Log time (s) that corresponds to video t=0 (direct offset).")
    g.add_argument("--log-event", choices=["throttle", "arm", "spoolup", "takeoff"],
                   default="throttle",
                   help="Log anchor event to align on. Default throttle (onset).")
    g.add_argument("--video-event-time", type=float, default=None,
                   help="Time (s) in the video of the same event (manual sync).")
    g.add_argument("--auto-video-sync", action="store_true",
                   help="Auto-detect the OSD throttle onset in the video (opencv).")
    g.add_argument("--osd-roi", default=None,
                   help="Throttle OSD region as 'x,y,w,h' pixels (override default).")
    g.add_argument("--no-trim", action="store_true",
                   help="Do not trim/silence audio before the throttle onset.")
    return p


def _build_timbre(args) -> Timbre:
    """Construct a Timbre from parsed CLI args (presets or numbers)."""
    if args.weight_grams is not None:
        load = weight_to_load(args.weight_grams, args.prop)
    else:
        load = resolve_weight(args.weight)
    if isinstance(args.reverb, str) and args.reverb.strip().lower() == "dry":
        wet = 0.0
    elif args.reverb is not None:
        wet = float(args.reverb)
    else:
        wet = None
    return Timbre(
        prop_condition=resolve_condition(args.prop_condition),
        weight_load=load,
        electrical_whine=float(args.whine),
        imbalance=float(args.imbalance),
        environment=args.environment,
        reverb_wet=wet,
        poles=args.poles,
    )


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2

    # --- just report log events ---------------------------------------------
    if args.events:
        from .decode import load_log
        from .sync import detect_log_events
        try:
            log = load_log(args.input)
            ev = detect_log_events(log)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        fmt = lambda x: f"{x:6.2f} s" if x is not None else "  (not found)"
        print(f"Log: {args.input}")
        print(f"  craft:    {log.craft_name or '(unknown)'}")
        print(f"  duration: {log.duration_s:.2f} s")
        print(f"  throttle: {fmt(ev.throttle)}   <- default sync anchor")
        print(f"  arm:      {fmt(ev.arm)}")
        print(f"  spool-up: {fmt(ev.spoolup)}")
        print(f"  takeoff:  {fmt(ev.takeoff)}")
        return 0

    # --- video sync / muxing branch -----------------------------------------
    if args.video:
        if not os.path.isfile(args.video):
            print(f"error: video not found: {args.video}", file=sys.stderr)
            return 2
        vout = args.output or (os.path.splitext(args.input)[0] + "_synced.mp4")
        roi = None
        if args.osd_roi:
            try:
                roi = tuple(int(v) for v in args.osd_roi.split(","))
                assert len(roi) == 4
            except Exception:
                print("error: --osd-roi must be 'x,y,w,h' integers", file=sys.stderr)
                return 2
        try:
            vr = render_video(
                input_path=args.input, video_path=args.video, output_path=vout,
                kv=args.kv, prop_in=args.prop, blades=args.blades, poles=args.poles,
                rpm_source=args.rpm_source, sr=args.sr, stereo=True,
                video_offset=args.video_offset, log_event=args.log_event,
                video_event_time=args.video_event_time,
                auto_video_sync=args.auto_video_sync, osd_roi=roi,
                trim_to_onset=not args.no_trim, timbre=_build_timbre(args),
                keep_wav=args.keep_wav, progress=lambda m: print(m),
            )
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"\nWrote {vr.video_path}\n"
            f"  aligned audio (full):  {vr.audio_path}\n"
            f"  trimmed clip (onset):  {vr.trimmed_audio_path}\n"
            f"  craft:         {vr.craft_name or '(unknown)'}\n"
            f"  video length:  {vr.video_duration_s:.2f} s\n"
            f"  rpm source:    {vr.rpm_source}\n"
            f"  offset (delta):{vr.delta_s:+.2f} s  (log time at video t=0)\n"
            f"  aligned on:    log {vr.log_event}={vr.log_event_time:.2f}s "
            f"<-> video {vr.video_event_time:.2f}s\n"
            f"  DaVinci: drop the trimmed clip at video t={vr.video_onset_s:.2f}s "
            f"(where OSD throttle starts rising)."
        )
        return 0

    output = args.output or (os.path.splitext(args.input)[0] + ".mp3")

    try:
        result = render(
            input_path=args.input,
            output_path=output,
            kv=args.kv,
            prop_in=args.prop,
            blades=args.blades,
            poles=args.poles,
            rpm_source=args.rpm_source,
            sr=args.sr,
            stereo=args.stereo,
            start_s=args.start,
            end_s=args.end,
            timbre=_build_timbre(args),
            keep_wav=args.keep_wav,
            progress=lambda m: print(m),
        )
    except Exception as exc:  # surface a clean message, not a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"\nWrote {result.output_path}\n"
        f"  craft:      {result.craft_name or '(unknown)'}\n"
        f"  duration:   {result.duration_s:.2f} s\n"
        f"  rpm source: {result.rpm_source}\n"
        f"  peak RPM:   {result.rpm_peak:.0f}\n"
        f"  sample rate:{result.sample_rate} Hz"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
