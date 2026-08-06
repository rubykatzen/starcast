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
lifecycle described above (`Propose`/`Apply`/`Reject`/`Distill`/`Rework`) has a
working contract, `proposal-shared.yml`: `Apply`/`Reject` are fully
implemented, `Propose` dequeues and creates a proposal with mocked field
values (real agent judgment not wired in yet), and `Distill`/`Rework` remain
placeholders (tracked in #11).

## Reusable workflows

### `route-issue-shared.yml`

Transfers an issue to another repository when a configured label is
applied, idempotently.

```yaml
jobs:
  route:
    uses: rubykatzen/starcast/.github/workflows/route-issue-shared.yml@v0.9
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
    uses: rubykatzen/starcast/.github/workflows/collect-issues-shared.yml@v0.9
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
    uses: rubykatzen/starcast/.github/workflows/collect-pull-requests-shared.yml@v0.9
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
    uses: rubykatzen/starcast/.github/workflows/proposal-shared.yml@v0.9
    with:
      regulations_repo: some-org/some-repo
      regulations_path: REGULATIONS.md
      issue_number: ${{ github.event.issue.number }}
      comment_id: ${{ github.event.comment.id }}
      telegram_chat_id: ${{ vars.TELEGRAM_CHAT_ID }}
      capacity_query: ${{ vars.PROPOSAL_CAPACITY_QUERY }}
      queue_query: ${{ vars.PROPOSAL_QUEUE_QUERY }}
      review_limit: 5
    secrets:
      token: ${{ secrets.PROJECTS_TOKEN }}
      model_credentials: ${{ secrets.AGENT_API_KEY }}
      telegram_bot_token: ${{ secrets.TELEGRAM_BOT_TOKEN }}
```

- **One CLI backs `dequeue`/`list-fields`/`propose`/`apply`/`reject`** —
  `actions/proposal` wraps a single Homebrew-style tool
  (`proposal.py <mode> --flag value`) rather than one script per
  action, so the five modes share transport, field-discovery, and
  mutation helpers instead of duplicating them.
- **`Apply`/`Reject` are implemented** — `Apply` copies the proposal's `title`
  and `body` onto the parent unconditionally, plus every proposal Issue Field
  whose name starts with `prefix` onto the same-named field on the parent
  (`type`/`parent`/`labels` are reserved, handled via native Issue Type,
  sub-issue, and Label mutations instead of a generic field write), then
  closes the proposal and any open sibling proposals of the same parent. An
  empty/unset proposal field (title/body included) leaves the parent's field
  untouched. `Reject` just closes the proposal. Both are idempotent:
  re-running against an already-closed proposal is a no-op.
- **`Propose` dequeues and creates, with mocked content** (#55, #11) — on
  each scheduled run it runs the consumer-supplied `capacity_query`, and if
  the result is below `review_limit`, runs `queue_query` and takes its first
  candidate; at capacity or an empty queue are both clean no-ops. Both
  queries are consumer-owned GraphQL text: `capacity_query` must alias
  exactly one scalar numeric field as `capacity`, `queue_query` exactly one
  array-valued field as `queue` (each element at least `{ id }`), found by a
  recursive walk matching alias name and value type — zero or more than one
  match is a hard configuration error. Only one candidate is ever dequeued
  per run, so no pagination/cursor state is needed between runs. When a
  candidate is found, `Propose` creates the proposal issue against it —
  Issue Type, sub-issue relationship, the control comment — but **fills every
  field with a trivial mock value rather than a real agent decision**; wiring
  in regulation-constrained judgment is tracked in #11. `Distill`/`Rework`
  remain placeholders.
- **The control comment has one fixed template**, posted by `Propose`
  immediately after creation and gated on by `Apply`/`Reject`/`Distill`/
  `Rework`: `- [ ] Apply`, `- [ ] Reject`, `- [ ] Distill`, `- [ ] Rework`,
  nothing else. Verifying that a triggering comment actually *is* a given
  proposal's control comment (as opposed to some other comment containing
  matching text) isn't enforced yet — tracked in #63.
- **`list-fields`** discovers which Issue Fields in a repository start with
  `prefix`, plus the always-available fixed set (`title`, `body`, `type`,
  `parent`, `labels`) — the menu a caller picks from when deciding what a
  proposal can hold.
- **Optional entity transitions** (#69) — `Propose`/`Apply` can each move
  whatever "controlling entity" tracks an issue's state (e.g. a
  `ProjectV2Item`) as the issue moves through the lifecycle: incoming → review
  on `Propose`, review → done on `Apply`. Three independently optional
  consumer-owned GraphQL pieces: `entity_query` resolves the entity's id
  (given an `issueId` variable, must alias exactly one scalar as `entity` —
  same alias+type extraction as `capacity`/`queue`), and
  `propose_transition_mutation`/`apply_transition_mutation` are then run with
  `issueId`/`entityId` as variables. Omitting a transition mutation is a
  clean no-op — the entity is never touched, only the issue/proposal. A
  *configured* transition that fails after a successful create/apply is a
  hard error, not swallowed — the action already succeeded, so silently
  continuing would leave the entity's state stale and risk the next
  `dequeue` re-selecting the same issue.
- **Self-gating** — `Apply`/`Reject`/`Distill`/`Rework` run only on
  `issue_comment` when the matching checkbox (e.g. `[x] Apply`) is checked;
  `Propose` runs only on `schedule`/`workflow_dispatch`.
- **Invariant check** — a `validate` job asserts that `issue_comment` runs
  carry `issue_number`/`comment_id` and that `schedule`/`workflow_dispatch`
  runs do not, and that `schedule`/`workflow_dispatch` runs carry
  `capacity_query`/`queue_query`/`review_limit`; every other job depends on
  it.
- **Concurrency** — `Apply`/`Reject`/`Distill`/`Rework` share a group keyed on
  `comment_id`; `Propose` uses its own group keyed on the calling repository,
  since a scheduled run has no comment to key on.
- **Optional Telegram reporting** (#46) — set `telegram_chat_id` and
  `telegram_bot_token` to have each job report which action ran and its
  outcome; omitting either is a clean no-op.
- **Self-contained permissions** — the workflow declares its own top-level
  `issues: write`, `contents: write`, `pull-requests: write` rather than
  relying on whatever permissions a consumer's calling job happens to grant.

#### Two `capacity_query`/`queue_query` shapes (#55)

The alias-based extraction doesn't care about schema depth or shape — only
that a scalar is aliased `capacity` and an array `queue` somewhere in the
response. Two structurally different sources demonstrate that: a plain
repository issue list, and a GitHub Project.

Repository-based (this repo's own dogfood caller,
`.github/workflows/proposal.yml`, uses this shape):

```graphql
query {
  repository(owner: "some-org", name: "some-repo") {
    issues(states: OPEN, filterBy: { type: "Proposal" }) {
      capacity: totalCount
    }
  }
}
```

```graphql
query {
  repository(owner: "some-org", name: "some-repo") {
    issues(states: OPEN, first: 5, orderBy: { field: CREATED_AT, direction: ASC }) {
      queue: nodes { id number }
    }
  }
}
```

Project-based — capacity from a Status field, queue from a differently
structured connection nested under `organization.projectV2` rather than
`repository`:

```graphql
query {
  organization(login: "some-org") {
    projectV2(number: 4) {
      items(first: 100, query: "status:Review") {
        capacity: totalCount
      }
    }
  }
}
```

```graphql
query {
  organization(login: "some-org") {
    projectV2(number: 4) {
      items(first: 5, query: "status:Queue") {
        queue: nodes {
          content { ... on Issue { id } }
        }
      }
    }
  }
}
```

`ProjectV2.items`'s `query` argument (including the `status:<option>`
search-string syntax), `ProjectV2Item.content { ... on Issue }`, and the
entity/transition queries below were all round-tripped live against a real
Project V2 in this org (`rubykatzen`/`Proposal Review`, #1), not just
schema-checked — see [Entity transitions](#entity-transitions-69) below.

#### Entity transitions (#69)

`entity_query` resolves the id of whatever entity (typically a
`ProjectV2Item`) tracks an issue's state, given an `issueId` variable —
same alias+type extraction as `capacity`/`queue`, a scalar aliased `entity`:

```graphql
query($issueId: ID!) {
  node(id: $issueId) {
    ... on Issue {
      projectItems(first: 10) {
        nodes { entity: id }
      }
    }
  }
}
```

`propose_transition_mutation`/`apply_transition_mutation` then run with
`$issueId`/`$entityId` as variables — a mutation only needs to declare the
ones it actually uses; extra keys present in the variables payload but not
referenced by the mutation text are simply ignored:

```graphql
mutation($entityId: ID!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: "PVT_..."
    itemId: $entityId
    fieldId: "PVTSSF_..."
    value: { singleSelectOptionId: "..." }
  }) { clientMutationId }
}
```

Both queries above were run against a real issue and a real Project item in
this org during development, not assumed: `entity_query` correctly resolved
the item id, and the mutation correctly moved its Status field. Note the
`projectItems` lookup has no project-scoping argument beyond pagination — if
an issue could belong to more than one Project, a consumer's `entity_query`
needs its own way to disambiguate (or accept that ambiguity as a hard error,
same as `capacity`/`queue`), since this simple form doesn't filter by
project/owner itself.

## Workflow API

Reusable workflows live directly in `.github/workflows/` and expose their
contract through `workflow_call` inputs, secrets, permissions, and outputs.

Consumers should reference a released version — currently `v0.9`, the
floating minor line (matching the convention `rubykatzen/baseline` and
`rubykatzen/releaser` already use for their own pre-1.0 floating tags,
e.g. `@v0.7`; SemVer treats `0.x` releases as initial development, where
minor bumps may be breaking, so pinning the minor rather than just the
major is the closer equivalent to a stable version pin until `v1` ships):

```yaml
jobs:
  example:
    uses: rubykatzen/starcast/.github/workflows/example.yml@v0.9
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
