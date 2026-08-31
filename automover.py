#!/usr/bin/env python3
"""Keyword-based file/folder mover driven by automover.yaml.

Default mode is a dry-run. Pass --apply to move anything.
Pass --prompt (or: prompt) to print an AI prompt for generating automover.yaml;
the CLI does not call a model.

Example config (automover.yaml or automover.yml):

    some_example_group:
      target_path: target_folder
      move_targets:
        files: true
        folders: true
        types:          # optional: image, audio, video, documents
          - image
        extensions:     # optional extra suffixes, unioned with types
          - heic
      keywords:
        - first_keyword
        - second_keyword
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TextIO

_FALLBACK_VERSION = "0.3.0"


def _load_version() -> str:
    path = Path(__file__).resolve().parent / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return _FALLBACK_VERSION
    return text or _FALLBACK_VERSION


VERSION = _load_version()
CONFIG_NAMES = ("automover.yaml", "automover.yml")

# Frozen, lowercase, leading-dot suffixes. Matching is case-insensitive.
FILE_TYPES: dict[str, frozenset[str]] = {
    "image": frozenset(
        {
            ".jpg",
            ".jpeg",
            ".jpe",
            ".jfif",
            ".png",
            ".gif",
            ".webp",
            ".bmp",
            ".tif",
            ".tiff",
            ".heic",
            ".heif",
            ".svg",
            ".ico",
            ".raw",
            ".dng",
            ".cr2",
            ".cr3",
            ".nef",
            ".arw",
            ".orf",
            ".rw2",
            ".raf",
            ".avif",
        }
    ),
    "audio": frozenset(
        {
            ".mp3",
            ".wav",
            ".flac",
            ".aac",
            ".m4a",
            ".ogg",
            ".opus",
            ".wma",
            ".aiff",
            ".aif",
            ".aifc",
            ".alac",
            ".ape",
            ".mid",
            ".midi",
            ".amr",
            ".oga",
            ".mka",
            ".ac3",
        }
    ),
    "video": frozenset(
        {
            ".mp4",
            ".mkv",
            ".mov",
            ".avi",
            ".webm",
            ".m4v",
            ".wmv",
            ".flv",
            ".mpeg",
            ".mpg",
            ".mpe",
            ".3gp",
            ".3g2",
            ".ogv",
            ".mts",
            ".m2ts",
            ".vob",
            ".m2v",
            ".asf",
            ".f4v",
        }
    ),
    "documents": frozenset(
        {
            ".pdf",
            ".doc",
            ".docx",
            ".docm",
            ".odt",
            ".rtf",
            ".txt",
            ".md",
            ".markdown",
            ".xls",
            ".xlsx",
            ".xlsm",
            ".csv",
            ".tsv",
            ".ppt",
            ".pptx",
            ".pptm",
            ".ods",
            ".odp",
            ".epub",
            ".pages",
            ".numbers",
            ".key",
            ".tex",
            ".ott",
            ".ots",
            ".otp",
            ".xps",
            ".djvu",
            ".djv",
        }
    ),
}

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_PARTIAL = 2


class ConfigError(Exception):
    """Invalid config file or flags."""


class UserAbort(Exception):
    """User asked to stop during a prompt."""


# ---------------------------------------------------------------------------
# YAML subset loader (stdlib only)
# Supports mappings, lists, booleans, quoted/unquoted strings, and comments.
# ---------------------------------------------------------------------------


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and in_double:
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _unquote_string(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        inner = raw[1:-1]
        if raw[0] == '"':
            inner = (
                inner.replace(r"\\", "\\")
                .replace(r"\"", '"')
                .replace(r"\n", "\n")
                .replace(r"\t", "\t")
            )
        return inner
    return raw


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if raw in ("null", "~"):
        return None
    return _unquote_string(raw)


def _parse_key(raw: str, lineno: int) -> str:
    raw = raw.strip()
    if not raw:
        raise ConfigError(f"line {lineno}: mapping key must be a non-empty string")
    key = _unquote_string(raw)
    if not isinstance(key, str) or not key:
        raise ConfigError(f"line {lineno}: mapping key must be a non-empty string")
    return key


def _split_mapping_entry(content: str, lineno: int) -> tuple[str, bool, str]:
    in_single = False
    in_double = False
    colon = None
    i = 0
    while i < len(content):
        ch = content[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            colon = i
            break
        i += 1
    if colon is None:
        raise ConfigError(f"line {lineno}: expected 'key:' in mapping, got {content!r}")
    key = _parse_key(content[:colon], lineno)
    rest = content[colon + 1 :].strip()
    if rest == "":
        return key, False, ""
    return key, True, rest


def _parse_value(lines: list[tuple[int, int, str]], idx: int, indent: int) -> tuple[Any, int]:
    if idx >= len(lines):
        raise ConfigError("unexpected end of file")
    lineno, line_indent, content = lines[idx]
    if line_indent != indent:
        raise ConfigError(f"line {lineno}: inconsistent indentation")
    if content == "-" or content.startswith("- "):
        return _parse_sequence(lines, idx, indent)
    return _parse_mapping(lines, idx, indent)


def _parse_mapping(
    lines: list[tuple[int, int, str]], idx: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while idx < len(lines):
        lineno, line_indent, content = lines[idx]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ConfigError(f"line {lineno}: unexpected indentation")
        if content == "-" or content.startswith("- "):
            raise ConfigError(f"line {lineno}: expected mapping key, got list item")
        key, has_inline, inline = _split_mapping_entry(content, lineno)
        if key in result:
            raise ConfigError(f"line {lineno}: duplicate key {key!r}")
        idx += 1
        if has_inline:
            result[key] = _parse_scalar(inline)
            continue
        if idx >= len(lines) or lines[idx][1] < indent:
            result[key] = None
            continue
        next_indent = lines[idx][1]
        next_content = lines[idx][2]
        if next_indent == indent and (next_content == "-" or next_content.startswith("- ")):
            value, idx = _parse_sequence(lines, idx, indent)
            result[key] = value
            continue
        if next_indent <= indent:
            result[key] = None
            continue
        value, idx = _parse_value(lines, idx, next_indent)
        result[key] = value
    return result, idx


def _parse_sequence(
    lines: list[tuple[int, int, str]], idx: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while idx < len(lines):
        lineno, line_indent, content = lines[idx]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ConfigError(f"line {lineno}: unexpected indentation")
        if content != "-" and not content.startswith("- "):
            break
        item = "" if content == "-" else content[2:].strip()
        idx += 1
        if item == "":
            if idx < len(lines) and lines[idx][1] > indent:
                value, idx = _parse_value(lines, idx, lines[idx][1])
                result.append(value)
            else:
                result.append(None)
        else:
            result.append(_parse_scalar(item))
    return result, idx


def load_simple_yaml(text: str) -> Any:
    lines: list[tuple[int, int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise ConfigError(f"line {lineno}: tabs are not allowed; use spaces")
        stripped = _strip_inline_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((lineno, indent, stripped.strip()))
    if not lines:
        raise ConfigError("YAML file is empty")
    value, idx = _parse_value(lines, 0, lines[0][1])
    if idx != len(lines):
        lineno, _, content = lines[idx]
        raise ConfigError(f"line {lineno}: unexpected content {content!r}")
    return value


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

REQUIRED_GROUP_KEYS = frozenset({"target_path", "move_targets"})
OPTIONAL_GROUP_KEYS = frozenset({"keywords", "globs"})
GROUP_KEYS = REQUIRED_GROUP_KEYS | OPTIONAL_GROUP_KEYS

REQUIRED_MOVE_TARGET_KEYS = frozenset({"files", "folders"})
OPTIONAL_MOVE_TARGET_KEYS = frozenset({"types", "extensions"})
MOVE_TARGET_KEYS = REQUIRED_MOVE_TARGET_KEYS | OPTIONAL_MOVE_TARGET_KEYS


def normalize_extension(raw: str) -> str:
    ext = raw.strip().lower()
    if not ext:
        raise ConfigError("extension must be a non-empty string")
    if "/" in ext or "\\" in ext:
        raise ConfigError(f"extension must not contain a path: {raw!r}")
    if not ext.startswith("."):
        ext = "." + ext
    if ext == ".":
        raise ConfigError(f"invalid extension: {raw!r}")
    return ext


def name_has_extension(filename: str, extensions: Iterable[str]) -> bool:
    """Match the last suffix (Path.suffix), not the stem.

    Compound values like .tar.gz match a trailing suffix chain via endswith.
    """
    lower = filename.lower()
    suffix = Path(filename).suffix.lower()
    for ext in extensions:
        if ext.count(".") > 1:
            if lower.endswith(ext):
                return True
        elif suffix == ext:
            return True
    return False


@dataclass(frozen=True)
class Group:
    name: str
    target_path: str
    target: Path
    move_files: bool
    move_folders: bool
    keywords: tuple[str, ...]
    globs: tuple[str, ...]
    types: tuple[str, ...]
    extensions: tuple[str, ...]


def _parse_unique_strings(value: Any, *, field: str, group_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"group {group_name!r}: {field} must be a non-empty list")
    parsed: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item:
            raise ConfigError(
                f"group {group_name!r}: {field} must contain non-empty strings"
            )
        if item in seen:
            raise ConfigError(f"group {group_name!r}: duplicate {field} entry {item!r}")
        seen.add(item)
        parsed.append(item)
    return parsed


def _parse_keywords(value: Any, *, group_name: str) -> list[str]:
    if value is None:
        return []
    return _parse_unique_strings(value, field="keywords", group_name=group_name)


def _parse_globs(value: Any, *, group_name: str) -> list[str]:
    if value is None:
        return []
    return _parse_unique_strings(value, field="globs", group_name=group_name)


def _parse_types(value: Any, *, group_name: str) -> list[str]:
    if value is None:
        return []
    parsed = _parse_unique_strings(value, field="move_targets.types", group_name=group_name)
    known = ", ".join(sorted(FILE_TYPES))
    for item in parsed:
        if item not in FILE_TYPES:
            raise ConfigError(
                f"group {group_name!r}: unknown type {item!r} "
                f"(supported: {known})"
            )
    return parsed


def _parse_extensions(value: Any, *, group_name: str) -> list[str]:
    if value is None:
        return []
    raw = _parse_unique_strings(
        value, field="move_targets.extensions", group_name=group_name
    )
    parsed: list[str] = []
    seen: set[str] = set()
    for item in raw:
        try:
            ext = normalize_extension(item)
        except ConfigError as exc:
            raise ConfigError(f"group {group_name!r}: {exc}") from exc
        if ext in seen:
            raise ConfigError(
                f"group {group_name!r}: duplicate extension {ext!r}"
            )
        seen.add(ext)
        parsed.append(ext)
    return parsed


def validate_groups(data: Any, cwd: Path) -> list[Group]:
    if not isinstance(data, dict) or not data:
        raise ConfigError("config root must be a non-empty mapping of group names")

    groups: list[Group] = []
    for name, body in data.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("group names must be non-empty strings")
        if not isinstance(body, dict):
            raise ConfigError(f"group {name!r}: must be a mapping")

        extra = set(body) - GROUP_KEYS
        if extra:
            raise ConfigError(
                f"group {name!r}: unknown keys: {', '.join(sorted(extra))}"
            )
        missing = REQUIRED_GROUP_KEYS - set(body)
        if missing:
            raise ConfigError(
                f"group {name!r}: missing keys: {', '.join(sorted(missing))}"
            )

        target_path = body["target_path"]
        if not isinstance(target_path, str) or not target_path.strip():
            raise ConfigError(f"group {name!r}: target_path must be a non-empty string")
        target_path = target_path.strip()
        if Path(target_path).is_absolute():
            raise ConfigError(
                f"group {name!r}: target_path must be relative, not {target_path!r}"
            )

        target = _resolve_inside_cwd(cwd, target_path, group_name=name)
        if target.exists() and not target.is_dir():
            raise ConfigError(
                f"group {name!r}: target_path {target_path!r} exists and is not a directory"
            )

        move_targets = body["move_targets"]
        if not isinstance(move_targets, dict):
            raise ConfigError(f"group {name!r}: move_targets must be a mapping")
        extra_mt = set(move_targets) - MOVE_TARGET_KEYS
        if extra_mt:
            raise ConfigError(
                f"group {name!r}: unknown move_targets keys: {', '.join(sorted(extra_mt))}"
            )
        missing_mt = REQUIRED_MOVE_TARGET_KEYS - set(move_targets)
        if missing_mt:
            raise ConfigError(
                f"group {name!r}: missing move_targets keys: {', '.join(sorted(missing_mt))}"
            )
        move_files = move_targets["files"]
        move_folders = move_targets["folders"]
        if not isinstance(move_files, bool) or not isinstance(move_folders, bool):
            raise ConfigError(
                f"group {name!r}: move_targets.files and folders must be booleans"
            )
        if not move_files and not move_folders:
            raise ConfigError(
                f"group {name!r}: move_targets.files and folders cannot both be false"
            )

        parsed_types = _parse_types(move_targets.get("types"), group_name=name)
        parsed_extensions = _parse_extensions(
            move_targets.get("extensions"), group_name=name
        )
        if (parsed_types or parsed_extensions) and not move_files:
            raise ConfigError(
                f"group {name!r}: move_targets.types/extensions require files: true"
            )

        parsed_keywords = _parse_keywords(body.get("keywords"), group_name=name)
        parsed_globs = _parse_globs(body.get("globs"), group_name=name)
        name_matchers = bool(parsed_keywords or parsed_globs)
        if move_folders and not name_matchers:
            raise ConfigError(
                f"group {name!r}: folders: true requires keywords or globs "
                "(types/extensions only apply to files)"
            )
        if (
            move_files
            and not name_matchers
            and not parsed_types
            and not parsed_extensions
        ):
            raise ConfigError(
                f"group {name!r}: files: true requires keywords, globs, types, or extensions"
            )

        groups.append(
            Group(
                name=name,
                target_path=target_path,
                target=target,
                move_files=move_files,
                move_folders=move_folders,
                keywords=tuple(parsed_keywords),
                globs=tuple(parsed_globs),
                types=tuple(parsed_types),
                extensions=tuple(parsed_extensions),
            )
        )
    return groups


def _resolve_inside_cwd(cwd: Path, target_path: str, group_name: str) -> Path:
    cwd_r = cwd.resolve()
    resolved = (cwd / target_path).resolve()
    try:
        resolved.relative_to(cwd_r)
    except ValueError as exc:
        raise ConfigError(
            f"group {group_name!r}: target_path {target_path!r} escapes the working directory"
        ) from exc
    if resolved == cwd_r:
        raise ConfigError(
            f"group {group_name!r}: target_path {target_path!r} must be a subdirectory, "
            "not the working directory"
        )
    return resolved


def find_config(cwd: Path, explicit: Optional[Path], warn: Callable[[str], None]) -> Path:
    if explicit is not None:
        path = explicit if explicit.is_absolute() else cwd / explicit
        if not path.is_file():
            raise ConfigError(f"config not found: {path}")
        return path.resolve()

    yaml_path = cwd / "automover.yaml"
    yml_path = cwd / "automover.yml"
    yaml_exists = yaml_path.is_file()
    yml_exists = yml_path.is_file()
    if yaml_exists and yml_exists:
        warn("both automover.yaml and automover.yml exist; using automover.yaml")
        return yaml_path.resolve()
    if yaml_exists:
        return yaml_path.resolve()
    if yml_exists:
        return yml_path.resolve()
    raise ConfigError(
        f"no automover.yaml or automover.yml in {cwd} "
        "(pass --config PATH to use another file)"
    )


def load_config(path: Path, cwd: Path) -> list[Group]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config {path}: {exc}") from exc
    try:
        data = load_simple_yaml(text)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return validate_groups(data, cwd)


# ---------------------------------------------------------------------------
# Matching and planning
# ---------------------------------------------------------------------------


@dataclass
class Match:
    group: Group
    keyword: Optional[str]
    reason: str


@dataclass
class Action:
    """One planned or completed operation."""

    source: Path
    dest: Optional[Path]
    group: Optional[Group]
    keyword: Optional[str]
    kind: str
    detail: str = ""
    reason: str = ""

    def rel(self, cwd: Path) -> str:
        try:
            return str(self.source.relative_to(cwd))
        except ValueError:
            return str(self.source)


def classify_entry(path: Path) -> Optional[str]:
    """Return 'dir', 'file', or None if the entry should be skipped as unsupported."""
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    if path.is_file():
        return "file"
    return "other"


def file_type_reason(name: str, group: Group) -> Optional[str]:
    """How this file hits the group's type filter.

    Returns None if the filter exists and the file does not match.
    Returns "" if there is no type/extension filter.
    """
    if not group.types and not group.extensions:
        return ""
    for type_name in group.types:
        if name_has_extension(name, FILE_TYPES[type_name]):
            return f"type: {type_name}"
    for ext in group.extensions:
        if name_has_extension(name, (ext,)):
            return f"extension: {ext}"
    return None


def name_match_reason(name: str, group: Group) -> Optional[str]:
    """How this basename hits keywords/globs.

    Returns None if name matchers exist and none hit.
    Returns "" if the group has no keywords or globs (type-only).
    """
    if not group.keywords and not group.globs:
        return ""
    for keyword in group.keywords:
        if keyword in name:
            return f"keyword: {keyword}"
    for pattern in group.globs:
        if fnmatch.fnmatchcase(name, pattern):
            return f"glob: {pattern}"
    return None


def matching_groups(name: str, kind: str, groups: Iterable[Group]) -> list[Match]:
    hits: list[Match] = []
    for group in groups:
        if kind == "dir" and not group.move_folders:
            continue
        if kind == "file" and not group.move_files:
            continue

        type_reason = ""
        if kind == "file":
            type_reason_or_miss = file_type_reason(name, group)
            if type_reason_or_miss is None:
                continue
            type_reason = type_reason_or_miss

        name_reason = name_match_reason(name, group)
        if name_reason is None:
            continue
        if not name_reason and (kind != "file" or not type_reason):
            continue

        parts: list[str] = []
        if name_reason:
            parts.append(name_reason)
        if type_reason:
            parts.append(type_reason)
        keyword_hit: Optional[str] = None
        if name_reason.startswith("keyword: "):
            keyword_hit = name_reason[len("keyword: ") :]
        hits.append(
            Match(group=group, keyword=keyword_hit, reason=", ".join(parts))
        )
    return hits


def unique_destination(parent: Path, name: str) -> Path:
    candidate = parent / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    n = 1
    while n <= 10_000:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1
    raise OSError(f"could not find a free name for {name!r} in {parent}")


def can_prompt(stdin: TextIO) -> bool:
    isatty = getattr(stdin, "isatty", lambda: False)
    return bool(isatty())


def _read_choice(stdin: TextIO, stdout: TextIO, prompt: str) -> str:
    stdout.write(prompt)
    stdout.flush()
    line = stdin.readline()
    if line == "":
        raise UserAbort("end of input during prompt")
    return line.strip()


def prompt_overlap(
    action_name: str,
    matches: list[Match],
    stdin: TextIO,
    prompt_out: TextIO,
) -> Optional[Match]:
    prompt_out.write(f"\n{action_name!r} matches multiple groups:\n")
    for i, match in enumerate(matches, 1):
        prompt_out.write(
            f"  [{i}] {match.group.name}  ({match.reason}) -> {match.group.target_path}/\n"
        )
    prompt_out.write("  [s] skip this item\n")
    prompt_out.write("  [q] abort\n")
    while True:
        choice = _read_choice(stdin, prompt_out, "Choose group or action: ")
        if choice.lower() == "q":
            raise UserAbort("aborted by user")
        if choice.lower() == "s":
            return None
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(matches):
                return matches[n - 1]
        prompt_out.write(f"  invalid choice {choice!r}; enter a listed number, s, or q\n")


def prompt_conflict(
    source_name: str,
    dest: Path,
    cwd: Path,
    stdin: TextIO,
    prompt_out: TextIO,
) -> Optional[Path]:
    try:
        dest_rel = dest.relative_to(cwd)
    except ValueError:
        dest_rel = dest
    prompt_out.write(
        f"\nConflict: {source_name!r} already exists at {dest_rel.as_posix()} (never overwrites)\n"
    )
    prompt_out.write("  [s] skip this item\n")
    prompt_out.write("  [r] rename to a unique name\n")
    prompt_out.write("  [q] abort\n")
    while True:
        choice = _read_choice(stdin, prompt_out, "Choose resolution: ")
        lowered = choice.lower()
        if lowered == "q":
            raise UserAbort("aborted by user")
        if lowered == "s":
            return None
        if lowered == "r":
            return unique_destination(dest.parent, dest.name)
        prompt_out.write(f"  invalid choice {choice!r}; enter s, r, or q\n")


@dataclass
class Plan:
    moves: list[Action] = field(default_factory=list)
    skipped: list[Action] = field(default_factory=list)
    overlaps: list[Action] = field(default_factory=list)
    conflicts: list[Action] = field(default_factory=list)


def _self_path() -> Optional[Path]:
    try:
        return Path(__file__).resolve()
    except (NameError, OSError):
        return None


def collect_candidates(
    cwd: Path,
    config_path: Path,
    groups: list[Group],
    verbose: bool,
) -> tuple[list[Path], list[Action]]:
    skipped: list[Action] = []
    targets = {g.target.resolve() for g in groups}
    config_resolved = config_path.resolve()
    self_path = _self_path()
    names_to_skip = set(CONFIG_NAMES)

    entries: list[Path] = []
    try:
        listed = sorted(cwd.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        raise ConfigError(f"could not list {cwd}: {exc}") from exc

    for entry in listed:
        name = entry.name
        try:
            resolved = entry.resolve()
        except OSError:
            skipped.append(
                Action(entry, None, None, None, "skip_unreadable", "could not resolve path")
            )
            continue

        if resolved == config_resolved or name in names_to_skip:
            if verbose:
                skipped.append(
                    Action(entry, None, None, None, "skip_config", "config file")
                )
            continue
        if self_path is not None and resolved == self_path:
            if verbose:
                skipped.append(
                    Action(entry, None, None, None, "skip_self", "automover script")
                )
            continue
        if name.startswith("."):
            if verbose:
                skipped.append(
                    Action(entry, None, None, None, "skip_hidden", "hidden entry")
                )
            continue
        if resolved in targets:
            if verbose:
                skipped.append(
                    Action(
                        entry, None, None, None, "skip_target", "group target directory"
                    )
                )
            continue

        kind = classify_entry(entry)
        if kind == "symlink":
            skipped.append(
                Action(entry, None, None, None, "skip_symlink", "symlinks are not moved")
            )
            continue
        if kind == "other":
            skipped.append(
                Action(entry, None, None, None, "skip_other", "not a regular file or folder")
            )
            continue
        entries.append(entry)

    return entries, skipped


# ---------------------------------------------------------------------------
# AI prompt for generating automover.yaml (no model is invoked)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InventoryItem:
    name: str
    kind: str
    types: tuple[str, ...]


@dataclass
class Inventory:
    files: list[InventoryItem] = field(default_factory=list)
    folders: list[InventoryItem] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def guess_types(filename: str) -> tuple[str, ...]:
    return tuple(
        type_name
        for type_name, exts in FILE_TYPES.items()
        if name_has_extension(filename, exts)
    )


def list_inventory(cwd: Path) -> Inventory:
    """Top-level files and folders automover would consider (no config required)."""
    inventory = Inventory()
    self_path = _self_path()
    names_to_skip = set(CONFIG_NAMES)
    try:
        listed = sorted(cwd.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        raise ConfigError(f"could not list {cwd}: {exc}") from exc

    for entry in listed:
        name = entry.name
        try:
            resolved = entry.resolve()
        except OSError:
            inventory.skipped.append((name, "unreadable"))
            continue
        if name in names_to_skip:
            inventory.skipped.append((name, "config file"))
            continue
        if self_path is not None and resolved == self_path:
            inventory.skipped.append((name, "automover script"))
            continue
        if name.startswith("."):
            inventory.skipped.append((name, "hidden"))
            continue
        kind = classify_entry(entry)
        if kind == "symlink":
            inventory.skipped.append((name, "symlink"))
            continue
        if kind == "other":
            inventory.skipped.append((name, "not a regular file or folder"))
            continue
        if kind == "dir":
            inventory.folders.append(InventoryItem(name, "dir", ()))
        else:
            inventory.files.append(InventoryItem(name, "file", guess_types(name)))
    return inventory


def format_type_catalog() -> str:
    lines = []
    for type_name in FILE_TYPES:
        exts = ", ".join(sorted(FILE_TYPES[type_name]))
        lines.append(f"  {type_name}: {exts}")
    return "\n".join(lines)


def _format_inventory_section(inventory: Inventory) -> str:
    lines: list[str] = []
    lines.append(f"Files ({len(inventory.files)}):")
    if inventory.files:
        for item in inventory.files:
            suffix = Path(item.name).suffix
            type_note = ", ".join(item.types) if item.types else "untyped"
            extra = f"  suffix={suffix}" if suffix else "  no suffix"
            lines.append(f"  - {item.name}  [{type_note}]{extra}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Folders ({len(inventory.folders)}):")
    if inventory.folders:
        for item in inventory.folders:
            lines.append(f"  - {item.name}/")
    else:
        lines.append("  (none)")
    if inventory.skipped:
        lines.append("")
        lines.append("Skipped by automover (do not target these):")
        for name, reason in inventory.skipped:
            lines.append(f"  - {name}  ({reason})")
    return "\n".join(lines)


def build_generation_prompt(
    cwd: Path,
    inventory: Inventory,
    existing_yaml: Optional[str] = None,
    existing_path: Optional[Path] = None,
) -> str:
    """Build a prompt an AI agent can use to write automover.yaml. Does not call a model."""
    existing_block = ""
    if existing_yaml is not None and existing_path is not None:
        existing_block = (
            "\n## Existing config\n"
            f"A config already exists at {existing_path.name}. "
            "Replace it with an improved file that still covers these items, "
            "or keep groups that still make sense.\n\n"
            "```yaml\n"
            f"{existing_yaml.rstrip()}\n"
            "```\n"
        )

    return f"""You are writing automover.yaml for the automover CLI.
