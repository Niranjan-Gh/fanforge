"""
FanForge - automatic fan curves for CPU + case fans (Windows)

Keep the app running (minimised is fine). When you close it, fans go back
to BIOS control. Tick "Start with Windows" so curves are active after every
boot.
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

APP_NAME = "FanForge"
VERSION = "2.2.0"
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
LINK_KEY = "__linked__"

MIN_PCT = 20          # lowest speed a curve point can be set to
SAFE_TEMP = 85        # at/above this CPU temp every fan goes to 100% instantly
RAMP_DOWN = 2         # max % the fan may drop per tick (avoids speed hunting)
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
    pts = sorted(points)
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
    """Load saved curves. Migrates an old fan_config.json on first run."""
    if not os.path.exists(CONFIG):
        for old in LEGACY_CONFIGS:
            if os.path.exists(old):
                try:
                    os.makedirs(DATA_DIR, exist_ok=True)
                    shutil.copyfile(old, CONFIG)
                except Exception:
                    pass
                break
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def pair_key(name):
    return " ".join(name.replace("Control", "").split())


def find_dll_dir():
    for d in (os.path.join(RES_DIR, "lib"), RES_DIR, SCRIPT_DIR,
              os.path.join(SCRIPT_DIR, "lib")):
        if os.path.exists(os.path.join(d, "LibreHardwareMonitorLib.dll")):
            return d
    return None


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
                          creationflags=0x08000000)  # CREATE_NO_WINDOW


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

    def _walk(self):
        for hw in self.pc.Hardware:
            yield hw
            for sub in hw.SubHardware:
                yield sub

    def update(self):
        for hw in self._walk():
            hw.Update()

    def cpu_temp(self):
        vals = {s.Name: s.Value for s in self.cpu_temps if s.Value is not None}
        if not vals:
            return None
        for name in ("CPU Package", "Core (Tctl/Tdie)", "CPU (Tctl/Tdie)",
                     "Core Max", "Tctl/Tdie"):
            if name in vals:
                return float(vals[name])
        return float(max(vals.values()))

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
        try:
            pct = max(float(c.MinSoftwareValue), min(float(c.MaxSoftwareValue), pct))
        except Exception:
            pass
        c.SetSoftware(float(pct))

    @staticmethod
    def release(control):
        try:
            control.Control.SetDefault()
        except Exception:
            pass

    def release_all(self):
        for c in self.controls:
            self.release(c)

    def close(self):
        try:
            self.pc.Close()
        except Exception:
            pass


# ------------------------------------------------------------ curve editor
class CurveEditor(tk.Canvas):
    W, H = 480, 300
    L, R, T, B = 46, 16, 16, 34
    T0, T1 = 20, 100

    def __init__(self, master, points, on_change):
        super().__init__(master, width=self.W, height=self.H,
                         bg="#14171c", highlightthickness=0)
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
        self.live_temp = temp
        self.draw()

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
            self.create_line(x, self.T, x, self.H - self.B, fill="#262b33")
            self.create_text(x, self.H - self.B + 14, text=f"{t}\u00b0", fill="#8a93a0",
                             font=("Segoe UI", 8))
        for p in range(0, 101, 20):
            y = self.ty(p)
            self.create_line(self.L, y, self.W - self.R, y, fill="#262b33")
            self.create_text(self.L - 6, y, text=f"{p}%", fill="#8a93a0",
                             anchor="e", font=("Segoe UI", 8))
        pts = sorted(self.points)
        flat = []
        for t, p in pts:
            flat += [self.tx(t), self.ty(p)]
        if len(flat) >= 4:
            self.create_line(*flat, fill="#4cc9f0", width=2)
        for t, p in pts:
            x, y = self.tx(t), self.ty(p)
            self.create_oval(x - 6, y - 6, x + 6, y + 6, fill="#4cc9f0", outline="#14171c", width=2)
        if self.live_temp is not None:
            lt = max(self.T0, min(self.T1, self.live_temp))
            x = self.tx(lt)
            self.create_line(x, self.T, x, self.H - self.B, fill="#ff9f43", dash=(4, 3))
            y = self.ty(curve_value(pts, lt))
            self.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#ff9f43", outline="")


# --------------------------------------------------------------------- app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {VERSION} - automatic fan curves")
        self.resizable(False, False)
        self.hw = Hardware()
        self.cfg = load_config()
        self.auto = tk.BooleanVar(value=True)
        self.linked = tk.BooleanVar(value=bool(self.cfg.get(LINK_KEY, False)))
        self.startup = tk.BooleanVar(value=startup_enabled())
        self.smooth = None
        self.rows = []
        self.master_editor = None
        self._update_result = None
        self._checking = False

        head = ttk.Frame(self, padding=(12, 10))
        head.pack(fill="x")
        self.temp_var = tk.StringVar(value="--.- \u00b0C")
        ttk.Label(head, text="CPU", font=("Segoe UI", 10)).pack(side="left")
        ttk.Label(head, textvariable=self.temp_var,
                  font=("Segoe UI", 20, "bold")).pack(side="left", padx=(8, 20))
        ttk.Checkbutton(head, text="Auto curves", variable=self.auto,
                        command=self._auto_toggled).pack(side="left")
        ttk.Button(head, text="Reset all to BIOS control",
                   command=self._reset_bios).pack(side="right")

        self.status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status, foreground="#b45309").pack(
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
        self.after(200, self._tick)
        self.after(4000, lambda: self._check_updates(manual=False))

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

        self.rows.append({"control": control, "id": cid, "editor": editor,
                          "rpm": self.hw.rpm_for(control), "rpm_var": rpm_var,
                          "pct_var": pct_var, "note_var": note_var, "current": None})

    def _copy_to_all(self, src):
        for r in self.rows:
            r["editor"].set_points(src.points)
        self._save()

    # ---- settings / startup / updates
    def _save(self):
        data = {r["id"]: r["editor"].points for r in self.rows}
        if self.master_editor is not None:
            data[MASTER_KEY] = self.master_editor.points
        data[LINK_KEY] = bool(self.linked.get())
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _startup_toggled(self):
        want = self.startup.get()
        ok, msg = set_startup(want)
        if not ok:
            self.startup.set(not want)
            messagebox.showerror(APP_NAME, f"Couldn't change the startup task:\n{msg}")

    def _check_updates(self, manual):
        if self._checking:
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
    def _auto_toggled(self):
        if not self.auto.get():
            self._reset_bios(keep_auto=True)

    def _reset_bios(self, keep_auto=False):
        self.hw.release_all()
        for r in self.rows:
            r["current"] = None
            r["pct_var"].set("BIOS")
        if not keep_auto:
            self.auto.set(False)

    def _tick(self):
        try:
            self.hw.update()
            raw = self.hw.cpu_temp()
            if raw is None:
                self.temp_var.set("n/a")
                self.status.set("Can't read CPU temperature - fans forced to 100% for safety.")
            else:
                self.temp_var.set(f"{raw:.1f} \u00b0C")
                self.status.set("")
                self.smooth = raw if self.smooth is None else 0.6 * self.smooth + 0.4 * raw
            live = self.smooth if raw is not None else None
            linked = self.linked.get() and self.master_editor is not None
            if self.master_editor is not None:
                self.master_editor.set_live(live)
            for r in self.rows:
                r["editor"].set_live(live)
                r["note_var"].set("Linked: this fan is following the \u2605 All fans curve." if linked else "")
                rpm = r["rpm"]
                r["rpm_var"].set(f"{int(rpm.Value)} RPM" if rpm is not None and rpm.Value is not None else "")
                if not self.auto.get():
                    continue
                points = self.master_editor.points if linked else r["editor"].points
                if raw is None or raw >= SAFE_TEMP:
                    target, panic = 100, True
                else:
                    target, panic = curve_value(points, self.smooth), False
                cur = r["current"]
                if cur is None or panic or target >= cur:
                    cur = target
                else:
                    cur = max(target, cur - RAMP_DOWN)
                r["current"] = cur
                self.hw.set_speed(r["control"], cur)
                r["pct_var"].set(f"{cur:.0f}%")
        except Exception as e:  # never let the loop die - keep fans safe
            self.status.set(f"Error: {e}")
        finally:
            self.after(POLL_MS, self._tick)

    def _close(self):
        self._save()
        self.hw.release_all()
        self.hw.close()
        self.destroy()


def main():
    if not is_admin():
        args = sys.argv[1:] if FROZEN else sys.argv
        params = " ".join(f'"{a}"' for a in args)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return
    try:
        App().mainloop()
    except Exception as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, str(e))


if __name__ == "__main__":
    main()
