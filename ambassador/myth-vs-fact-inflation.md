# Myth vs. fact: "Pocket prints new POKT to pay node runners"

**The myth.** Suppliers are paid from freshly printed tokens, so the more the network is used, the more the supply grows.

**The fact.** On Shannon, a supplier is paid only when an application's stake is burned first, and slightly less is minted than was burned. Usage shrinks supply; it does not grow it.

## How to check it yourself

```bash
pocketd query tokenomics params --network main
```

Read on 13 November 2026:

```
mint_ratio: "0.975"
mint_equals_burn_claim_distribution:
  supplier:     "0.79"
  proposer:     "0.14"
  dao:          "0.045"
  source_owner: "0.025"
```

When a session settles, the chain burns the cost of the relays from the application that sent them, then mints `mint_ratio` of that amount and splits it.

## The same thing with 100 POKT

| | POKT |
|---|---|
| Burned from the application's stake | 100.000 |
| Minted | 97.500 |
| → suppliers (79%) | 77.025 |
| → block proposer and validators (14%) | 13.650 |
| → DAO (4.5%) | 4.3875 |
| → service owner (2.5%) | 2.4375 |
| **Net change in supply** | **−2.500** |

Nobody is paid unless that first row happens. No traffic means no burn and no mint.

## Where the myth comes from

It was true of the earlier network. Before Shannon, suppliers were paid by minting new POKT for the relays they served, and no application stake was burned to cover it, so usage did add to supply. Older articles and videos still describe that model. The burn-then-mint settlement above replaced it.

## Two honest footnotes

- There is a second, very small parameter, `global_inflation_per_claim`, currently `0.000001`. It mints one-millionth of each settlement on top (split 80% supplier, 10% DAO, 10% service owner). On the 100 POKT above that is 0.0001 POKT, against 2.5 removed. It does not change the sign.
- All of these are governance parameters. They can be changed by a vote, which is why the command matters more than this page.

## What this fact does not say

It describes a mechanism, not a market. Whether supply shrinks quickly or slowly depends entirely on how much paid traffic the network carries, and nothing here says anything about price.

## Sources

- [POKT Tokenomics](https://docs.pocket.network/token/tokenomics)
- Live: `pocketd query tokenomics params --network main`
