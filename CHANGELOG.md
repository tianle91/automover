# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The current version is the single line in [`VERSION`](VERSION). `python3 automover.py --version` prints it.

## [Unreleased]

## [0.3.0] - 2026-08-31

### Added

- `prompt` / `--prompt` lists top-level files and folders and prints a prompt an AI agent can use to generate `automover.yaml`. The CLI does not call a model.
- GitHub Actions CI running the stdlib unittest suite on Python 3.9 and 3.12.
- Optional `globs` list: case-sensitive `fnmatch` against the full basename (`IMG_*`, `*.jpg`).
- YAML subset loader accepts same-indent sequences (`keywords:` then `- item` at the same indent).

### Changed

- Python bytecode caches are gitignored.
- Extensions match `Path.suffix` (last suffix), not the stem and not a raw `endswith` on the whole name. Compound suffixes like `tar.gz` still match the trailing name.
- `video` no longer includes `.ts` (TypeScript collision). Use `.m2ts` / `.mts` or `extensions`.
- Documented that `documents` includes `.txt` / `.md` / `.csv`, and that the runtime is Python 3.9+.

### Fixed

- Removed duplicate `media` / `documents` / `archives` groups from `examples/automover.yaml`.

## [0.2.0] - 2026-08-31

### Added

- Optional `move_targets.types`: `image`, `audio`, `video`, `documents`.
- Optional `move_targets.extensions` (unioned with `types`, case-insensitive suffixes).
- Keywords are optional for file-only groups that set `types` or `extensions`. Folders still require keywords.

## [0.1.0] - 2026-08-31

### Added

- Initial CLI: read `automover.yaml` or `automover.yml`, match top-level basenames with case-sensitive keyword substrings, move into per-group `target_path` directories.
- Dry-run by default; `--apply` performs moves.
- Conflict handling: prompt to skip or rename; `--skip-conflicts` skips; never overwrites.
- Multi-group overlap: prompt to choose a group; `--first-group-wins` uses YAML order.
- `--validate`, `--config`, `--cwd`, `--verbose`.
- Stdlib-only YAML subset parser and unit tests.
