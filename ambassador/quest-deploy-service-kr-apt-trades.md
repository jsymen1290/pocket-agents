# Quest submission: Deploy a Working Pocket Service

**Service:** KR Apartment Trades (`kr-apt-trades-v1`)
**Network:** Pocket MainNet
**Preapproval:** confirmed by the Ambassador team on 14 November 2026

## What it is

KR Apartment Trades tells an agent what apartments in a Korean district actually sold for. It reads the Ministry of Land, Infrastructure and Transport's real-transaction register and returns each reported sale (complex, area, floor, price in KRW, price per m², broker or direct deal, cancellation flag), plus monthly medians and quartiles with cancelled deals left out. When a question is ambiguous, for example a district name that exists in two cities, it asks for clarification instead of guessing.

## Links

- Service ID: `kr-apt-trades-v1`
- Explorer: https://explorer.pocket.network/service/kr-apt-trades-v1
- Public docs (Agentic Portal page): https://agent.pocket.network/services/kr-apt-trades-v1
- Schemas and a captured example: https://agent.pocket.network/services/kr-apt-trades-v1/descriptor.json
- Paid endpoint through the portal: https://agent.pocket.network/v1/kr-apt-trades-v1 ($0.005 per call)

## On-chain facts

- Owner: `pokt139kcncr45rukhlzyncgua8ln2jpfjysyzu5t2c`
- Supplier serving it: `pokt10hhuv4yllh4yuvt32j4k093n99pr4vmr0exxxk`
- Compute units per relay: 50,000
- The service card (`pocket-service-card/v1`) is stored on chain in the service metadata and describes the API, its endpoints and a health check.

## Verified relay

Relays from real callers are claimed and settled on MainNet. One recent example:

- Claim transaction: `F768C8C736077FF04917CE3AE995B97B5AAB4A11D7E1BEE7021FAC2AB1DC4C48`
- Height 1016193, result code 0, 20 November 2026
- The batch includes the `kr-apt-trades-v1` session `b1dcb0f7b7ce…`, claimed amount 48,521 upokt

To check it yourself:

```bash
pocketd query tx F768C8C736077FF04917CE3AE995B97B5AAB4A11D7E1BEE7021FAC2AB1DC4C48 --network=main
pocketd query service show-service kr-apt-trades-v1 --network=main
```

In the seven days to 20 November 2026 it served about 2,000 relays, the most of any data service we own.

## Limits

- Data covers reported sales only. Recent months fill in as late reports arrive, and the response says so.
- It reports and summarises. It makes no forecast and gives no price opinion.

*Disclosure: I own the service and run its supplier.*
