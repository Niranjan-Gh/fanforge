# FanForge

Automatic fan curves for your CPU and case fans on Windows. Reads CPU temperature
and controls the fan headers you enable, with a draggable curve per fan and an
**All fans** master curve. The interface uses classic Windows styling on modern Windows.

Built on [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (MPL-2.0).

## Features
- Per-fan temperature -> speed curves (drag points, double-click to add, right-click to remove)
- **All fans** master curve with Silent / Balanced / Performance presets and a "link all fans" switch
- Live CPU temperature, fan RPM and current speed
- Requests 100% duty on enabled fans at 85 ?C or when the CPU sample is invalid, late, or unavailable
- Slows fans gradually and attempts to restore firmware control on reset, normal exit, or a header write failure
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

Settings are validated and saved by replacing `%APPDATA%\FanForge\fan_config.json`
atomically. Existing curves load without granting control of any header. A corrupt
file starts FanForge in monitoring mode and displays a settings error.

## Enable a fan header

1. Trace the physical connection and match it to the controller and header in the
   motherboard documentation. Generic labels can refer to pump outputs. Leave
   pumps and any unidentified output under firmware control.
2. Open the fan's tab and check **Allow software control**. FanForge blocks
   outputs whose reported names or identifiers contain ?pump?. Opt-ins persist
   across restarts. After changing hardware or firmware, clear old opt-ins and
   verify the mapping again before enabling control.
3. Verify that the fan starts and stays spinning at the lowest duty it will use.
   Test each curve you use, including the linked master curve and presets.
   The editor's 20% floor is a software limit and cannot guarantee spin-up.
4. Keep firmware fan curves and thermal shutdown configured. Use **Reset all to
   BIOS control** to pause automatic control and request firmware control.

A failed write disables subsequent speed commands for the affected header for this
session and attempts firmware restore. Other enabled headers continue independently.
Once the cause is fixed, toggle the affected header off and back on to retry.

## Run from source
```powershell
pip install -r requirements.txt
.\scripts\fetch_lhm.ps1        # downloads LibreHardwareMonitor DLLs into .\lib
python fanforge.py
```

## Test and build locally

```powershell
python -m unittest discover -s tests -v
```

The tests use fakes and do not open the driver or write to hardware. The Windows
checks workflow runs the tests and builds the executable on pull requests.
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
- Controller support varies. FanForge requires an advertised 100% software
  maximum. Displayed duty is the requested value and RPM is optional telemetry.
  A successful API call does not prove the hardware changed modes or speed.
- Polling uses a monotonic clock. An update or poll gap over three seconds triggers
  a full-speed request at the next opportunity. The hardware worker polls separately
  from the window, so moving the window does not suspend control. LibreHardwareMonitor does not
  expose a reliable timestamp for each sensor sample here. A cached value that
  remains plausible during successful polls can be indistinguishable from a steady
  temperature.
- The hardware worker runs inside FanForge. A blocked driver call, frozen process,
  forced termination, Windows shutdown, or power loss can prevent the failsafe or
  cleanup from running. Firmware may retain the last duty setting. There is no
  independent hardware watchdog or fan-stall interlock. On normal exit, the window
  waits for the current driver call and firmware restore to finish.
- The Windows mutex blocks a second FanForge instance across sessions. Other fan
  utilities can still contend for the same hardware. Run one controller at a time.

## Disclaimer
You're controlling cooling hardware. Use at your own risk and keep an eye on temperatures
the first time you run a new curve.

## License
MIT. LibreHardwareMonitor is MPL-2.0.
