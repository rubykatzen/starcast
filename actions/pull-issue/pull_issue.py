#!/usr/bin/env python3
"""Pull open issues from a set of organizations/repos into a GitHub Project V2.

This is the hub-side counterpart to actions/intake-issue: instead of every
donor repo configuring its own caller workflow and token (the push model),
one workflow here is configured once with a scope (organizations and/or
individual repos) and periodically discovers and pulls in whatever open
issues currently exist there. Donor repos need zero configuration.

Discovery uses GitHub's `search` query, not a per-repo issues() scan:
verified directly that `search(query: "org:A org:B repo:C/D ...")` ORs
multiple org:/repo: qualifiers together in one call, cost 1 regardless of
how many are configured. That is NOT true of ProjectV2.items' own `query`
filter — verified directly that it does not support the same multi-repo OR
syntax (two repo: qualifiers is parsed as AND, impossible, so it matches
nothing; an explicit "OR" or a comma-joined list isn't understood and
silently falls back to an effectively unfiltered scan). So project
membership is checked with one items(query: "repo:X") call per *unique
repo that actually has a candidate issue*, not per configured org/repo and
not an unscoped full-project scan — cost scales with active donor repos,
not with the shared project's total size or the configured scope's size.

GitHub's search API has its own hard ceiling worth knowing about: verified
directly against real orgs that `search` reports an accurate `issueCount`
(1093 in one real test) but the connection itself only ever yields the
first 1000 nodes, however many pages you walk. A configured scope whose
total open-issue count exceeds 1000 will silently miss whatever's past
that cutoff — there's no error, no warning from the API. Not a concern at
current scale, but a real ceiling if the configured scope grows large.

Everything below the discovery step (idempotency rules, why membership is
checked before any mutation, the archived-item unarchive hazard, the
Status-repair case) is the same reasoning as actions/intake-issue's
intake_issue.py; see that module's docstring for the full detail. The
GraphQL helper and resolve_project/resolve_status_option/add_item/
set_status are duplicated here rather than imported from that action —
there's no cross-action import mechanism in this repo, and introducing one
for two call sites would be exactly the kind of abstraction the README
says to wait on until it's validated by more than one real need.
"""

import json
import os
import subprocess
import sys


def gh_graphql(query: str, **variables: str | int | None) -> dict:
    """Run a GraphQL query/mutation via `gh api graphql`.

    Values are typed by their Python type: `int` and `None` go through
    `-F` (gh's typed flag, which converts numbers and the literal "null"
    to real JSON types); everything else goes through `-f` (always a raw
    JSON string), so a string value is never accidentally re-interpreted
    as a number/bool/null even if it looks like one.
    """
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            args += ["-F", f"{key}=null"]
        elif isinstance(value, int):
            args += ["-F", f"{key}={value}"]
        else:
            args += ["-f", f"{key}={value}"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)["data"]


def resolve_project(owner: str, number: int) -> str:
    data = gh_graphql(
        """
        query($owner: String!, $number: Int!) {
          organization(login: $owner) {
            projectV2(number: $number) { id }
          }
        }
        """,
        owner=owner,
        number=number,
    )
    project = data["organization"]["projectV2"]
    if not project:
        print(f"ERROR: no Project #{number} found in org '{owner}'", file=sys.stderr)
        sys.exit(1)
    return project["id"]


def resolve_status_option(project_id: str, field_name: str, option_name: str) -> tuple[str, str]:
    data = gh_graphql(
        """
        query($id: ID!, $field: String!) {
          node(id: $id) {
            ... on ProjectV2 {
              field(name: $field) {
                ... on ProjectV2SingleSelectField { id options { id name } }
              }
            }
          }
        }
        """,
        id=project_id,
        field=field_name,
    )
    field = data["node"]["field"]
    if not field:
        print(f"ERROR: Project has no field named '{field_name}'", file=sys.stderr)
        sys.exit(1)
    for option in field["options"]:
        if option["name"] == option_name:
            return field["id"], option["id"]
    print(f"ERROR: field '{field_name}' has no option named '{option_name}'", file=sys.stderr)
    sys.exit(1)


