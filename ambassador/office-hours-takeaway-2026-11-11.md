# Office Hours takeaway (11 November 2026): service owners can set their own price from 23 November

*Source: the team's written recap of the 11 November Office Hours, [posted in `#community-chat`](https://discord.com/channels/553741558869131266/553757870123122708/1570684758063845438). I am working from that recap, not from a recording, and I mark below where a detail was described as not final.*

## The one thing I took away

**From 23 November, a service owner may raise the compute units per relay (CUPR) of their own service, and should tell the team first so the Agentic Portal price is adjusted to match.**

Until now most data services sat at whatever CUPR they were registered with, and the portal charged a flat $0.005 per call for everything. This change turns CUPR into what it was designed to be: the owner's price tag.

## Why it matters, in numbers

What an application burns for one relay is fixed arithmetic (`pocketd query shared params --network main`):

```
CUPR × 153,156 ÷ 1,000,000 = upokt burned per relay
```

| Service | CUPR today | Burned per relay |
|---|---|---|
| `eth` | 1,579 | 0.00024 POKT |
| `solana` | 5,033 | 0.00077 POKT |
| `kr-apt-trades-v1` (mine) | 50,000 | 0.00766 POKT |
| `literature-search` | 100,000 | 0.01532 POKT |

Of each burn, 97.5% is minted back and split 79% supplier, 14% validators, 4.5% DAO, 2.5% service owner. So CUPR is the only dial that changes what a supplier and an owner earn per call. A service that does expensive work (a paid upstream API, a large model, heavy parsing) and a service that returns a cached number were paid alike unless the owner had set CUPR deliberately.

The "tell the team" step is the important part. The portal sells calls in USDC and the team converts that to POKT to keep the application stakes funded. If an owner doubles CUPR and the portal price stays put, each call burns twice the POKT for the same USDC. Raising CUPR without coordinating would drain the stake that pays everyone.

## Three smaller points from the same session

- **EVM services were re-priced to an Ethereum baseline.** The recap notes that billed compute units for some chains may fall without usage falling. Anyone comparing weekly CU totals across this change should compare relay counts instead.
- **Venice AI integration is targeted for 23 November** as a private-inference option.
- **Paying in POKT may be discounted.** A discount was discussed with figures that were explicitly not confirmed, so I am not repeating them as fact.

## What I am doing with it

- The portal-listed services I own all sit at 50,000 CU. Before 23 November I am writing down, per service, what one call actually costs me upstream, so a CUPR request to the team is a number with a reason.
- For suppliers: check the CUPR of what you serve after the 23rd. `pocketd query service show-service <id> --network main` shows it, and the formula above gives the per-relay figure in seconds.

## Question I would ask next time

When an owner changes CUPR mid-session, which value applies to relays already served in that session: the value at session start or at settlement?
