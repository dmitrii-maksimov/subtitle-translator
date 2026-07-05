# Changelog

All notable changes to this project are documented here. Each released version
**must** have its own section describing what changed — the GitHub release notes
are generated from this file (see `scripts/extract_changelog.py`).

Format: one `## <version>` header per release, newest first.

## 1.4.4

### Fixed
- Startup update check no longer skipped by a once-per-day throttle — the app
  now checks for a newer release on every launch, so the "Update available"
  dialog appears reliably (previously it only showed via the manual
  "Check for updates now" button).

### Added
- The current app version is now shown in the window title and on the
  Settings tab.

## 1.4.3

### Changed
- "Overwrite the original file" is now enabled by default for new installs.
- "File downloading (live)" is always available again; the Kodi toggle now
  only disables the Kodi *integration* inside that dialog (hides Play-on-Kodi,
  pause and progress; skips the Kodi client and poller) instead of hiding the
  whole feature.

## 1.4.2

### Added
- A "Show Kodi integration" toggle in Settings that hides/shows the Kodi tab
  and the "Following Kodi" button. Hidden by default on fresh installs;
  users upgrading from an earlier build keep it visible.

## 1.4.1

### Added
- Assisted auto-update via GitHub Releases: the app checks for a newer version
  and can download and launch the right installer in one click.
- Linux `.deb` package alongside the AppImage.
- Runtime app version (`__version__`), injected from the git tag at build time.

## 1.4.0

### Changed
- Windows builds now ship a proper installer (`SubtitleTranslator-Setup.exe`,
  built with Inno Setup) instead of a portable one-file executable.
