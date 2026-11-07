# claude-quality-gate

A Claude Code plugin that holds every Python and Swift file Claude writes to its
language's checkers, as it writes it: Python to ruff, complexipy and pyright; Swift to
SwiftLint, swift-format, the gate's own syntax rules and, at Stop, the compiler. After
each `Edit`, `Write` or `MultiEdit` of a `.py` or `.swift` file, the gate checks the whole
file and places each finding against `HEAD`:

| Where the finding is | What happens |
| --- | --- |
| **new code**: a function `HEAD` does not have, a changed line outside any function, or a file `HEAD` lacks | **blocks**: Claude is told to fix it, or, for a size finding, to fix it or [make the case to keep it](#keeping-a-size-finding) |
| **old code this change touched**: a function `HEAD` has, with a changed line | **blocks**, just as new code does: a function Claude edits must pass in full, including what was wrong with it before. A project can [only report these](#project-configuration) |
| **old code elsewhere** in the file | counted in one line, never required |

Files outside a git repository (scratch files), files git ignores, and Python files the
ruff configuration excludes are not checked.

### Code written through the shell

`sed -i`, a heredoc, a `cp` or `mv` into the repository, a code generator: none of these
go through `Edit` or `Write`, so Bash is gated too. Before each Bash command the gate
records the content hash of every uncommitted Python and Swift file in the session's
repositories (its working directory's and its project's); after the command, whether it
succeeded or failed, every such file whose hash changed or that is new is checked exactly
as an edit would be. A command answers only for what it wrote, never for edits made before
it ran. A command that writes no code costs two `git status` calls.

## What it checks in Python

- **ruff**, at the project's configuration, or at [`baseline/ruff.toml`](baseline/ruff.toml)
  when the project has none.
- **The size and shape rules** (cyclomatic complexity, returns, branches, statements,
  arguments, positional arguments, nesting, `RET`, `SIM`): always run, whatever the
  configuration selects, and an inline `# noqa` does not silence them. A limit that is
  wrong is changed in configuration, where the change is seen.
- **complexipy**, cognitive complexity per function: limit 10, stricter than the 15 of
  SonarSource's rule S3776, or the project's `[tool.complexipy] max-complexity-allowed`.
- **pyright** in standard mode, errors only, resolving imports through the project's
  `.venv` when it has one. A project's own `[tool.pyright]` or `pyrightconfig.json`
  applies.

Every checker runs through `uvx` at the version pinned in
[`quality_gate/tools.py`](quality_gate/tools.py), never from a project's environment, so
every project is held to the same thing and needs nothing installed. Checkers run in
parallel, so a check takes about as long as pyright: under a second for a small file,
about two for a thousand lines. A checker that cannot run is reported to Claude and does
not block: the gate fails open.

The gate leaves nothing in the projects it checks: ruff runs without its cache, and
complexipy's cache goes to a temporary directory.

## What it checks in Swift

As each file is written:

- **SwiftLint**, at [`baseline/swiftlint.yml`](baseline/swiftlint.yml): size, the
  constructs never allowed, and likely bugs. It lints a copy of the file with every
  `swiftlint:` comment stripped, so none silences anything, and the gate reports each such
  comment as a finding of its own. Test files (under a `Tests` directory, or named
  `*Tests.swift`) may compare against literal numbers.
- **The gate's syntax rules**, from a small [swift-syntax](https://github.com/swiftlang/swift-syntax)
  helper in [`swift/`](swift): cognitive complexity per function (limit 10, after
  SonarSource's definition, each closure measured as a function of its own), a SwiftUI
  view's `body` (30 lines of code), every `try?`, `@unchecked Sendable`,
  `nonisolated(unsafe)`, and commented-out code. It also reads each file's functions,
  types, accessors and closures, which is what places a finding against `HEAD`.
- **swift-format**, for style, at the project's `.swift-format` or at
  [`baseline/swift-format.json`](baseline/swift-format.json) (4-space indentation, 100
  columns). Its findings come with the command that fixes them.
- **A package manifest** must build in Swift 6 language mode.

| Limit | Value |
| --- | --- |
| cognitive complexity | 10 |
| cyclomatic complexity (`switch` cases not counted) | 8 |
| function or closure body | 40 lines |
| a view's `body` | 30 lines |
| parameters, defaulted ones included | 5 |
| nesting | types 2 deep, functions 3 |
| a type, extensions included | 300 lines |
| a file | 400 lines |

Never allowed, and never kept: force unwraps, `try!`, `as!`, implicitly unwrapped
optionals, `try?`, `@unchecked Sendable`, `nonisolated(unsafe)`, magic numbers (except in
tests and inside a `#Preview` block, whose literal values are samples), commented-out code, and
any `swiftlint:` comment.

At Stop, since a module does not build halfway through a multi-file edit:

- **The compiler.** Each SwiftPM package the session wrote Swift in is built, its tests
  included. Every error blocks wherever it is, since the build is broken, so an error in a
  file the change never opened is caught too. Every warning in the package's own files is
  placed like any other finding: warnings as errors for the package, without forcing the
  flag on its dependencies. Swift 6 language mode makes concurrency checking strict. An
  Xcode project is not built; its Swift files get every check above.

SwiftLint is downloaded on first use at the version pinned in
[`quality_gate/swift_tools.py`](quality_gate/swift_tools.py), checked against its SHA-256.
The syntax helper is built on first use at the swift-syntax version its `Package.resolved`
pins: that takes a few minutes, in the background, and the checks that need it fail open
until it is ready, saying so. Both live in `~/Library/Caches/claude-quality-gate`, as do
the builds at Stop, so a package's own `.build` is never touched. swift-format and the
compiler are the active Xcode toolchain's. A check takes about a third of a second; a
build at Stop as long as an incremental build takes.

## Keeping a size finding

Most code that trips a size rule is better refactored, but some is the right size as
written: a numeric routine that needs every one of its parameters, a state machine whose
transitions belong together. The rules that measure size can be kept: in Python C901,
PLR0911, PLR0912, PLR0913, PLR0915, PLR0917, PLR1702 and cognitive complexity; in Swift
cyclomatic complexity, function, closure, type and file length, parameter count, cognitive
complexity and a view's body. Lint, type and style findings (RET, SIM, PLR5501, Swift's
nesting and everything else) are always fixed. A file too long is kept for the file.

Each size finding in new or touched code is reported with a short id. When Claude judges
the code better as written, it records its case (the refactor it rejected, and why) and
carries on:

```sh
uv run python quality_gate propose a1b2c3 "the refactor rejected, and why this is better"
```

At the end it lists each kept finding in its summary, with the command to approve it.
Only you approve, in your own terminal:

```sh
uv run python quality_gate approve           # the cases awaiting you
uv run python quality_gate approve a1b2c3    # approve one
```

To reject a case, do nothing: it lapses, and Claude fixes the code. The hooks deny Claude
any Bash command that runs `approve` and any edit under `~/.claude/quality-gate/`, so an
approval cannot be carried over from an earlier "yes". The denial matches the command's
text, so it also catches a Bash command that merely quotes it, such as a commit message.

An approval is kept in `~/.claude/quality-gate/approved.jsonl`, never in a repository,
so projects shared with others carry no trace of it. It names a function and a rule, not
a line, and covers that function up to the value approved: a later agent that touches it
is told it was kept on purpose, and if it grows past that value it blocks again.

Within one session a size finding blocks once; after that it is a reminder. Before Claude
stops, the gate holds it back, once, over any finding in new or touched code it leaves
neither fixed nor argued for, and over any case its summary does not yet give. Cases and
session memory live in the system's temporary directory and lapse after a week unseen; a
lapsed case is simply made again if the change still holds the code.

## Project configuration

Configuration is looked for from the file's directory up to the repository root. In
Swift, only swift-format's is the project's own (`.swift-format`); SwiftLint and the
syntax rules hold every project to the gate's baseline.

- **None**: the baseline applies.
- **A `[tool.ruff]` table, `ruff.toml` or `.ruff.toml`**: replaces the baseline whole. To
  keep the baseline and change only part of it, extend it:

  ```toml
  [tool.ruff]
  extend = "~/code/claude-qualty-gate/baseline/ruff.toml"

  [tool.ruff.lint.pylint]
  max-args = 10
  ```

  A project shared with others is better off with a complete configuration of its own,
  since that path exists only on this machine.
- **`[tool.quality-gate] touched-functions` in `pyproject.toml`**: `"block"`, the default,
  holds every old function a change touches to every rule. `"report"` blocks on new code
  only and lists findings in touched old functions without blocking on them:

  ```toml
  [tool.quality-gate]
  touched-functions = "report"
  ```

## Install

Requires `uv` (for `uv` and `uvx`) and `git` on `PATH`, and for Swift, Xcode. The first
check downloads the pinned checkers, and the first Swift check also builds the syntax
helper.

```sh
claude plugin marketplace add ~/code/claude-qualty-gate
claude plugin install quality-gate@claude-quality-gate
```

Installed at user scope, it applies in every project. Sessions run a copy made at install
(under `~/.claude/plugins/cache/`), not this folder, and an update is recognised by the
version in `.claude-plugin/plugin.json`. To ship a change: bump that version, commit, run
`claude plugin update quality-gate@claude-quality-gate`, and restart the session.

To turn it off in one project, in that project's `.claude/settings.json`:

```json
{ "enabledPlugins": { "quality-gate@claude-quality-gate": false } }
```

## By hand

```sh
uv run python quality_gate FILE ...     # exits 1 on findings in new or touched code
uv run python quality_gate --outdated   # each pinned checker against its latest release
uv run python quality_gate approve      # cases awaiting approval; with an id, approve one
```

## Development

```sh
uv run pytest
swift test --package-path swift --scratch-path /tmp/gate-syntax-build
```

The end-to-end tests run the real checkers on scratch repositories, waiting the first time
for the syntax helper to build. Build the helper outside the repository, as above: an
install copies this whole folder, ignored files included, and swift-syntax's build is half
a gigabyte. To try the plugin in one session without installing it:
`claude --plugin-dir ~/code/claude-qualty-gate`.
