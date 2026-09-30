# fpv-propsynth

**Generate realistic FPV drone sound from a Betaflight blackbox (`.bbl`) log — and
automatically sync it to your video.**

Silent HD/DJI footage? This tool reads the motor data your flight controller recorded
(RPM, throttle, voltage) and synthesizes a matching drone sound in real time with the
flight. It can then detect where the throttle starts rising in your video (from the OSD)
and line the sound up automatically, muxing it back into the clip.

> Procedural synthesis — no sample libraries. The pitch tracks real motor RPM; you set
> KV, prop size, prop condition, weight and environment to shape the character.

## Demo

<!-- Replace with your demo video link -->
📹 **Demo video:** _add your link here_

---

## Features

- Decodes Betaflight/INAV `.bbl` logs in pure Python (via `orangebox`) — no external decoder.
- Uses **real motor RPM** (`eRPM`) when logged; otherwise estimates from KV × voltage × throttle.
- Procedural engine: blade-pass tones + harmonics, thrust envelope, aerodynamic noise, 4-motor beating.
- **Physical realism controls:** prop condition, drone weight, motor electrical whine, imbalance.
- **Environment acoustics:** baked-in reverb + air absorption (field / room / warehouse / bando / forest).
- **Automatic video sync** via the OSD throttle indicator, plus manual offset options.
- Outputs `.mp3` / `.wav`, or a muxed video (`.mov` with editor-friendly PCM audio, or `.mp4`).
- CLI **and** a simple GUI. Windows `.exe` available; runs from source on Linux/macOS.

---

## Requirements

| Software | Required? | Notes |
|---|---|---|
| **Python 3.9+** | Yes (from source) | Not needed if you use the Windows `.exe`. |
| **ffmpeg + ffprobe** | **Yes** | For mp3/mov encoding and video probing/muxing. Must be on `PATH`. |
| numpy, orangebox | Yes | Installed via pip (see below). |
| opencv-python | Optional | Only for automatic video sync (`--auto-video-sync`). |
| scipy | Optional | Slightly faster noise filtering; falls back to numpy. |

**Install ffmpeg:**
- Windows: `winget install Gyan.FFmpeg` (or download from ffmpeg.org and add to PATH)
- Linux (Debian/Ubuntu): `sudo apt install ffmpeg`
- Linux (Fedora): `sudo dnf install ffmpeg` · (Arch): `sudo pacman -S ffmpeg`
- macOS: `brew install ffmpeg`

---

## Install

### Windows — ready-to-run executable
Download `DroneSound.exe` from the Releases page and double-click it (GUI). No Python
needed, but **ffmpeg must still be installed and on PATH**.

### From source (Windows / Linux / macOS)
```bash
git clone https://github.com/<your-username>/fpv-propsynth.git
cd fpv-propsynth
python -m pip install -e ".[video]"      # includes OpenCV for video sync
# or minimal:  python -m pip install -e .
```
This installs two commands:
- `propsynth` — the CLI
- `propsynth-gui` — the GUI

You can also run without installing:
```bash
python -m pip install -r requirements.txt
python run.py --help          # CLI
python run.py --gui           # GUI
```

---

## Quick start

```bash
# Audio only:
propsynth flight.bbl --kv 7200 --prop 2.5 --stereo -o flight.mp3

# Inspect the log's sync anchor + events:
propsynth --events flight.bbl

# Auto-sync the sound to a video and mux it (editor-friendly .mov):
propsynth flight.bbl --video clip.mov --auto-video-sync --kv 7200 --prop 2.5 -o synced.mov
```

Output length always matches the flight (real time). For video, the sound is aligned so it
starts exactly where the OSD throttle begins to rise.

---

## Options reference

