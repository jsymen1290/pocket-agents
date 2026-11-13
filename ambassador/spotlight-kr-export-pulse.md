# Service spotlight: KR Export Pulse, the earliest read on global chip demand

**Service:** `kr-export-pulse-v1` · [portal page](https://agent.pocket.network/services/kr-export-pulse-v1) · [explorer](https://explorer.pocket.network/service/kr-export-pulse-v1)
**Category:** finance · **Price:** $0.005 per call · **Compute units per relay:** 50,000

## What it does

South Korea publishes its export totals three times a month: after the 10th, the 20th and month end. Because Korea ships a large share of the world's memory chips, cars and ships, economists treat these 10-day figures as one of the first hard data points on global trade each month, weeks ahead of most countries' numbers.

The figures come from the Korea Customs Service through a Korean government API, with Korean item names and one catch that trips people up: the 1st-to-10th figure of this month must be compared with the 1st-to-10th figure of another month, not with a full month.

KR Export Pulse does that work and returns:

- `GET /v1/pulse`: the latest period, change versus the same stage of the prior month and prior year, and which items drove the change
- `GET /v1/tenday`: the 10-day series for ten major items (semiconductors, cars, ships, steel and others)
- `GET /v1/hs?hs=`: monthly trade by HS code and country
- `GET /v1/revisions`: a log of figures that were later revised
- `POST /v1/query`: the same in plain Korean or English

## What an answer looks like

The portal's captured example (29 September 2026) asks, in Korean, how semiconductor exports changed from the previous month. The response covers 1 to 20 September, marked `provisional: true`:

| | USD thousand |
|---|---|
| Total exports, 1st to 20th | 71,409,349 |
| of which semiconductors | 34,128,119 |
| Change vs. same 20 days of August | +29.32% |
| Change vs. same 20 days of September 2025 | +78.3% |

It also returns the rules it applied, including "same stage compared (1-10 vs 1-10, month vs month)" and a warning that item contributions are a subset and do not sum to 100.

## Who it is for

- **Macro and semiconductor analysts** who want the number minutes after release without parsing a Korean API.
- **AI assistants** asked "how are Korean chip exports doing?", which otherwise answer from a news article of unknown date.
- **Dashboards** that need the revision log, so last month's chart does not silently change.

## What I like about it as a Pocket service

It is honest about uncertainty. Provisional data is labelled provisional, comparisons state their basis, and revisions are recorded instead of overwritten. That is the behaviour an agent needs from a data source it cannot question.

## Where to learn more

- Schemas and the full captured example: https://agent.pocket.network/services/kr-export-pulse-v1/descriptor.json
- How to pay for a call: https://agent.pocket.network/agent-integration
- Source data: Korea Customs Service, via the Korean public data portal (data.go.kr)

## Limits

- Korean exports only; it is a signal about global demand, not a measurement of it.
- Ten-day figures are provisional and are revised.
- It reports and compares. It makes no forecast and no market call.

*Disclosure: I own and supply this service. The figures above are from the portal's published example, not a new call.*
