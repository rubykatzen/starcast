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

The first planned reusable workflow will place newly opened issues into a
cross-repository clarification process. No reusable workflow is published by
the reboot yet.

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
