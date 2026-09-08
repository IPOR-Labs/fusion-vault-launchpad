# Security policy

## Scope

This repository contains tooling that **prepares and deploys** IPOR Fusion PlasmaVaults. It never holds funds itself, but a bug here can misconfigure a vault that does. Please treat findings in the pipeline (role assignment, substrate encoding, price feed wiring, verification, rehearsal) as security-relevant.

Vulnerabilities in the IPOR Fusion **smart contracts** are out of scope for this repository; report them through the channels listed on [ipor.io](https://ipor.io).

## Reporting a vulnerability

Do **not** open a public issue for a security problem.

Use GitHub's private vulnerability reporting for this repository: **Security → Report a vulnerability**. Include the affected file or step, a minimal reproduction (a strategy JSON and the command you ran, with secrets removed) and the impact you see.

We will acknowledge the report, work with you on a fix, and credit you in the release notes unless you ask otherwise.

## Never share

Private keys, `.env` contents, RPC URLs with API keys or `.deploy-state/` files from a live run. Redact them before sending anything.
