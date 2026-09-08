# Canonical commands. Every target is a thin wrapper around a documented command,
# so an agent (or a human) can run the lifecycle without remembering flags.
# `make help` lists them. Variables:
#   STRATEGY  path to the strategy JSON     (default: the shipped example)
#   FORK_RPC  RPC to fork from for `make fork` (default: Base's official public RPC; some public
#             endpoints refuse the archive reads a fork needs, a private RPC is most reliable)
#   PORT      anvil port                    (default: 8546)

PY        ?= .venv/bin/python
STRATEGY  ?= strategies/example-usdc-aave-base.json
FORK_RPC  ?= https://mainnet.base.org
PORT      ?= 8546
NAME       = $(basename $(notdir $(STRATEGY)))
ANVIL_KEY  = 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
ANVIL     ?= anvil

.PHONY: help install doctor test lint dry-run fork rehearse exercise diff verify clean-state

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## create .venv and install pinned dependencies
	python3 -m venv .venv && $(PY) -m pip install -q --upgrade pip && $(PY) -m pip install -q -r requirements-dev.txt

doctor: ## check the environment: python, SDK, RPC, anvil, .env (never prints secrets)
	$(PY) tools/doctor.py $(STRATEGY)

test: ## unit tests (no chain, no key)
	$(PY) -m pytest

lint: ## reconcile the .md spec with the .json spec
	$(PY) tools/spec_lint.py $(STRATEGY)

dry-run: ## simulate: validate, preview addresses, print the full plan (no key, nothing sent)
	$(PY) -m deploy $(STRATEGY)

fork: ## start a fresh anvil fork of FORK_RPC on PORT (foreground; run in its own terminal)
	-lsof -ti :$(PORT) | xargs -r kill
	$(ANVIL) --fork-url $(FORK_RPC) --port $(PORT)

rehearse: ## broadcast the pipeline against the local anvil fork with anvil's test key, then use the vault (deposit, execute, withdraw)
	RPC_URL=http://127.0.0.1:$(PORT) DEPLOYER_PRIVATE_KEY=$(ANVIL_KEY) $(PY) -m deploy $(STRATEGY) --broadcast --rehearse

exercise: ## re-run only the rehearsal stage against the vault deployed on the fork
	RPC_URL=http://127.0.0.1:$(PORT) DEPLOYER_PRIVATE_KEY=$(ANVIL_KEY) $(PY) -m deploy $(STRATEGY) --rehearse-only

diff: ## compare the dry-run plan with the last broadcast run
	$(PY) tools/plan_diff.py .deploy-state/$(NAME).plan.json .deploy-state/$(NAME).run.json

verify: ## re-read the chain against the JSON using the recorded state (uses RPC_URL from .env)
	$(PY) -m deploy $(STRATEGY) --verify-only

clean-state: ## delete the rehearsal state for STRATEGY (never do this after a live run)
	rm -f .deploy-state/$(NAME).json .deploy-state/$(NAME).run.json
