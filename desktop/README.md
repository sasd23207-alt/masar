# مِسار — Windows Desktop Package

This folder builds an installable desktop version of Masar for one Windows computer. The application opens in its own window; it does not require Render or an external server.

## Intended behavior

- The installer creates the desktop and Start Menu shortcuts.
- Electron provides the desktop window and launches the bundled Python backend on `127.0.0.1:8787`.
- The backend and its SQLite database run on the same computer. The local HTTP process is bundled inside the app; it is not an internet-hosted server.
- The database is stored in `%ProgramData%\Masar\data\masar.db`. Backups and logs are stored under `%ProgramData%\Masar\backups` and `%ProgramData%\Masar\logs`.
- Those data folders deliberately survive app uninstall and upgrades. The installer never ships a database and does not overwrite one.
- First installation asks for the school's activation code and saves it in `%ProgramData%\Masar\.env`. An upgrade preserves that file.
- WhatsApp credentials are not included or configured by this package. Core local school records do not depend on WhatsApp.

## Windows 7 compatibility

The legacy build uses 32-bit Python 3.8.10 with PyInstaller 5.13.2, 32-bit Node.js 16.20.2, and Electron 22.3.27 (ia32), versions selected to target Windows 7. Target: Windows 7 SP1 or newer, x86 (32-bit). Windows 7 must have its required servicing updates installed, including KB2533623 (or the applicable update rollup).

**This is a legacy compatibility target, not a claim of security support.** Python 3.8 and Electron 22 are out of security support, and Windows 7 is out of support. Prefer Windows 10/11 for real school records. The package must be tested on an actual Windows 7 SP1 32-bit computer before describing Windows 7 support as verified.

## Build

The GitHub Actions workflow `.github/workflows/windows-installer.yml` builds the Windows backend and desktop shell, then creates `Masar-Setup-Windows7-32bit.exe` with Inno Setup. It uploads the installer as an Actions artifact. The source repository contains only build instructions and source files; it does not contain the school's private activation code or its database.

A normal app update replaces application files only. A reinstall should not remove `%ProgramData%\Masar`. Nevertheless, local data can still be lost if the disk fails, the data folder is manually deleted, or the operating system is reinstalled. Make a backup to a separate USB/external drive regularly; backups stored on the same internal disk do not protect against disk failure.

## Verification status

- [WRITTEN-ONLY] Desktop launcher and Inno Setup configuration are provided in source.
- [WRITTEN-ONLY] Windows 7-compatible dependency versions are pinned.
- [NOT VERIFIED] The generated installer has not yet been built and installed on a real Windows 7 computer.
- [NOT VERIFIED] Upgrade, uninstall/data retention, reboot persistence, and backup restoration must pass tests before production use.
