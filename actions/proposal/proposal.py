#!/usr/bin/env python3
"""Unified CLI for the proposal lifecycle (#11, #55): dequeue, list-fields,
propose, apply, reject.

One executable with subcommands (`proposal.py <mode> --flag value`,
Homebrew-style) rather than one script per action, so the five modes
share transport, field-discovery, and mutation helpers instead of
duplicating them. `--github-token` is deliberately not among the flags
below -- it's read from the GH_TOKEN environment variable, the same
convention `gh` itself uses, so it never appears in argv/process
listings.

## Model recap (see #11 for the full design)

A proposal is a real Issue, created as a native GitHub sub-issue of the
issue it proposes changes to (its "parent"). `title`/`body` are copied
onto the parent unconditionally; every other proposal Issue Field whose
name starts with `--prefix` targets the same-named Issue Field on the
parent. Three stripped names are reserved: `type` (native Issue Type,
via `updateIssueIssueType`), `parent` (a full issue URL, via
`addSubIssue`), and `labels` (via `addLabelsToLabelable`). An
empty/unset proposal field means "leave the parent's field untouched."

`list-fields` and `propose` extend the model to the *creation* side:
`list-fields` discovers which prefixed Issue Fields exist in a
repository (so a caller -- in the future, an agent -- knows what it can
fill in), and `propose` creates the proposal issue itself.

`propose` does not decide content itself -- that's real agent
judgment, out of scope for a deterministic script. It takes a
`--model-response` JSON blob (`{"title": ..., "body": ...,
"fields": {...}}`) and does only the mechanical part: creates the
issue, writes the given field values, posts the control comment. The
`propose-context` mode is the other half: it gathers everything a
model needs to produce that JSON -- regulations text, the candidate
issue's title/body, and the catalog of fields available to fill in --
as one self-contained prompt, so a caller can hand it straight to a
model (e.g. via `actions/ai-inference` wrapping GitHub Copilot CLI) and
feed the raw response back into `propose --model-response` without
either mode needing to know about the other's transport.

The control comment's exact template is defined once here
(CONTROL_COMMENT_BODY) and posted by `propose` immediately after
creation. Verifying that a later `apply`/`reject` truly acts on *that*
comment (as opposed to some other comment containing matching text) is
tracked separately in #63 -- not yet enforced here.

## Transitioning a controlling entity (#69)

`propose`/`apply` optionally transition whatever "controlling entity"
tracks an issue's state (e.g. a ProjectV2Item) as the issue moves
through the lifecycle -- incoming to review on `propose`, review to
done on `apply`. Three independently optional consumer-owned GraphQL
pieces, same philosophy as capacity_query/queue_query: `--entity-query`
resolves the entity id (must alias exactly one scalar as `entity`, same
alias+type-disambiguated extraction as `capacity`/`queue`) given an
`issueId` variable; `--transition-mutation` is then run with `issueId`
and (if resolved) `entityId` as variables. Omitting the mutation is a
no-op -- the entity is never touched. A *configured* transition that
fails after a successful create/apply is a hard error, not swallowed:
the action succeeded but the entity's state is now stale, and
continuing silently risks the next dequeue re-selecting the same issue.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid

DEFAULT_PREFIX = "Proposal "
DEFAULT_PROPOSAL_TYPE = "Proposal"
RESERVED_FIELD_NAMES = ("type", "parent", "labels")
CONTROL_COMMENT_BODY = "- [ ] Apply\n- [ ] Reject\n- [ ] Distill\n- [ ] Rework\n"
ISSUE_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)$")


# ---------------------------------------------------------------- transport

def gh_graphql(query: str, **variables: object) -> dict:
    """Run a GraphQL query/mutation via `gh api graphql --input -`.

    A full JSON request body, not per-variable `-f`/`-F` flags: several
    modes here need list/object-shaped variables (`issueFields`,
    `labelIds`, a partial `UpdateIssueInput`), and `-f`/`-F` only support
    scalar values -- a JSON-encoded string passed through `-f` is sent as
    a literal GraphQL string, not a parsed list/object. Confirmed live
    against disposable scratch issues during development, not assumed.
    """
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


def write_output(**fields: str) -> None:
    """Write step outputs, safe for multiline values (e.g. `prompt`).

    A plain `key=value\\n` line breaks if `value` itself contains a
    newline -- GITHUB_OUTPUT would parse the continuation as new,
    unrelated key=value pairs. Uses the delimiter form GitHub documents
    for multiline values (`key<<DELIM\\nvalue\\nDELIM\\n`) unconditionally,
    since it's valid for single-line values too.
    """
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            for key, value in fields.items():
                delimiter = f"ghadelimiter_{uuid.uuid4().hex}"
                f.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
    summary = "### proposal\n\n" + "".join(f"- {k}: {v}\n" for k, v in fields.items())
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary)


# --------------------------------------------------------- generic helpers

def find_aliased(obj, name: str, predicate, matches: list | None = None) -> list:
    """Recursively collect every value at key `name` matching `predicate`."""
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


# --------------------------------------------------- issue field catalog

def fetch_repository_issue_fields(owner: str, name: str) -> dict[str, dict]:
    """Return the repository's Issue Field catalog, keyed by name."""
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


