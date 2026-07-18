# StarCast

StarCast is an open toolkit for organizing human-agent workflows on GitHub.
It provides reusable workflow building blocks for processes in which an agent
works on an issue, a person reviews the result, and the work either advances or
returns for another pass.

## Model

```text
Queue -> Agent work -> Human review -> Accepted
  ^                         |
  +--------- Rework --------+
```

- An **issue** is the durable work object.
- A **Project** represents an action performed on issues, not a topic or an
  agent identity.
- Project **Status** is the process state machine.
- A comment, patch, pull request, or other **review artifact** carries the
  agent's proposed result without overwriting the source before approval.
- Returning an item to the queue preserves history and gives the next agent
  pass its feedback context.

StarCast is intended for processes that need an observable queue, repeated
agent execution, human validation, and an auditable rework loop. It is not a
task database or a general replacement for GitHub Issues and Projects.

## Status

The repository is being rebuilt around this model. The previous autonomous
editorial pipeline implementation has been removed and is not supported. Its
history remains available in Git.

The first published reusable workflow, `intake-issue-shared.yml`, places issues into
a Project and sets an initial Status — the entry point for the
cross-repository clarification process.

## Reusable workflows

### `intake-issue-shared.yml`

Adds issues to a GitHub Project V2 and sets a Status field, idempotently.

```yaml
jobs:
  intake:
    uses: rubykatzen/starcast/.github/workflows/intake-issue-shared.yml@v0.2
    with:
      project_owner: my-org
      project_number: 4
      initial_status: Incoming
      issue_number: ${{ github.event.issue.number }}  # omit to reconcile every open issue
      issue_types: Task,Bug                            # omit to accept every type
    secrets:
      project_token: ${{ secrets.PROJECT_TOKEN }}
```

- **Event-driven intake**: pass `issue_number` from an `issues: opened`
  caller to add exactly that issue.
- **Reconcile sweep**: omit `issue_number` to scan every open issue in the
  calling repository and add whichever are missing — a recovery path for
  missed webhook deliveries or failed runs. Callers drive this from their own
  `on: schedule` (a `schedule` trigger only fires for the repository that
  owns the workflow file, so it cannot live inside a reusable workflow) plus
  `workflow_dispatch` for manual runs. There's no built-in default interval —
  measured GraphQL cost is a few points per run (well under the 5,000/hour
  budget), so the choice isn't about load; it's about how much staleness
  before recovery is acceptable. `rubykatzen/starcast` itself runs every 2
  hours (`0 */2 * * *`) as a reasonable starting point.
- **Idempotent, including archived items**: an issue already linked to the
  target project — whether its Project item is archived or not — is left
  untouched. It is never unarchived, never re-added, and this is never an
  error.
- **Type filtering**: `issue_types` matches against GitHub's native Issue
  Type field (`issue.issueType.name`), not labels.
- `project_token` needs write access to the calling repository's issues and
  to Projects owned by `project_owner`; StarCast stores no consumer secrets.
  It can't be named `github_token` — GitHub reserves that name in
  `workflow_call`'s `secrets:` block because it collides with the built-in
  `GITHUB_TOKEN`.

This repository is itself a consumer: `intake-issue-clarification.yml` routes issues
opened in `rubykatzen/starcast` into the shared `dupmachine/Clarification`
Project, combining event-driven intake and the scheduled reconcile sweep in
one caller (one `with:` block, so config and future filters only need to be
set in one place instead of kept in sync across two files).

### `route-issue-shared.yml`

Transfers an issue to another repository when a configured label is
applied, idempotently.

```yaml
jobs:
  route:
    uses: rubykatzen/starcast/.github/workflows/route-issue-shared.yml@v0.2
    with:
      routes: '{"Household": "dupmachine/ground-control", "Meds": "dupmachine/meds"}'
      label_name: ${{ github.event.label.name }}
      issue_number: ${{ github.event.issue.number }}
      create_labels_if_missing: false
    secrets:
      token: ${{ secrets.ROUTE_TOKEN }}
```

Caller triggers on `issues: labeled`.

- **Exact match only**: `routes` maps exact label names to `owner/repo`.
  A label with no configured route is a clean no-op, not an error —
  StarCast never derives a destination from untrusted label text.
