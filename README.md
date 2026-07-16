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
    uses: rubykatzen/starcast/.github/workflows/intake-issue-shared.yml@v1
    with:
      project_owner: my-org
      project_number: 4
      initial_status: Incoming
      issue_number: ${{ github.event.issue.number }}  # omit to reconcile every open issue
      issue_types: Task,Bug                            # omit to accept every type
    secrets:
      github_token: ${{ secrets.PROJECT_TOKEN }}
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
- `github_token` needs write access to the calling repository's issues and
  to Projects owned by `project_owner`; StarCast stores no consumer secrets.

This repository is itself a consumer: `clarification-intake.yml` and
`clarification-reconcile.yml` route issues opened in `rubykatzen/starcast`
into the shared `dupmachine/Clarification` Project.

## Workflow API

Reusable workflows live directly in `.github/workflows/` and expose their
contract through `workflow_call` inputs, secrets, permissions, and outputs.

Consumers should reference a released major version:

```yaml
jobs:
  example:
    uses: rubykatzen/starcast/.github/workflows/example.yml@v1
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
