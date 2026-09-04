# 09 — Using this repository with your AI agent

This repository does not depend on any particular AI product. Every step is a plain command with a documented flag set, and the agent instructions live in one file, `AGENTS.md`. This page tells you how each common tool finds that file, and what to say to your agent to get started.

## 1. How your tool finds the instructions

| Tool | Reads automatically | Provided in this repo |
|---|---|---|
| OpenAI Codex (CLI and cloud) | `AGENTS.md` | `AGENTS.md` |
| Claude Code | `CLAUDE.md` | `CLAUDE.md`, which imports `AGENTS.md` |
| Google Gemini CLI | `GEMINI.md` | `GEMINI.md` → points to `AGENTS.md` |
| Cursor | `.cursor/rules/*.mdc`, also `AGENTS.md` in recent versions | `.cursor/rules/agents.mdc` → points to `AGENTS.md` |
| GitHub Copilot (agent mode, coding agent) | `.github/copilot-instructions.md` | provided → points to `AGENTS.md` |
| Windsurf | `.windsurf/rules/*.md` | `.windsurf/rules/agents.md` → points to `AGENTS.md` |
| xAI Grok (grok-cli) | `.grok/GROK.md` | `.grok/GROK.md` → points to `AGENTS.md` |
| Aider | `CONVENTIONS.md` when passed with `--read` | `CONVENTIONS.md` → points to `AGENTS.md` |
| Anything else, or a chat interface without file access | nothing | paste `AGENTS.md` into the conversation, or use the prompt below |

The pointer files are one paragraph each and say the same thing: read `AGENTS.md` first. If your tool is not listed, tell the agent to read `AGENTS.md`; that is all it needs.

## 2. A prompt to start any agent

Paste this as your first message, adjusting the last line:

```
You are working in the fusion-vault-launchpad repository. Before anything else, read AGENTS.md
and follow its reading order. Then run `python tools/doctor.py` (or `make doctor`) and tell me
which modes are available in this environment. Follow the autonomy boundary in AGENTS.md exactly:
never ask me for a private key in this conversation, never broadcast to a live chain without my
explicit "yes" for the exact file, and report failures as failures.

My goal: <describe the vault you want, or "dry-run the shipped example so I can see how this works">.
```

## 3. What to expect from a well-behaved agent

1. It reads `AGENTS.md`, the concepts page and the human-in-the-loop page before touching anything.
2. It runs the doctor and the unit tests, then a dry-run of the example, and shows you the plan.
3. It asks you the `[client]` questions one at a time, with a recommended default, and writes your answers into `strategies/<name>.md` and `.json` as it goes.
4. It dry-runs your strategy until the plan reads right, then rehearses on a local fork and shows you the verification report and the plan/run diff.
5. Only then does it ask you to put your key and private RPC into `.env` yourself, and asks for an explicit "yes" before running the live command, which it shows you in full first.

If your agent skips a step, asks for your key in chat, or proposes a live broadcast before a fork rehearsal, stop it and point it at `AGENTS.md` §5. The pipeline itself also refuses a live broadcast that lacks the explicit flag or still contains placeholder addresses, but the agent's discipline is your first line of defence.

## 4. Commands the agent will use

All are plain commands; `make help` lists the wrapped versions.

| Purpose | Command |
|---|---|
| Environment check | `python tools/doctor.py [strategy.json]` |
| Unit tests | `python -m pytest` |
| Reconcile `.md` with `.json` | `python tools/spec_lint.py strategies/<name>.json` |
| Dry-run | `python -m deploy strategies/<name>.json` |
| Start a fork | `anvil --fork-url <rpc> --port 8546` |
| Rehearse on the fork | `RPC_URL=http://127.0.0.1:8546 DEPLOYER_PRIVATE_KEY=<anvil #0 key> python -m deploy strategies/<name>.json --broadcast` |
| Plan vs run diff | `python tools/plan_diff.py .deploy-state/<name>.plan.json .deploy-state/<name>.run.json` |
| Live deployment | `python -m deploy strategies/<name>.json --broadcast --i-understand-this-is-live` with `.env` filled in |
| Verify a live vault | `python -m deploy strategies/<name>.json --verify-only` |

## 5. Smaller or less capable models

The repository is designed so that correctness does not depend on the model's judgement:

- Validation is in code (schema, semantic checks, whitelist gate, guards), not in the instructions.
- Every failure message says what to do next.
- The dangerous action needs a flag the agent has to type on purpose, a key the human has to place, and passes only if no placeholder address remains.

That said, a less capable model will need more of your attention during intake, where the questions are open-ended. Review `strategies/<name>.md` yourself before the fork rehearsal; it is short and written for you.
