"""The Swift backend: its pieces, then the gate end to end on scratch repos with the real
SwiftLint, syntax helper and swift-format.

The end-to-end tests need Xcode. The first run downloads SwiftLint and builds the syntax
helper, which takes a few minutes; after that each test takes well under a second.
"""

from __future__ import annotations

import json
import shutil
import textwrap
import time
from pathlib import Path

import pytest
from conftest import commit
from test_gate import run_hook

import keep
import languages
import placement
import shell
import swift
import swift_tools
from checks import BY_FILE, BY_LINE, Finding, Target
from gate import Outcome, check
from keep import Triage
from placement import CHANGED, ELSEWHERE, NEW
from report import text
from tools import ToolError

HELPER_BUILD_S = 900
POLL_S = 5

CLEAN = textwrap.dedent(
    """\
    struct Panel {
        let width: Int

        func doubled() -> Int {
            width * 2
        }
    }
    """
)

UNSAFE = textwrap.dedent(
    """\
    func value(of text: String?) -> Int {
        // let fallback = compute(text)
        let parsed = try? parse(text)
        return parsed ?? Int(text!) ?? 7
    }

    func parse(_ text: String?) throws -> Int { 0 }
    """
)


def rules(findings: list[Finding]) -> set[str]:
    return {finding.rule for finding in findings}


# The pieces -------------------------------------------------------------------------------


def test_each_file_goes_to_its_language():
    assert languages.for_path("a/View.swift") is languages.SWIFT
    assert languages.for_path("a/mod.py") is languages.PYTHON
    assert languages.for_path("a/notes.md") is None


def test_a_swiftlint_size_finding_says_how_far_over_it_is():
    item = {
        "rule_id": "function_body_length",
        "line": 3,
        "reason": "Function body should span 40 lines or less excluding comments and "
        "whitespace: currently spans 52 lines",
    }
    finding = swift._from_swiftlint(item)
    assert finding.message.endswith("(52 > 40)")
    assert finding.scope == BY_LINE


def test_a_file_too_long_is_about_the_whole_file():
    item = {"rule_id": "file_length", "line": 400, "reason": "File should contain 300 lines "
            "or less: currently contains 400"}
    assert swift._from_swiftlint(item).scope == BY_FILE


def test_a_whole_file_finding_is_placed_by_any_change_and_kept_by_the_file(tmp_path: Path):
    unchanged = placement.History((), frozenset(), frozenset())
    touched = placement.History((), frozenset(), frozenset({9}))
    assert placement.place_file(unchanged) == ELSEWHERE
    assert placement.place_file(touched) == CHANGED
    assert placement.place_file(placement.History((), None, frozenset())) == NEW
    outcome = Outcome(Target(tmp_path / "a.swift", tmp_path))
    long_file = Finding("swiftlint", "file_length", 1, "too long (400 > 300)", BY_FILE)
    size = keep.size_of(outcome, long_file)
    assert size is not None
    assert (size.function, size.value) == (keep.WHOLE_FILE, 400)


def test_test_files_are_known_by_their_directory_or_name(tmp_path: Path):
    assert swift.is_test(Target(tmp_path / "Tests" / "AppTests" / "Grid.swift", tmp_path))
    assert swift.is_test(Target(tmp_path / "GridTests.swift", tmp_path))
    assert not swift.is_test(Target(tmp_path / "Sources" / "App" / "Grid.swift", tmp_path))


def test_a_package_must_build_in_swift_6():
    manifest = {
        "toolsVersion": {"_version": "5.10.0"},
        "targets": [
            {"name": "Lib", "settings": []},
            {"name": "Old", "settings": [{"kind": {"swiftLanguageMode": {"_0": "5"}}}]},
        ],
    }
    assert swift._language_modes(manifest) == [
        "swift-tools-version 5.10.0 builds in Swift 5 language mode",
        "target Old builds in Swift 5 mode",
    ]
    assert swift._language_modes({"toolsVersion": {"_version": "6.0.0"}, "targets": []}) == []


def test_the_projects_swift_format_configuration_is_used(repo: Path):
    target = Target(repo / "Sources" / "App" / "View.swift", repo)
    assert swift.format_config(target) == swift.SWIFT_FORMAT_CONFIG
    commit(repo, {".swift-format": "{}"})
    assert swift.format_config(target) == repo / ".swift-format"


