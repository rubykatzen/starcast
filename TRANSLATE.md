# Translate — regulations

This document constrains the agent behind `Propose` for the **Translate**
Project (`https://github.com/orgs/rubykatzen/projects/1`). It is the
`regulations_repo`/`regulations_path` target for this repository's own
`proposal.yml` caller — a real (if narrow) test case for the proposal
lifecycle described in #11, not a demonstration of general translation
quality.

## Task

For each issue `Propose` dequeues from the Translate Project's Incoming
column, propose:

- **`title`** — the issue's title, translated to English. If the title is
  already English, propose it unchanged (an unchanged proposal is still a
  valid proposal; `Apply`'s empty-value-skip rule does not apply here,
  since a translated-into-itself title is a real, non-empty value).
- **`body`** — the issue's body, translated to English the same way.
  Preserve Markdown structure (headings, lists, code blocks, links) —
  translate prose, not syntax.
- **`Proposal Size`** — estimate a size purely from how much text the
  issue's title/body actually contain, one of `□ XS`/`□ S`/`□ M`/`□ L`/
  `□ XL` (matching this repository's existing label names, for
  consistency, though this field never reads or writes those labels
  themselves — it's a judgment call about the *text*, not a label
  lookup). Rough guide, not a rigid word-count formula:
  - `□ XS` — a one-line title, little or no body.
  - `□ S` — a short paragraph.
  - `□ M` — several paragraphs, an ordinary issue write-up.
  - `□ L` — long, multi-section (multiple headings/lists).
  - `□ XL` — very long and dense.

  This is a judgment call about how much *text* there is, not an
  estimate of implementation effort — a short title describing a huge
  task is still `□ XS` here.

## What this is not

- Not a general translation service — scope is exactly `title`/`body` of
  issues that land in this one Project.
- Not an effort estimator — `Proposal Size` measures the text's own
  length/density, not how much work the described task would take.
  Deliberately reuses the XS–XL names from this repository's existing
  effort-sizing labels for a familiar scale, not their meaning.
- Not authoritative regulations for any other consumer of
  `proposal-shared.yml` — this file exists for this repository's own
  dogfood/test use of the workflow it also publishes, per #11's model.

## Non-goals for now

- No language detection heuristics are specified here — assume the agent
  invoking `Propose` can judge source language on its own.
- No precise word-count formula for `Proposal Size` — the five bands
  above are a guide for agent judgment, not a threshold to compute
  against.
