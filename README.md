# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)

Self-hosted Planetary Industry manager for EVE Online.

If this project helps you, Ingame-ISK donations to `DrNightmare` are welcome.

## Read The Docs

- [Deutsch](README.de.md)
- [English](README.en.md)
- [Simplified Chinese](README.zh-Hans.md)

## Highlights

- Dashboard, Skyhooks, Characters, Corporation, Jita Market, System Analyzer, Compare, System Mix, and PI Chain Planner
- DB-backed caches for market prices, dashboard values, skyhook values, GUI translations, and static planet details
- Dashboard extractor balance indicators, balance filters, extractor-rate filters, and tier filters
- System Analyzer with single-planet recommendation filter and expandable planet details including planet number and radius
- GUI languages: German, English, and Simplified Chinese
- Linux, Docker Compose, and native Windows setup/update scripts

## Page Guides

- `Dashboard`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `Skyhooks`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `Characters`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `Corporation`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `Jita Market`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `PI Chain Planner`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `System Analyzer`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `System Mix`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)
- `Compare`: [Deutsch](README.de.md) | [English](README.en.md) | [Simplified Chinese](README.zh-Hans.md)

## Update Scripts

- Linux native: `bash scripts/update_linux.sh`
- Linux Docker Compose: `bash scripts/update_linux.sh --compose`
- Windows native: `powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1`
- Windows Docker Compose: `powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1 -Compose`

Both update scripts also support a custom branch:

- Linux: `bash scripts/update_linux.sh --branch main`
- Windows: `powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1 -Branch main`

## CCP Notice

EVE Online and all related logos and designs are trademarks or registered trademarks of CCP ehf. This project is not affiliated with, endorsed by, or connected to CCP ehf.

## License

MIT. See [LICENSE](LICENSE).
