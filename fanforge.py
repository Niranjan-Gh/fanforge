"""
FanForge - automatic fan curves for CPU + case fans (Windows)

FanForge monitors by default. Only explicitly enabled fan headers are controlled.
"""
import ctypes
import json
from copy import deepcopy
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

from fanforge_safety import (ARMED_KEY, LINK_KEY, MIN_PCT, SAFE_TEMP, Header,
                             fresh_temperature, valid_temperature, validate_config, write_config)

APP_NAME = "FanForge"
VERSION = "2.3.0"
UPDATE_REPO = "Niranjan-Gh/fanforge"   # filled in by publish.ps1
TASK_NAME = "FanForge"

FROZEN = getattr(sys, "frozen", False)
SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
RES_DIR = getattr(sys, "_MEIPASS", SCRIPT_DIR)          # bundled files live here
DATA_DIR = os.path.join(os.environ.get("APPDATA", SCRIPT_DIR), APP_NAME)
CONFIG = os.path.join(DATA_DIR, "fan_config.json")
LEGACY_CONFIGS = (
    os.path.join(SCRIPT_DIR, "fan_config.json"),
    os.path.join(os.path.expanduser("~"), "FanForge", "fan_config.json"),
)

MASTER_KEY = "__master__"
POLL_MS = 1000
DEFAULT_CURVE = [[30, 25], [45, 35], [60, 55], [70, 75], [80, 100]]
PRESETS = {
    "Silent": [[30, 20], [50, 25], [65, 40], [75, 65], [82, 100]],
    "Balanced": DEFAULT_CURVE,
    "Performance": [[30, 40], [45, 55], [55, 70], [65, 85], [75, 100]],
}


# ----------------------------------------------------------------- helpers
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def curve_value(points, t):
    pts = points
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= t <= x2:
            if x2 == x1:
                return y2
            return y1 + (y2 - y1) * (t - x1) / (x2 - x1)
    return pts[-1][1]


def load_config():
    source = next((path for path in (CONFIG, *LEGACY_CONFIGS)
                   if os.path.isfile(path)), None)
    if source is None:
        return {}, ""
    try:
        with open(source, encoding="utf-8-sig") as stream:
            return validate_config(json.load(stream)), ""
    except (OSError, ValueError) as exc:
        return {}, f"Settings could not be loaded: {exc}"


def pair_key(name):
    return " ".join(name.replace("Control", "").split())


def find_dll_dir():
    directory = os.path.join(RES_DIR, "lib")
    return directory if os.path.isfile(os.path.join(directory, "LibreHardwareMonitorLib.dll")) else None


def parse_version(s):
    nums = [int(n) for n in re.findall(r"\d+", str(s).split("-")[0])]
    return tuple((nums + [0, 0, 0])[:3])


def fetch_latest():
    """Returns (tag, release_url) of the latest GitHub release."""
    if UPDATE_REPO.startswith("YOUR_"):
        raise RuntimeError("Update repo isn't configured yet (UPDATE_REPO in fanforge.py).")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
        headers={"User-Agent": f"{APP_NAME}/{VERSION}",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.load(r)
    return data["tag_name"], data["html_url"]


# --------------------------------------------------- start with Windows
def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=5, creationflags=0x08000000)  # CREATE_NO_WINDOW


def launch_command():
    if FROZEN:
        return f'"{sys.executable}" --minimized'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{os.path.abspath(__file__)}" --minimized'


def startup_enabled():
    try:
        return _run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0
    except Exception:
        return False


def set_startup(enable):
    try:
        if enable:
            r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", launch_command(),
                      "/SC", "ONLOGON", "/RL", "HIGHEST", "/DELAY", "0000:15", "/F"])
        else:
            r = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- hardware
