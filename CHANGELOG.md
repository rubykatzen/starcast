# Changelog

## [Unreleased]

### Added

- `actions/proposal` composite action: a single Homebrew-style CLI
  (`proposal.py <mode> --flag value`) covering the whole proposal
  lifecycle's deterministic surface — `dequeue`, `list-fields`,
  `propose`, `apply`, `reject` — sharing transport, field-discovery, and
  mutation helpers instead of one script per action.
  - `dequeue`: consumer-owned `capacity_query`/`queue_query` gate and
    source the next proposal candidate, one per run — see
    `capacity_query`/`queue_query`/`review_limit` inputs on
    `proposal-shared.yml`'s `propose` job. `capacity_query` must alias
    exactly one scalar numeric field as `capacity`, `queue_query` exactly
    one array-valued field as `queue`; both are found by a recursive,
    type-disambiguated alias search rather than a schema-aware mapping.
    The dequeued candidate's own `id` is found the same recursive way,
    not a flat lookup — needed for queue shapes where `id` is nested
    (e.g. a Project-based `queue_query` typically exposes it as
    `content { ... on Issue { id } }`).
  - `list-fields`: discovers the prefixed proposal Issue Fields available
    in a repository.
  - `propose`: creates a proposal issue against a parent — Issue Type,
    sub-issue relationship, field values, and the control comment. Field
    *values* are mocked (a trivial type-appropriate placeholder) rather
    than agent-decided; real regulation-constrained judgment is tracked
    in #11.
  - `apply`/`reject`: unchanged behavior from the actions they replace
    (below), now reached via `mode: apply`/`mode: reject`.
- `proposal-shared.yml`'s `propose` job now dequeues a candidate and
  creates a (mocked) proposal for it, instead of a placeholder notice.
