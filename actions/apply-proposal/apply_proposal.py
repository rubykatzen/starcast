#!/usr/bin/env python3
"""Apply a proposal's fields onto its parent issue, deterministically (#11).

A proposal is a native GitHub sub-issue of the issue it proposes changes
to (its `parent`). `title` and `body` are copied onto the parent
unconditionally, the same mechanical action as every other field — not
implied only by the proposal holding its own title/body, per #11. Every
proposal Issue Field whose name starts with the configured prefix
targets the same-named Issue Field on the parent by convention — no
declarative field mapping is needed. Three stripped names are reserved
and handled specially rather than written through `setIssueFieldValue`:
`type` (the parent's native Issue Type), `parent` (a full issue URL;
attaches the *parent's own* parent via `addSubIssue`), and `labels` (a
multi-select; each option becomes a native Label via
`addLabelsToLabelable`). An empty/unset proposal field (title/body
included) means "leave the parent's corresponding field untouched," not
"clear it."

Applying closes the proposal and every sibling proposal of the same
parent (other open sub-issues whose Issue Type matches proposal_type_name),
so only the newest, un-superseded proposal is ever actionable. Re-running
Apply against an already-closed proposal is a no-op — the mutations above
are not safely re-playable against a proposal whose fields a human may
have since edited to prepare a fresh Apply, so a closed proposal is
treated as already handled rather than re-processed.

Schema shapes (IssueField*/IssueFieldValue* unions, setIssueFieldValue,
addSubIssue, updateIssueIssueType) were verified against the live GitHub
GraphQL schema via introspection, not assumed.

gh_graphql sends the whole request body as JSON via `gh api graphql
--input -` rather than per-variable `-f`/`-F` flags: this script's
mutations need list- and object-shaped variables (`issueFields`,
`labelIds`, a partial `UpdateIssueInput`), and `-f`/`-F` only support
scalar values — a JSON-encoded string passed through `-f` is sent as a
literal GraphQL string, not a parsed list/object. Confirmed live against
a disposable scratch issue, not assumed: `--input -` round-trips a
nested object and an array correctly, and omitting a key from an input
object (rather than setting it to `null`) is what actually leaves that
field untouched — passing an explicit `null` is a different, unverified
code path this script avoids entirely by construction.
"""

import json
import os
import re
import subprocess
import sys

ISSUE_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)$")


def gh_graphql(query: str, **variables: object) -> dict:
    """Run a GraphQL query/mutation via `gh api graphql --input -`."""
    payload = json.dumps({"query": query, "variables": variables})
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


def fetch_proposal(owner: str, name: str, number: int) -> dict | None:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) {
              id
              closed
              title
              body
              parent {
                id
                number
                repository { owner { login } name }
              }
            }
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


