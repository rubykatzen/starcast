#!/usr/bin/env python3
"""Add open issues to a GitHub Project V2 and set an initial Status.

Two modes, selected by whether ISSUE_NUMBER is set:
  - single-issue mode: process exactly one issue (event-driven intake).
  - reconcile mode: paginate every open issue in the calling repository and
    add any that are missing from the project (failure-recovery sweep).

Idempotency: an issue already linked to the target project — archived or
not — is never re-added or unarchived. Membership is checked with a query
before any mutation runs, rather than relying on addProjectV2ItemById's own
idempotency, because that mutation's behavior for archived items isn't
something we can safely assume.
"""

import json
import os
import subprocess
import sys

# projectItems page size. An issue linked to more than this many projects
# could have its target project item missed on the first page, which would
# break the idempotency guarantee for that issue; 100 is GitHub's max page
# size and comfortably covers realistic usage.
PROJECT_ITEMS_PAGE_SIZE = 100


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


ISSUE_FIELDS = f"""
    id
    number
    issueType {{ name }}
    projectItems(first: {PROJECT_ITEMS_PAGE_SIZE}, includeArchived: true) {{
      nodes {{ project {{ id }} }}
    }}
"""


def fetch_single_issue(owner: str, repo: str, number: int) -> list[dict]:
    data = gh_graphql(
        f"""
        query($owner: String!, $repo: String!, $number: Int!) {{
          repository(owner: $owner, name: $repo) {{
            issue(number: $number) {{ {ISSUE_FIELDS} }}
          }}
        }}
        """,
        owner=owner,
        repo=repo,
        number=number,
    )
    issue = data["repository"]["issue"]
    if not issue:
        print(f"ERROR: issue #{number} not found in {owner}/{repo}", file=sys.stderr)
        sys.exit(1)
    return [issue]


def fetch_open_issues(owner: str, repo: str) -> list[dict]:
    issues = []
    cursor = None
    while True:
        data = gh_graphql(
            f"""
            query($owner: String!, $repo: String!, $after: String) {{
              repository(owner: $owner, name: $repo) {{
                issues(states: OPEN, first: 100, after: $after) {{
                  pageInfo {{ hasNextPage endCursor }}
                  nodes {{ {ISSUE_FIELDS} }}
                }}
              }}
            }}
            """,
            owner=owner,
            repo=repo,
            after=cursor,
        )
        page = data["repository"]["issues"]
        issues += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return issues


def add_issue(project_id: str, issue_node_id: str, field_id: str, option_id: str) -> None:
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
    item_id = data["addProjectV2ItemById"]["item"]["id"]
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


def already_in_project(issue: dict, project_id: str) -> bool:
    return any(item["project"]["id"] == project_id for item in issue["projectItems"]["nodes"])


def main() -> None:
    project_owner = os.environ["PROJECT_OWNER"]
    project_number = int(os.environ["PROJECT_NUMBER"])
    status_field = os.environ.get("STATUS_FIELD", "Status")
    initial_status = os.environ["INITIAL_STATUS"]
    issue_number = os.environ.get("ISSUE_NUMBER") or None
    issue_types = [t.strip() for t in os.environ.get("ISSUE_TYPES", "").split(",") if t.strip()]
    repo_owner, repo_name = os.environ["REPOSITORY"].split("/")

    project_id = resolve_project(project_owner, project_number)
    field_id, option_id = resolve_status_option(project_id, status_field, initial_status)

    issues = (
        fetch_single_issue(repo_owner, repo_name, int(issue_number))
        if issue_number
        else fetch_open_issues(repo_owner, repo_name)
    )

    added, skipped_present, skipped_type = [], [], []
    for issue in issues:
        if issue_types and (issue["issueType"] or {}).get("name") not in issue_types:
            skipped_type.append(issue["number"])
            continue
        if already_in_project(issue, project_id):
            skipped_present.append(issue["number"])
            continue
        add_issue(project_id, issue["id"], field_id, option_id)
        added.append(issue["number"])

    summary = (
        f"### intake-issue: {repo_owner}/{repo_name} -> {project_owner}/#{project_number}\n\n"
        f"- Added ({initial_status}): {added or 'none'}\n"
        f"- Already present (untouched, incl. archived): {skipped_present or 'none'}\n"
        f"- Skipped (issue type filter): {skipped_type or 'none'}\n"
    )
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary)


if __name__ == "__main__":
    main()
