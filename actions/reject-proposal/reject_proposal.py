#!/usr/bin/env python3
"""Close a proposal Issue, deterministically (#11).

Reject is intentionally the smallest possible action: close the proposal,
touch nothing else. Closing an already-closed issue is a no-op on
GitHub's side, so no separate idempotency check is needed here.
"""

import json
import os
import subprocess
import sys


def gh_graphql(query: str, **variables: str | int | None) -> dict:
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
    payload = json.loads(result.stdout)
    if "errors" in payload:
        print(json.dumps(payload["errors"]), file=sys.stderr)
        sys.exit(1)
    return payload["data"]


def fetch_proposal(owner: str, name: str, number: int) -> dict | None:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) { id closed }
          }
        }
        """,
        owner=owner,
        name=name,
        number=number,
    )
    repository = data["repository"]
    if repository is None:
        print(f"ERROR: repository '{owner}/{name}' not found or inaccessible", file=sys.stderr)
        sys.exit(1)
    return repository["issue"]


def close_issue(issue_id: str) -> None:
    gh_graphql(
        """
        mutation($issueId: ID!) {
          closeIssue(input: {issueId: $issueId}) {
            clientMutationId
          }
        }
        """,
        issueId=issue_id,
    )


def write_output(result: str, summary: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"result={result}\n")
            f.write(f"summary={summary}\n")
    print(f"### reject-proposal\n\n- result: {result}\n- summary: {summary}\n")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"### reject-proposal\n\n- result: {result}\n- summary: {summary}\n")


def main() -> None:
    owner, name = os.environ["REPOSITORY"].split("/")
    proposal_number = int(os.environ["ISSUE_NUMBER"])

    proposal = fetch_proposal(owner, name, proposal_number)
    if proposal is None:
        print(f"ERROR: issue #{proposal_number} not found in {owner}/{name}", file=sys.stderr)
        sys.exit(1)

    label = f"{owner}/{name}#{proposal_number}"
    if proposal["closed"]:
        write_output("no-op", f"{label} is already closed")
        return

    close_issue(proposal["id"])
    write_output("rejected", f"{label} closed")


if __name__ == "__main__":
    main()
