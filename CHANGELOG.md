# Changelog

## 2.3.0
- Require per-header opt-in, preserve existing curves, and leave recognized pump outputs under firmware control.
- Request full duty on enabled fans during overheating, sensor failures, and late polls. Isolate failed headers and attempt firmware restore.
- Validate and save settings atomically, and prevent concurrent FanForge instances across Windows sessions.
- Add a classic Windows interface with compact controls and higher-contrast graphs.
- Pin build dependencies, verify the hardware library download, and add Windows build and regression checks.
- Document sensor freshness, watchdog, and firmware restore limitations.

## 2.1.0
- Packaged as a single `FanForge.exe` (built automatically by GitHub Actions)
- Settings now live in `%APPDATA%\FanForge` (old `fan_config.json` is migrated automatically)
- "Start with Windows" option (scheduled task, runs elevated, starts minimised)
- Built-in "Check for updates" (also checks quietly at startup)
- Master "All fans" curve with Silent / Balanced / Performance presets and a link toggle
- Per-fan curves, live RPM, safety override at 85 C, gradual ramp-down