class Hardware:
    def __init__(self):
        dll_dir = find_dll_dir()
        if dll_dir is None:
            raise FileNotFoundError(
                "LibreHardwareMonitorLib.dll not found.\n\n"
                "Running from source: run scripts\\fetch_lhm.ps1 first.\n"
                "Using the .exe: re-download FanForge from the GitHub releases page."
            )
        sys.path.append(dll_dir)
        import clr  # from pythonnet

        for name in sorted(os.listdir(dll_dir)):  # dependencies first (HidSharp etc.)
            if name.lower().endswith(".dll") and name != "LibreHardwareMonitorLib.dll":
                try:
                    clr.AddReference(os.path.join(dll_dir, name))
                except Exception:
                    pass
        clr.AddReference(os.path.join(dll_dir, "LibreHardwareMonitorLib.dll"))
        from LibreHardwareMonitor.Hardware import Computer

        self.pc = Computer()
        self.pc.IsCpuEnabled = True
        self.pc.IsMotherboardEnabled = True
        self.pc.IsControllerEnabled = True
        try:
            self.pc.Open()

            self.cpu_temps, self.fans, self.controls = [], [], []
            self.update()
            for hw in self._walk():
                hw_type = str(hw.HardwareType)
                for s in hw.Sensors:
                    st = str(s.SensorType)
                    if st == "Temperature" and hw_type == "Cpu":
                        self.cpu_temps.append(s)
                    elif st == "Fan":
                        self.fans.append(s)
                    elif st == "Control" and s.Control is not None:
                        self.controls.append(s)

        except Exception:
            self.close()
            raise

    def _walk(self):
        for hw in self.pc.Hardware:
            yield hw
            for sub in hw.SubHardware:
                yield sub

    def update(self):
        for hw in self._walk():
            hw.Update()

    def cpu_temp(self):
        preferred = {"CPU Package", "Core (Tctl/Tdie)", "CPU (Tctl/Tdie)",
                     "Core Max", "Tctl/Tdie"}
        values = []
        for sensor in self.cpu_temps:
            value = valid_temperature(sensor.Value)
            if value is None and sensor.Name in preferred:
                return None
            if value is not None:
                values.append(value)
        return max(values, default=None)

    def rpm_for(self, control):
        hw_id = str(control.Hardware.Identifier)
        key = pair_key(control.Name)
        for f in self.fans:
            if str(f.Hardware.Identifier) == hw_id and pair_key(f.Name) == key:
                return f
        return None

    @staticmethod
    def set_speed(control, pct):
        c = control.Control
        minimum, maximum = float(c.MinSoftwareValue), float(c.MaxSoftwareValue)
        if not 0 <= minimum <= maximum <= 100 or maximum < 100:
            raise ValueError("Controller does not advertise a 100% software range")
        c.SetSoftware(float(max(minimum, pct)))

    @staticmethod
    def release(control):
        control.Control.SetDefault()

    def close(self):
        try:
            self.pc.Close()
        except Exception:
            pass


