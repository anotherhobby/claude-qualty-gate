# claude-quality-gate

A Claude Code plugin that holds every Python file Claude writes to ruff, complexipy and
pyright, as it writes it. After each `Edit`, `Write` or `MultiEdit` of a `.py` file, the
gate checks the whole file and places each finding against `HEAD`:

| Where the finding is | What happens |
| --- | --- |
| **new code**: a function `HEAD` does not have, a changed line outside any function, or a file `HEAD` lacks | **blocks**: Claude is told to fix it before moving on |
| **old code this change touched**: a function `HEAD` has, with a changed line | listed for Claude as cleanup it may take, never required |
| **old code elsewhere** in the file | counted in one line |

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
- **complexipy**, cognitive complexity per function: limit 15, the threshold of
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
uv run python quality_gate FILE ...     # exits 1 on findings in new code
uv run python quality_gate --outdated   # each pinned checker against its latest release
```

## Development

```sh
uv run pytest
```

The end-to-end tests run the real checkers on scratch repositories. To try the plugin in
one session without installing it: `claude --plugin-dir ~/code/claude-qualty-gate`.
