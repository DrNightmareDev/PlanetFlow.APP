# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)

Self-hosted Planetary Industry manager for EVE Online.

If this project helps you, Ingame-ISK donations to `DrNightmare` are welcome.

## Read The Docs

- [Deutsch](README.de.md)
- [English](README.en.md)
- [Simplified Chinese](README.zh-Hans.md)

## Highlights

- Dashboard, Skyhooks, Characters, Corporation, System Analyzer, Compare, System Mix, and PI Chain Planner
- DB-backed caches for market prices, dashboard values, skyhook values, and GUI translations
- GUI languages: German, English, and Simplified Chinese
- Linux, Docker Compose, and native Windows setup/update scripts

## Update Scripts

- Linux native: `bash scripts/update_linux.sh`
- Linux Docker Compose: `bash scripts/update_linux.sh --compose`
- Windows native: `powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1`
- Windows Docker Compose: `powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1 -Compose`

Both update scripts also support a custom branch:

- Linux: `bash scripts/update_linux.sh --branch main`
- Windows: `powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1 -Branch main`

## License

MIT. See [LICENSE](LICENSE).
