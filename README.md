# StarCast

StarCast is an open toolkit for organizing human-agent workflows on GitHub.
It provides reusable workflow building blocks for processes in which an agent
works on an issue, a person reviews the result, and the work either advances or
returns for another pass.

## Model

```text
Queue -> Propose -> Human review -> Accepted
  ^                      |
  +-- Reject / Rework ---+
```

- An **issue** is the durable work object.
- A **Project** represents an action performed on issues, not a topic or an
  agent identity.
- Project **Status** is the process state machine.
- A **proposal** is itself an issue (Type `Proposal`, a native GitHub
  sub-issue of the one it proposes changes to) — its own fields hold the
  proposed values directly, not text encoded inside a comment. It is the
  review artifact: the agent's proposed result, which does not overwrite the
  source before approval.
- Returning an item to the queue preserves history and gives the next agent
  pass its feedback context.
- The human's response to a proposal splits into what needs judgment and what
  doesn't. `Apply` and `Reject` are mechanical — copy mapped fields, or close
  the proposal — safe to run as a bare deterministic job with no agent
  involved. `Distill` and `Rework` need an agent bound to the consumer's own
  regulations: diffing what changed and classifying the lesson, or revising
  the proposal from feedback left in comments instead of direct field edits.
  This split, not just what an action does, is what decides which kind of
  executor a workflow needs. See #11.

StarCast is intended for processes that need an observable queue, repeated
agent execution, human validation, and an auditable rework loop. It is not a
task database or a general replacement for GitHub Issues and Projects.

## Status

The repository is being rebuilt around this model. The previous autonomous
editorial pipeline implementation has been removed and is not supported. Its
history remains available in Git.

