"""Step 01b: grant configuration roles to the deployer (initial_owner).

After `clone`, only OWNER_ROLE is set on initial_owner. To run all subsequent
config steps (add_fuses, grant_substrates, fees, pre-hooks, etc.) the signer
needs the corresponding role. We grant the deployer every role from
`cfg.roles.grants` so the same signer can carry out the full pipeline.

Idempotency and the RPC propagation race are handled via `role_helpers`:
hasRole reads bypass the SDK wrapper, and after each grant we poll until the
RPC reports the role as visible — otherwise a subsequent dependent grant
(e.g. ALPHA whose admin is ATOMIST) may fail eth_estimateGas on a backend
that hasn't yet seen the prior tx.

s11_roles runs at the end and grants the JSON-target accounts in addition.
"""
from __future__ import annotations

from ipor_fusion.core.access import AccessManager
from web3 import Web3

from deploy.role_helpers import has_role_raw, wait_for_member
from deploy.roles import resolve_role_id

NAME = "01b_bootstrap_roles"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    access = AccessManager(session.ctx, instance["access_manager"])
    web3 = session.ctx.web3
    am_address = Web3.to_checksum_address(instance["access_manager"])
    deployer = Web3.to_checksum_address(session.signer)
    granted = 0
    skipped = 0
    failed = []
    tx_hashes = []
    seen = set()
    for grant in cfg.raw["roles"]["grants"]:
        role_name = grant["role"]
        role_id = resolve_role_id(role_name)
        if role_id in seen:
            continue
        seen.add(role_id)
        rec = session.recorder.add(
            NAME, action="grantRole(bootstrap)", key=role_name,
            target=am_address, function="grantRole(uint64,address,uint32)",
            args={"role": role_name, "role_id": role_id, "account": str(deployer), "delay": 0},
        )
        if has_role_raw(web3, am_address, role_id, deployer):
            skipped += 1
            rec.skipped = True
            rec.note = "deployer already holds role"
            continue
        if not broadcast:
            granted += 1
            continue
        try:
            receipt = access.grant_role(role_id, deployer, 0).send()
            tx_hash = receipt["transactionHash"].hex()
            tx_hashes.append(tx_hash)
            rec.executed = True
            rec.tx_hash = tx_hash
            granted += 1
        except Exception as e:
            print(f"[{NAME}]   skip {role_name} ({role_id}): {e}")
            failed.append(role_name)
            continue
        if not wait_for_member(web3, am_address, role_id, deployer):
            print(f"[{NAME}]   warning: {role_name} ({role_id}) not visible after grant — subsequent dependent grants may flake")
    print(f"[{NAME}] deployer={deployer} granted={granted} already_had={skipped} failed={len(failed)}")
    if broadcast:
        # accounts recorded so hardening can probe role holders without eth_getLogs
        state.record(NAME, tx_hashes=tx_hashes, notes={"accounts": [str(deployer)]})
        if failed:
            raise RuntimeError(
                f"{NAME}: failed to grant {failed} — fix manually (likely role-admin not yet held by deployer) "
                f"then resume with --from-step 2"
            )
