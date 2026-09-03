# Disclaimer

This project is open-source software released under the [MIT License](./LICENSE). It is provided "as is", without warranty of any kind.

**Work in progress.** This toolkit is under active development and we cannot guarantee it is free of defects. Interfaces and defaults may change between versions. If you rely on a particular behaviour, pin the commit you validated and re-validate after updating.

**Outputs are inputs to your judgement.** Strategy specifications, configurations, dry-run plans, rehearsal results and verification reports produced with this toolkit are aids for a careful operator. A passing check means the known checks passed; it is not a guarantee that a strategy is error-free, that a vault is safely configured, or that it will perform as modelled. Review generated plans before acting on them, and use the fork rehearsal as intended.

**Your keys, your funds.** Creating a vault requires a private key that you provide, fund and control. Anyone with that key controls the deployer account and, during the trial period, every role on the vault. Keep it in `.env`, never share it with a chat interface, and rotate it if it is ever exposed.

**Your strategy, your decisions.** Whoever designs, deploys and operates a vault is responsible for its design, its parameters, its operation and its outcomes, including compliance with the laws that apply to them. Market, liquidity, oracle, protocol-integration and smart-contract risks are inherent to any on-chain strategy and are outside the scope of this repository. Information about the IPOR Fusion contracts and their audits is available at [docs.ipor.io](https://docs.ipor.io).

**AI agents.** This repository is designed to be operated by AI agents. An agent can misread a specification, a chain state or an instruction. The human operator remains responsible for every transaction sent with their key; the agent's checks and confirmations do not transfer that responsibility.

**Not advice.** Nothing in this repository is financial, investment, legal or tax advice, and nothing here is an offer to buy, sell or hold any asset.

**Liability.** To the extent permitted by law, IPOR Labs AG and the contributors are not liable for any loss or damage arising from the use of this repository or its outputs, as set out in the license.

Questions, bug reports and improvements are welcome; see [`CONTRIBUTING.md`](./CONTRIBUTING.md).
