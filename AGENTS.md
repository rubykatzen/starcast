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

The current stable line is `v0.5`. Consumers should use `@v0.5` or an immutable
commit SHA. Do not recommend `@main` for stable consumers.

## Workflow behavior

- `route-issue-shared.yml` transfers an issue according to an explicit label
  routing map.
- `pull-issue-shared.yml` expands configured organizations to repositories,
  combines them with explicit repositories, and processes each unique
  repository independently. It paginates open issues directly and does not use
  GitHub Search. It adds only issues missing from the target Project, treats
  archived Project items as already present, and leaves Status assignment to
  Project automation. Its `organizations` and `repositories` inputs are JSON
  string arrays.
- `pull-pr-shared.yml` follows the same repository discovery and Project
  membership rules for open pull requests, including drafts. Fork pull
  requests are scoped by their base repository.

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
