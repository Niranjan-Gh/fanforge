# FanForge

Automatic fan curves for your CPU and case fans on Windows. Reads CPU temperature
and drives every fan header your motherboard exposes, with a draggable curve per
fan plus an **All fans** master curve.

Built on [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (MPL-2.0).

## Features
- Per-fan temperature -> speed curves (drag points, double-click to add, right-click to remove)
- **All fans** master curve with Silent / Balanced / Performance presets and a "link all fans" switch
- Live CPU temperature, fan RPM and current speed
- Safety: every fan goes to 100% at 85 C or if the CPU temperature can't be read; speed ramps down gradually
- Closing the app (or "Reset all to BIOS control") hands fans back to the BIOS
- Optional "Start with Windows" (scheduled task, runs elevated, starts minimised)
- Built-in update check against GitHub releases

## Install
1. Install the hardware driver once (recent LibreHardwareMonitor needs it), then reboot:
   ```powershell
   winget install namazso.PawnIO
   ```
2. Download `FanForge.exe` from the
   [latest release](https://github.com/Niranjan-Gh/fanforge/releases/latest) and run it
   (it asks for admin rights - needed for hardware access).
3. In the BIOS, set your fan headers to PWM/Auto mode (not "Full speed", and not DC for 4-pin fans),
   otherwise software control can be ignored.

Windows SmartScreen / antivirus may warn about an unsigned, PyInstaller-built exe. The release
includes a `.sha256` file so you can verify the download, and the whole build is in
`.github/workflows/release.yml`.

Settings are stored in `%APPDATA%\FanForge\fan_config.json`.

## Run from source
```powershell
pip install -r requirements.txt
.\scripts\fetch_lhm.ps1        # downloads LibreHardwareMonitor DLLs into .\lib
python fanforge.py
```

## Build the exe locally
```powershell
.\build.ps1                    # -> dist\FanForge.exe
```

## Shipping an update
```powershell
.\release.ps1 2.2.0
```
This bumps `VERSION`, commits, tags `v2.2.0` and pushes. GitHub Actions builds the exe and
attaches it to a new release; running copies of FanForge offer the update via **Check for updates**.

First-time setup of the GitHub repo: `.\publish.ps1` (needs `git` and the GitHub CLI `gh`).
The repo must be **public** for the in-app update check to work.

## Limitations
- Laptops usually can't be controlled (fans belong to the embedded controller).
- Fans on USB hubs / vendor RGB-fan controllers may not be exposed.
- Not every motherboard Super I/O chip is supported by LibreHardwareMonitor.

## Disclaimer
You're controlling cooling hardware. Use at your own risk and keep an eye on temperatures
the first time you run a new curve.

## License
MIT. LibreHardwareMonitor is MPL-2.0.