def fetch_proposal_field_values(owner: str, name: str, number: int) -> list[dict]:
    """Return every Issue Field value set on the proposal, normalized.

    Each entry is {"field_name", "value", "names"}: `value` carries the
    scalar for text/number/date fields, `names` carries the selected
    option name(s) for single/multi-select fields. Exactly one of the two
    is populated, matching which union member the field resolved to.
    """
    values = []
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($owner: String!, $name: String!, $number: Int!, $after: String) {
              repository(owner: $owner, name: $name) {
                issue(number: $number) {
                  issueFieldValues(first: 100, after: $after) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                      ... on IssueFieldTextValue { value field { ... on IssueFieldText { name } } }
                      ... on IssueFieldNumberValue { value field { ... on IssueFieldNumber { name } } }
                      ... on IssueFieldDateValue { value field { ... on IssueFieldDate { name } } }
                      ... on IssueFieldSingleSelectValue { name field { ... on IssueFieldSingleSelect { name } } }
                      ... on IssueFieldMultiSelectValue { options { name } field { ... on IssueFieldMultiSelect { name } } }
                    }
                  }
                }
              }
            }
            """,
            owner=owner,
            name=name,
            number=number,
            after=cursor,
        )
        page = data["repository"]["issue"]["issueFieldValues"]
        for node in page["nodes"]:
            field_name = node["field"]["name"]
            if "options" in node:
                values.append({"field_name": field_name, "value": None, "names": [o["name"] for o in node["options"]]})
            elif "name" in node:
                values.append({"field_name": field_name, "value": None, "names": [node["name"]]})
            else:
                values.append({"field_name": field_name, "value": node.get("value"), "names": None})
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return values


def fetch_repository_issue_fields(owner: str, name: str) -> dict[str, dict]:
    """Return the target repository's Issue Field catalog, keyed by name."""
    fields = {}
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($owner: String!, $name: String!, $after: String) {
              repository(owner: $owner, name: $name) {
                issueFields(first: 100, after: $after) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    ... on IssueFieldText { id name dataType }
                    ... on IssueFieldNumber { id name dataType }
                    ... on IssueFieldDate { id name dataType }
                    ... on IssueFieldSingleSelect { id name dataType options { id name } }
                    ... on IssueFieldMultiSelect { id name dataType options { id name } }
                  }
                }
              }
            }
            """,
            owner=owner,
            name=name,
            after=cursor,
        )
        repository = data["repository"]
        if repository is None:
            print(f"ERROR: repository '{owner}/{name}' not found or inaccessible", file=sys.stderr)
            sys.exit(1)
        page = repository["issueFields"]
        for node in page["nodes"]:
            fields[node["name"]] = node
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return fields


def resolve_issue_type(owner: str, name: str, type_name: str) -> str | None:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!, $typeName: String!) {
          repository(owner: $owner, name: $name) {
            issueType(name: $typeName) { id }
          }
        }
        """,
        owner=owner,
        name=name,
        typeName=type_name,
    )
    issue_type = data["repository"]["issueType"]
    return issue_type["id"] if issue_type else None


def resolve_issue_id_by_url(url: str) -> str | None:
    match = ISSUE_URL_RE.match(url.strip())
    if not match:
        print(f"::warning::'{url}' is not a full https://github.com/owner/repo/issues/N URL, skipping parent link", file=sys.stderr)
        return None
    owner, name, number = match.group(1), match.group(2), int(match.group(3))
    data = gh_graphql(
        """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) { id }
          }
        }
        """,
        owner=owner,
        name=name,
        number=number,
    )
    repository = data["repository"]
    issue = repository["issue"] if repository else None
    return issue["id"] if issue else None


def resolve_label_ids(owner: str, name: str, label_names: list[str]) -> list[str]:
    ids = []
    for label_name in label_names:
        data = gh_graphql(
            """
            query($owner: String!, $name: String!, $labelName: String!) {
              repository(owner: $owner, name: $name) {
                label(name: $labelName) { id }
              }
            }
            """,
            owner=owner,
            name=name,
            labelName=label_name,
        )
        label = data["repository"]["label"]
        if label is None:
            print(f"::warning::label '{label_name}' does not exist in {owner}/{name}, skipping", file=sys.stderr)
            continue
        ids.append(label["id"])
    return ids


def build_field_input(field: dict, value: str | None, names: list[str] | None) -> dict | None:
    """Build one IssueFieldCreateOrUpdateInput entry, or None to skip it."""
    data_type = field["dataType"]
    payload = {"fieldId": field["id"]}
    if data_type == "TEXT":
        payload["textValue"] = value
    elif data_type == "NUMBER":
        try:
            payload["numberValue"] = float(value)
        except (TypeError, ValueError):
            print(f"::warning::'{field['name']}' expects a number, got '{value}', skipping", file=sys.stderr)
            return None
    elif data_type == "DATE":
        payload["dateValue"] = value
    elif data_type in ("SINGLE_SELECT", "MULTI_SELECT"):
        options_by_name = {option["name"]: option["id"] for option in field["options"]}
        selected = [options_by_name[n] for n in (names or []) if n in options_by_name]
        missing = [n for n in (names or []) if n not in options_by_name]
        for name in missing:
            print(f"::warning::'{field['name']}' has no option named '{name}' on the parent repository, skipping it", file=sys.stderr)
        if not selected:
            return None
        if data_type == "SINGLE_SELECT":
            payload["singleSelectOptionId"] = selected[0]
        else:
            payload["multiSelectOptionIds"] = selected
    else:
        print(f"::warning::'{field['name']}' has unrecognized data type '{data_type}', skipping", file=sys.stderr)
        return None
    return payload