- **Idempotent, verified against real transfers**: after a transfer, the
  issue's old id and its `owner/repo#number` address both stop resolving
  on the source side. A retry that can't find the issue there anymore is
  treated as an already-completed transfer, not an error.
- **Source equal to destination** is a clean no-op.
- **Label carry-over** is off by default (`create_labels_if_missing:
  false`) — GitHub's own transfer behavior otherwise silently drops a
  label with no same-named counterpart at the destination, which is
  usually what you want for a routing label. Set it to `true` to have
  GitHub create the label at the destination instead.
- `token` needs write access to both the source and destination
  repositories; StarCast stores no consumer secrets.

### `pull-issue-shared.yml`

Pulls open issues from a configured set of organizations and/or individual
repositories into a GitHub Project V2, idempotently — the hub-side
counterpart to `intake-issue-shared.yml`. Instead of every donor repo
configuring its own caller workflow and token (the push model above), one
workflow here is configured once and periodically discovers whatever open
issues currently exist in scope. Donor repos need zero configuration.

```yaml
jobs:
  pull:
    uses: rubykatzen/starcast/.github/workflows/pull-issue-shared.yml@v0.2
    with:
      organizations: dupmachine,rubykatzen   # every repo in each org is in scope
      repos: some-owner/some-repo             # individual repos, comma-separated
      project_owner: dupmachine
      project_number: 4
      initial_status: Incoming
    secrets:
      token: ${{ secrets.PULL_TOKEN }}
```

Caller drives cadence from its own `on: schedule` (same reason as
`intake-issue-shared.yml`'s reconcile sweep — a `schedule` trigger can't
live inside a reusable workflow) plus `workflow_dispatch` for manual runs.
At least one of `organizations`/`repos` must be set.

- **Discovery is one search call**, not one query per configured org/repo:
  `search` ORs multiple `org:`/`repo:` qualifiers together, so cost stays
  low regardless of how large the configured scope is.
- **No content filter yet** — every open issue found in scope is pulled.
  Label- or type-based filtering is a natural addition once a real need
  shows up.
- **Idempotent, including archived items** — same guarantees as
  `intake-issue-shared.yml`: an issue already linked to the project is
  never re-added, never unarchived, and a real Status value is never
  overwritten.
- `token` needs read access across every configured organization/repo plus
  write access to the Project — a broader, more centralized credential
  than the push model's per-repo Project-only token. That's the direct
  trade-off for not configuring anything on the donor side.
- **1,000-result ceiling**: GitHub's search API only ever returns the
  first 1,000 matching issues, however many pages you walk — verified
  directly (a real query reported `issueCount: 1093` but the connection
  yielded exactly 1000 nodes). No error, no warning. Not a concern at
  small scale, but a real ceiling if the configured scope's total open
  issue count grows past it.

## Workflow API

Reusable workflows live directly in `.github/workflows/` and expose their
contract through `workflow_call` inputs, secrets, permissions, and outputs.

Consumers should reference a released version — currently `v0.2`, the
floating minor line (matching the convention `rubykatzen/baseline` and
`rubykatzen/releaser` already use for their own pre-1.0 floating tags,
e.g. `@v0.7`; SemVer treats `0.x` releases as initial development, where
minor bumps may be breaking, so pinning the minor rather than just the
major is the closer equivalent to a stable version pin until `v1` ships):

```yaml
jobs:
  example:
    uses: rubykatzen/starcast/.github/workflows/example.yml@v0.2
```

Pinning an immutable commit SHA provides the strongest supply-chain guarantee.
Branch references such as `@main` are development-only and must not be used by
stable consumers.

## Principles

- Keep caller workflows thin and process logic centralized.
- Keep transitions idempotent and recoverable after partial failure.
- Pass credentials from the caller; StarCast never stores consumer secrets.
- Request the minimum permissions required by each workflow.
- Keep process state in Projects and durable domain metadata on issues.
- Add abstractions only after more than one real process validates them.

## Versioning

StarCast follows Semantic Versioning for public workflow contracts.

- `v1.2.3` is an immutable release.
- `v1` follows the latest compatible `v1.x.x` release.
- Changes to required inputs, secrets, outputs, permissions, or behavior may be
  breaking and require a new major version.
- User-facing changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