def proposal_fields(owner: str, name: str, prefix: str) -> dict[str, dict]:
    """Issue Fields whose name starts with `prefix`, keyed by stripped name."""
    return {
        field_name[len(prefix):]: field
        for field_name, field in fetch_repository_issue_fields(owner, name).items()
        if field_name.startswith(prefix) and field_name[len(prefix):] not in RESERVED_FIELD_NAMES
    }


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
        print(f"::warning::'{url}' is not a full https://github.com/owner/repo/issues/N URL, skipping", file=sys.stderr)
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
        for missing in (n for n in (names or []) if n not in options_by_name):
            print(f"::warning::'{field['name']}' has no option named '{missing}', skipping it", file=sys.stderr)
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


def model_value_for_field(field: dict, raw: object) -> tuple[str | None, list[str] | None]:
    """Convert one `--model-response` "fields" entry into build_field_input's
    (value, names) shape, based on the field's actual data type -- the
    model always writes a plain string (or, for MULTI_SELECT, a list of
    strings); this is not a second copy of the model's judgment, just a
    shape adapter.
    """
    data_type = field["dataType"]
    if data_type == "MULTI_SELECT":
        names = raw if isinstance(raw, list) else [raw]
        return None, [str(n) for n in names]
    if data_type == "SINGLE_SELECT":
        return None, [str(raw)]
    return str(raw), None


