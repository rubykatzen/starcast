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

The current stable line is `v0.9`. Consumers should use `@v0.9` or an immutable
commit SHA. Do not recommend `@main` for stable consumers.

## Workflow behavior

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
  that `schedule`/`workflow_dispatch` runs do not, and that
  `schedule`/`workflow_dispatch` runs carry `capacity_query`/`queue_query`/
  `review_limit`/the `model_credentials` secret. On `issue_comment` runs,
  `validate` also verifies the
  triggering comment is actually the proposal's first/control comment
  (#63) via `mode: check-control-comment`, shared here rather than
  duplicated across `apply`/`reject`/`distill`/`rework` since all four
  already depend on this job; a mismatch is a hard error, not a silent
  no-op, and success marks the comment 👀. `actions/telegram-notify`
  (#46) backs the reporting step in every job; a missing
  `chat_id`/`bot_token` is a no-op, and a delivery failure never fails
  the calling job.
  - `actions/proposal` is one composite action wrapping one CLI,
    `proposal.py <mode> --flag value` (Homebrew-style subcommands),
    so `dequeue`/`list-fields`/`propose`/`apply`/`reject` share transport
    (`gh api graphql --input -` with a full JSON body, not per-variable
    `-f`/`-F` flags — several modes need list/object-shaped variables that
    `-f`/`-F` cannot express), field-discovery, and mutation helpers
    instead of duplicating them per action. `--github-token` is not a
    flag; it's read from `GH_TOKEN` so it never appears in argv.
    - `apply` copies the proposal's `title`/`body` onto the parent
      unconditionally, plus every proposal Issue Field whose name starts
      with `prefix` onto the same-named Issue Field on the parent (the
      proposal's native sub-issue `parent`), via `setIssueFieldValue`.
      `type`, `parent`, and `labels` are reserved stripped names handled
      via `updateIssueIssueType`, `addSubIssue`, and
      `addLabelsToLabelable` respectively rather than a generic field
      write. An empty/unset proposal field (title/body included) leaves
      the parent's field untouched, by omitting that key from the
      mutation's input object rather than passing an explicit `null` —
      confirmed live which of the two actually leaves a field untouched,
      not assumed. Applying closes the proposal and every open sibling
      proposal of the same parent (other sub-issues whose Issue Type
      matches `proposal_type_name`). Re-running against an already-closed
      proposal is a no-op.
    - `reject` closes the proposal — nothing else. Closing an
      already-closed issue is a no-op on GitHub's side.
    - `dequeue` (#55) checks `capacity_query` against `review_limit`, and
      if there's room, runs `queue_query` and takes its first element.
      Both queries are consumer-owned GraphQL text; extraction is a
      recursive walk of the response matching alias name
      (`capacity`/`queue`) and disambiguated by value type (scalar vs.
      array), not schema-aware — zero or more than one match for either
      alias is a hard error. Only one candidate is ever dequeued per run,
      so no pagination state is needed between runs. Capacity accounting
      once a proposal exists and excluding issues that already have one
      are both the consumer's responsibility (via their own
      `capacity_query`/`queue_query` definitions), not enforced here.
    - `list-fields` discovers Issue Fields in a repository whose name
      starts with `prefix`, stripped, plus the always-available fixed set
      (`title`, `body`, `type`, `parent`, `labels`) — the menu a caller
      (in the future, an agent) picks from.
    - `propose-context` gathers everything a model needs to decide a
      proposal's content -- regulations text (fetched from
      `regulations_repo`/`regulations_path`'s default branch via
      `repository.object(expression: "HEAD:<path>")`), the candidate
      issue's title/body, and the field catalog from `proposal_fields` --
      into one self-contained prompt, output as `prompt`. Produces text
      only; agnostic to whichever inference mechanism a caller wires in.
    - `propose` creates a proposal issue against a parent: resolves the
      Issue Type, creates the sub-issue, writes whichever fields
      `--model-response` (a JSON blob shaped `{"title", "body",
      "fields": {...}}`, produced from `propose-context`'s prompt by a
      real model call) included, and posts the control comment
      (`CONTROL_COMMENT_BODY`) as the first comment. Does not decide
      content itself -- an unknown field name in the response is a
      warning and a skip, not an error, since the model's output isn't
      trusted to match the catalog exactly.
    - `propose`/`apply` optionally transition whatever "controlling
      entity" tracks an issue's state (e.g. a `ProjectV2Item`) as it
      moves through the lifecycle (#69): `entity_query` resolves the
      entity id given an `issueId` variable, using the same alias+type
      extraction as `capacity`/`queue` (a scalar aliased `entity`);
      `propose_transition_mutation`/`apply_transition_mutation` then run
      with `issueId`/`entityId` as variables (GraphQL only requires an
      operation's *declared* variables to be used, not every key present
      in the variables payload, so a mutation just declares whichever it
      needs). All three inputs are independently optional; omitting a
      transition mutation is a no-op. A *configured* transition mutation
      that fails after a successful create/apply is a hard error, not
      swallowed — it would otherwise leave the entity's state stale.
    - `check-control-comment` (#63) resolves the issue's actual first
      comment (by `databaseId`, matching `github.event.comment.id`'s
      numeric form) and compares it against the id the caller supplied;
      a mismatch is a hard error. Success adds a 👀 reaction to it and
      leaves the reaction in place — a persistent record, not removed on
      completion.
    - `distill`/`rework` remain placeholders.

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
