#!/usr/bin/env python3
"""Transfer an issue to another repository based on a configured label.

Idempotency was verified directly against disposable scratch repos, not
assumed: after a real transfer, the issue's old node id stops resolving
(NOT_FOUND), and querying the source repo by number also returns null —
the issue is simply gone from the source repo. So a retry that can't find
the issue there anymore is a completed transfer, not an error: this
script resolves the issue by owner/repo/number right before mutating and
treats "not found" as a successful no-op.

createLabelsIfMissing was also verified directly: without it, a label
with no same-named counterpart at the destination is silently dropped
(not an error). With it, the label is created and attached — but, like
other GitHub Projects/Issues mutations, the response from the transfer
call itself doesn't reliably reflect that immediately; this script
reports what the mutation returned rather than re-querying to confirm.
"""

import json
import os
import subprocess
import sys


def gh_graphql(query: str, **variables: str | int | bool | None) -> dict:
    """Run a GraphQL query/mutation via `gh api graphql`.

    Values are typed by their Python type: `int`/`bool`/`None` go through
    `-F` (gh's typed flag, which converts numbers, true/false, and the
    literal "null" to real JSON types); everything else goes through `-f`
    (always a raw JSON string), so a string value is never accidentally
    re-interpreted as a number/bool/null even if it looks like one.
    """
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            args += ["-F", f"{key}=null"]
        elif isinstance(value, (int, bool)):
            args += ["-F", f"{key}={str(value).lower() if isinstance(value, bool) else value}"]
        else:
            args += ["-f", f"{key}={value}"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)["data"]


def resolve_repo_id(owner: str, name: str) -> str | None:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) { id }
        }
        """,
        owner=owner,
        name=name,
    )
    repo = data["repository"]
    return repo["id"] if repo else None


def resolve_issue_id(owner: str, name: str, number: int) -> str | None:
    """Return the issue's node id, or None if the repository has no such issue.

    Runs the query directly rather than through gh_graphql: `gh api
    graphql` exits non-zero whenever the response carries a GraphQL
    `errors` array, and a nonexistent issue number produces exactly that
    (a NOT_FOUND error alongside `"issue": null`) — verified this is what
    a completed transfer looks like on retry, so it must not be treated
    as fatal here the way every other query/mutation in this script is.
    The repository itself failing to resolve is still treated as fatal:
    that's a real misconfiguration, not an idempotent no-op.
    """
    args = [
        "gh",
        "api",
        "graphql",
        "-f",
        """query=
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) { id }
          }
        }
        """,
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={number}",
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    repository = (payload.get("data") or {}).get("repository")
    if repository is None:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    issue = repository.get("issue")
    return issue["id"] if issue else None


def transfer_issue(issue_id: str, repository_id: str, create_labels_if_missing: bool) -> dict:
    data = gh_graphql(
        """
        mutation($issueId: ID!, $repositoryId: ID!, $createLabels: Boolean!) {
          transferIssue(input: {
            issueId: $issueId
            repositoryId: $repositoryId
            createLabelsIfMissing: $createLabels
          }) { issue { number url } }
        }
        """,
        issueId=issue_id,
        repositoryId=repository_id,
        createLabels=create_labels_if_missing,
    )
    return data["transferIssue"]["issue"]


def summarize(**fields: str) -> None:
    body = "### route-issue\n\n" + "".join(f"- {k}: {v}\n" for k, v in fields.items())
    print(body)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(body)


def main() -> None:
    routes = json.loads(os.environ["ROUTES"])
    label_name = os.environ["LABEL_NAME"]
    create_labels_if_missing = os.environ.get("CREATE_LABELS_IF_MISSING", "false") == "true"
    issue_number = int(os.environ["ISSUE_NUMBER"])
    repo_owner, repo_name = os.environ["REPOSITORY"].split("/")

    destination = routes.get(label_name)
    if destination is None:
        summarize(result="no-op", reason=f"no route configured for label '{label_name}'")
        return

    dest_owner, dest_name = destination.split("/")
    if (dest_owner.lower(), dest_name.lower()) == (repo_owner.lower(), repo_name.lower()):
        summarize(result="no-op", reason=f"destination '{destination}' is the source repository")
        return

    destination_id = resolve_repo_id(dest_owner, dest_name)
    if destination_id is None:
        print(f"ERROR: destination repository '{destination}' not found or inaccessible", file=sys.stderr)
        sys.exit(1)

    issue_id = resolve_issue_id(repo_owner, repo_name, issue_number)
    if issue_id is None:
        # No issue at this owner/repo/number anymore — verified this is
        # exactly what a completed transfer looks like on retry, not a
        # real error.
        summarize(
            result="no-op",
            reason=f"issue #{issue_number} not found in {repo_owner}/{repo_name} — already transferred",
        )
        return

    result = transfer_issue(issue_id, destination_id, create_labels_if_missing)
    summarize(
        result="transferred",
        source=f"{repo_owner}/{repo_name}#{issue_number}",
        destination=destination,
        new_issue=f"#{result['number']} ({result['url']})",
    )


if __name__ == "__main__":
    main()
