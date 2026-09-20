"""Prepare-only helpers: print the exact files/commands the USER runs with pocketd.

This module never executes pocketd, never touches a keyring and never reads a mnemonic. The
output is text the user copies into a terminal on the machine that holds the key. Commands follow
https://docs.pocket.network/node-operators/supplier-staking/ and
https://docs.pocket.network/validators/delegation/ (fetched 2026-09-19).
"""
from .chain import UPOKT, is_address, is_valoper

FEES_UPOKT = 200000  # documented example fee for staking txs (0.2 POKT)


def pokt_to_upokt(pokt):
    s = str(pokt).strip()
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, ""
    if not whole.isdigit() or not (frac == "" or frac.isdigit()) or len(frac) > 6:
        raise ValueError("bad POKT amount %r" % pokt)
    return int(whole) * UPOKT + int((frac + "000000")[:6])


def operator_key_commands(key_name="pokt-operator"):
    return "\n".join([
        "# 1) Operator key on the Mac (hot key; holds only gas, never the stake). Run yourself; the mnemonic is shown ONCE.",
        "pocketd keys add %s --keyring-backend file" % key_name,
        "pocketd keys show %s -a --keyring-backend file   # -> pokt1... operator address (public, safe to share)" % key_name,
        "",
        "# 2) Owner address = your self-custody wallet (Ledger+Keplr, Soothe Vault or Keplr/Leap). Never import it here.",
        "#    Send ~1 POKT of gas from the owner to the operator address after it exists.",
    ])


def delegate_command(validator, pokt, key_name="pokt-owner", node=None):
    if not is_valoper(validator):
        raise ValueError("validator must be poktvaloper1...")
    up = pokt_to_upokt(pokt)
    node_flag = " --node=%s" % node if node else ""
    return "\n".join([
        "# Track A: delegate %s POKT (%d upokt) to %s. Unbonding 21 days (1814400s)." % (pokt, up, validator),
        "# Dry-run first (no broadcast):",
        "pocketd tx staking delegate %s %dupokt --from=%s --network=main --fees=%dupokt --keyring-backend file --dry-run%s" % (validator, up, key_name, FEES_UPOKT, node_flag),
        "# Unsigned tx for a hardware/offline signer:",
        "pocketd tx staking delegate %s %dupokt --from=%s --network=main --fees=%dupokt --generate-only%s > delegate.unsigned.json" % (validator, up, key_name, FEES_UPOKT, node_flag),
        "# Only after you have read the dry-run output and decided: drop --dry-run / --generate-only to broadcast.",
    ])


def supplier_stake_config(owner, operator, stake_pokt, services, rev_share=None):
    """YAML text for `pocketd tx supplier stake-supplier --config`.
    services: list of dicts {service_id, url, rpc_type}. rev_share: {address: percent} (must sum to 100)."""
    if not is_address(owner) or not is_address(operator):
        raise ValueError("owner/operator must be pokt1...")
    up = pokt_to_upokt(stake_pokt)
    lines = ["owner_address: %s" % owner, "operator_address: %s" % operator, "stake_amount: %dupokt" % up]
    if rev_share:
        total = sum(int(v) for v in rev_share.values())
        if total != 100:
            raise ValueError("rev_share must sum to 100, got %d" % total)
        lines.append("default_rev_share_percent:")
        for addr, pct in rev_share.items():
            lines.append("  %s: %d" % (addr, int(pct)))
    lines.append("services:")
    for s in services:
        lines += ["  - service_id: %s" % s["service_id"],
                  "    endpoints:",
                  "      - publicly_exposed_url: %s" % s["url"],
                  "        rpc_type: %s" % s.get("rpc_type", "JSON_RPC")]
    return "\n".join(lines) + "\n"


def supplier_stake_commands(config_path, operator_key="pokt-operator"):
    return "\n".join([
        "# Track C: stake a supplier (min_stake is read from chain params in STATE.json; 59,500 POKT on 2026-09-19).",
        "pocketd tx supplier stake-supplier --config %s --from=%s --network=main --fees=%dupokt --keyring-backend file --dry-run" % (config_path, operator_key, FEES_UPOKT),
        "# The stake is debited from owner_address; the tx must be signed by the OWNER key for the initial stake,",
        "# then the operator key can update services. Check the doc page before broadcasting: the exact signer rule is enforced on-chain.",
        "pocketd query supplier show-supplier <operator pokt1...> --network=main   # verify after inclusion",
    ])


def relayminer_config(operator_key, smt_store, services, node_rpc="https://sauron-rpc.infra.pocket.network", node_grpc="sauron-grpc.infra.pocket.network:443"):
    """Minimal RelayMiner YAML per docs/node-operators/relayminer-setup (public full node for the pilot; own full node for production)."""
    lines = ["default_signing_key_names:", "  - %s" % operator_key, "smt_store_path: %s" % smt_store,
             "pocket_node:", "  query_node_rpc_url: %s" % node_rpc, "  query_node_grpc_url: %s" % node_grpc, "  tx_node_rpc_url: %s" % node_rpc,
             "suppliers:"]
    for s in services:
        lines += ["  - service_id: %s" % s["service_id"],
                  "    listen_url: http://0.0.0.0:%d" % int(s.get("listen_port", 8545)),
                  "    service_config:",
                  "      backend_url: %s" % s["backend_url"]]
    return "\n".join(lines) + "\n"


def add_service_commands(service_id, name, cu_per_relay, card_path, key_name="pokt-owner"):
    """Register (or update) a service with an on-chain card. Docs: /services/register/ (2026-09-19).
    Fee: add_service_fee governance param (MainNet 1,000 POKT observed 2026-09-19), charged once on creation."""
    import re as _re
    if not _re.match(r"^[A-Za-z0-9_-]{1,42}$", service_id):
        raise ValueError("service id must be 1-42 chars of a-z A-Z 0-9 - _ (immutable)")
    return "\n".join([
        "# Register service %r (id is immutable; check https://explorer.pocket.network/services for collisions first)" % service_id,
        "pocketd query service params --network=main                      # add_service_fee (1,000 POKT on 2026-09-19)",
        "pocketd tx service add-service %s \"%s\" %s --card-file %s --from=%s --network=main --gas auto --gas-prices 1upokt --gas-adjustment 1.5 --keyring-backend file --dry-run" % (service_id, name, cu_per_relay, card_path, key_name),
        "# drop --dry-run to broadcast; re-run the same command later (omit --card-file to keep the card) to update pricing/name",
        "pocketd query service show-service %s --network=main             # verify after inclusion" % service_id,
    ])
