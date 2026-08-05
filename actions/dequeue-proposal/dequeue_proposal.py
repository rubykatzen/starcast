#!/usr/bin/env python3
"""Check review capacity and dequeue at most one proposal candidate (#55).

Consumers supply capacity_query/queue_query as raw GraphQL text; StarCast
never inspects their schema. capacity_query must alias exactly one
scalar numeric field as `capacity`, found by a recursive walk of the
response matching on alias name and disambiguated by value type (scalar
vs. array) rather than position or depth -- no JSONPath/expression
language on top of GraphQL. queue_query must alias exactly one
array-valued field as `queue`; only its first element is ever consumed
-- one candidate per run, never a batch, so no pagination/cursor state
is needed between runs.

Zero or more than one match for either alias is a hard configuration
error: silently picking one would hide a consumer mistake rather than
surface it.

Post-creation capacity accounting (capacity_query going up once a
proposal exists) and double-proposal prevention (queue_query excluding
issues that already have one) are both the consumer's own responsibility
-- out of scope here, by design (#55).
"""

import json
import os
import subprocess
import sys


def gh_graphql(query: str) -> dict:
    payload = json.dumps({"query": query, "variables": {}})
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    if "errors" in data:
        print(json.dumps(data["errors"]), file=sys.stderr)
        sys.exit(1)
    return data["data"]


def find_aliased(obj, name: str, predicate, matches: list | None = None) -> list:
    """Recursively collect every value at key `name` matching `predicate`.

    Traversal continues into a matched value too, so a spurious
    duplicate alias nested inside the real one is still caught by the
    "exactly one match" check in extract_one rather than silently
    shadowed.
    """
    if matches is None:
        matches = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == name and predicate(value):
                matches.append(value)
            find_aliased(value, name, predicate, matches)
    elif isinstance(obj, list):
        for item in obj:
            find_aliased(item, name, predicate, matches)
    return matches


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_one(data: dict, alias: str, predicate, input_name: str):
    matches = find_aliased(data, alias, predicate)
    if len(matches) != 1:
        print(
            f"ERROR: {input_name} must alias exactly one matching field as "
            f"'{alias}' (found {len(matches)})",
            file=sys.stderr,
        )
        sys.exit(1)
    return matches[0]


def write_output(result: str, issue_id: str, summary: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"result={result}\n")
            f.write(f"issue_id={issue_id}\n")
            f.write(f"summary={summary}\n")
    print(f"### dequeue-proposal\n\n- result: {result}\n- summary: {summary}\n")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"### dequeue-proposal\n\n- result: {result}\n- summary: {summary}\n")


def main() -> None:
    capacity_query = os.environ["CAPACITY_QUERY"]
    queue_query = os.environ["QUEUE_QUERY"]
    review_limit = float(os.environ["REVIEW_LIMIT"])

    capacity_data = gh_graphql(capacity_query)
    capacity = extract_one(capacity_data, "capacity", is_number, "capacity_query")

    if capacity >= review_limit:
        write_output("no-op-at-capacity", "", f"capacity={capacity} >= review_limit={review_limit}")
        return

    queue_data = gh_graphql(queue_query)
    queue = extract_one(queue_data, "queue", lambda v: isinstance(v, list), "queue_query")

    if not queue:
        write_output("no-op-empty-queue", "", f"capacity={capacity} < review_limit={review_limit}, queue is empty")
        return

    candidate = queue[0]
    issue_id = candidate.get("id") if isinstance(candidate, dict) else None
    if not issue_id:
        print("ERROR: queue_query's first queue element has no 'id' field", file=sys.stderr)
        sys.exit(1)

    write_output(
        "dequeued",
        issue_id,
        f"capacity={capacity} < review_limit={review_limit}, dequeued {issue_id}",
    )


if __name__ == "__main__":
    main()