class FanWorker:
    """Runs driver calls and fan control independently of the Tk event loop."""

    def __init__(self, hw, outputs):
        self.hw = hw
        self.outputs = outputs
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="FanForge hardware")
        self.settings = ()
        self.snapshot = None
        self.failures = []
        self.last_poll = None
        self.smooth = None
        self.rearmed = {}

    def run(self):
        try:
            while not self.stop.is_set():
                started = time.monotonic()
                self.poll(started)
                self.stop.wait(max(0, POLL_MS / 1000 - (time.monotonic() - started)))
        finally:
            for cid, header, _ in self.outputs:
                if not header.restore() and not header.restore():
                    self.failures.append(cid)

    def poll(self, started):
        try:
            self.hw.update()
            raw = fresh_temperature(self.hw.cpu_temp(), started, time.monotonic(),
                                    self.last_poll)
            self.last_poll = started
        except Exception:
            raw = None
            self.last_poll = None
        settings = self.settings
        warning = ""
        if raw is None:
            warning = "CPU reading unavailable or late. Requesting full speed on enabled fans."
        elif raw >= SAFE_TEMP:
            warning = "CPU temperature is high. Requesting full speed on enabled fans."
        try:
            self.drive(raw, settings)
        except Exception:
            self.drive(None, settings)
            warning = "Control loop error. Requesting full speed on enabled fans."
        readings = []
        for _, header, rpm in self.outputs:
            note = header.fault
            if header.owned and header.current is None:
                note = "Firmware restore failed. " + note
            try:
                value = rpm.Value if rpm is not None else None
                rpm_text = f"{int(value)} RPM" if value is not None and value >= 0 else ""
            except Exception:
                rpm_text = ""
            readings.append((header.current, note, rpm_text))
        if any(note for _, note, _ in readings):
            warning = "Fan header fault. Check the affected header tab."
        # A single immutable snapshot replaces the previous one. No UI backlog.
        self.snapshot = (raw, warning, tuple(readings))

    def drive(self, raw, settings):
        emergency = raw is None or raw >= SAFE_TEMP
        if emergency or self.smooth is None:
            self.smooth = raw
        else:
            self.smooth = 0.6 * self.smooth + 0.4 * raw
        for (cid, header, _), (points, generation) in zip(self.outputs, settings):
            if self.stop.is_set():
                return
            if points is None:
                header.restore()
                continue
            if generation != self.rearmed.get(cid, 0) and header.restore():
                header.fault = ""
                self.rearmed[cid] = generation
            if emergency:
                header.apply(100, emergency=True)
            else:
                header.apply(curve_value(points, max(raw, self.smooth)))

    def finish(self):
        self.stop.set()
        self.thread.join()
        return self.failures


# ------------------------------------------------------------ curve editor
class CurveEditor(tk.Canvas):
    W, H = 466, 240
    L, R, T, B = 46, 16, 16, 34
    T0, T1 = 20, 100

    def __init__(self, master, points, on_change):
        super().__init__(master, width=self.W, height=self.H,
                         bg="#ffffff", highlightthickness=0, relief="sunken", borderwidth=2)
        self.points = [list(p) for p in points]
        self.on_change = on_change
        self.drag = None
        self.live_temp = None
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._add)
        self.bind("<Button-3>", self._remove)
        self.draw()

    def tx(self, t):
        return self.L + (t - self.T0) / (self.T1 - self.T0) * (self.W - self.L - self.R)

    def ty(self, p):
        return self.H - self.B - p / 100 * (self.H - self.T - self.B)

    def fx(self, x):
        return self.T0 + (x - self.L) / (self.W - self.L - self.R) * (self.T1 - self.T0)

    def fy(self, y):
        return (self.H - self.B - y) / (self.H - self.T - self.B) * 100

    def set_points(self, points):
        self.points = [list(p) for p in points]
        self.draw()

    def set_live(self, temp):
        if temp == self.live_temp:
            return
        self.live_temp = temp
        self.draw_live()

    def _nearest(self, e):
        best, best_d = None, 14
        for i, (t, p) in enumerate(self.points):
            d = ((self.tx(t) - e.x) ** 2 + (self.ty(p) - e.y) ** 2) ** 0.5
            if d < best_d:
                best, best_d = i, d
        return best

    def _press(self, e):
        self.drag = self._nearest(e)

    def _move(self, e):
        i = self.drag
        if i is None:
            return
        lo = self.points[i - 1][0] + 1 if i > 0 else self.T0
        hi = self.points[i + 1][0] - 1 if i < len(self.points) - 1 else self.T1
        self.points[i][0] = int(max(lo, min(hi, round(self.fx(e.x)))))
        self.points[i][1] = int(max(MIN_PCT, min(100, round(self.fy(e.y)))))
        self.draw()

    def _release(self, _e):
        if self.drag is not None:
            self.drag = None
            self.on_change()

    def _add(self, e):
        if self._nearest(e) is not None:
            return
        t = int(round(self.fx(e.x)))
        if not (self.T0 <= t <= self.T1) or any(p[0] == t for p in self.points):
            return
        p = int(max(MIN_PCT, min(100, round(self.fy(e.y)))))
        self.points.append([t, p])
        self.points.sort()
        self.draw()
        self.on_change()

    def _remove(self, e):
        i = self._nearest(e)
        if i is not None and len(self.points) > 2:
            del self.points[i]
            self.draw()
            self.on_change()

    def draw(self):
        self.delete("all")
        for t in range(self.T0, self.T1 + 1, 10):
            x = self.tx(t)
            self.create_line(x, self.T, x, self.H - self.B, fill="#d8e3f0")
            self.create_text(x, self.H - self.B + 14, text=f"{t}\u00b0", fill="#28466a",
                             font=("Tahoma", 8))
        for p in range(0, 101, 20):
            y = self.ty(p)
            self.create_line(self.L, y, self.W - self.R, y, fill="#d8e3f0")
            self.create_text(self.L - 6, y, text=f"{p}%", fill="#28466a",
                             anchor="e", font=("Tahoma", 8))
        pts = sorted(self.points)
        flat = []
        for t, p in pts:
            flat += [self.tx(t), self.ty(p)]
        if len(flat) >= 4:
            self.create_line(*flat, fill="#1458b0", width=2)
        for t, p in pts:
            x, y = self.tx(t), self.ty(p)
            self.create_oval(x - 6, y - 6, x + 6, y + 6, fill="#1458b0", outline="#ffffff", width=2)
        self.draw_live()

    def draw_live(self):
        self.delete("live")
        if self.live_temp is not None:
            lt = max(self.T0, min(self.T1, self.live_temp))
            x = self.tx(lt)
            self.create_line(x, self.T, x, self.H - self.B, fill="#a74200", dash=(4, 3), tags="live")
            y = self.ty(curve_value(self.points, lt))
            self.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#a74200", outline="", tags="live")


