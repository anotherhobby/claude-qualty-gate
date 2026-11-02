"""The compiler at Stop: diagnostics read from a build, and the Stop hook end to end on a
scratch package. The end-to-end tests need Xcode and take a few seconds each to build."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest
from conftest import commit
from test_gate import run_hook

import swift_build
from checks import BY_BUILD, BY_LINE
from gate import check
from placement import NEW

MANIFEST = textwrap.dedent(
    """\
    // swift-tools-version:6.0
    import PackageDescription

    let package = Package(name: "Shapes", targets: [.target(name: "Shapes")])
    """
)

AREA = textwrap.dedent(
    """\
    public func area(side: Int) -> Int {
        side * side
    }
    """
)

USE = textwrap.dedent(
    """\
    public func floor(side: Int) -> Int {
        area(side: side)
    }
    """
)


def test_diagnostics_are_read_per_file_in_the_package_only(tmp_path: Path):
    package = tmp_path.resolve()
    output = "\n".join([
        f"{package}/Sources/A/a.swift:2:9: warning: never used",
        f"{package}/Sources/A/a.swift:2:9: warning: never used",
        f"{package}/Sources/A/b.swift:5:1: error: cannot find 'area' in scope",
        "/elsewhere/Dependency.swift:1:1: warning: deprecated",
        "warning: a package warning with no file",
    ])
    found = swift_build._diagnostics(package, output)
    a, b = package / "Sources/A/a.swift", package / "Sources/A/b.swift"
    assert set(found) == {a, b}
    assert [(f.rule, f.line, f.scope) for f in found[a]] == [("warning", 2, BY_LINE)]
    assert [(f.rule, f.line, f.scope) for f in found[b]] == [("error", 5, BY_BUILD)]


def test_a_file_outside_any_package_has_no_package(repo: Path):
    assert swift_build.package_root(repo / "Loose.swift", repo) is None
    commit(repo, {"Package.swift": MANIFEST})
    assert swift_build.package_root(repo / "Sources" / "Shapes" / "a.swift", repo) == repo


@pytest.fixture
def package(repo: Path) -> Path:
    if shutil.which("xcrun") is None:
        pytest.skip("needs Xcode")
    swift_build._built.clear()
    commit(repo, {
        "Package.swift": MANIFEST,
        "Sources/Shapes/Area.swift": AREA,
        "Sources/Shapes/Floor.swift": USE,
    })
    return repo


def test_an_error_the_change_causes_in_a_file_it_never_opened_blocks(package: Path):
    area = package / "Sources" / "Shapes" / "Area.swift"
    area.write_text(AREA.replace("area(", "squareArea("))
    floor = package / "Sources" / "Shapes" / "Floor.swift"
    assert floor in swift_build.stop_paths([area])
    outcome = check(floor, stop=True)
    assert outcome is not None
    assert [(f.rule, f.line) for f in outcome.placed[NEW]] == [("error", 2)]


def test_a_warning_in_new_code_blocks_at_stop_only(package: Path):
    area = package / "Sources" / "Shapes" / "Area.swift"
    unused = "    let unused = side\n    return side * side"
    area.write_text(AREA.replace("    side * side", unused))
    edited = check(area)
    assert edited is not None
    assert "warning" not in {f.rule for f in edited.blocking}
    stopped = check(area, stop=True)
    assert stopped is not None
    assert [(f.rule, f.line) for f in stopped.blocking if f.tool == "swiftc"] == [("warning", 2)]


def test_the_stop_hook_holds_the_agent_back_over_a_broken_build(package: Path):
    area = package / "Sources" / "Shapes" / "Area.swift"
    area.write_text(AREA.replace("area(", "squareArea("))
    edit = {"session_id": "s-build", "tool_input": {"file_path": str(area)}}
    run_hook(edit)
    stopping = {"session_id": "s-build", "stop_hook_active": False}
    answer = json.loads(run_hook(stopping, "--stop").stdout)
    assert answer["decision"] == "block"
    assert "Floor.swift:2: error cannot find 'area' in scope" in answer["reason"]
    assert (package / ".build").exists() is False
