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
| `target_path` | Destination directory, relative to the working directory. Created if needed. Must stay inside the working directory. |
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

| Flag | Effect |
|---|---|
| `--apply` | Actually move items. Without this, automover only reports. |
| `--skip-conflicts` | If the destination name already exists, skip instead of prompting. **Never overwrites.** |
| `--first-group-wins` | If an item matches multiple groups, use the first group in the YAML file instead of prompting. |
| `--config PATH` | Use a config file other than `automover.yaml` / `automover.yml`. |
| `--cwd PATH` | Scan a different working directory. |
| `--prompt` | Print a prompt an AI agent can use to generate `automover.yaml` from the top-level listing. Does not call a model. Same as `automover.py prompt`. |

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

- Scans **only the top level** of the working directory (no recursion).
- Keyword match is a case-sensitive substring of the **basename**. Glob match is case-sensitive `fnmatch` on the same basename (not the stem), so `IMG_*` and `*.jpg` both work.
- Multiple keywords/globs in one group are OR. `types` and `extensions` together are OR (union). Name matchers **and** the type filter are AND.
- Extensions use the last suffix (`Path.suffix`), not the stem. `Photo.JPG` is an image. `tar.gz` is matched as a trailing compound suffix.
- Hidden names (starting with `.`), the config file, each group's target directory, and symlinks are skipped.
- Re-running is idempotent: items already inside a target folder are not scanned.

## Conflicts and overlaps

**Destination exists** (never overwritten):

- Interactive `--apply`: prompt to skip or rename (`file (1).txt`, …).
- `--skip-conflicts`: skip the item.
- Non-interactive `--apply` without that flag: error (so CI does not hang or silently skip).

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
