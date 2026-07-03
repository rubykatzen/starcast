# Starcast

Framework for autonomous AI editorial pipelines on GitHub Actions + Claude Code Action.

Each project defines its own roles and playbooks. The framework provides the runtime: actions, reusable workflows, and the agent handbook.

## Structure

```text
actions/run-agent/   ← composite action: assembles system prompt and runs the agent
```

## How it works

1. A card (GitHub Issue) enters the pipeline with label `waiting`
2. The pipeline workflow reads the card's project Status to determine role + playbook
3. The agent runs: fetches content, translates, publishes — depending on the phase
4. The card advances to the next status

## Projects using Starcast

- [starcast-xbox](https://github.com/dupmachine/starcast-xbox) — Xbox Game Pass news in Russian
