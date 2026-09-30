"""Minimal tkinter GUI wrapping the render pipeline."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .render import render, render_video
from .synth import Timbre, resolve_condition, resolve_weight


def _open_folder(path: str) -> None:
    """Open a folder in the OS file browser (Windows/macOS/Linux)."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606 (Windows only)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.grid(sticky="nsew")
        master.title("Drone Sound Generator")
        master.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        self.in_var = tk.StringVar()
        self.kv_var = tk.StringVar(value="7200")
        self.prop_var = tk.StringVar(value="2.5")
        self.blades_var = tk.StringVar(value="2")
        self.poles_var = tk.StringVar(value="14")
        self.src_var = tk.StringVar(value="auto")
        self.stereo_var = tk.BooleanVar(value=True)
        self.video_var = tk.StringVar()
        self.logevent_var = tk.StringVar(value="throttle")
        self.videotime_var = tk.StringVar()
        self.offset_var = tk.StringVar()
        self.auto_var = tk.BooleanVar(value=True)
        self.roi_var = tk.StringVar()
        self.trim_var = tk.BooleanVar(value=True)
        # sound character
        self.cond_var = tk.StringVar(value="good")
        self.wt_var = tk.StringVar(value="normal")
        self.env_var = tk.StringVar(value="field")
        self.reverb_var = tk.DoubleVar(value=-1.0)   # -1 => environment default
        self.whine_var = tk.DoubleVar(value=0.12)
        self.imb_var = tk.DoubleVar(value=0.25)
        self.status = tk.StringVar(value="Select a .bbl file to begin.")

        row = 0
        ttk.Label(self, text="Blackbox file (.bbl):").grid(row=row, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.in_var, width=42).grid(
            row=row, column=1, sticky="ew")
        ttk.Button(self, text="Browse...", command=self._pick).grid(
            row=row, column=2, padx=4)

        def field(label, var, **kw):
            nonlocal row
            row += 1
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=2)
            widget = kw.pop("widget", None)
            if widget is None:
                widget = ttk.Entry(self, textvariable=var, width=12)
            widget.grid(row=row, column=1, sticky="w")
            return widget

        field("Motor KV (for estimate):", self.kv_var)
        field("Propeller size (inches):", self.prop_var)
        field("Blades per prop:", self.blades_var)
        field("Motor poles:", self.poles_var)
        field("RPM source:", self.src_var,
              widget=ttk.Combobox(self, textvariable=self.src_var, width=10,
                                  values=["auto", "erpm", "estimate"],
                                  state="readonly"))

        row += 1
        ttk.Checkbutton(self, text="Stereo", variable=self.stereo_var).grid(
            row=row, column=1, sticky="w")

        # --- optional video sync section ------------------------------------
        row += 1
        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        ttk.Label(self, text="Video sync (optional)", font=("", 9, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w")

        row += 1
        ttk.Label(self, text="Video file:").grid(row=row, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.video_var, width=42).grid(
            row=row, column=1, sticky="ew")
        ttk.Button(self, text="Browse...", command=self._pick_video).grid(
            row=row, column=2, padx=4)

        row += 1
        ttk.Button(self, text="Detect log events", command=self._detect_events).grid(
            row=row, column=1, sticky="w", pady=2)

        row += 1
        ttk.Label(self, text="Align on log event:").grid(row=row, column=0, sticky="w")
        ttk.Combobox(self, textvariable=self.logevent_var, width=10, state="readonly",
                     values=["throttle", "arm", "spoolup", "takeoff"]).grid(
            row=row, column=1, sticky="w")

        row += 1
        ttk.Checkbutton(self, text="Auto-detect OSD throttle onset (OpenCV)",
                        variable=self.auto_var).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(self, text="…or video event time (s):").grid(
            row=row, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.videotime_var, width=12).grid(
            row=row, column=1, sticky="w")

        row += 1
        ttk.Label(self, text="…or direct offset (s):").grid(
            row=row, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.offset_var, width=12).grid(
            row=row, column=1, sticky="w")

        row += 1
        ttk.Label(self, text="OSD throttle ROI (x,y,w,h):").grid(
            row=row, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.roi_var, width=22).grid(
            row=row, column=1, sticky="w")

        row += 1
        ttk.Checkbutton(self, text="Trim sound to start at throttle onset",
                        variable=self.trim_var).grid(row=row, column=1, sticky="w")

        # --- sound character section ----------------------------------------
        row += 1
        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        ttk.Label(self, text="Sound character", font=("", 9, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w")

        row += 1
        ttk.Label(self, text="Prop condition:").grid(row=row, column=0, sticky="w")
        ttk.Combobox(self, textvariable=self.cond_var, width=10, state="readonly",
                     values=["new", "good", "worn", "damaged"]).grid(
            row=row, column=1, sticky="w")

        row += 1
        ttk.Label(self, text="Weight:").grid(row=row, column=0, sticky="w")
        ttk.Combobox(self, textvariable=self.wt_var, width=10, state="readonly",
                     values=["light", "normal", "heavy"]).grid(
            row=row, column=1, sticky="w")

        row += 1
        ttk.Label(self, text="Environment:").grid(row=row, column=0, sticky="w")
        ttk.Combobox(self, textvariable=self.env_var, width=12, state="readonly",
                     values=["field", "room", "warehouse", "bando", "forest"]).grid(
            row=row, column=1, sticky="w")

        def slider(label, var, frm, to):
            nonlocal row
            row += 1
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w")
            ttk.Scale(self, from_=frm, to=to, variable=var,
                      orient="horizontal", length=180).grid(
                row=row, column=1, sticky="w")

        slider("Reverb wet (-1=auto):", self.reverb_var, -1.0, 1.0)
        slider("Electrical whine:", self.whine_var, 0.0, 1.0)
        slider("Imbalance beating:", self.imb_var, 0.0, 1.0)

        row += 1
        self.gen_btn = ttk.Button(self, text="Generate", command=self._generate)
        self.gen_btn.grid(row=row, column=1, sticky="w", pady=8)

        row += 1
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.grid(row=row, column=0, columnspan=3, sticky="ew")

        row += 1
        ttk.Label(self, textvariable=self.status, wraplength=420,
                  foreground="#555").grid(row=row, column=0, columnspan=3,
                                          sticky="w", pady=(6, 0))

    def _pick(self):
        path = filedialog.askopenfilename(
            filetypes=[("Blackbox logs", "*.bbl *.bfl *.txt"), ("All files", "*.*")])
        if path:
            self.in_var.set(path)

    def _pick_video(self):
        path = filedialog.askopenfilename(
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi"), ("All files", "*.*")])
        if path:
            self.video_var.set(path)

    def _detect_events(self):
        inp = self.in_var.get().strip()
        if not inp or not os.path.isfile(inp):
            messagebox.showerror("Drone Sound", "Choose a valid blackbox file first.")
            return
        try:
            from .decode import load_log
            from .sync import detect_log_events
            ev = detect_log_events(load_log(inp))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Drone Sound", str(exc))
            return
        f = lambda x: f"{x:.2f}s" if x is not None else "n/a"
        self._set_status(
            f"Log events -> throttle: {f(ev.throttle)} (anchor)   arm: {f(ev.arm)}   "
            f"spool-up: {f(ev.spoolup)}   takeoff: {f(ev.takeoff)}")

    def _set_status(self, msg):
        self.status.set(msg)
        self.update_idletasks()

    def _timbre(self, poles):
        wet = self.reverb_var.get()
        return Timbre(
            prop_condition=resolve_condition(self.cond_var.get()),
            weight_load=resolve_weight(self.wt_var.get()),
            electrical_whine=float(self.whine_var.get()),
            imbalance=float(self.imb_var.get()),
            environment=self.env_var.get(),
            reverb_wet=None if wet < 0 else float(wet),
            poles=poles,
        )

    def _generate(self):
        inp = self.in_var.get().strip()
        if not inp or not os.path.isfile(inp):
            messagebox.showerror("Drone Sound", "Please choose a valid blackbox file.")
            return
        try:
            kv = float(self.kv_var.get())
            prop = float(self.prop_var.get())
            blades = int(self.blades_var.get())
            poles = int(self.poles_var.get())
        except ValueError:
            messagebox.showerror("Drone Sound", "KV/prop/blades/poles must be numbers.")
            return

        video = self.video_var.get().strip()
        self.gen_btn.config(state="disabled")
        self.bar.start(12)

        def num_or_none(s):
            s = s.strip()
            return float(s) if s else None

        if video:
            if not os.path.isfile(video):
                self._fail("Video file not found.")
                return
            out = os.path.splitext(inp)[0] + "_synced.mov"
            offset = num_or_none(self.offset_var.get())
            vtime = num_or_none(self.videotime_var.get())
            auto = self.auto_var.get() and offset is None and vtime is None
            roi = None
            roi_txt = self.roi_var.get().strip()
            if roi_txt:
                try:
                    roi = tuple(int(v) for v in roi_txt.split(","))
                    assert len(roi) == 4
                except Exception:
                    self._fail("OSD ROI must be 'x,y,w,h' integers.")
                    return

            def work_video():
                try:
                    result = render_video(
                        input_path=inp, video_path=video, output_path=out,
                        kv=kv, prop_in=prop, blades=blades, poles=poles,
                        rpm_source=self.src_var.get(), stereo=self.stereo_var.get(),
                        video_offset=offset, log_event=self.logevent_var.get(),
                        video_event_time=vtime, auto_video_sync=auto,
                        osd_roi=roi, trim_to_onset=self.trim_var.get(),
                        timbre=self._timbre(poles),
                        progress=lambda m: self.after(0, self._set_status, m),
                    )
                    self.after(0, self._done_video, result)
                except Exception as exc:  # noqa: BLE001
                    self.after(0, self._fail, str(exc))

            threading.Thread(target=work_video, daemon=True).start()
            return

        out = os.path.splitext(inp)[0] + ".mp3"

        def work():
            try:
                result = render(
                    input_path=inp, output_path=out, kv=kv, prop_in=prop,
                    blades=blades, poles=poles, rpm_source=self.src_var.get(),
                    stereo=self.stereo_var.get(), timbre=self._timbre(poles),
                    progress=lambda m: self.after(0, self._set_status, m),
                )
                self.after(0, self._done, result)
            except Exception as exc:  # noqa: BLE001
                self.after(0, self._fail, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, result):
        self.bar.stop()
        self.gen_btn.config(state="normal")
        self._set_status(
            f"Wrote {result.output_path}  ({result.duration_s:.1f}s, "
            f"source={result.rpm_source}, peak {result.rpm_peak:.0f} RPM)")
        if messagebox.askyesno("Drone Sound", "Done! Open the output folder?"):
            _open_folder(os.path.dirname(os.path.abspath(result.output_path)))

    def _done_video(self, r):
        self.bar.stop()
        self.gen_btn.config(state="normal")
        self._set_status(
            f"Wrote {os.path.basename(r.video_path)}  (offset {r.delta_s:+.2f}s, "
            f"log {r.log_event}={r.log_event_time:.2f}s <-> video "
            f"{r.video_event_time:.2f}s). Trimmed clip: "
            f"{os.path.basename(r.trimmed_audio_path)} -> place at video "
            f"t={r.video_onset_s:.2f}s")
        if messagebox.askyesno("Drone Sound", "Done! Open the output folder?"):
            _open_folder(os.path.dirname(os.path.abspath(r.video_path)))

    def _fail(self, msg):
        self.bar.stop()
        self.gen_btn.config(state="normal")
        self._set_status("Error: " + msg)
        messagebox.showerror("Drone Sound", msg)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