Do not move, copy, or delete files. Do not run automover. Output only the YAML file contents (no markdown fences, no commentary).

## What automover does
It scans only the top level of the working directory (no recursion) and moves matching files/folders into per-group target directories. Dry-run is the default; the user will run --apply later.

## Schema
Each top-level key is a group. Groups are tried in file order. First match can overlap; avoid overlap when you can.

```yaml
group_name:
  target_path: relative/folder
  move_targets:
    files: true
    folders: false
    types:
      - image
    extensions:
      - eml
  keywords:
    - IMG_
  globs:
    - "DSC*"
    - "vacation-????.*"
```

Rules:
- target_path must be a relative subdirectory of the working directory (not `.`, not absolute, no `..` escape).
- move_targets.files and folders are required booleans; at least one must be true.
- types is optional. Supported values only:
{format_type_catalog()}
- extensions is optional (jpg or .jpg). Unioned with types. Match the last suffix (Path.suffix), not the stem. Compound suffixes like tar.gz match the trailing name.
- types/extensions apply to files only. folders: true requires keywords or globs.
- keywords are case-sensitive substrings of the full basename (including extension).
- globs are case-sensitive fnmatch patterns against the full basename (so IMG_* and *.jpg both work). Do not glob the stem; use extensions for suffix-only filters.
- A group needs at least one of: keywords, globs, types, extensions. Name matchers (keywords/globs) are OR; the type/extension filter is AND with the name matchers.
- Do not invent types. Use extensions for suffixes that are not in the lists above. video does not include .ts (TypeScript collision).
- documents includes office files plus .txt/.md/.csv and similar text notes, not HTML.
- Prefer types for media/document dumps; use globs for patterned names; use keywords for distinctive tokens.
- Do not write groups that would match automover.yaml, hidden names, or symlinks.
- Reuse existing destination folder names from the listing when they already look like sort buckets.
- Nested lists may be indented under their key, or placed at the same indent (both are valid).
- Output must be parseable by automover's YAML subset: mappings, lists, booleans, # comments, quoted strings. No tabs, no flow lists like [a, b].

