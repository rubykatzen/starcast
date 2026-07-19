#!/usr/bin/env python3
"""Collect open issues from organizations/repos into a GitHub Project V2.

One workflow is configured centrally with a scope (organizations and/or
individual repositories) and periodically discovers and collects whatever
open issues currently exist there. Donor repositories need zero
configuration.

Organizations are expanded to their repositories first, then combined
with the explicitly configured repositories and deduplicated. Each
repository is processed independently: its complete `issues(states: OPEN)`
connection is compared with Project items filtered to that repository.
Using repository connections avoids GitHub Search's 1,000-result ceiling.

Membership is checked before any mutation, and archived items never reach
the add mutation because it would unarchive them. Status is deliberately
left to the target Project's automation.
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


def fetch_organization_repositories(organization: str) -> list[str]:
    """Return every repository visible to the token in one organization."""
    repositories = []
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($organization: String!, $after: String) {
              organization(login: $organization) {
                repositories(first: 100, after: $after) {
                  pageInfo { hasNextPage endCursor }
                  nodes { nameWithOwner }
                }
              }
            }
            """,
            organization=organization,
            after=cursor,
        )
        owner = data["organization"]
        if not owner:
            print(f"ERROR: organization '{organization}' not found or inaccessible", file=sys.stderr)
            sys.exit(1)
        page = owner["repositories"]
        repositories += [repo["nameWithOwner"] for repo in page["nodes"]]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return repositories


def fetch_open_issues(repo: str) -> list[dict]:
    """Return every open issue in one repository."""
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        print(f"ERROR: repository '{repo}' must use the 'owner/name' format", file=sys.stderr)
        sys.exit(1)
    if not owner or not name or "/" in name:
        print(f"ERROR: repository '{repo}' must use the 'owner/name' format", file=sys.stderr)
        sys.exit(1)

    issues = []
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($owner: String!, $name: String!, $after: String) {
              repository(owner: $owner, name: $name) {
                issues(states: OPEN, first: 100, after: $after) {
                  pageInfo { hasNextPage endCursor }
                  nodes { id number }
                }
              }
            }
            """,
            owner=owner,
            name=name,
            after=cursor,
        )
        repository = data["repository"]
        if not repository:
            print(f"ERROR: repository '{repo}' not found or inaccessible", file=sys.stderr)
            sys.exit(1)
        page = repository["issues"]
        issues += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return issues


def deduplicate(values: list[str]) -> list[str]:
    """Deduplicate GitHub names case-insensitively, preserving order."""
    unique = []
    seen = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def parse_json_array(raw_value: str, input_name: str) -> list[str]:
    """Validate and return one JSON-array action input."""
    try:
        values = json.loads(raw_value)
    except json.JSONDecodeError as error:
        print(f"ERROR: '{input_name}' must be valid JSON: {error.msg}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        print(f"ERROR: '{input_name}' must be a JSON array of strings", file=sys.stderr)
        sys.exit(1)

    values = [value.strip() for value in values]
    if any(not value for value in values):
        print(f"ERROR: '{input_name}' must not contain empty strings", file=sys.stderr)
        sys.exit(1)
    return deduplicate(values)


def fetch_project_items_for_repo(project_owner: str, project_number: int, repo: str) -> dict[str, dict]:
    """Return existing Project items by issue node ID for one repository."""
    index: dict[str, dict] = {}
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($owner: String!, $number: Int!, $after: String, $filter: String!) {
              organization(login: $owner) {
                projectV2(number: $number) {
                  items(first: 100, after: $after, query: $filter, archivedStates: [ARCHIVED, NOT_ARCHIVED]) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                      id
                      isArchived
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


def add_item(project_id: str, issue_node_id: str) -> None:
    gh_graphql(
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


def main() -> None:
    project_owner = os.environ["PROJECT_OWNER"]
    project_number = int(os.environ["PROJECT_NUMBER"])
    organizations = parse_json_array(os.environ.get("ORGANIZATIONS", "[]"), "organizations")
    configured_repositories = parse_json_array(
        os.environ.get("REPOSITORIES", "[]"), "repositories"
    )
    if not organizations and not configured_repositories:
        print("ERROR: at least one organization or repository must be configured", file=sys.stderr)
        sys.exit(1)

    project_id = resolve_project(project_owner, project_number)

    repositories = list(configured_repositories)
    for organization in organizations:
        repositories += fetch_organization_repositories(organization)
    repositories = deduplicate(repositories)

    added, skipped_present, skipped_archived = [], [], []
    for repo in repositories:
        issues = fetch_open_issues(repo)
        if not issues:
            continue
        project_items = fetch_project_items_for_repo(project_owner, project_number, repo)
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
                else:
                    skipped_present.append(label)
                continue
            add_item(project_id, issue["id"])
            added.append(label)

    summary = (
        f"### collect-issues: {', '.join(organizations + configured_repositories)} "
        f"-> {project_owner}/#{project_number}\n\n"
        f"- Added: {added or 'none'}\n"
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
