from __future__ import annotations

import textwrap
from pathlib import Path

from conftest import commit

from placement import (
    CHANGED,
    ELSEWHERE,
    NEW,
    History,
    changed_lines,
    function_line,
    history,
    place,
    repo_root,
    spans,
)

SOURCE = textwrap.dedent(
    """\
    import functools


    @functools.cache
    def top():
        def inner():
            return 1
        return inner()


    class Box:
        def method(self):
            return 2
    """
)


def test_spans_name_methods_through_their_class_and_count_decorators():
    assert spans(SOURCE) == (
        ("top", 4, 8),
        ("top.inner", 6, 7),
        ("Box.method", 12, 13),
    )


def test_source_that_does_not_parse_has_no_functions():
    assert spans("def broken(:\n") == ()


def test_function_line_finds_the_first_line_or_falls_back_to_one():
    functions = spans(SOURCE)
    assert function_line(functions, "Box.method") == 12
    assert function_line(functions, "missing") == 1


def test_changed_lines_reads_hunks_and_marks_pure_deletions():
    diff = "@@ -3,0 +4,2 @@\n+a\n+b\n@@ -10 +12 @@\n-x\n+y\n@@ -20,3 +21,0 @@\n"
    assert changed_lines(diff) == {4, 5, 12, 21}


class TestPlace:
    functions = (("old", 1, 10), ("old.added", 4, 6), ("brand_new", 12, 15))
    old_names = frozenset({"old"})

    def placed(self, line: int, changed: set[int]) -> str:
        return place(line, History(self.functions, self.old_names, frozenset(changed)))

    def test_a_file_head_lacks_is_all_new(self):
        assert place(2, History(self.functions, None, frozenset())) == NEW

    def test_a_function_head_lacks_is_new(self):
        assert self.placed(13, set()) == NEW

    def test_a_function_added_inside_an_old_one_is_new(self):
        assert self.placed(5, set()) == NEW

    def test_an_old_function_the_change_touched_is_changed(self):
        assert self.placed(2, {9}) == CHANGED

    def test_an_old_function_the_change_left_alone_is_elsewhere(self):
        assert self.placed(2, {13}) == ELSEWHERE

    def test_outside_every_function_a_changed_line_is_new(self):
        assert self.placed(11, {11}) == NEW

    def test_outside_every_function_an_unchanged_line_is_elsewhere(self):
        assert self.placed(11, set()) == ELSEWHERE


def test_history_against_head(repo: Path):
    commit(repo, {"mod.py": "def old():\n    return 1\n"})
    (repo / "mod.py").write_text("def old():\n    return 2\n\n\ndef new():\n    return 3\n")
    file = history(repo / "mod.py", repo)
    assert file.old_names == {"old"}
    assert file.changed == {2, 3, 4, 5, 6}
    assert [name for name, _, _ in file.functions] == ["old", "new"]


def test_an_untracked_file_has_no_copy_at_head(repo: Path):
    commit(repo, {"keep.py": "x = 1\n"})
    (repo / "fresh.py").write_text("def f():\n    return 1\n")
    assert history(repo / "fresh.py", repo).old_names is None


def test_repo_root_inside_and_outside_git(repo: Path, tmp_path: Path):
    commit(repo, {"pkg/mod.py": "x = 1\n"})
    assert repo_root(repo / "pkg") == repo
    loose = tmp_path / "loose"
    loose.mkdir()
    assert repo_root(loose) is None