def fetch_candidate_issues(organizations: list[str], repos: list[str]) -> list[dict]:
    """Return every open issue across the configured orgs/repos, one search call
    (paginated), each tagged with its own repository."""
    terms = [f"org:{org}" for org in organizations] + [f"repo:{repo}" for repo in repos]
    query = " ".join([*terms, "is:issue", "is:open"])
    issues = []
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($search: String!, $after: String) {
              search(query: $search, type: ISSUE, first: 100, after: $after) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  ... on Issue {
                    id
                    number
                    repository { nameWithOwner }
                  }
                }
              }
            }
            """,
            search=query,
            after=cursor,
        )
        page = data["search"]
        issues += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return issues


def fetch_project_items_for_repo(
    project_owner: str, project_number: int, status_field: str, repo: str
) -> dict[str, dict]:
    """Return {issue_node_id: {item_id, is_archived, status}} for one repo's
    items in the project. See the module docstring for why this can only be
    scoped to a single repo per call."""
    index: dict[str, dict] = {}
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($owner: String!, $number: Int!, $after: String, $statusField: String!, $filter: String!) {
              organization(login: $owner) {
                projectV2(number: $number) {
                  items(first: 100, after: $after, query: $filter, archivedStates: [ARCHIVED, NOT_ARCHIVED]) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                      id
                      isArchived
                      fieldValueByName(name: $statusField) {
                        ... on ProjectV2ItemFieldSingleSelectValue { name }
                      }
                      content { ... on Issue { id } }
                    }
                  }
                }
              }
            }
            """,
            owner=project_owner,
            number=project_number,
            after=cursor,
            statusField=status_field,
            filter=f"repo:{repo}",
        )
        page = data["organization"]["projectV2"]["items"]
        for item in page["nodes"]:
            content = item.get("content")
            if content and "id" in content:
                index[content["id"]] = item
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return index


def add_item(project_id: str, issue_node_id: str) -> str:
    data = gh_graphql(
        """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item { id }
          }
        }
        """,
        projectId=project_id,
        contentId=issue_node_id,
    )
    return data["addProjectV2ItemById"]["item"]["id"]


def set_status(project_id: str, item_id: str, field_id: str, option_id: str) -> None:
    gh_graphql(
        """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId
            itemId: $itemId
            fieldId: $fieldId
            value: { singleSelectOptionId: $optionId }
          }) { projectV2Item { id } }
        }
        """,
        projectId=project_id,
        itemId=item_id,
        fieldId=field_id,
        optionId=option_id,
    )


def main() -> None:
    project_owner = os.environ["PROJECT_OWNER"]
    project_number = int(os.environ["PROJECT_NUMBER"])
    status_field = os.environ.get("STATUS_FIELD", "Status")
    initial_status = os.environ["INITIAL_STATUS"]
    organizations = [o.strip() for o in os.environ.get("ORGANIZATIONS", "").split(",") if o.strip()]
    repos = [r.strip() for r in os.environ.get("REPOS", "").split(",") if r.strip()]

    if not organizations and not repos:
        print("ERROR: at least one of 'organizations' or 'repos' must be set", file=sys.stderr)
        sys.exit(1)

    project_id = resolve_project(project_owner, project_number)
    field_id, option_id = resolve_status_option(project_id, status_field, initial_status)

    candidates = fetch_candidate_issues(organizations, repos)

    by_repo: dict[str, list[dict]] = {}
    for issue in candidates:
        by_repo.setdefault(issue["repository"]["nameWithOwner"], []).append(issue)

    added, repaired, skipped_present, skipped_archived = [], [], [], []
    for repo, issues in by_repo.items():
        project_items = fetch_project_items_for_repo(project_owner, project_number, status_field, repo)
        for issue in issues:
            label = f"{repo}#{issue['number']}"
            existing = project_items.get(issue["id"])
            if existing:
                if existing["isArchived"]:
                    # Never call addProjectV2ItemById or any mutation on an
                    # archived item — the add mutation itself unarchives it
                    # as a side effect, and there's no way to undo that
                    # after the fact. Leave it fully alone.
                    skipped_archived.append(label)
                elif existing["fieldValueByName"] is None:
                    set_status(project_id, existing["id"], field_id, option_id)
                    repaired.append(label)
                else:
                    skipped_present.append(label)
                continue
            item_id = add_item(project_id, issue["id"])
            set_status(project_id, item_id, field_id, option_id)
            added.append(label)

    summary = (
        f"### pull-issue: {', '.join(organizations + repos)} -> {project_owner}/#{project_number}\n\n"
        f"- Added ({initial_status}): {added or 'none'}\n"
        f"- Repaired (had no Status, now {initial_status}): {repaired or 'none'}\n"
        f"- Already present (untouched): {skipped_present or 'none'}\n"
        f"- Already present, archived (untouched): {skipped_archived or 'none'}\n"
    )
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary)


if __name__ == "__main__":
    main()