- `entity_query`, `propose_transition_mutation`, and
  `apply_transition_mutation` inputs on `proposal-shared.yml`: optionally
  transition whatever "controlling entity" tracks an issue's state (e.g.
  a `ProjectV2Item`) as it moves through the lifecycle — incoming to
  review on `propose`, review to done on `apply` (#69). All three are
  independently optional consumer-owned GraphQL text; `entity_query` uses
  the same alias+type extraction as `capacity`/`queue`. A configured
  transition mutation that fails after a successful create/apply is a
  hard error, not swallowed.
- `check-control-comment` mode on `actions/proposal`, run by
  `proposal-shared.yml`'s `validate` job on `issue_comment` runs before
  `apply`/`reject`/`distill`/`rework` act: verifies the triggering
  comment is actually the proposal's first/control comment, not just any
  comment containing matching checkbox text (#63). A mismatch is a hard
  error. Success adds a 👀 reaction to the comment, mirroring the
  Copilot coding agent's own acknowledgment convention.

### Changed

- **Breaking:** `actions/apply-proposal` and `actions/reject-proposal`
  (added in v0.8.0) are removed; their behavior moves to
  `actions/proposal` with `mode: apply`/`mode: reject`.
- `proposal-shared.yml`'s `validate` job now also requires
  `capacity_query`/`queue_query`/`review_limit` on schedule/
  `workflow_dispatch` runs.
- `issue_number`/`comment_id` inputs on `proposal-shared.yml` change from
  `type: number` to `type: string`.

### Fixed

- `proposal-shared.yml`'s `validate` job rejected every schedule/
  `workflow_dispatch` run: an unset optional `type: number` input
  resolves to `0` in GitHub Actions, not empty, so
  `[ -n "${{ inputs.issue_number }}" ]` was always true even when the
  caller never set it. Never caught before now — this is the first time
  the workflow was exercised through a real dispatch rather than direct
  CLI calls against the underlying script. Fixed by switching
  `issue_number`/`comment_id` to `type: string`, whose unset default is
  genuinely empty.

### Removed

- `route-issue-shared.yml` reusable workflow and its `actions/route-issue`
  composite action: label-based issue transfer had no proposal or review
  step, so it never fit the human-agent loop this repo builds for, and its
  only consumer (`dupmachine/ground-control`) vendored the logic locally
  instead of depending on it. Breaking for any other consumer still pinned
  to it (#110).

## [v0.8.0] - 2026-08-05

### Added

- `actions/apply-proposal` composite action: copies a proposal's mapped
  Issue Fields onto its parent (`type`/`parent`/`labels` handled as
  reserved names via native Issue Type, sub-issue, and Label mutations),
  then closes the proposal and any open sibling proposals of the same
  parent. Idempotent — a no-op against an already-closed proposal.
- `actions/reject-proposal` composite action: closes a proposal, nothing
  else.

### Changed

- `proposal-shared.yml`'s `apply` and `reject` jobs now run the actions
  above instead of a placeholder notice.

## [v0.7.0] - 2026-08-04

### Changed

- **Breaking:** `proposal-lifecycle-shared.yml` is renamed to
  `proposal-shared.yml`, matching the `<name>-shared.yml` pattern used by the
  other reusable workflows. The workflow's `name:` and its Telegram-report
  `workflow:` value change from "Proposal Lifecycle" to "Proposal", and its
  concurrency group prefixes shorten from `proposal-lifecycle-*` to
  `proposal-*`.

## [v0.6.0] - 2026-08-04

### Added

- `proposal-lifecycle-shared.yml` reusable workflow: the contract for the
  five-job proposal lifecycle (`apply`, `reject`, `distill`, `rework`,
  `propose`) described in #11, with self-gating, an input-invariant check,
  and per-comment/per-repository concurrency groups. Domain logic for each
  action is not implemented yet — this release ships the contract and
  reporting scaffold only.
- `actions/telegram-notify` composite action: optionally report a job's
  triggered action and outcome to a Telegram chat (#46). Opt-in per
  consumer; a missing chat id/bot token is a no-op and a delivery failure
  never fails the calling job.

## [v0.5.0] - 2026-07-19

### Added

- `collect-pull-requests-shared.yml` reusable workflow and
  `actions/collect-pull-requests` composite action: collect open pull requests,
  including drafts, from configured
  organizations/repositories into a GitHub Project V2. Existing active and
  archived Project items are left untouched, and Status is owned by Project
  automation.

### Changed

- **Breaking:** `pull-issue-shared.yml` and `actions/pull-issue` are renamed to
  `collect-issues-shared.yml` and `actions/collect-issues` for consistent naming
  across issue and pull-request collection.

## [v0.4.0] - 2026-07-19

### Changed

- **Breaking:** `pull-issue-shared.yml` and `actions/pull-issue` replace the
  comma-separated `organizations` and `repos` inputs with JSON-array
  `organizations` and `repositories` inputs.

## [v0.3.1] - 2026-07-19

### Removed

- `intake-issue-shared.yml` reusable workflow and `actions/intake-issue`
  composite action. Use `pull-issue-shared.yml` for centralized repository or
  organization intake.

## [v0.3.0] - 2026-07-18

### Added

- `pull-issue-shared.yml` reusable workflow: pulls open issues from a
  configured set of organizations and/or repositories into a GitHub
  Project V2, idempotently — the hub-side (pull) counterpart to
  `intake-issue-shared.yml`'s push model. Donor repos need no
  configuration; organizations are expanded to repositories, and each
  repository's open issues are paginated independently.

## [v0.2.0] - 2026-07-16

### Added

- `route-issue-shared.yml` reusable workflow: transfers an issue to
  another repository when a configured label is applied, idempotently
  (verified: a retry after a completed transfer is a clean no-op, not a
  duplicate or an error). Exact label-to-repo routing only; unmatched
  labels and source-equals-destination are clean no-ops.

## [v0.1.2] - 2026-07-16

- perf: scope project.items() query to the calling repo (#29)
- chore: pin actions/intake-issue to @v0.1, fix README's @v1 example (#26)

## [v0.1.1] - 2026-07-16

- fix: check membership via project-side scan, not issue.projectItems (#24)

## [v0.1.0] - 2026-07-16

### Added

- `intake-issue-shared.yml` reusable workflow: adds issues to a GitHub Project V2 and
  sets an initial Status, idempotently (including archived items), with
  event-driven and reconcile-sweep modes and optional Issue Type filtering.
- `intake-issue-clarification.yml`: this repository now routes its own issues
  into a shared clarification Project via `intake-issue-shared.yml`, combining
  event-driven intake and a scheduled reconcile sweep (every 2h) in one caller.

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