def set_issue_field_values(issue_id: str, field_inputs: list[dict]) -> None:
    if not field_inputs:
        return
    gh_graphql(
        """
        mutation($issueId: ID!, $issueFields: [IssueFieldCreateOrUpdateInput!]!) {
          setIssueFieldValue(input: {issueId: $issueId, issueFields: $issueFields}) {
            clientMutationId
          }
        }
        """,
        issueId=issue_id,
        issueFields=field_inputs,
    )


def update_issue_title_body(issue_id: str, title: str | None, body: str | None) -> None:
    """Copy title/body onto the parent, omitting whichever is empty.

    Built as one `$input` object rather than named `$title`/`$body`
    variables: a key that is *absent* from the input object leaves that
    field untouched, but a key present with value `null` is a distinct,
    unverified code path this avoids by construction (confirmed live
    against a disposable scratch issue — see the module docstring).
    """
    if not title and not body:
        return
    input_object = {"id": issue_id}
    if title:
        input_object["title"] = title
    if body:
        input_object["body"] = body
    gh_graphql(
        """
        mutation($input: UpdateIssueInput!) {
          updateIssue(input: $input) {
            clientMutationId
          }
        }
        """,
        input=input_object,
    )


def update_issue_type(issue_id: str, issue_type_id: str) -> None:
    gh_graphql(
        """
        mutation($issueId: ID!, $issueTypeId: ID!) {
          updateIssueIssueType(input: {issueId: $issueId, issueTypeId: $issueTypeId}) {
            clientMutationId
          }
        }
        """,
        issueId=issue_id,
        issueTypeId=issue_type_id,
    )


def add_sub_issue(issue_id: str, sub_issue_id: str) -> None:
    gh_graphql(
        """
        mutation($issueId: ID!, $subIssueId: ID!) {
          addSubIssue(input: {issueId: $issueId, subIssueId: $subIssueId}) {
            clientMutationId
          }
        }
        """,
        issueId=issue_id,
        subIssueId=sub_issue_id,
    )


def add_labels(labelable_id: str, label_ids: list[str]) -> None:
    if not label_ids:
        return
    gh_graphql(
        """
        mutation($labelableId: ID!, $labelIds: [ID!]) {
          addLabelsToLabelable(input: {labelableId: $labelableId, labelIds: $labelIds}) {
            clientMutationId
          }
        }
        """,
        labelableId=labelable_id,
        labelIds=label_ids,
    )


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


def fetch_open_sibling_proposals(parent_id: str, proposal_type_name: str, exclude_id: str) -> list[dict]:
    siblings = []
    cursor = None
    while True:
        data = gh_graphql(
            """
            query($parentId: ID!, $after: String) {
              node(id: $parentId) {
                ... on Issue {
                  subIssues(first: 100, after: $after) {
                    pageInfo { hasNextPage endCursor }
                    nodes { id number state issueType { name } }
                  }
                }
              }
            }
            """,
            parentId=parent_id,
            after=cursor,
        )
        page = data["node"]["subIssues"]
        for node in page["nodes"]:
            issue_type = node.get("issueType")
            if (
                node["id"] != exclude_id
                and node["state"] == "OPEN"
                and issue_type
                and issue_type["name"] == proposal_type_name
            ):
                siblings.append(node)
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return siblings