### Core
| Option | Default | Meaning |
|---|---|---|
| `input` | — | Path to the `.bbl` / `.bfl` log (positional). |
| `-o, --output` | `<input>.mp3` | Output file (`.mp3` / `.wav`; or `.mov` / `.mp4` with `--video`). |
| `--kv` | 7200 | Motor KV (used only when RPM is estimated). |
| `--prop` | 2.5 | Propeller diameter (inches). |
| `--blades` | 2 | Blades per propeller. |
| `--poles` | 14 | Motor magnet poles (eRPM→RPM and whine frequency). |
| `--rpm-source` | auto | `auto` (eRPM if logged, else estimate), `erpm`, or `estimate`. |
| `--sr` | 48000 | Sample rate (Hz). |
| `--stereo` | off | 2-channel output (motors panned FL/FR/RL/RR). |
| `--start` / `--end` | full | Trim to a time window (seconds). |
| `--keep-wav` | off | Keep the intermediate WAV next to the mp3. |

### Sound character (physical realism)
| Option | Default | Meaning |
|---|---|---|
| `--prop-condition` | good | `new` / `good` / `worn` / `damaged` or `0..1`. Wear → whine, roughness, noise. |
| `--weight` | normal | `light` / `normal` / `heavy` or `0..1`. Heavier → deeper thrum, more turbulence. |
| `--weight-grams` | — | Real all-up weight in grams (overrides `--weight` via disk loading). |
| `--environment` | field | `field` / `room` / `warehouse` / `bando` / `forest` (baked-in reverb + air absorption). |
| `--reverb` | per-env | Reverb wet amount `0..1`, or `dry`. |
| `--whine` | 0.12 | Motor electrical "singing" `0..1` (rises with RPM & poles). |
| `--imbalance` | 0.25 | Per-motor beating `0..1`. |

### Video sync
| Option | Default | Meaning |
|---|---|---|
| `--video` | — | Video file to align to and mux into (enables video mode). |
| `--auto-video-sync` | off | Detect the OSD throttle onset in the video (needs OpenCV). |
| `--log-event` | throttle | Log anchor: `throttle` / `arm` / `spoolup` / `takeoff`. |
| `--video-event-time` | — | Manual: time (s) in the video of that event. |
| `--video-offset` | — | Manual: log time (s) that maps to video `t=0`. |
| `--osd-roi` | auto | Throttle OSD region `x,y,w,h` in pixels (override the default). |
| `--no-trim` | off | Keep pre-throttle idle sound instead of silencing it. |
| `--events` | — | Print detected log events (throttle/arm/spool-up/takeoff) and exit. |

**Examples**
```bash
# Worn props, heavy build, echoey abandoned building:
propsynth flight.bbl --kv 7200 --prop 2.5 \
  --prop-condition damaged --weight heavy --environment bando --reverb 0.4 --whine 0.5

# Clean and dry (close to the raw engine):
propsynth flight.bbl --kv 7200 --prop 2.5 --environment field --reverb dry

# Manual sync: you read the throttle-rise frame off the OSD (e.g. 3.72 s):
propsynth flight.bbl --video clip.mov --log-event throttle --video-event-time 3.72 -o synced.mov
```

---

## GUI

```bash
propsynth-gui        # or: python run.py --gui
```
Pick a `.bbl`, set KV / prop / blades / poles, and (optionally) a video. Sections:
- **Sound character:** prop condition, weight, environment, reverb/whine/imbalance sliders.
- **Video sync:** "Detect log events", auto-OSD checkbox, manual event time / offset, OSD ROI, trim toggle.

For video it writes `<log>_synced.mov` (PCM audio) plus a full and a throttle-onset-trimmed
audio file, and tells you the exact video time to place the trimmed clip in your editor.

---

## Video sync — how it works

The blackbox starts recording before the video, and there's no shared clock. The sound is
aligned on a **shared event: the throttle starting to rise.**
- In the log: `rcCommand[3]` leaves idle.
- In the video: the **OSD throttle number leaves 0** — detected from the pixels (no OCR).

`propsynth` computes the offset, trims the audio to start at that instant, and muxes it in.
The default OSD region suits a **centered throttle readout at 1080p**; for other layouts or
resolutions pass `--osd-roi x,y,w,h`. If auto-detection is off, use `--video-event-time` (read
the frame yourself) or `--video-offset`.

## Stick overlay

