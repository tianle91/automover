from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import automover  # noqa: E402


class TtyIO(io.StringIO):
    def isatty(self) -> bool:
        return True


SAMPLE = """\
# sample automover config
photos:
  target_path: pictures
  move_targets:
    files: true
    folders: false
  keywords:
    - IMG_
    - photo
docs:
  target_path: documents
  move_targets:
    files: true
    folders: true
  keywords:
    - invoice
    - report
"""


def write_tree(files: dict[str, str | None]) -> tempfile.TemporaryDirectory:
    """Create a temp dir. Value None means a directory; str means file contents."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, content in files.items():
        path = root / rel
        if content is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return tmp


def run(
    cwd: Path,
    argv: list[str],
    stdin_text: str = "",
    tty: bool = False,
) -> tuple[int, str, str]:
    stdin: io.StringIO = TtyIO(stdin_text) if tty else io.StringIO(stdin_text)
    stdout = TtyIO() if tty else io.StringIO()
    stderr = io.StringIO()
    code = automover.main(
        ["--cwd", str(cwd), *argv],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


class YamlLoaderTests(unittest.TestCase):
    def test_parses_comments_and_booleans(self):
        data = automover.load_simple_yaml(SAMPLE)
        self.assertEqual(set(data), {"photos", "docs"})
        self.assertTrue(data["photos"]["move_targets"]["files"])
        self.assertFalse(data["photos"]["move_targets"]["folders"])
        self.assertEqual(data["docs"]["keywords"], ["invoice", "report"])

    def test_quoted_strings_and_inline_comments(self):
        text = """\
group:
  target_path: "my folder"  # comment
  move_targets:
    files: true
    folders: false
  keywords:
    - "hello world"
    - plain
"""
        data = automover.load_simple_yaml(text)
        self.assertEqual(data["group"]["target_path"], "my folder")
        self.assertEqual(data["group"]["keywords"][0], "hello world")

    def test_rejects_tabs(self):
        with self.assertRaises(automover.ConfigError) as ctx:
            automover.load_simple_yaml("group:\n\ttarget_path: x\n")
        self.assertIn("tabs", str(ctx.exception))

    def test_rejects_duplicate_keys(self):
        with self.assertRaises(automover.ConfigError):
            automover.load_simple_yaml("a: 1\na: 2\n")

    def test_same_indent_lists(self):
        text = """\
group:
  target_path: out
  keywords:
  - first
  - second