# -------------------------------------------------------------- mutations

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
    """Set title/body, omitting whichever is empty.

    A key *absent* from the input object leaves that field untouched; a
    key present with value `null` is a different, unverified code path
    this avoids by construction (confirmed live during development).
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


def add_comment(issue_id: str, body: str) -> None:
    gh_graphql(
        """
        mutation($issueId: ID!, $body: String!) {
          addComment(input: {subjectId: $issueId, body: $body}) {
            clientMutationId
          }
        }
        """,
        issueId=issue_id,
        body=body,
    )


def add_reaction(subject_id: str, content: str) -> None:
    gh_graphql(
        """
        mutation($subjectId: ID!, $content: ReactionContent!) {
          addReaction(input: {subjectId: $subjectId, content: $content}) {
            clientMutationId
          }
        }
        """,
        subjectId=subject_id,
        content=content,
    )


# ------------------------------------------------ entity transition (#69)

def is_id_scalar(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def resolve_entity_id(entity_query: str, issue_id: str) -> str | None:
    """Resolve the controlling entity's id for `issue_id` via consumer text.

    Mirrors capacity_query/queue_query's extraction exactly: the query
    must alias exactly one scalar field as `entity`, found by the same
    alias-name + value-type recursive search. Returns None if
    entity_query is empty -- callers treat that as "no entity to thread
    through," not an error.
    """
    if not entity_query:
        return None
    data = gh_graphql(entity_query, issueId=issue_id)
    return extract_one(data, "entity", is_id_scalar, "--entity-query")


def run_transition_mutation(mutation_text: str, issue_id: str, entity_id: str | None) -> None:
    """Run a consumer-owned transition mutation, if configured.

    Both `issueId` and `entityId` are always supplied as variables --
    GraphQL only requires an operation's *declared* variables to be
    used, not every key present in the variables payload, so the
    consumer's mutation just declares whichever of the two it needs.
    A no-op when mutation_text is empty: the controlling entity, if
    any, is left untouched.
    """
    if not mutation_text:
        return
    gh_graphql(mutation_text, issueId=issue_id, entityId=entity_id)


# ------------------------------------------------------------- dequeue mode

def cmd_dequeue(args: argparse.Namespace) -> None:
    review_limit = float(args.review_limit)

    capacity_data = gh_graphql(args.capacity_query)
    capacity = extract_one(capacity_data, "capacity", is_number, "--capacity-query")

    if capacity >= review_limit:
        write_output(result="no-op-at-capacity", issue_id="", summary=f"capacity={capacity} >= review_limit={review_limit}")
        return

    queue_data = gh_graphql(args.queue_query)
    queue = extract_one(queue_data, "queue", lambda v: isinstance(v, list), "--queue-query")

    if not queue:
        write_output(result="no-op-empty-queue", issue_id="", summary=f"capacity={capacity} < review_limit={review_limit}, queue is empty")
        return

    # A queue element's `id` may be nested (e.g. a Project-based
    # queue_query typically exposes it as `content { ... on Issue { id }
    # }`, not flat) -- found the same way capacity/queue themselves are,
    # a recursive alias search, not a flat dict lookup.
    candidate = queue[0]
    ids = find_aliased(candidate, "id", is_id_scalar)
    if len(ids) != 1:
        print(
            f"ERROR: queue_query's first queue element must contain exactly one 'id' field (found {len(ids)})",
            file=sys.stderr,
        )
        sys.exit(1)
    issue_id = ids[0]

    write_output(result="dequeued", issue_id=issue_id, summary=f"capacity={capacity} < review_limit={review_limit}, dequeued {issue_id}")


# ---------------------------------------------------------- list-fields mode

def cmd_list_fields(args: argparse.Namespace) -> None:
    owner, name = args.repository.split("/")
    fields = proposal_fields(owner, name, args.prefix)
    catalog = {
        "fixed": ["title", "body", "type", "parent", "labels"],
        "custom": [
            {
                "name": field_name,
                "dataType": field["dataType"],
                "options": [o["name"] for o in field["options"]] if "options" in field else None,
            }
            for field_name, field in fields.items()
        ],
    }
    payload = json.dumps(catalog)
    write_output(result="listed", fields=payload, summary=f"{len(fields)} custom field(s) found with prefix '{args.prefix}'")


# -------------------------------------------------------------- propose mode

def resolve_parent(parent_issue_id: str) -> dict:
    data = gh_graphql(
        """
        query($id: ID!) {
          node(id: $id) {
            ... on Issue {
              number
              repository { owner { login } name }
            }
          }
        }
        """,
        id=parent_issue_id,
    )
    parent = data["node"]
    if parent is None:
        print(f"ERROR: parent issue '{parent_issue_id}' not found", file=sys.stderr)
        sys.exit(1)
    return parent


def fetch_issue_title_body(owner: str, name: str, number: int) -> tuple[str, str]:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) { title body }
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
    issue = repository["issue"]
    if issue is None:
        print(f"ERROR: issue #{number} not found in {owner}/{name}", file=sys.stderr)
        sys.exit(1)
    return issue["title"], issue["body"] or ""


def fetch_regulations_text(owner: str, name: str, path: str) -> str:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!, $expr: String!) {
          repository(owner: $owner, name: $name) {
            object(expression: $expr) {
              ... on Blob { text }
            }
          }
        }
        """,
        owner=owner,
        name=name,
        expr=f"HEAD:{path}",
    )
    repository = data["repository"]
    if repository is None:
        print(f"ERROR: regulations repository '{owner}/{name}' not found or inaccessible", file=sys.stderr)
        sys.exit(1)
    obj = repository["object"]
    if obj is None:
        print(f"ERROR: '{path}' not found on {owner}/{name}'s default branch", file=sys.stderr)
        sys.exit(1)
    return obj["text"]


