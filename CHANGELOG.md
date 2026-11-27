# Changelog

## 2.1.0
- Packaged as a single `FanForge.exe` (built automatically by GitHub Actions)
- Settings now live in `%APPDATA%\FanForge` (old `fan_config.json` is migrated automatically)
- "Start with Windows" option (scheduled task, runs elevated, starts minimised)
- Built-in "Check for updates" (also checks quietly at startup)
- Master "All fans" curve with Silent / Balanced / Performance presets and a link toggle
- Per-fan curves, live RPM, safety override at 85 C, gradual ramp-down
