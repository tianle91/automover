# automover

A single-file CLI that reads `automover.yaml` (or `automover.yml`) in the current directory and moves matching **top-level** files and folders into target folders by keyword.

Default mode is a **dry-run**. Nothing is moved until you pass `--apply`.

## Config

```yaml
some_example_group:
  # moves files and/or folders into ./target_folder
  target_path: target_folder
  move_targets:
    files: true
    folders: true
  keywords:
    # case sensitive substrings of the basename
    - first_keyword
    - second_keyword
    - third_keyword
```

Each top-level key is a group. Groups are tried in file order.

| Field | Meaning |
|---|---|
| `target_path` | Destination directory, relative to the working directory. Created if needed. Must stay inside the working directory. |
| `move_targets.files` / `folders` | Whether this group considers files, folders, or both. At least one must be true. |
| `keywords` | Case-sensitive **substrings**. An item matches a group if any keyword appears in its basename. |

See `examples/automover.yaml` for a fuller sample.

## Usage

```bash
python3 automover.py              # dry-run: print the plan
python3 automover.py --apply      # perform moves
python3 automover.py --validate   # schema-check the config only
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

If both `automover.yaml` and `automover.yml` exist, `.yaml` is used and a warning is printed.

## Matching rules (v1)

- Scans **only the top level** of the working directory (no recursion).
- Match is a case-sensitive substring of the **basename**.
- Multiple keywords in one group are OR.
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

Python 3.8+ standard library only. No packages to install.

```bash
python3 -m unittest discover -s tests
```