The current reusable workflows cover centralized Project intake for issues and
pull requests, plus explicit label-based issue routing. The proposal-as-issue
lifecycle described above (`Propose`/`Apply`/`Reject`/`Distill`/`Rework`) now
has a scaffolded contract, `proposal-shared.yml` — its five jobs are
wired and self-gated, but the domain logic behind each action is not
implemented yet (tracked in #11).

## Reusable workflows

### `route-issue-shared.yml`

Transfers an issue to another repository when a configured label is
applied, idempotently.

```yaml
jobs:
  route:
    uses: rubykatzen/starcast/.github/workflows/route-issue-shared.yml@v0.6
    with:
      routes: >-
        {
          "Household": "some-org/some-repo",
          "Meds": "another-org/another-repo"
        }
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

### `collect-issues-shared.yml`

Collects open issues from a configured set of organizations and/or individual
repositories into a GitHub Project V2, idempotently. One workflow is configured
centrally and periodically discovers whatever open issues currently exist in
scope. Donor repositories need zero configuration.

```yaml
jobs:
  collect:
    uses: rubykatzen/starcast/.github/workflows/collect-issues-shared.yml@v0.6
    with:
      organizations: >-
        [
          "some-org",
          "another-org"
        ]
      repositories: >-
        [
          "some-org/some-repo"
        ]
      project_owner: some-org
      project_number: 4
    secrets:
      token: ${{ secrets.COLLECT_TOKEN }}
```

Caller drives cadence from its own `on: schedule`, because a `schedule`
trigger cannot live inside a reusable workflow, plus `workflow_dispatch` for
manual runs. At least one organization or repository must be configured.

- **Repository-based discovery** — configured organizations are expanded to
  their repositories, combined with explicitly configured repositories, and
  deduplicated. Each repository's complete open-issues connection is then
  processed independently.
- **No content filter yet** — every open issue found in scope is collected.
  Label- or type-based filtering is a natural addition once a real need
  shows up.
- **Idempotent, including archived items** — an issue already linked to the
  project is never re-added or unarchived.
- **Status is owned by Project automation** — this workflow only adds the
  issue; configure the target Project's `Item added to project` automation
  to assign the desired initial Status.
- `token` needs read access across every configured organization/repo plus
  write access to the Project. StarCast stores no consumer secrets.

### `collect-pull-requests-shared.yml`

Collects open pull requests from configured organizations and/or repositories
into a GitHub Project V2. Pull requests opened from forks belong to their base
repository for source matching. Draft pull requests are included.

```yaml
jobs:
  collect:
    uses: rubykatzen/starcast/.github/workflows/collect-pull-requests-shared.yml@v0.6
    with:
      organizations: >-
        [
          "some-org"
        ]
      repositories: >-
        [
          "some-org/some-repo"
        ]
      project_owner: some-org
      project_number: 4
    secrets:
      token: ${{ secrets.COLLECT_TOKEN }}
```

The caller owns the `on: schedule` and optional `workflow_dispatch` triggers.
At least one organization or repository must be configured.

- **All open pull requests** — drafts and ready-for-review pull requests are
  both included; merged and closed pull requests are not added.
- **Repository-based discovery** — organizations are expanded to repositories,
  then every repository's complete open pull-request connection is paginated.
- **Idempotent, including archived items** — a pull request already linked to
  the Project is never re-added or unarchived.
- **Status is owned by Project automation** — the workflow only adds the pull
  request and does not mutate Project fields.
- `token` needs read access across every configured organization/repository
  plus write access to the Project.

### `proposal-shared.yml`

A single reusable `workflow_call` contract for the five-action proposal
lifecycle (`Propose`, `Apply`, `Reject`, `Distill`, `Rework`) described in #11:
a proposal is a native GitHub sub-issue of the parent it proposes changes to,
and its own fields are copied onto the parent by naming convention.

```yaml
on:
  issue_comment:
    types: [edited]
  schedule:
    - cron: '*/15 * * * *'
jobs:
  handle:
    uses: rubykatzen/starcast/.github/workflows/proposal-shared.yml@v0.6
    with:
      regulations_repo: some-org/some-repo
      regulations_path: REGULATIONS.md
      issue_number: ${{ github.event.issue.number }}
      comment_id: ${{ github.event.comment.id }}
      telegram_chat_id: ${{ vars.TELEGRAM_CHAT_ID }}
    secrets:
      token: ${{ secrets.PROJECTS_TOKEN }}
      model_credentials: ${{ secrets.AGENT_API_KEY }}
      telegram_bot_token: ${{ secrets.TELEGRAM_BOT_TOKEN }}
```

- **Contract only, for now** — the workflow's five jobs are wired, self-gated
  by trigger and checkbox, and each reports its outcome; the domain logic
  behind `Apply`/`Reject`/`Distill`/`Rework`/`Propose` is not implemented yet
  (tracked in #11).
- **Self-gating** — `Apply`/`Reject`/`Distill`/`Rework` run only on
  `issue_comment` when the matching checkbox (e.g. `[x] Apply`) is checked;
  `Propose` runs only on `schedule`/`workflow_dispatch`.
- **Invariant check** — a `validate` job asserts that `issue_comment` runs
  carry `issue_number`/`comment_id` and that `schedule`/`workflow_dispatch`
  runs do not; every other job depends on it.
- **Concurrency** — `Apply`/`Reject`/`Distill`/`Rework` share a group keyed on
  `comment_id`; `Propose` uses its own group keyed on the calling repository,
  since a scheduled run has no comment to key on.
- **Optional Telegram reporting** (#46) — set `telegram_chat_id` and
  `telegram_bot_token` to have each job report which action ran and its
  outcome; omitting either is a clean no-op.
- **Self-contained permissions** — the workflow declares its own top-level
  `issues: write`, `contents: write`, `pull-requests: write` rather than
  relying on whatever permissions a consumer's calling job happens to grant.

## Workflow API

Reusable workflows live directly in `.github/workflows/` and expose their
contract through `workflow_call` inputs, secrets, permissions, and outputs.

Consumers should reference a released version — currently `v0.6`, the
floating minor line (matching the convention `rubykatzen/baseline` and
`rubykatzen/releaser` already use for their own pre-1.0 floating tags,
e.g. `@v0.7`; SemVer treats `0.x` releases as initial development, where
minor bumps may be breaking, so pinning the minor rather than just the
major is the closer equivalent to a stable version pin until `v1` ships):

```yaml
jobs:
  example:
    uses: rubykatzen/starcast/.github/workflows/example.yml@v0.6
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