def test_swift_written_through_the_shell_is_attributed(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payload = {"cwd": str(repo), "tool_use_id": "toolu_swift"}
    monkeypatch.setattr(shell, "STATE", tmp_path / "state")
    shell.before(payload)
    (repo / "View.swift").write_text(CLEAN)
    assert shell.written(payload) == [repo / "View.swift"]


# End to end -------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def swift_tools_ready() -> None:
    """SwiftLint downloaded and the syntax helper built, waiting for the build if need be."""
    if shutil.which("xcrun") is None:
        pytest.skip("needs Xcode")
    swift_tools.swiftlint()
    deadline = time.monotonic() + HELPER_BUILD_S
    while True:
        try:
            swift_tools.helper()
        except ToolError as error:
            if time.monotonic() > deadline or "last attempt failed" in str(error):
                pytest.fail(str(error))
            time.sleep(POLL_S)
        else:
            return


@pytest.mark.usefixtures("swift_tools_ready")
def test_a_clean_file_has_nothing(repo: Path):
    commit(repo, {"Panel.swift": CLEAN})
    outcome = check(repo / "Panel.swift")
    assert outcome is not None
    assert (outcome.blocking, outcome.errors) == ([], [])


@pytest.mark.usefixtures("swift_tools_ready")
def test_what_is_never_allowed_blocks_in_new_code(repo: Path):
    (repo / "Value.swift").write_text(UNSAFE)
    outcome = check(repo / "Value.swift")
    assert outcome is not None
    assert outcome.errors == []
    assert {"force_unwrapping", "no_magic_numbers", "try-optional", "commented-out-code"} <= (
        rules(outcome.blocking)
    )


@pytest.mark.usefixtures("swift_tools_ready")
def test_a_swiftlint_comment_silences_nothing_and_is_a_finding_itself(repo: Path):
    source = "// swiftlint:disable force_cast\nfunc cast(_ value: Any) -> Int { value as! Int }\n"
    (repo / "Cast.swift").write_text(source)
    outcome = check(repo / "Cast.swift")
    assert outcome is not None
    assert {"force_cast", "swiftlint-disable"} <= rules(outcome.blocking)


PAIR = textwrap.dedent(
    """\
    func first(_ x: Int?) -> Int {
        x!
    }

    func second(_ x: Int?) -> Int {
        x!
    }
    """
)


@pytest.mark.usefixtures("swift_tools_ready")
def test_a_touched_function_blocks_and_an_untouched_one_is_counted(repo: Path):
    commit(repo, {"Pair.swift": PAIR})
    (repo / "Pair.swift").write_text(PAIR.replace("    x!\n}\n\n", "    return x!\n}\n\n", 1))
    outcome = check(repo / "Pair.swift")
    assert outcome is not None
    assert [f.line for f in outcome.placed[CHANGED]] == [2]
    assert [f.line for f in outcome.placed[ELSEWHERE]] == [6]


@pytest.mark.usefixtures("swift_tools_ready")
def test_cognitive_complexity_is_a_size_finding_kept_by_its_function(repo: Path):
    branches = "\n".join(f"        if a == {n} {{ if b {{ return {n} }} }}" for n in range(5))
    source = f"enum Pick {{\n    static func pick(_ a: Int, _ b: Bool) -> Int {{\n{branches}\n"
    source += "        return 0\n    }\n}\n"
    (repo / "Pick.swift").write_text(source)
    outcome = check(repo / "Pick.swift")
    assert outcome is not None
    cognitive = [f for f in outcome.blocking if f.rule == "cognitive-complexity"]
    assert len(cognitive) == 1
    size = keep.size_of(outcome, cognitive[0])
    assert size is not None
    assert size.function == "Pick.pick(_:_:)"


@pytest.mark.usefixtures("swift_tools_ready")
def test_tests_may_compare_against_literal_values(repo: Path):
    source = "import Testing\n\n@Test func doubles() {\n    #expect(2 * 21 == 42)\n}\n"
    path = repo / "Tests" / "PanelTests" / "Doubling.swift"
    commit(repo, {"Tests/PanelTests/Doubling.swift": ""})
    path.write_text(source)
    outcome = check(path)
    assert outcome is not None
    assert "no_magic_numbers" not in rules(outcome.blocking)


@pytest.mark.usefixtures("swift_tools_ready")
def test_a_preview_may_use_literal_values_and_other_code_may_not(repo: Path):
    source = textwrap.dedent(
        """\
        import SwiftUI

        func padded(_ width: Int) -> Int { width + 12 }

        #Preview("Wide") {
            Color.red.frame(width: 1920, height: 720)
        }
        """
    )
    (repo / "Wide.swift").write_text(source)
    outcome = check(repo / "Wide.swift")
    assert outcome is not None
    magic = [f.line for f in outcome.blocking if f.rule == "no_magic_numbers"]
    assert magic == [3]


@pytest.mark.usefixtures("swift_tools_ready")
def test_style_findings_come_with_the_command_that_fixes_them(repo: Path):
    (repo / "Panel.swift").write_text(CLEAN.replace("    let width", "  let width"))
    outcome = check(repo / "Panel.swift")
    assert outcome is not None
    assert "Indentation" in rules(outcome.blocking)
    said = text(Triage(outcome, fix=outcome.blocking))
    assert "fixed by: xcrun swift-format format -i --configuration" in said


@pytest.mark.usefixtures("swift_tools_ready")
def test_the_hook_blocks_a_swift_edit(repo: Path):
    (repo / "Value.swift").write_text(UNSAFE)
    ran = run_hook({"tool_name": "Write", "tool_input": {"file_path": str(repo / "Value.swift")}})
    assert ran.returncode == 0, ran.stderr
    assert "Value.swift:3: try-optional" in json.loads(ran.stdout)["reason"]
