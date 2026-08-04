# StarCast Repository Guide

StarCast is a toolkit for observable human-agent workflows built on GitHub
Issues, Projects V2, reusable workflows, and composite actions. The previous
autonomous editorial pipeline has been removed; do not restore its cast,
production, or publishing conventions.

## System model

- An Issue is the durable work object.
- A Project represents an action performed on issues.
- Project Status represents process state.
- Agent output is a reviewable artifact, not an in-place replacement for the
  source material.
- Rework preserves history and returns the issue to an earlier process state.

## Public API

Treat these files as versioned consumer contracts:

- `.github/workflows/*-shared.yml`: reusable workflow inputs, secrets,
  permissions, outputs, and behavior.
- `actions/*/action.yml`: composite action inputs, outputs, and behavior.

The current stable line is `v0.8`. Consumers should use `@v0.8` or an immutable
commit SHA. Do not recommend `@main` for stable consumers.

## Workflow behavior

- `route-issue-shared.yml` transfers an issue according to an explicit label
  routing map.
- `collect-issues-shared.yml` expands configured organizations to repositories,
  combines them with explicit repositories, and processes each unique
  repository independently. It paginates open issues directly and does not use
  GitHub Search. It adds only issues missing from the target Project, treats
  archived Project items as already present, and leaves Status assignment to
  Project automation. Its `organizations` and `repositories` inputs are JSON
  string arrays.
- `collect-pull-requests-shared.yml` follows the same repository discovery and
  Project membership rules for open pull requests, including drafts. Fork
  pull requests are scoped by their base repository.
- `proposal-shared.yml` is the five-job contract (`apply`, `reject`,
  `distill`, `rework`, `propose`) for the proposal-lifecycle model in #11.
  Each job is self-gated on `github.event_name` and, for the four
  comment-triggered jobs, on which checkbox is checked. A `validate` job
  enforces that `issue_comment` runs carry `issue_number`/`comment_id` and
  that `schedule`/`workflow_dispatch` runs do not. `actions/telegram-notify`
  (#46) backs the reporting step in every job; a missing `chat_id`/`bot_token`
  is a no-op, and a delivery failure never fails the calling job.
  - `actions/apply-proposal` copies every proposal Issue Field whose name
    starts with `prefix` onto the same-named Issue Field on the parent (the
    proposal's native sub-issue `parent`), via `setIssueFieldValue`. `type`,
    `parent`, and `labels` are reserved stripped names handled via
    `updateIssueIssueType`, `addSubIssue`, and `addLabelsToLabelable`
    respectively rather than a generic field write. An empty/unset proposal
    field leaves the parent's field untouched. Applying closes the proposal
    and every open sibling proposal of the same parent (other sub-issues
    whose Issue Type matches `proposal_type_name`). Re-running against an
    already-closed proposal is a no-op.
  - `actions/reject-proposal` closes the proposal — nothing else. Closing an
    already-closed issue is a no-op on GitHub's side.
  - `distill`/`rework`/`propose` are still placeholders.

## Engineering rules

- Preserve idempotency across retries and partial failures.
- Check Project membership before mutations. Never re-add archived items,
  because the add mutation can unarchive them.
- Paginate every GitHub connection; do not introduce fixed result windows.
- Keep caller workflows thin and behavior in versioned shared workflows or
  composite actions.
- Pass credentials from consumers and request only the permissions required.
- Prefer existing GraphQL and `gh api` patterns over new dependencies.
- Keep changes scoped; public contract changes require CHANGELOG documentation
  and an appropriate version bump.

## Verification

Run the full repository checks before committing:

```bash
pre-commit run --all-files
```

The configured checks cover YAML, Markdown, Python linting, and GitHub Actions
syntax. Avoid live Project mutations during tests unless the target and cleanup
plan are explicitly controlled.

## Releases

StarCast follows Semantic Versioning during initial development:

- `v0.x.y` is an immutable release tag.
- `v0.x` follows the latest patch in that minor line.
- Breaking public API changes require a new minor release before `v1`.

Use `releaser status` to inspect readiness and `releaser patch|minor|major` to
run the repository release workflow.