def cmd_propose_context(args: argparse.Namespace) -> None:
    """Gather everything a model needs to decide a proposal's content.

    Outputs one self-contained prompt: the regulations text, the
    candidate issue's title/body, and the catalog of fields available to
    propose, plus an explicit instruction for the JSON response shape
    `propose --model-response` expects back. Deliberately produces text
    only -- it does not itself call a model, so it stays agnostic to
    whichever inference mechanism a caller wires in.
    """
    parent = resolve_parent(args.parent_issue_id)
    owner = parent["repository"]["owner"]["login"]
    name = parent["repository"]["name"]

    title, body = fetch_issue_title_body(owner, name, parent["number"])

    regulations_owner, regulations_name = args.regulations_repo.split("/")
    regulations_text = fetch_regulations_text(regulations_owner, regulations_name, args.regulations_path)

    fields = proposal_fields(owner, name, args.prefix)
    if fields:
        field_lines = []
        for field_name, field in fields.items():
            data_type = field["dataType"]
            if data_type in ("SINGLE_SELECT", "MULTI_SELECT"):
                options = ", ".join(o["name"] for o in field["options"])
                field_lines.append(f'- "{field_name}" ({data_type}, one of: {options})')
            else:
                field_lines.append(f'- "{field_name}" ({data_type})')
        fields_block = "\n".join(field_lines)
    else:
        fields_block = "(no custom fields configured)"

    prompt = f"""# Regulations

{regulations_text}

# Issue to propose against ({owner}/{name}#{parent['number']})

## Title

{title}

## Body

{body}

# Available proposal fields

Besides `title` and `body` (always proposable), these custom fields are
available:

{fields_block}

# Required response format

Respond with a single JSON object and nothing else -- no prose, no
Markdown code fence. Shape:

{{
  "title": "<proposed title>",
  "body": "<proposed body>",
  "fields": {{
    "<field name>": "<value>"
  }}
}}

Only include a field in "fields" if you have a real value to propose
for it -- omit it entirely rather than guessing. For a field whose type
is listed as MULTI_SELECT, its value must be a JSON array of option
name strings, even if you are only selecting one option. For every
other type, its value is a single string (write dates as YYYY-MM-DD)."""

    write_output(
        result="context",
        prompt=prompt,
        summary=f"context gathered for {owner}/{name}#{parent['number']} ({len(fields)} custom field(s))",
    )


def cmd_propose(args: argparse.Namespace) -> None:
    """Create a proposal against args.parent_issue_id.

    Takes the model's decision as a `--model-response` JSON blob
    (produced from `propose-context`'s prompt by whatever inference
    mechanism the caller wires in) and does only the mechanical part:
    create the issue, write the given field values, post the control
    comment. Does not itself decide content -- that's `propose-context`
    plus a real model call, not this mode's job.
    """
    parent = resolve_parent(args.parent_issue_id)
    owner = parent["repository"]["owner"]["login"]
    name = parent["repository"]["name"]

    issue_type_id = resolve_issue_type(owner, name, args.type)
    if issue_type_id is None:
        print(f"ERROR: Issue Type '{args.type}' does not exist in {owner}/{name}", file=sys.stderr)
        sys.exit(1)

    try:
        model_response = json.loads(args.model_response)
    except json.JSONDecodeError as error:
        print(f"ERROR: --model-response is not valid JSON: {error}", file=sys.stderr)
        sys.exit(1)

    title = model_response.get("title")
    if not title:
        print("ERROR: --model-response must include a non-empty 'title'", file=sys.stderr)
        sys.exit(1)
    body = model_response.get("body") or ""
    model_fields = model_response.get("fields") or {}

    fields = proposal_fields(owner, name, args.prefix)

    create_data = gh_graphql(
        """
        mutation($input: CreateIssueInput!) {
          createIssue(input: $input) {
            issue { id number }
          }
        }
        """,
        input={
            "repositoryId": args.repository_id if args.repository_id else _resolve_repository_id(owner, name),
            "title": title,
            "body": body,
            "issueTypeId": issue_type_id,
            "parentIssueId": args.parent_issue_id,
        },
    )
    proposal = create_data["createIssue"]["issue"]

    field_inputs = []
    filled = []
    for field_name, raw_value in model_fields.items():
        field = fields.get(field_name)
        if field is None:
            print(f"::warning::model proposed unknown field '{field_name}', skipping", file=sys.stderr)
            continue
        value, names = model_value_for_field(field, raw_value)
        field_input = build_field_input(field, value, names)
        if field_input is not None:
            field_inputs.append(field_input)
            filled.append(field_name)
    set_issue_field_values(proposal["id"], field_inputs)

    add_comment(proposal["id"], CONTROL_COMMENT_BODY)

    transitioned = "no"
    if args.transition_mutation:
        entity_id = resolve_entity_id(args.entity_query, args.parent_issue_id)
        run_transition_mutation(args.transition_mutation, args.parent_issue_id, entity_id)
        transitioned = f"yes (entity={entity_id})" if entity_id else "yes (no entity)"

    write_output(
        result="proposed",
        issue_id=proposal["id"],
        summary=(
            f"created {owner}/{name}#{proposal['number']} for parent #{parent['number']}; "
            f"fields={','.join(filled) if filled else '(none)'}; transitioned={transitioned}"
        ),
    )