def checkbox_images(style):
    """Keep the classic layout and draw a clear tick for the selected state."""
    images = []
    tick = (
        "      ##",
        "     ###",
        "#   ### ",
        "## ###  ",
        " ####   ",
        "  ##    ",
    )
    for selected, disabled in ((False, False), (True, False),
                               (False, True), (True, True)):
        box = tk.PhotoImage(master=style.master, width=16, height=14)
        box.put("#a5a091" if disabled else "#67819c", to=(0, 0, 13, 13))
        box.put("#ece9d8" if disabled else "#e2ebf4", to=(1, 1, 12, 12))
        box.put("#f3f1e8" if disabled else "#ffffff", to=(2, 2, 12, 12))
        if selected:
            color = "#777465" if disabled else "#205b24"
            for y, line in enumerate(tick, start=3):
                box.put("{" + " ".join(color if pixel == "#" else
                                       "#f3f1e8" if disabled else "#ffffff"
                                       for pixel in line) + "}", to=(3, y))
        images.append(box)

    unchecked, checked, disabled, disabled_checked = images
    style.element_create("FanForge.indicator", "image", unchecked,
                         ("disabled", "selected", disabled_checked),
                         ("disabled", disabled), ("selected", checked))
    layout = style.layout("TCheckbutton")
    elements = layout[0][1]["children"][0][1]["children"][0][1]["children"]
    elements[0] = ("FanForge.indicator", elements[0][1])
    style.layout("TCheckbutton", layout)
    return images