Draw a **Betaflight-style stick (gimbal) overlay** onto your video — two boxes with a
crosshair and a moving dot, rendered from the log's `rcCommand` and synced the same way as
the audio. You don't need Betaflight Blackbox Explorer's video export for this (that has no
audio and a black background that's hard to composite); this draws it directly on your clip.

```bash
propsynth flight.bbl --video clip.mp4 --auto-video-sync --overlay -o final.mov
```

Overlay options:
| Option | Default | Meaning |
|---|---|---|
| `--overlay` | off | Enable the stick overlay (re-encodes the video). |
| `--overlay-position` | bottom-center | `bottom-center/left/right`, `top-*`, `center`. |
| `--overlay-pos` | — | Custom center `x,y` as fractions `0..1` (overrides preset). |
| `--overlay-size` | 0.12 | Box size as a fraction of frame height. |
| `--overlay-opacity` | 0.35 | Box fill opacity `0..1`. |
| `--overlay-mode` | 2 | Transmitter stick mode (1–4). Mode 2 = throttle+yaw left. |
| `--overlay-no-labels` | off | Hide the µs value labels / "Mode N". |

In the GUI: tick **"Add stick overlay"** and set position/mode/size/opacity.
Enabling the overlay re-encodes the video (H.264), so it takes longer than an audio-only mux.

### DaVinci Resolve note
Resolve on Windows often won't decode **AAC in MP4** (imports video-only). This tool defaults
the muxed output to **`.mov` with uncompressed PCM audio**, which Resolve reads reliably. If
you specifically need `.mp4`, the audio is AAC — use the `.mov` for Resolve.

---

## Betaflight setup

### Record more flight in less space
Blackbox tab:
- **Logging rate:** `1/8 (500 Hz)` (or `1/16`) — motor/throttle change slowly; plenty for sound.
- **Debug fields — keep ON:** Motor, RPM, RC Commands, Battery.
- **Turn OFF:** Gyro, Gyro (Unfiltered), PID, Setpoint, Magnetometer, Altitude, RSSI,
  Attitude, Debug Log, GPS, Servo, Accelerometer. (Gyro Unfiltered is the biggest hog.)

This typically cuts the log to a fraction of the size, so you fit several times more flight.

### Get *real* RPM (better sound, no KV guessing)
- Enable the **RPM** debug field.
- Configuration tab: **Bidirectional DShot ON** (DShot300/600), set **Motor poles** (usually 14).
- The log then carries `eRPM`, and `propsynth` uses measured RPM automatically (`--rpm-source auto`).

### OSD throttle (for automatic video sync)
- Enable a **Throttle position (%)** element on your OSD so the video shows throttle going 0→100.
- The tool detects when it leaves 0. Default region assumes a centered readout; otherwise use
  `--osd-roi x,y,w,h`.

---

## Build the Windows .exe

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name DroneSound --collect-submodules orangebox app_gui.py
# -> dist/DroneSound.exe   (ffmpeg still required on the target PC)
```

---

## Troubleshooting

- **No audio in the video (DaVinci):** use the `.mov` output (PCM), not `.mp4` (AAC).
- **Pitch sounds wrong / flat:** your log has no `eRPM` → set the correct `--kv` (and enable
  bidirectional DShot + RPM logging for real RPM next time).
- **Auto-sync picks the wrong moment:** the OSD throttle isn't where expected — pass
  `--osd-roi x,y,w,h`, or sync manually with `--video-event-time` / `--video-offset`.
- **`ffmpeg not found`:** install ffmpeg and make sure `ffmpeg`/`ffprobe` are on your PATH.

---

## How it works

1. **decode** — parse the `.bbl` into per-frame arrays (motor, eRPM, throttle, voltage, accel).
2. **rpm** — `RPM = eRPM × 100 / (poles/2)` when logged, else KV × voltage × throttle estimate.
3. **synth** — per-motor phase-accumulated harmonic stack (blade-pass emphasized) + thrust
   envelope + noise + electrical whine + imbalance; environment reverb via FFT convolution.
4. **render** — interpolate to audio rate, synthesize, write WAV, encode via ffmpeg; for video,
   detect the throttle onset, align, and mux.

Realism is *physically plausible procedural synthesis*, not a recording of a specific motor/prop.

---

## License

MIT — see [LICENSE](LICENSE).
