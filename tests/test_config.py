from __future__ import annotations

from pathlib import Path

from checks import (
    DEFAULT_COGNITIVE_LIMIT,
    Target,
    cognitive_limit,
    has_own_ruff_config,
    touched_functions_block,
)
from tools import is_behind, report_line, version_tuple


def target(root: Path, rel: str = "pkg/sub/mod.py") -> Target:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    return Target(path, root)


def test_no_configuration_means_the_baseline(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert not has_own_ruff_config(target(tmp_path))


def test_a_pyproject_ruff_table_is_the_projects_own(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 90\n")
    assert has_own_ruff_config(target(tmp_path))


def test_a_ruff_toml_in_a_subdirectory_counts(tmp_path: Path):
    t = target(tmp_path)
    (tmp_path / "pkg" / ".ruff.toml").write_text("line-length = 90\n")
    assert has_own_ruff_config(t)


def test_configuration_above_the_root_is_not_looked_at(tmp_path: Path):
    (tmp_path / "ruff.toml").write_text("line-length = 90\n")
    root = tmp_path / "project"
    assert not has_own_ruff_config(target(root))


def test_cognitive_limit_is_the_nearest_setting_or_the_default(tmp_path: Path):
    t = target(tmp_path)
    assert cognitive_limit(t) == DEFAULT_COGNITIVE_LIMIT
    (tmp_path / "pyproject.toml").write_text("[tool.complexipy]\nmax-complexity-allowed = 9\n")
    assert cognitive_limit(t) == 9


def test_touched_functions_block_unless_the_project_only_reports_them(tmp_path: Path):
    t = target(tmp_path)
    assert touched_functions_block(t)
    settings = tmp_path / "pyproject.toml"
    settings.write_text('[tool.quality-gate]\ntouched-functions = "report"\n')
    assert not touched_functions_block(t)
    settings.write_text('[tool.quality-gate]\ntouched-functions = "sometimes"\n')
    assert touched_functions_block(t)


def test_a_broken_pyproject_is_no_configuration(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff\n")
    assert not has_own_ruff_config(target(tmp_path))


def test_versions_compare_numerically():
    assert version_tuple("1.1.414") == (1, 1, 414)
    assert version_tuple("1.0rc1") is None
    assert is_behind("0.9.9", "0.10.0")
    assert not is_behind("0.17.1", "0.17.1")
    assert report_line("ruff", "0.17.1", "0.18.0").startswith("  UP ")
    assert report_line("ruff", "0.17.1", None).startswith("  ?  ")
