# Changelog

## [Unreleased]

### Added

- `intake-issue-shared.yml` reusable workflow: adds issues to a GitHub Project V2 and
  sets an initial Status, idempotently (including archived items), with
  event-driven and reconcile-sweep modes and optional Issue Type filtering.
- `clarification.yml`: this repository now routes its own issues into the
  `dupmachine`/`Clarification` Project via `intake-issue-shared.yml`,
  combining event-driven intake and a scheduled reconcile sweep (every 2h)
  in one caller.

## [v0.0.1] - 2026-07-16

- chore: add releaser prepare/publish workflows (#16)
- chore: reconnect rubykatzen/baseline lint setup (#15)
- refactor: reboot StarCast around human-agent workflows
- fix: resolve all pymarkdown and yamllint violations
- fix: add actionlint hook required by check-precommit-sync
- chore: add baseline lint setup
- style: split imports onto separate lines (E401)
- fix: remove unsupported mcp_config input from claude-code-action
- fix: use GITHUB_ACTION_PATH to locate resolve.py in composite action
- fix: pass status_options via env to avoid JSON quoting issues in bash
- refactor: extract resolve-mcp python script to separate file
- fix: pass servers via env var to avoid YAML triple-quote parse error
- resolve-mcp: use \${VAR} placeholders instead of reading env at build time
- docs: add example comment for SMITHERY_SERVERS variable
- refactor: replace Python frontmatter parser with awk
- refactor: extract get-card-status action from resolve-playbook
- refactor: extract Smithery resolution into resolve-mcp action
- feat: resolve Smithery MCP servers from playbook frontmatter
- refactor: parameterize all hardcoded labels across actions
- feat: add mark-result action
- pipeline: add error label when no playbook found, remove ran output
- run-agent: accept github_token instead of app_id + private_key
- resolve-playbook: fix jq --arg syntax for project lookup
- run-agent: add ran output to signal whether agent executed
- lint-playbooks: include checkout in action
- refactor: lint-playbooks as composite action, not reusable workflow
- feat: add lint-playbooks reusable workflow
- run-agent: absorb token generation, checkout, and resolve-playbook
- resolve-playbook: find project by repo name instead of hardcoded ID
- feat: add resolve-playbook action
- remove: dead parse-project-event action
- docs: update playbook example names in action.yml
- readme: write framework README
- review: fix repo name, rename Cast Handbook, remove Output contracts, fix security-flag label
- escalation: use error label instead of needs-human
- rename: Production → Project in CLAUDE.md
- refactor: rename cast/ to roles/ throughout
- fix(run-agent): allow bot actors (starcast-bot GitHub App)
- fix(run-agent): use ANTHROPIC_SMALL_FAST_MODEL env var instead of unknown CLI flag
- fix(run-agent): override small-fast-model to avoid claude/ protocol in omniroute
- revert(run-agent): remove max_turns (unsupported input)
- fix(run-agent): move max_turns from claude_args to proper action input
- fix(run-agent): cap agent at 10 turns to prevent context overflow
- feat(run-agent): log system prompt and direct prompt to CI output
- feat(run-agent): enable full agent output in CI logs
- fix(run-agent): skip permission prompts for unattended CI execution
- fix(run-agent): use prompt/claude_args/env instead of unsupported inputs
- feat(run-agent): add anthropic_base_url and model inputs
- Simplify parse-project-event: remove is_issue output
- Add parse-project-event composite action

All notable changes to StarCast will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- Repositioned StarCast as an open toolkit for human-agent workflows on GitHub.
- Defined the Project state machine and review artifact model.

### Removed

- Removed the unsupported autonomous editorial pipeline implementation.
