# automover

A single-file CLI that reads `automover.yaml` (or `automover.yml`) in the current directory and moves matching **top-level** files and folders into target folders by keyword and/or file type.

Default mode is a **dry-run**. Nothing is moved until you pass `--apply`.

Version is the single line in [`VERSION`](VERSION); `python3 automover.py --version` prints it. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Config

```yaml
some_example_group:
  # moves files and/or folders into ./target_folder
  target_path: target_folder
  move_targets:
    files: true
    folders: true
    types:          # optional: image, audio, video, documents
      - image
    extensions:     # optional extra suffixes (unioned with types)
      - eml
  keywords:
    # case sensitive substrings of the basename; optional if types/extensions/globs are set
    - first_keyword
  globs:
    - "IMG_*"
    - "report-????.*"
```

Each top-level key is a group. Groups are tried in file order.

| Field | Meaning |
|---|---|
| `target_path` | Destination directory, relative to the directory containing the config file. Created if needed. If it resolves outside the scan path, this emits a warning by default. |
| `move_targets.files` / `folders` | Whether this group considers files, folders, or both. At least one must be true. |
| `move_targets.types` | Optional file-type categories. Supported: `image`, `audio`, `video`, `documents`. `video` does not include `.ts` (TypeScript). `documents` includes office files and also `.txt` / `.md` / `.csv`, not HTML. |
| `move_targets.extensions` | Optional suffix list (`jpg` or `.jpg`). Unioned with `types`. Matches the last suffix (`Path.suffix`), not the stem. Compound values like `tar.gz` match the trailing name. |
| `keywords` | Case-sensitive **substrings** of the full basename. |
| `globs` | Case-sensitive `fnmatch` patterns against the full basename (`IMG_*`, `*.jpg`). Keywords and globs are OR. |

Type filters apply to **files only**. Folders still match on keywords or globs.

A file matches when:

1. `files: true`, and
2. if `keywords` and/or `globs` are set, the basename hits at least one of them, and
3. if `types` and/or `extensions` are set, the last suffix is in that allow-list

See `examples/automover.yaml` for a fuller sample.

## Usage

```bash
python3 automover.py              # dry-run: print the plan
python3 automover.py --apply      # perform moves
python3 automover.py --validate   # schema-check the config only
python3 automover.py prompt       # print an AI prompt to generate automover.yaml
python3 automover.py --version    # print the version from VERSION
python3 automover.py -v           # also list hidden/unmatched/config skips
```

Useful flags:

| Flag | Effect | Default |
|---|---|---|
| `--apply` | Actually move items. | Dry-run; report without moving. |
| `--skip-conflicts` | If the destination name already exists, skip it. | Prompt in an interactive apply; error in a non-interactive apply. |
| `--overwrite` | Replace an existing destination file or folder. Existing folders are replaced in full, not merged. Cannot be combined with `--skip-conflicts`. | Prompt in an interactive apply; error in a non-interactive apply. |
| `--first-group-wins` | If an item matches multiple groups, use the first group in YAML order. | Prompt in an interactive apply; error in a non-interactive apply. |
| `--config PATH` | Use a specific config. Absolute paths are used directly; relative paths resolve from the current directory. | Look for `automover.yaml`, then `automover.yml`, in the current directory. |
| `--scan-path PATH` | Set the directory whose top-level entries are scanned and moved. | Current directory. |
| `--no-warn-external-targets` | Suppress warnings for `target_path` values that resolve outside the scan path. | Warn about external targets. |
| `--prompt` | Print a prompt an AI agent can use to generate YAML from the scan-path listing. Also accepted as `automover.py prompt`; does not call a model. | Normal dry-run/apply workflow. |
| `--validate` | Validate the selected config without scanning or moving entries. | Normal dry-run/apply workflow. |
| `-v`, `--verbose` | List skipped unmatched, hidden, config, and target entries. | Summary output only. |
| `--version` | Print the version and exit. | Run automover. |

If both `automover.yaml` and `automover.yml` exist, `.yaml` is used and a warning is printed.

## Generating a config with an AI agent

`prompt` does not run a model. It lists every top-level file and folder automover would consider (plus guessed `image` / `audio` / `video` / `documents` types) and prints a filled-in schema prompt to stdout. Pipe or paste that into an agent, then save the YAML it returns as `automover.yaml`.

```bash
python3 automover.py prompt > /tmp/automover-prompt.txt
# ...ask an agent to write automover.yaml from that prompt...
python3 automover.py --validate
python3 automover.py            # dry-run the generated config
```

If a config already exists, it is included so the agent can revise it. Hidden names, the config file, this script, and symlinks are listed as skipped.

## Matching rules (v1)

- Scans **only the top level** of the scan path (no recursion).
- Keyword match is a case-sensitive substring of the **basename**. Glob match is case-sensitive `fnmatch` on the same basename (not the stem), so `IMG_*` and `*.jpg` both work.
- Multiple keywords/globs in one group are OR. `types` and `extensions` together are OR (union). Name matchers **and** the type filter are AND.
- Extensions use the last suffix (`Path.suffix`), not the stem. `Photo.JPG` is an image. `tar.gz` is matched as a trailing compound suffix.
- Hidden names (starting with `.`), the config file, each target directory inside the scan path, and symlinks are skipped. Targets are resolved relative to the config file's directory; targets outside the scan path are supported and emit a warning unless `--no-warn-external-targets` is used.
- Re-running is idempotent: items already inside a target folder are not scanned.

## Conflicts and overlaps

**Destination exists:**

- Interactive `--apply`: prompt to skip, rename (`file (1).txt`, …), or overwrite.
- `--skip-conflicts`: skip the item.
- `--overwrite`: replace the destination. For folders, this removes the old folder and its contents after the move succeeds.
- Non-interactive `--apply` without either flag: error (so CI does not hang or silently skip).

**Multiple groups match:**

- Interactive `--apply`: prompt to pick a group or skip.
- `--first-group-wins`: take the first group in file order.
- Non-interactive `--apply` without that flag: error.

Dry-run reports these as “would prompt” unless the corresponding flag is passed, in which case the plan shows the resolved action.

## Requirements

Python 3.9+ standard library only. No packages to install.

```bash
python3 -m unittest discover -s tests
```

Pull requests and pushes to `main` run that same command on GitHub Actions (Python 3.9 and 3.12).
