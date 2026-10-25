# claude-quality-gate

A Claude Code plugin that holds every Python file Claude writes to ruff, complexipy and
pyright, as it writes it. After each `Edit`, `Write` or `MultiEdit` of a `.py` file, the
gate checks the whole file and places each finding against `HEAD`:

| Where the finding is | What happens |
| --- | --- |
| **new code**: a function `HEAD` does not have, a changed line outside any function, or a file `HEAD` lacks | **blocks**: Claude is told to fix it, or, for a size finding, to fix it or [make the case to keep it](#keeping-a-size-finding) |
| **old code this change touched**: a function `HEAD` has, with a changed line | **blocks**, just as new code does: a function Claude edits must pass in full, including what was wrong with it before. A project can [only report these](#project-configuration) |
| **old code elsewhere** in the file | counted in one line, never required |

Files outside a git repository (scratch files), files git ignores, and files the ruff
configuration excludes are not checked.

### Python written through the shell

`sed -i`, a heredoc, a `cp` or `mv` into the repository, a code generator: none of these
go through `Edit` or `Write`, so Bash is gated too. Before each Bash command the gate
records the content hash of every uncommitted Python file in the session's repositories
(its working directory's and its project's); after the command, whether it succeeded or
failed, every Python file whose hash changed or that is new is checked exactly as an edit
would be. A command answers only for what it wrote, never for edits made before it ran. A
command that writes no Python costs two `git status` calls.

## What it checks

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

## Keeping a size finding

Most code that trips a size rule is better refactored, but some is the right size as
written: a numeric routine that needs every one of its parameters, a state machine whose
transitions belong together. The rules that measure size (C901, PLR0911, PLR0912, PLR0913,
PLR0915, PLR0917, PLR1702 and cognitive complexity) can be kept; lint, type and style
findings (RET, SIM, PLR5501 and everything else) are always fixed.

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

Configuration is looked for from the file's directory up to the repository root.

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

Requires `uv` (for `uv` and `uvx`) and `git` on `PATH`. The first check downloads the
pinned checkers.

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
```

The end-to-end tests run the real checkers on scratch repositories. To try the plugin in
one session without installing it: `claude --plugin-dir ~/code/claude-qualty-gate`.