"""
        data = automover.load_simple_yaml(text)
        self.assertEqual(data["group"]["keywords"], ["first", "second"])

    def test_empty_file(self):
        with self.assertRaises(automover.ConfigError):
            automover.load_simple_yaml("\n# only comments\n")


class SchemaTests(unittest.TestCase):
    def test_validate_ok(self):
        with write_tree({"automover.yaml": SAMPLE}) as name:
            cwd = Path(name)
            groups = automover.load_config(cwd / "automover.yaml", cwd)
            self.assertEqual([g.name for g in groups], ["photos", "docs"])
            self.assertEqual(groups[0].target, (cwd / "pictures").resolve())

    def test_unknown_key(self):
        text = SAMPLE.replace("  keywords:", "  extra: 1\n  keywords:")
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(cwd / "automover.yaml", cwd)
            self.assertIn("unknown keys", str(ctx.exception))

    def test_both_move_targets_false(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: false
    folders: false
  keywords:
    - x
"""
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(cwd / "automover.yaml", cwd)
            self.assertIn("cannot both be false", str(ctx.exception))

    def test_empty_keywords(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: true
  keywords: []
"""
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError):
                automover.load_config(cwd / "automover.yaml", cwd)

    def test_unknown_type(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
    types:
      - photos
"""
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(cwd / "automover.yaml", cwd)
            self.assertIn("unknown type", str(ctx.exception))
            self.assertIn("documents", str(ctx.exception))

    def test_document_singular_rejected(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
    types:
      - document
"""
        with write_tree({"automover.yaml": text}) as name:
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(Path(name) / "automover.yaml", Path(name))
            self.assertIn("unknown type", str(ctx.exception))

    def test_types_require_files(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: false
    folders: true
    types:
      - image
  keywords:
    - foo
"""
        with write_tree({"automover.yaml": text}) as name:
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(Path(name) / "automover.yaml", Path(name))
            self.assertIn("require files: true", str(ctx.exception))

    def test_folders_require_keywords(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: true
    types:
      - image
"""
        with write_tree({"automover.yaml": text}) as name:
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(Path(name) / "automover.yaml", Path(name))
            self.assertIn("folders: true requires keywords or globs", str(ctx.exception))

    def test_files_require_some_matcher(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
"""
        with write_tree({"automover.yaml": text}) as name:
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(Path(name) / "automover.yaml", Path(name))
            self.assertIn("requires keywords, globs, types, or extensions", str(ctx.exception))

    def test_duplicate_extension_with_and_without_dot(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
    extensions:
      - jpg
      - .JPG
"""
        with write_tree({"automover.yaml": text}) as name:
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(Path(name) / "automover.yaml", Path(name))
            self.assertIn("duplicate extension", str(ctx.exception))

    def test_target_escapes_cwd(self):
        text = """\
g:
  target_path: ../outside
  move_targets:
    files: true
    folders: true
  keywords:
    - x
"""
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(cwd / "automover.yaml", cwd)
            self.assertIn("escapes", str(ctx.exception))

    def test_target_is_cwd(self):
        text = """\
g:
  target_path: .
  move_targets:
    files: true
    folders: true
  keywords:
    - x
"""
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(cwd / "automover.yaml", cwd)
            self.assertIn("working directory", str(ctx.exception))

    def test_absolute_target(self):
        text = """\
g:
  target_path: /tmp/out
  move_targets:
    files: true
    folders: true
  keywords:
    - x
"""
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(cwd / "automover.yaml", cwd)
            self.assertIn("relative", str(ctx.exception))

    def test_target_exists_as_file(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: true
  keywords:
    - x
"""
        with write_tree({"automover.yaml": text, "out": "not a dir"}) as name:
            cwd = Path(name)
            with self.assertRaises(automover.ConfigError) as ctx:
                automover.load_config(cwd / "automover.yaml", cwd)
            self.assertIn("not a directory", str(ctx.exception))


class FindConfigTests(unittest.TestCase):
    def test_prefers_yaml_when_both_exist(self):
        with write_tree(
            {"automover.yaml": SAMPLE, "automover.yml": SAMPLE}
        ) as name:
            warnings: list[str] = []
            path = automover.find_config(Path(name), None, warnings.append)
            self.assertTrue(path.name.endswith("automover.yaml"))
            self.assertTrue(warnings)

    def test_accepts_yml(self):
        with write_tree({"automover.yml": SAMPLE}) as name:
            path = automover.find_config(Path(name), None, lambda m: None)
            self.assertTrue(path.name.endswith("automover.yml"))

    def test_missing(self):
        with write_tree({}) as name:
            with self.assertRaises(automover.ConfigError):
                automover.find_config(Path(name), None, lambda m: None)


class MatchTests(unittest.TestCase):
    def test_substring_case_sensitive(self):
        with write_tree({"automover.yaml": SAMPLE}) as name:
            groups = automover.load_config(Path(name) / "automover.yaml", Path(name))
        hits = automover.matching_groups("IMG_1234.jpg", "file", groups)
        self.assertEqual([h.group.name for h in hits], ["photos"])
        self.assertEqual(hits[0].keyword, "IMG_")

        self.assertEqual(automover.matching_groups("img_1234.jpg", "file", groups), [])

    def test_type_filter(self):
        with write_tree({"automover.yaml": SAMPLE}) as name:
            groups = automover.load_config(Path(name) / "automover.yaml", Path(name))
        # photos only moves files
        self.assertEqual(automover.matching_groups("IMG_album", "dir", groups), [])
        self.assertEqual(
            [h.group.name for h in automover.matching_groups("IMG_album", "file", groups)],
            ["photos"],
        )

    def test_overlap(self):
        text = """\
a:
  target_path: a
  move_targets:
    files: true
    folders: true
  keywords:
    - draft
b:
  target_path: b
  move_targets:
    files: true
    folders: true
  keywords:
    - invoice
"""
        with write_tree({"automover.yaml": text}) as name:
            groups = automover.load_config(Path(name) / "automover.yaml", Path(name))
        hits = automover.matching_groups("draft_invoice.pdf", "file", groups)
        self.assertEqual([h.group.name for h in hits], ["a", "b"])


class CliTests(unittest.TestCase):
    def test_validate(self):
        with write_tree({"automover.yaml": SAMPLE}) as name:
            code, out, err = run(Path(name), ["--validate"])
        self.assertEqual(code, 0, err)
        self.assertIn("Config OK", out)

    def test_dry_run_does_not_move(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "x",
                "invoice.pdf": "y",
                "notes.txt": "z",
                "report_folder": None,
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, [])
            self.assertEqual(code, 0, err)
            self.assertIn("Dry-run", out)
            self.assertTrue((cwd / "IMG_1.jpg").is_file())
            self.assertFalse((cwd / "pictures").exists())
            self.assertIn("IMG_1.jpg -> pictures/IMG_1.jpg", out)
            self.assertIn("invoice.pdf -> documents/invoice.pdf", out)
            self.assertIn("report_folder -> documents/report_folder", out)
            self.assertNotIn("notes.txt", out)

    def test_apply_moves_files_and_folders(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "x",
                "invoice.pdf": "y",
                "report_folder/inside.txt": "z",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertFalse((cwd / "IMG_1.jpg").exists())
            self.assertTrue((cwd / "pictures" / "IMG_1.jpg").is_file())
            self.assertTrue((cwd / "documents" / "invoice.pdf").is_file())
            self.assertTrue((cwd / "documents" / "report_folder" / "inside.txt").is_file())
            self.assertIn("Moved:", out)

    def test_idempotent_second_run(self):
        with write_tree(
            {"automover.yaml": SAMPLE, "IMG_1.jpg": "x"}
        ) as name:
            cwd = Path(name)
            self.assertEqual(run(cwd, ["--apply"])[0], 0)
            code, out, err = run(cwd, ["--apply", "--verbose"])
            self.assertEqual(code, 0, err)
            self.assertIn("0 moved", out)
            self.assertTrue((cwd / "pictures" / "IMG_1.jpg").is_file())

    def test_does_not_move_config_even_if_keyword_matches(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: true
  keywords:
    - automover
"""
        with write_tree({"automover.yaml": text}) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply", "--verbose"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "automover.yaml").is_file())
            self.assertFalse((cwd / "out" / "automover.yaml").exists())

    def test_skips_hidden(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: true
  keywords:
    - secret
"""
        with write_tree({"automover.yaml": text, ".secret.txt": "x"}) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply", "--verbose"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / ".secret.txt").is_file())
            self.assertIn("hidden", out)

    def test_skips_target_directory(self):
        with write_tree(
            {"automover.yaml": SAMPLE, "pictures/keep.txt": "x"}
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply", "--verbose"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "pictures" / "keep.txt").is_file())

    def test_files_only_leaves_folders(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
  keywords:
    - foo
"""
        with write_tree(
            {"automover.yaml": text, "foo.txt": "a", "foo_dir": None}
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "out" / "foo.txt").is_file())
            self.assertTrue((cwd / "foo_dir").is_dir())

    def test_overlap_dry_run_reports_prompt(self):
        text = """\
a:
  target_path: a
  move_targets:
    files: true
    folders: true
  keywords:
    - draft
b:
  target_path: b
  move_targets:
    files: true
    folders: true
  keywords:
    - invoice
"""
        with write_tree(
            {"automover.yaml": text, "draft_invoice.pdf": "x"}
        ) as name:
            code, out, err = run(Path(name), [])
            self.assertEqual(code, 0, err)
            self.assertIn("Would prompt (multi-group overlap)", out)
            self.assertTrue((Path(name) / "draft_invoice.pdf").is_file())

    def test_overlap_first_group_wins(self):
        text = """\
a:
  target_path: a
  move_targets:
    files: true
    folders: true
  keywords:
    - draft
b:
  target_path: b
  move_targets:
    files: true
    folders: true
  keywords:
    - invoice
"""
        with write_tree(
            {"automover.yaml": text, "draft_invoice.pdf": "x"}
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply", "--first-group-wins"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "a" / "draft_invoice.pdf").is_file())
            self.assertFalse((cwd / "b").exists() and any((cwd / "b").iterdir()))

    def test_overlap_apply_without_tty_errors(self):
        text = """\
a:
  target_path: a
  move_targets:
    files: true
    folders: true
  keywords:
    - draft
b:
  target_path: b
  move_targets:
    files: true
    folders: true
  keywords:
    - invoice
"""
        with write_tree(
            {"automover.yaml": text, "draft_invoice.pdf": "x"}
        ) as name:
            code, out, err = run(Path(name), ["--apply"])
            self.assertEqual(code, 1)
            self.assertIn("--first-group-wins", err)
            self.assertTrue((Path(name) / "draft_invoice.pdf").is_file())

    def test_overlap_prompt_choose_second_group(self):
        text = """\
a:
  target_path: a
  move_targets:
    files: true
    folders: true
  keywords:
    - draft
b:
  target_path: b
  move_targets:
    files: true
    folders: true
  keywords:
    - invoice
"""
        with write_tree(
            {"automover.yaml": text, "draft_invoice.pdf": "x"}
        ) as name:
            cwd = Path(name)
            code, out, err = run(
                cwd, ["--apply"], stdin_text="2\n", tty=True
            )
            self.assertEqual(code, 0, err + out)
            self.assertTrue((cwd / "b" / "draft_invoice.pdf").is_file())
            self.assertFalse((cwd / "a" / "draft_invoice.pdf").exists())

    def test_conflict_dry_run(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "new",
                "pictures/IMG_1.jpg": "old",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, [])
            self.assertEqual(code, 0, err)
            self.assertIn("Would prompt (destination exists", out)
            self.assertEqual((cwd / "IMG_1.jpg").read_text(), "new")
            self.assertEqual((cwd / "pictures" / "IMG_1.jpg").read_text(), "old")

    def test_conflict_skip_flag(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "new",
                "pictures/IMG_1.jpg": "old",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply", "--skip-conflicts"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "IMG_1.jpg").is_file())
            self.assertEqual((cwd / "pictures" / "IMG_1.jpg").read_text(), "old")
            self.assertIn("destination already exists", out)

    def test_conflict_apply_without_tty_errors(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "new",
                "pictures/IMG_1.jpg": "old",
            }
        ) as name:
            code, out, err = run(Path(name), ["--apply"])
            self.assertEqual(code, 1)
            self.assertIn("--skip-conflicts", err)

    def test_conflict_prompt_rename(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "new",
                "pictures/IMG_1.jpg": "old",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(
                cwd, ["--apply"], stdin_text="r\n", tty=True
            )
            self.assertEqual(code, 0, err + out)
            self.assertFalse((cwd / "IMG_1.jpg").exists())
            self.assertEqual((cwd / "pictures" / "IMG_1.jpg").read_text(), "old")
            self.assertEqual((cwd / "pictures" / "IMG_1 (1).jpg").read_text(), "new")
            self.assertIn("renamed", out)

    def test_conflict_prompt_skip(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "new",
                "pictures/IMG_1.jpg": "old",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(
                cwd, ["--apply"], stdin_text="s\n", tty=True
            )
            self.assertEqual(code, 0, err + out)
            self.assertTrue((cwd / "IMG_1.jpg").is_file())
            self.assertEqual((cwd / "pictures" / "IMG_1.jpg").read_text(), "old")

    def test_missing_config(self):
        with write_tree({"file.txt": "x"}) as name:
            code, out, err = run(Path(name), [])
            self.assertEqual(code, 1)
            self.assertIn("no automover.yaml", err)

    def test_yml_extension(self):
        with write_tree({"automover.yml": SAMPLE, "IMG_1.jpg": "x"}) as name:
            code, out, err = run(Path(name), ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue((Path(name) / "pictures" / "IMG_1.jpg").is_file())

    def test_explicit_config(self):
        with write_tree(
            {"custom.yaml": SAMPLE, "IMG_1.jpg": "x"}
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply", "--config", str(cwd / "custom.yaml")])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "pictures" / "IMG_1.jpg").is_file())

    def test_user_abort_on_prompt(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "new",
                "pictures/IMG_1.jpg": "old",
            }
        ) as name:
            code, out, err = run(
                Path(name), ["--apply"], stdin_text="q\n", tty=True
            )
            self.assertEqual(code, 1)
            self.assertIn("aborted", err)
            self.assertTrue((Path(name) / "IMG_1.jpg").is_file())

    def test_never_overwrites(self):
        with write_tree(
            {
                "automover.yaml": SAMPLE,
                "IMG_1.jpg": "new",
                "pictures/IMG_1.jpg": "old",
            }
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply", "--skip-conflicts"])
            self.assertEqual((cwd / "pictures" / "IMG_1.jpg").read_text(), "old")

    def test_skips_symlink(self):
        with write_tree({"automover.yaml": SAMPLE, "real_IMG.jpg": "x"}) as name:
            cwd = Path(name)
            link = cwd / "IMG_link.jpg"
            os.symlink(cwd / "real_IMG.jpg", link)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue(link.is_symlink())
            self.assertFalse((cwd / "pictures" / "IMG_link.jpg").exists())
            self.assertIn("symlinks are not moved", out)

    def test_substring_in_middle(self):
        with write_tree(
            {"automover.yaml": SAMPLE, "vacation_photo_1.png": "x"}
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "pictures" / "vacation_photo_1.png").is_file())

    def test_nested_target_path_created(self):
        text = """\
g:
  target_path: sorted/images
  move_targets:
    files: true
    folders: false
  keywords:
    - IMG_
"""
        with write_tree({"automover.yaml": text, "IMG_2.jpg": "x"}) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "sorted" / "images" / "IMG_2.jpg").is_file())


class TypeFilterTests(unittest.TestCase):
    def test_supported_type_names(self):
        self.assertEqual(
            set(automover.FILE_TYPES),
            {"image", "audio", "video", "documents"},
        )
        self.assertIn(".pdf", automover.FILE_TYPES["documents"])
        self.assertIn(".jpg", automover.FILE_TYPES["image"])
        self.assertIn(".mp3", automover.FILE_TYPES["audio"])
        self.assertIn(".mp4", automover.FILE_TYPES["video"])
        self.assertNotIn(".ts", automover.FILE_TYPES["video"])

    def test_type_only_moves_images(self):
        text = """\
g:
  target_path: pictures
  move_targets:
    files: true
    folders: false
    types:
      - image
"""
        with write_tree(
            {
                "automover.yaml": text,
                "vacation.jpg": "x",
                "clip.mp4": "y",
                "notes.txt": "z",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "pictures" / "vacation.jpg").is_file())
            self.assertTrue((cwd / "clip.mp4").is_file())
            self.assertTrue((cwd / "notes.txt").is_file())
            self.assertIn("type: image", out)

    def test_keyword_and_type_are_and(self):
        text = """\
g:
  target_path: pictures
  move_targets:
    files: true
    folders: false
    types:
      - image
  keywords:
    - trip
"""
        with write_tree(
            {
                "automover.yaml": text,
                "trip.jpg": "x",
                "trip.mp4": "y",
                "other.png": "z",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "pictures" / "trip.jpg").is_file())
            self.assertTrue((cwd / "trip.mp4").is_file())
            self.assertTrue((cwd / "other.png").is_file())

    def test_types_and_extensions_union(self):
        text = """\
g:
  target_path: pictures
  move_targets:
    files: true
    folders: false
    types:
      - image
    extensions:
      - heic
      - .foo
"""
        with write_tree(
            {
                "automover.yaml": text,
                "a.png": "x",
                "b.heic": "y",
                "c.foo": "z",
                "d.txt": "no",
            }
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "pictures" / "a.png").is_file())
            self.assertTrue((cwd / "pictures" / "b.heic").is_file())
            self.assertTrue((cwd / "pictures" / "c.foo").is_file())
            self.assertTrue((cwd / "d.txt").is_file())

    def test_extension_case_insensitive(self):
        text = """\
g:
  target_path: pictures
  move_targets:
    files: true
    folders: false
    types:
      - image
"""
        with write_tree({"automover.yaml": text, "Photo.JPG": "x"}) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "pictures" / "Photo.JPG").is_file())

    def test_compound_extension(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
    extensions:
      - tar.gz
"""
        with write_tree(
            {"automover.yaml": text, "backup.tar.gz": "x", "backup.gz": "y"}
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "out" / "backup.tar.gz").is_file())
            self.assertTrue((cwd / "backup.gz").is_file())

    def test_documents_type(self):
        text = """\
g:
  target_path: docs
  move_targets:
    files: true
    folders: false
    types:
      - documents
"""
        with write_tree(
            {
                "automover.yaml": text,
                "report.pdf": "x",
                "sheet.xlsx": "y",
                "readme.md": "z",
                "song.mp3": "no",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "docs" / "report.pdf").is_file())
            self.assertTrue((cwd / "docs" / "sheet.xlsx").is_file())
            self.assertTrue((cwd / "docs" / "readme.md").is_file())
            self.assertTrue((cwd / "song.mp3").is_file())
            self.assertIn("type: documents", out)

    def test_audio_and_video_types(self):
        text = """\
g:
  target_path: media
  move_targets:
    files: true
    folders: false
    types:
      - audio
      - video
"""
        with write_tree(
            {
                "automover.yaml": text,
                "song.mp3": "x",
                "clip.mkv": "y",
                "pic.png": "z",
            }
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "media" / "song.mp3").is_file())
            self.assertTrue((cwd / "media" / "clip.mkv").is_file())
            self.assertTrue((cwd / "pic.png").is_file())

    def test_folders_ignore_type_filter(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: true
    types:
      - image
  keywords:
    - album
"""
        with write_tree(
            {
                "automover.yaml": text,
                "album.jpg": "x",
                "album.txt": "y",
                "album_dir": None,
            }
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "out" / "album.jpg").is_file())
            self.assertTrue((cwd / "album.txt").is_file())
            self.assertTrue((cwd / "out" / "album_dir").is_dir())


class GlobAndExtensionTests(unittest.TestCase):
    def test_glob_basename_not_stem(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
  globs:
    - "IMG_*"
    - "*.PDF"
"""
        with write_tree(
            {
                "automover.yaml": text,
                "IMG_1.jpg": "x",
                "photo.jpg": "y",
                "notes.PDF": "z",
            }
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "out" / "IMG_1.jpg").is_file())
            self.assertTrue((cwd / "photo.jpg").is_file())
            self.assertTrue((cwd / "out" / "notes.PDF").is_file())
            self.assertIn("glob: IMG_*", out)

    def test_glob_case_sensitive_pattern(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
  globs:
    - "*.jpg"
"""
        with write_tree(
            {"automover.yaml": text, "a.jpg": "x", "b.JPG": "y"}
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "out" / "a.jpg").is_file())
            self.assertTrue((cwd / "b.JPG").is_file())

    def test_folders_may_use_globs_without_keywords(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: false
    folders: true
  globs:
    - "album_*"
"""
        with write_tree(
            {"automover.yaml": text, "album_one": None, "other": None}
        ) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--apply"])
            self.assertEqual(code, 0, err)
            self.assertTrue((cwd / "out" / "album_one").is_dir())
            self.assertTrue((cwd / "other").is_dir())

    def test_keyword_or_glob(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
  keywords:
    - invoice
  globs:
    - "scan-????.pdf"
"""
        with write_tree(
            {
                "automover.yaml": text,
                "invoice.pdf": "x",
                "scan-2024.pdf": "y",
                "other.pdf": "z",
            }
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "out" / "invoice.pdf").is_file())
            self.assertTrue((cwd / "out" / "scan-2024.pdf").is_file())
            self.assertTrue((cwd / "other.pdf").is_file())

    def test_extension_uses_last_suffix_not_stem(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
    extensions:
      - jpg
"""
        with write_tree(
            {
                "automover.yaml": text,
                "photo.jpg": "x",
                "photo.jpg.exe": "y",
                "jpg": "z",
            }
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "out" / "photo.jpg").is_file())
            self.assertTrue((cwd / "photo.jpg.exe").is_file())
            self.assertTrue((cwd / "jpg").is_file())

    def test_ts_is_not_video(self):
        text = """\
g:
  target_path: out
  move_targets:
    files: true
    folders: false
    types:
      - video
"""
        with write_tree(
            {"automover.yaml": text, "app.ts": "x", "clip.mp4": "y"}
        ) as name:
            cwd = Path(name)
            run(cwd, ["--apply"])
            self.assertTrue((cwd / "app.ts").is_file())
            self.assertTrue((cwd / "out" / "clip.mp4").is_file())


class PromptTests(unittest.TestCase):
    def test_prompt_does_not_require_config(self):
        with write_tree({"vacation.jpg": "x", "clip.mp4": "y", "ProjectX": None}) as name:
            cwd = Path(name)
            code, out, err = run(cwd, ["--prompt"])
            self.assertEqual(code, 0, err)
            self.assertIn("vacation.jpg", out)
            self.assertIn("[image]", out)
            self.assertIn("clip.mp4", out)
            self.assertIn("[video]", out)
            self.assertIn("ProjectX/", out)
            self.assertIn("Output only the YAML", out)
            self.assertIn("types is optional", out)
            self.assertIn("documents:", out)
            self.assertNotIn("Existing config", out)
            self.assertFalse((cwd / "automover.yaml").exists())

    def test_prompt_subcommand_alias(self):
        with write_tree({"notes.pdf": "x"}) as name:
            code, out, err = run(Path(name), ["prompt"])
            self.assertEqual(code, 0, err)
            self.assertIn("notes.pdf", out)
            self.assertIn("[documents]", out)

    def test_prompt_includes_existing_config(self):
        with write_tree({"automover.yaml": SAMPLE, "IMG_1.jpg": "x"}) as name:
            code, out, err = run(Path(name), ["--prompt"])
            self.assertEqual(code, 0, err)
            self.assertIn("Existing config", out)
            self.assertIn("photos:", out)
            self.assertIn("IMG_1.jpg", out)
            self.assertIn("automover.yaml  (config file)", out)

    def test_prompt_skips_hidden_and_symlinks(self):
        with write_tree({"visible.txt": "x", ".secret": "y"}) as name:
            cwd = Path(name)
            os.symlink(cwd / "visible.txt", cwd / "link.txt")
            code, out, err = run(cwd, ["--prompt"])
            self.assertEqual(code, 0, err)
            self.assertIn("visible.txt", out)
            self.assertIn(".secret  (hidden)", out)
            self.assertIn("link.txt  (symlink)", out)

    def test_prompt_rejects_apply(self):
        with write_tree({"a.txt": "x"}) as name:
            code, out, err = run(Path(name), ["--prompt", "--apply"])
            self.assertEqual(code, 1)
            self.assertIn("cannot be combined", err)

    def test_prompt_explicit_missing_config_errors(self):
        with write_tree({"a.txt": "x"}) as name:
            code, out, err = run(
                Path(name), ["--prompt", "--config", "missing.yaml"]
            )
            self.assertEqual(code, 1)
            self.assertIn("config not found", err)

    def test_guess_types(self):
        self.assertEqual(automover.guess_types("a.JPG"), ("image",))
        self.assertEqual(automover.guess_types("a.mp3"), ("audio",))
        self.assertEqual(automover.guess_types("a.bin"), ())

    def test_expand_prompt_command(self):
        self.assertEqual(
            automover.expand_prompt_command(["prompt", "--cwd", "/tmp"]),
            ["--prompt", "--cwd", "/tmp"],
        )
        self.assertEqual(
            automover.expand_prompt_command(["--cwd", "/tmp", "prompt"]),
            ["--cwd", "/tmp", "--prompt"],
        )


class VersionTests(unittest.TestCase):
    def test_version_file_matches_module(self):
        text = (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
        self.assertEqual(automover.VERSION, text)
        self.assertRegex(text, r"^\d+\.\d+\.\d+$")

    def test_changelog_documents_current_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{automover.VERSION}]", changelog)

    def test_cli_version(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                automover.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(automover.VERSION, buf.getvalue())


class UniqueNameTests(unittest.TestCase):
    def test_increments(self):
        with tempfile.TemporaryDirectory() as name:
            parent = Path(name)
            (parent / "file.txt").write_text("a")
            (parent / "file (1).txt").write_text("b")
            dest = automover.unique_destination(parent, "file.txt")
            self.assertEqual(dest.name, "file (2).txt")


if __name__ == "__main__":
    unittest.main()