def write_output(result: str, summary: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"result={result}\n")
            f.write(f"summary={summary}\n")
    print(f"### apply-proposal\n\n- result: {result}\n- summary: {summary}\n")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"### apply-proposal\n\n- result: {result}\n- summary: {summary}\n")


def main() -> None:
    owner, name = os.environ["REPOSITORY"].split("/")
    proposal_number = int(os.environ["ISSUE_NUMBER"])
    prefix = os.environ.get("PREFIX", "Proposal ")
    proposal_type_name = os.environ.get("PROPOSAL_TYPE_NAME", "Proposal")

    proposal = fetch_proposal(owner, name, proposal_number)
    if proposal is None:
        print(f"ERROR: issue #{proposal_number} not found in {owner}/{name}", file=sys.stderr)
        sys.exit(1)

    if proposal["closed"]:
        write_output("no-op", f"{owner}/{name}#{proposal_number} is already closed")
        return

    parent = proposal["parent"]
    if parent is None:
        print(f"ERROR: {owner}/{name}#{proposal_number} has no parent issue to apply onto", file=sys.stderr)
        sys.exit(1)

    parent_owner = parent["repository"]["owner"]["login"]
    parent_name = parent["repository"]["name"]
    parent_id = parent["id"]

    applied = []

    # Copied unconditionally, same as every prefixed field — not implied
    # only by the proposal holding its own title/body, per #11.
    update_issue_title_body(parent_id, proposal["title"], proposal["body"])
    if proposal["title"]:
        applied.append("title")
    if proposal["body"]:
        applied.append("body")

    field_values = fetch_proposal_field_values(owner, name, proposal_number)
    parent_fields = fetch_repository_issue_fields(parent_owner, parent_name)

    generic_inputs = []
    for entry in field_values:
        if not entry["field_name"].startswith(prefix):
            continue
        key = entry["field_name"][len(prefix):]
        if not key:
            continue

        if key == "type":
            type_name = entry["value"] or (entry["names"][0] if entry["names"] else None)
            if not type_name:
                continue
            issue_type_id = resolve_issue_type(parent_owner, parent_name, type_name)
            if issue_type_id is None:
                print(f"::warning::Issue Type '{type_name}' does not exist in {parent_owner}/{parent_name}, skipping", file=sys.stderr)
                continue
            update_issue_type(parent_id, issue_type_id)
            applied.append(f"type={type_name}")

        elif key == "parent":
            url = entry["value"]
            if not url:
                continue
            container_id = resolve_issue_id_by_url(url)
            if container_id is None:
                continue
            add_sub_issue(container_id, parent_id)
            applied.append(f"parent={url}")

        elif key == "labels":
            label_names = entry["names"] or []
            if not label_names:
                continue
            label_ids = resolve_label_ids(parent_owner, parent_name, label_names)
            add_labels(parent_id, label_ids)
            applied.append(f"labels={','.join(label_names)}")

        else:
            if entry["value"] in (None, "") and not entry["names"]:
                continue  # empty/unset means leave the parent field untouched
            field = parent_fields.get(key)
            if field is None:
                print(f"::warning::no Issue Field named '{key}' on {parent_owner}/{parent_name}, skipping", file=sys.stderr)
                continue
            field_input = build_field_input(field, entry["value"], entry["names"])
            if field_input is not None:
                generic_inputs.append(field_input)
                applied.append(key)

    set_issue_field_values(parent_id, generic_inputs)

    siblings = fetch_open_sibling_proposals(parent_id, proposal_type_name, exclude_id=proposal["id"])
    close_issue(proposal["id"])
    closed_numbers = [proposal_number]
    for sibling in siblings:
        close_issue(sibling["id"])
        closed_numbers.append(sibling["number"])

    write_output(
        "applied",
        f"parent={parent_owner}/{parent_name}#{parent['number']}; "
        f"fields={','.join(applied) if applied else '(none)'}; "
        f"closed={','.join('#' + str(n) for n in closed_numbers)}",
    )


if __name__ == "__main__":
    main()