def _resolve_repository_id(owner: str, name: str) -> str:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) { id }
        }
        """,
        owner=owner,
        name=name,
    )
    repository = data["repository"]
    if repository is None:
        print(f"ERROR: repository '{owner}/{name}' not found or inaccessible", file=sys.stderr)
        sys.exit(1)
    return repository["id"]


# ---------------------------------------------------------------- apply mode

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


def cmd_apply(args: argparse.Namespace) -> None:
    owner, name = args.repository.split("/")
    proposal_number = int(args.issue_number)

    proposal = fetch_proposal(owner, name, proposal_number)
    if proposal is None:
        print(f"ERROR: issue #{proposal_number} not found in {owner}/{name}", file=sys.stderr)
        sys.exit(1)

    if proposal["closed"]:
        write_output(result="no-op", issue_id="", summary=f"{owner}/{name}#{proposal_number} is already closed")
        return

    parent = proposal["parent"]
    if parent is None:
        print(f"ERROR: {owner}/{name}#{proposal_number} has no parent issue to apply onto", file=sys.stderr)
        sys.exit(1)

    parent_owner = parent["repository"]["owner"]["login"]
    parent_name = parent["repository"]["name"]
    parent_id = parent["id"]

    applied = []

    update_issue_title_body(parent_id, proposal["title"], proposal["body"])
    if proposal["title"]:
        applied.append("title")
    if proposal["body"]:
        applied.append("body")

    field_values = fetch_proposal_field_values(owner, name, proposal_number)
    parent_fields = fetch_repository_issue_fields(parent_owner, parent_name)

    generic_inputs = []
    for entry in field_values:
        if not entry["field_name"].startswith(args.prefix):
            continue
        key = entry["field_name"][len(args.prefix):]
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

    siblings = fetch_open_sibling_proposals(parent_id, args.type, exclude_id=proposal["id"])
    close_issue(proposal["id"])
    closed_numbers = [proposal_number]
    for sibling in siblings:
        close_issue(sibling["id"])
        closed_numbers.append(sibling["number"])

    transitioned = "no"
    if args.transition_mutation:
        entity_id = resolve_entity_id(args.entity_query, parent_id)
        run_transition_mutation(args.transition_mutation, parent_id, entity_id)
        transitioned = f"yes (entity={entity_id})" if entity_id else "yes (no entity)"

    write_output(
        result="applied",
        issue_id=parent_id,
        summary=(
            f"parent={parent_owner}/{parent_name}#{parent['number']}; "
            f"fields={','.join(applied) if applied else '(none)'}; "
            f"closed={','.join('#' + str(n) for n in closed_numbers)}; "
            f"transitioned={transitioned}"
        ),
    )


# ------------------------------------------- check-control-comment mode (#63)

def fetch_first_comment(owner: str, name: str, issue_number: int) -> dict | None:
    data = gh_graphql(
        """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) {
              comments(first: 1) {
                nodes { id databaseId }
              }
            }
          }
        }
        """,
        owner=owner,
        name=name,
        number=issue_number,
    )
    repository = data["repository"]
    if repository is None:
        print(f"ERROR: repository '{owner}/{name}' not found or inaccessible", file=sys.stderr)
        sys.exit(1)
    issue = repository["issue"]
    if issue is None:
        print(f"ERROR: issue #{issue_number} not found in {owner}/{name}", file=sys.stderr)
        sys.exit(1)
    nodes = issue["comments"]["nodes"]
    return nodes[0] if nodes else None


def cmd_check_control_comment(args: argparse.Namespace) -> None:
    """Verify the triggering comment is the proposal's control comment (#63).

    The control comment is identified structurally -- the issue's first
    comment, posted by `propose` immediately after creation, before any
    other mutation -- not by matching its text. A comment containing the
    right checkbox text but posted later is not the control comment, and
    acting on it would be a spurious trigger; this is a hard error, not
    a silent no-op, so a human notices via the failed run rather than
    wondering why nothing happened.

    On success, marks the comment with a 👀 reaction -- "an executor has
    picked this up," mirroring the Copilot coding agent's own
    acknowledgment convention. Left in place afterward, as a persistent
    record, not removed on completion.
    """
    owner, name = args.repository.split("/")
    issue_number = int(args.issue_number)
    comment_id = int(args.comment_id)

    first_comment = fetch_first_comment(owner, name, issue_number)
    if first_comment is None or first_comment["databaseId"] != comment_id:
        print(
            f"ERROR: comment {comment_id} is not {owner}/{name}#{issue_number}'s control "
            "comment (its first comment) -- refusing to act on it",
            file=sys.stderr,
        )
        sys.exit(1)

    add_reaction(first_comment["id"], "EYES")
    write_output(
        result="verified",
        issue_id=first_comment["id"],
        summary=f"{owner}/{name}#{issue_number} comment {comment_id} confirmed as control comment",
    )


# --------------------------------------------------------------- reject mode

def cmd_reject(args: argparse.Namespace) -> None:
    owner, name = args.repository.split("/")
    proposal_number = int(args.issue_number)

    proposal = fetch_proposal(owner, name, proposal_number)
    if proposal is None:
        print(f"ERROR: issue #{proposal_number} not found in {owner}/{name}", file=sys.stderr)
        sys.exit(1)

    label = f"{owner}/{name}#{proposal_number}"
    if proposal["closed"]:
        write_output(result="no-op", issue_id="", summary=f"{label} is already closed")
        return

    close_issue(proposal["id"])
    write_output(result="rejected", issue_id=proposal["id"], summary=f"{label} closed")


# --------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proposal.py")
    subparsers = parser.add_subparsers(required=True, dest="mode")

    dequeue = subparsers.add_parser("dequeue", help="check review capacity and dequeue at most one candidate (#55)")
    dequeue.add_argument("--capacity-query", required=True)
    dequeue.add_argument("--queue-query", required=True)
    dequeue.add_argument("--review-limit", required=True)
    dequeue.set_defaults(func=cmd_dequeue)

    list_fields = subparsers.add_parser("list-fields", help="list the prefixed Proposal Issue Fields available in a repository")
    list_fields.add_argument("--repository", required=True)
    list_fields.add_argument("--prefix", default=DEFAULT_PREFIX)
    list_fields.set_defaults(func=cmd_list_fields)

    propose_context = subparsers.add_parser(
        "propose-context",
        help="gather regulations/issue/field-catalog into one prompt for a model to decide proposal content",
    )
    propose_context.add_argument("--parent-issue-id", required=True)
    propose_context.add_argument("--regulations-repo", required=True)
    propose_context.add_argument("--regulations-path", required=True)
    propose_context.add_argument("--prefix", default=DEFAULT_PREFIX)
    propose_context.set_defaults(func=cmd_propose_context)

    propose = subparsers.add_parser("propose", help="create a proposal against a parent issue from a model's decided content")
    propose.add_argument("--parent-issue-id", required=True)
    propose.add_argument("--repository-id", default=None)
    propose.add_argument("--prefix", default=DEFAULT_PREFIX)
    propose.add_argument("--type", default=DEFAULT_PROPOSAL_TYPE)
    propose.add_argument("--model-response", required=True, help='JSON {"title", "body", "fields": {...}} from propose-context + a model call')
    propose.add_argument("--entity-query", default=None, help="resolves the controlling entity's id (#69); optional")
    propose.add_argument("--transition-mutation", default=None, help="moves the controlling entity incoming -> review (#69); optional")
    propose.set_defaults(func=cmd_propose)

    apply_ = subparsers.add_parser("apply", help="copy a proposal's fields onto its parent and close it")
    apply_.add_argument("--repository", required=True)
    apply_.add_argument("--issue-number", required=True)
    apply_.add_argument("--prefix", default=DEFAULT_PREFIX)
    apply_.add_argument("--type", default=DEFAULT_PROPOSAL_TYPE)
    apply_.add_argument("--entity-query", default=None, help="resolves the controlling entity's id (#69); optional")
    apply_.add_argument("--transition-mutation", default=None, help="moves the controlling entity review -> done (#69); optional")
    apply_.set_defaults(func=cmd_apply)

    reject = subparsers.add_parser("reject", help="close a proposal, nothing else")
    reject.add_argument("--repository", required=True)
    reject.add_argument("--issue-number", required=True)
    reject.set_defaults(func=cmd_reject)

    check_control_comment = subparsers.add_parser(
        "check-control-comment",
        help="verify a comment is the proposal's first/control comment, then mark it picked up (#63)",
    )
    check_control_comment.add_argument("--repository", required=True)
    check_control_comment.add_argument("--issue-number", required=True)
    check_control_comment.add_argument("--comment-id", required=True)
    check_control_comment.set_defaults(func=cmd_check_control_comment)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