## Working directory
{cwd}

## Top-level entries automover can move
{_format_inventory_section(inventory)}
{existing_block}
## Your task
Write a complete automover.yaml that sorts the listed entries into sensible groups. Cover as many entries as is reasonable without catch-all keywords so short they match everything. If the directory is already tidy or empty of movable entries, still emit a minimal valid config with a comment explaining that.
"""


def load_optional_config_text(
    cwd: Path, explicit: Optional[Path]
) -> tuple[Optional[Path], Optional[str]]:
    try:
        path = find_config(cwd, explicit, lambda _m: None)
    except ConfigError:
        if explicit is not None:
            raise
        return None, None
    try:
        return path, path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config {path}: {exc}") from exc


def expand_prompt_command(argv: list[str]) -> list[str]:
    """Turn a bare `prompt` token into --prompt (so `automover.py prompt` works)."""
    flags_with_value = {"--cwd", "--config"}
    out: list[str] = []
    expecting_value = False
    for tok in argv:
        if expecting_value:
            out.append(tok)
            expecting_value = False
            continue
        if tok in flags_with_value:
            out.append(tok)
            expecting_value = True
            continue
        if tok == "prompt":
            out.append("--prompt")
            continue
        out.append(tok)
    return out


def plan_moves(
    cwd: Path,
    entries: list[Path],
    groups: list[Group],
    *,
    apply: bool,
    skip_conflicts: bool,
    first_group_wins: bool,
    stdin: TextIO,
    prompt_out: TextIO,
    verbose: bool,
) -> Plan:
    plan = Plan()
    interactive = can_prompt(stdin)

    for entry in entries:
        kind = classify_entry(entry)
        if kind not in ("file", "dir"):
            continue
        hits = matching_groups(entry.name, kind, groups)
        if not hits:
            if verbose:
                plan.skipped.append(
                    Action(entry, None, None, None, "skip_unmatched", "no match")
                )
            continue

        chosen: Optional[Match]
        if len(hits) == 1:
            chosen = hits[0]
        elif first_group_wins:
            chosen = hits[0]
        elif apply and interactive:
            chosen = prompt_overlap(entry.name, hits, stdin, prompt_out)
            if chosen is None:
                plan.skipped.append(
                    Action(
                        entry,
                        None,
                        None,
                        None,
                        "skip_overlap",
                        "matches multiple groups; skipped by user",
                    )
                )
                continue
        elif apply and not interactive:
            names = ", ".join(m.group.name for m in hits)
            raise ConfigError(
                f"{entry.name!r} matches multiple groups ({names}). "
                "Re-run with --first-group-wins, or run --apply in a terminal to choose."
            )
        else:
            detail = ", ".join(
                f"{m.group.name} ({m.reason})" for m in hits
            )
            plan.overlaps.append(
                Action(entry, None, None, None, "overlap", detail)
            )
            continue

        dest = chosen.group.target / entry.name
        if dest.exists():
            if skip_conflicts:
                plan.skipped.append(
                    Action(
                        entry,
                        dest,
                        chosen.group,
                        chosen.keyword,
                        "skip_conflict",
                        "destination already exists",
                        chosen.reason,
                    )
                )
                continue
            if apply and interactive:
                renamed = prompt_conflict(entry.name, dest, cwd, stdin, prompt_out)
                if renamed is None:
                    plan.skipped.append(
                        Action(
                            entry,
                            dest,
                            chosen.group,
                            chosen.keyword,
                            "skip_conflict",
                            "destination already exists; skipped by user",
                            chosen.reason,
                        )
                    )
                    continue
                dest = renamed
            elif apply and not interactive:
                try:
                    dest_rel = dest.relative_to(cwd)
                except ValueError:
                    dest_rel = dest
                raise ConfigError(
                    f"destination already exists: {dest_rel.as_posix()} "
                    "(never overwrites). Re-run with --skip-conflicts, "
                    "or run --apply in a terminal to choose skip/rename."
                )
            else:
                plan.conflicts.append(
                    Action(
                        entry,
                        dest,
                        chosen.group,
                        chosen.keyword,
                        "conflict",
                        "destination already exists",
                        chosen.reason,
                    )
                )
                continue

        note = ""
        if dest.name != entry.name:
            note = "renamed because destination existed"
        plan.moves.append(
            Action(
                source=entry,
                dest=dest,
                group=chosen.group,
                keyword=chosen.keyword,
                kind="move",
                detail=note,
                reason=chosen.reason,
            )
        )
    return plan


# ---------------------------------------------------------------------------
# Execute and report
# ---------------------------------------------------------------------------


def perform_moves(plan: Plan) -> list[Action]:
    failed: list[Action] = []
    remaining: list[Action] = []
    for action in plan.moves:
        assert action.dest is not None
        try:
            action.dest.parent.mkdir(parents=True, exist_ok=True)
            if action.dest.exists():
                raise OSError("destination appeared before the move (never overwrites)")
            shutil.move(str(action.source), str(action.dest))
            remaining.append(action)
        except OSError as exc:
            failed.append(
                Action(
                    source=action.source,
                    dest=action.dest,
                    group=action.group,
                    keyword=action.keyword,
                    kind="failed",
                    detail=str(exc),
                    reason=action.reason,
                )
            )
    plan.moves = remaining
    return failed


def _rel(path: Path, cwd: Path) -> str:
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return str(path)


def print_plan(
    plan: Plan,
    cwd: Path,
    *,
    apply: bool,
    verbose: bool,
    stdout: TextIO,
) -> None:
    def emit_move(action: Action) -> None:
        assert action.dest is not None and action.group is not None
        extra = f"  ({action.detail})" if action.detail else ""
        matched = f"  {action.reason}" if action.reason else ""
        stdout.write(
            f"  {_rel(action.source, cwd)} -> {_rel(action.dest, cwd)}{extra}\n"
            f"    group: {action.group.name}{matched}\n"
        )

    verb = "Moved" if apply else "Would move"
    if plan.moves:
        stdout.write(f"{verb}:\n")
        for action in plan.moves:
            emit_move(action)
        stdout.write("\n")

    if plan.overlaps:
        stdout.write("Would prompt (multi-group overlap):\n")
        for action in plan.overlaps:
            stdout.write(f"  {action.rel(cwd)}\n    matches: {action.detail}\n")
        stdout.write(
            "  pass --first-group-wins to use the first group in file order, "
            "or re-run with --apply in a terminal to choose.\n\n"
        )

    if plan.conflicts:
        stdout.write("Would prompt (destination exists; never overwrites):\n")
        for action in plan.conflicts:
            dest = _rel(action.dest, cwd) if action.dest is not None else "?"
            stdout.write(f"  {action.rel(cwd)} -> {dest}\n")
        stdout.write(
            "  pass --skip-conflicts to skip these items, "
            "or re-run with --apply in a terminal to choose skip/rename.\n\n"
        )

    skipped_to_show = plan.skipped if verbose else [
        a for a in plan.skipped if a.kind in {"skip_conflict", "skip_overlap", "skip_symlink", "skip_other"}
    ]
    if skipped_to_show:
        stdout.write("Skipped:\n")
        for action in skipped_to_show:
            stdout.write(f"  {action.rel(cwd)}  ({action.detail or action.kind})\n")
        stdout.write("\n")


def print_summary(
    plan: Plan,
    failed: list[Action],
    *,
    apply: bool,
    unmatched_verbose: int,
    stdout: TextIO,
) -> None:
    n_move = len(plan.moves)
    n_skip = len(plan.skipped)
    n_overlap = len(plan.overlaps)
    n_conflict = len(plan.conflicts)
    n_fail = len(failed)
    if apply:
        stdout.write(
            f"Summary: {n_move} moved, {n_skip} skipped, {n_fail} failed"
        )
    else:
        stdout.write(
            f"Summary: {n_move} would move, {n_overlap} overlap, "
            f"{n_conflict} conflict, {n_skip} skipped, {n_fail} failed"
        )
    if unmatched_verbose:
        stdout.write(f", {unmatched_verbose} unmatched")
    stdout.write("\n")
    if not apply:
        stdout.write("Dry-run: no files were moved. Re-run with --apply to perform moves.\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automover",
        description=(
            "Move top-level files and folders using automover.yaml. "
            "Match on case-sensitive keyword substrings, globs, and/or file types. "
            "Default mode is dry-run. "
            "Use --prompt (or: prompt) to print an AI prompt for generating the YAML; "
            "automover does not call a model."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform moves. Default is dry-run (report only).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Config file path (default: automover.yaml or automover.yml in the working directory)",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        help="Working directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--skip-conflicts",
        action="store_true",
        help=(
            "If a destination name already exists, skip the item instead of prompting. "
            "Never overwrites."
        ),
    )
    parser.add_argument(
        "--first-group-wins",
        action="store_true",
        help=(
            "If an item matches multiple groups, use the first group in file order "
            "instead of prompting."
        ),
    )
    parser.add_argument(
        "--prompt",
        action="store_true",
        help=(
            "List top-level files and folders and print a prompt an AI agent can use "
            "to generate automover.yaml. Does not call a model or move files. "
            "Also accepted as: automover.py prompt"
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the config file and exit without scanning.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="List skipped unmatched, hidden, config, and target entries.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"automover {VERSION}",
    )
    return parser


def main(
    argv: Optional[list[str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    parser = build_parser()
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(expand_prompt_command(raw_argv))

    cwd = (args.cwd if args.cwd is not None else Path.cwd())
    try:
        cwd = cwd.resolve()
    except OSError as exc:
        stderr.write(f"error: could not resolve working directory: {exc}\n")
        return EXIT_USAGE
    if not cwd.is_dir():
        stderr.write(f"error: working directory is not a directory: {cwd}\n")
        return EXIT_USAGE

    def warn(message: str) -> None:
        stderr.write(f"warning: {message}\n")

    if args.prompt:
        if args.apply or args.validate:
            stderr.write("error: --prompt cannot be combined with --apply or --validate\n")
            return EXIT_USAGE
        try:
            inventory = list_inventory(cwd)
            existing_path, existing_yaml = load_optional_config_text(cwd, args.config)
        except ConfigError as exc:
            stderr.write(f"error: {exc}\n")
            return EXIT_USAGE
        stdout.write(
            build_generation_prompt(
                cwd,
                inventory,
                existing_yaml=existing_yaml,
                existing_path=existing_path,
            )
        )
        return EXIT_OK

    try:
        config_path = find_config(cwd, args.config, warn)
        groups = load_config(config_path, cwd)
    except ConfigError as exc:
        stderr.write(f"error: {exc}\n")
        return EXIT_USAGE

    if args.validate:
        stdout.write(f"Config OK: {config_path} ({len(groups)} group(s))\n")
        return EXIT_OK

    mode = "apply" if args.apply else "dry-run"
    stdout.write(f"== automover {mode} ==\n")
    stdout.write(f"Config: {config_path}\n")
    stdout.write(f"Working directory: {cwd}\n\n")

    try:
        entries, early_skipped = collect_candidates(
            cwd, config_path, groups, verbose=args.verbose
        )
        plan = plan_moves(
            cwd,
            entries,
            groups,
            apply=args.apply,
            skip_conflicts=args.skip_conflicts,
            first_group_wins=args.first_group_wins,
            stdin=stdin,
            prompt_out=stderr,
            verbose=args.verbose,
        )
        plan.skipped = early_skipped + plan.skipped
    except UserAbort as exc:
        stderr.write(f"aborted: {exc}\n")
        return EXIT_USAGE
    except ConfigError as exc:
        stderr.write(f"error: {exc}\n")
        return EXIT_USAGE

    failed: list[Action] = []
    if args.apply:
        failed = perform_moves(plan)
        for action in failed:
            stderr.write(
                f"error: failed to move {_rel(action.source, cwd)}: {action.detail}\n"
            )

    print_plan(plan, cwd, apply=args.apply, verbose=args.verbose, stdout=stdout)
    unmatched = sum(1 for a in plan.skipped if a.kind == "skip_unmatched")
    print_summary(
        plan,
        failed,
        apply=args.apply,
        unmatched_verbose=unmatched if args.verbose else 0,
        stdout=stdout,
    )

    if failed:
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\naborted: interrupted\n")
        sys.exit(EXIT_USAGE)