# --------------------------------------------------------------------- app
class App(tk.Tk):
    def __init__(self, hw):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION} - automatic fan curves")
        self.resizable(False, False)
        self.hw = hw
        self.cfg, self.config_error = load_config()
        self.tick_id = None
        self.closing = False
        self.configure(background="#ece9d8")
        self.option_add("*Font", ("Tahoma", 9))
        style = ttk.Style(self)
        style.theme_use("classic")
        self.checkbox_images = checkbox_images(style)
        style.configure(".", background="#ece9d8", foreground="#18263a", font=("Tahoma", 9))
        style.configure("TButton", padding=(7, 3), relief="raised")
        style.configure("TNotebook.Tab", padding=(7, 3))
        style.map("TNotebook.Tab", background=[("selected", "#fcfaf0")])
        style.configure("Status.TLabel", relief="sunken", padding=(6, 4))
        tk.Label(self, text="FanForge  |  Cooling Control", bg="#2056a6", fg="white",
                 font=("Tahoma", 11, "bold"), anchor="w", padx=9, pady=4).pack(fill="x")
        self.auto = tk.BooleanVar(value=True)
        self.linked = tk.BooleanVar(value=bool(self.cfg.get(LINK_KEY, False)))
        self.startup = tk.BooleanVar(value=startup_enabled())
        self.rows = []
        self.master_editor = None
        self._update_result = None
        self._checking = False

        head = ttk.Frame(self, padding=(12, 10))
        head.pack(fill="x")
        self.temp_var = tk.StringVar(value="--.- \u00b0C")
        ttk.Label(head, text="CPU", font=("Tahoma", 10)).pack(side="left")
        ttk.Label(head, textvariable=self.temp_var,
                  font=("Tahoma", 20, "bold")).pack(side="left", padx=(8, 20))
        ttk.Checkbutton(head, text="Auto curves", variable=self.auto,
                        command=self._auto_toggled).pack(side="left")
        ttk.Button(head, text="Reset all to BIOS control",
                   command=self._reset_bios).pack(side="right")

        self.status = tk.StringVar(value=self.config_error)
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", wraplength=480).pack(
            fill="x", padx=12)

        if not self.hw.controls:
            found = ", ".join(f.Name for f in self.hw.fans) or "none"
            ttk.Label(
                self, wraplength=480, padding=16,
                text=("No controllable fan headers were found.\n\n"
                      f"Fan speed sensors seen: {found}\n\n"
                      "Possible causes: the PawnIO driver isn't installed "
                      "(run: winget install namazso.PawnIO, then reboot), an "
                      "unsupported motherboard / Super I/O chip, fans on a hub or "
                      "RGB controller, or a laptop (fans are controlled by the "
                      "embedded controller)."),
            ).pack()
        else:
            nb = ttk.Notebook(self)
            nb.pack(padx=12, pady=8)
            self._build_master_tab(nb)
            for c in self.hw.controls:
                self._build_tab(nb, c)

        ttk.Label(self, foreground="#6b7280", padding=(12, 0, 12, 4),
                  text="Drag points  \u2022  double-click to add  \u2022  right-click to remove"
                  ).pack(anchor="w")

        foot = ttk.Frame(self, padding=(12, 0, 12, 10))
        foot.pack(fill="x")
        ttk.Label(foot, text=f"v{VERSION}", foreground="#6b7280").pack(side="left")
        ttk.Checkbutton(foot, text="Start with Windows", variable=self.startup,
                        command=self._startup_toggled).pack(side="left", padx=14)
        ttk.Button(foot, text="Check for updates",
                   command=lambda: self._check_updates(manual=True)).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._close)
        if "--minimized" in sys.argv:
            self.iconify()
        outputs = tuple((r["id"], r.pop("header"), r.pop("rpm")) for r in self.rows)
        self.worker = FanWorker(hw, outputs)
        self.rendered = None
        self._publish_settings()
        self.tick_id = self.after(200, self._tick)
        self.after(4000, lambda: self._check_updates(manual=False))
        self.worker.thread.start()

    # ---- tabs
    def _build_master_tab(self, nb):
        frame = ttk.Frame(nb, padding=10)
        nb.add(frame, text="\u2605 All fans")
        self.master_editor = CurveEditor(
            frame, self.cfg.get(MASTER_KEY, DEFAULT_CURVE), self._save)
        self.master_editor.grid(row=0, column=0, columnspan=4)

        ttk.Checkbutton(
            frame, variable=self.linked, command=self._save,
            text="Link ALL fans to this curve (overrides the individual fan curves)",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 2))

        ttk.Label(frame, text="Presets:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        for i, name in enumerate(PRESETS):
            ttk.Button(frame, text=name,
                       command=lambda n=name: self._apply_preset(n)
                       ).grid(row=2, column=i + 1, sticky="w", pady=(4, 0), padx=(0, 4))

    def _apply_preset(self, name):
        self.master_editor.set_points(PRESETS[name])
        self._save()

    def _build_tab(self, nb, control):
        cid = str(control.Identifier)
        frame = ttk.Frame(nb, padding=10)
        nb.add(frame, text=control.Name)
        editor = CurveEditor(frame, self.cfg.get(cid, DEFAULT_CURVE), self._save)
        editor.grid(row=0, column=0, columnspan=3)

        rpm_var, pct_var = tk.StringVar(value="-"), tk.StringVar(value="-")
        note_var = tk.StringVar(value="")
        ttk.Label(frame, text="Speed:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(frame, textvariable=pct_var, width=8).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(frame, textvariable=rpm_var).grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Label(frame, textvariable=note_var, foreground="#b45309").grid(
            row=3, column=0, columnspan=3, sticky="w")

        btns = ttk.Frame(frame)
        btns.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(btns, text="Reset curve",
                   command=lambda: (editor.set_points(DEFAULT_CURVE), self._save())).pack(side="left")
        ttk.Button(btns, text="Copy curve to all fans",
                   command=lambda: self._copy_to_all(editor)).pack(side="left", padx=6)

        pump = "pump" in f"{control.Name} {cid}".lower()
        enabled = tk.BooleanVar(value=not pump and cid in self.cfg.get(ARMED_KEY, []))
        header = Header(lambda pct: self.hw.set_speed(control, pct),
                        lambda: self.hw.release(control))
        row = {"id": cid, "editor": editor, "enabled": enabled, "header": header,
               "rpm": self.hw.rpm_for(control), "rpm_var": rpm_var,
               "pct_var": pct_var, "note_var": note_var, "generation": 0}
        toggle = ttk.Checkbutton(frame, text="Allow software control",
                                 variable=enabled, command=lambda: self._enable_header(row))
        toggle.grid(row=4, column=0, columnspan=3, sticky="w")
        if pump:
            toggle.state(["disabled"])
            note_var.set("Pump header stays under firmware control.")
            row["pump"] = True
        self.rows.append(row)

    def _enable_header(self, row):
        enabled = row["enabled"]
        if enabled.get():
            if not messagebox.askyesno(
                APP_NAME, "Confirm this output is a verified fan header without a pump "
                "and the fan can run at the minimum duty in its curve. Enable software control?"
            ):
                enabled.set(False)
                return
            row["generation"] += 1
        if not self._save(publish_on_error=False):
            enabled.set(False)
            self._publish_settings()

    def _copy_to_all(self, src):
        for r in self.rows:
            r["editor"].set_points(src.points)
        self._save()

    # ---- settings / startup / updates
    def _save(self, publish_on_error=True):
        data = dict(self.cfg)
        data.update({r["id"]: r["editor"].points for r in self.rows})
        if self.master_editor is not None:
            data[MASTER_KEY] = self.master_editor.points
        data[LINK_KEY] = self.linked.get()
        detected = {r["id"] for r in self.rows}
        enabled = set(data.get(ARMED_KEY, [])) - detected
        enabled.update(r["id"] for r in self.rows if r["enabled"].get())
        data[ARMED_KEY] = sorted(enabled)
        try:
            write_config(CONFIG, data)
        except (OSError, ValueError) as exc:
            self.config_error = f"Settings could not be saved: {exc}"
            self.status.set(self.config_error)
            if publish_on_error:
                self._publish_settings()
            return False
        self.cfg = deepcopy(data)
        self.config_error = ""
        self._publish_settings()
        return True

    def _startup_toggled(self):
        want = self.startup.get()
        ok, msg = set_startup(want)
        if not ok:
            self.startup.set(not want)
            messagebox.showerror(APP_NAME, f"Couldn't change the startup task:\n{msg}")

    def _check_updates(self, manual):
        if self._checking or self.closing:
            return
        self._checking = True
        self._update_result = None

        def work():
            try:
                self._update_result = ("ok", fetch_latest())
            except Exception as e:
                self._update_result = ("err", str(e))

        threading.Thread(target=work, daemon=True).start()
        self.after(400, lambda: self._poll_update(manual))

    def _poll_update(self, manual):
        if self.closing:
            return
        res = self._update_result
        if res is None:
            self.after(400, lambda: self._poll_update(manual))
            return
        self._checking = False
        kind, val = res
        if kind == "err":
            if manual:
                messagebox.showinfo(APP_NAME, f"Couldn't check for updates:\n{val}")
            return
        tag, url = val
        if parse_version(tag) > parse_version(VERSION):
            if messagebox.askyesno(
                    APP_NAME,
                    f"{APP_NAME} {tag} is available (you have v{VERSION}).\n\n"
                    "Open the download page?"):
                webbrowser.open(url)
        elif manual:
            messagebox.showinfo(APP_NAME, f"You're up to date (v{VERSION}).")

    # ---- fan control
    def _publish_settings(self):
        linked = self.master_editor if self.linked.get() else None
        active = self.auto.get()
        self.worker.settings = tuple(
            (tuple(tuple(point) for point in (linked or row["editor"]).points)
             if active and row["enabled"].get() else None, row["generation"])
            for row in self.rows
        )

    def _auto_toggled(self):
        self._publish_settings()

    def _reset_bios(self):
        self.auto.set(False)
        self._publish_settings()

    def _tick(self):
        if self.closing:
            if self.worker.thread.is_alive():
                self.tick_id = self.after(200, self._tick)
            else:
                self.destroy()
            return
        try:
            snapshot = self.worker.snapshot
            if snapshot is None:
                return
            raw, warning, readings = snapshot
            if snapshot is not self.rendered:
                self.rendered = snapshot
                self._set_text(self.temp_var, "N/A" if raw is None else f"{raw:.1f} ?C")
                for row, (current, note, rpm) in zip(self.rows, readings):
                    self._set_text(row["note_var"], note[:76] or (
                        "Pump header stays under firmware control." if row.get("pump") else ""))
                    self._set_text(row["pct_var"], "Fault" if note else
                                   "BIOS" if current is None else f"{current}%")
                    self._set_text(row["rpm_var"], rpm)
            self._set_text(self.status, warning or self.config_error or "Monitoring CPU temperature")
            for editor in (self.master_editor, *(row["editor"] for row in self.rows)):
                if editor is not None and editor.winfo_viewable():
                    editor.set_live(raw)
                    break
        finally:
            self.tick_id = self.after(200, self._tick)

    @staticmethod
    def _set_text(variable, value):
        if variable.get() != value:
            variable.set(value)

    def report_callback_exception(self, exc_type, exc, traceback):
        self._reset_bios()
        self.config_error = f"Interface error. Automatic control paused: {exc}"
        self.status.set(self.config_error)

    def _close(self):
        self.closing = True
        self.worker.stop.set()
        self.status.set("Restoring firmware control. Waiting for the hardware driver?")


def main():
    if not is_admin():
        args = sys.argv[1:] if FROZEN else sys.argv
        params = subprocess.list2cmdline(args)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.CreateMutexW(None, False, r"Global\FanForge.HardwareControl")
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    hw = app = None
    failure = None
    try:
        if error == 183:  # ERROR_ALREADY_EXISTS
            if "--minimized" not in sys.argv:
                ctypes.windll.user32.MessageBoxW(None, "FanForge is already running.", APP_NAME, 0)
            return
        hw = Hardware()
        app = App(hw)
        app.mainloop()
    except Exception as exc:
        failure = str(exc)
    finally:
        failures = []
        try:
            if app is not None:
                failures = app.worker.finish()
        finally:
            if hw is not None:
                hw.close()
            kernel.CloseHandle(handle)
        if failures:
            ctypes.windll.user32.MessageBoxW(
                None, "Firmware control could not be confirmed for:\n" + "\n".join(failures),
                APP_NAME, 0x10)
    if failure:
        ctypes.windll.user32.MessageBoxW(None, failure, APP_NAME, 0x10)


if __name__ == "__main__":
    main()
