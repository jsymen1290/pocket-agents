# Demand brief: "Is the Korean market open on this day?" as a Pocket service

**Proposed service:** a Korea Exchange (KRX) trading-calendar API for agents and trading systems
**Status on Pocket:** not available. I searched the Agentic Portal catalogue (120 services, registry `5e5430de26daa9f9`, 2026-11-13) for `calendar`, `holiday`, `trading day` and `market hours`. Nothing serves an exchange calendar for any country.

## The developer need

Any system that touches Korean equities has to know which days KRX trades: backtests need the right number of sessions, schedulers must not fire orders into a closed market, and settlement dates are counted in trading days. Developers get this from open-source calendar libraries, and for Korea those libraries are repeatedly wrong, because the Korean calendar changes by government decision after the library version was released.

## Evidence of real demand

**Source 1: `exchange_calendars` issue #611, "2026 Constitution Day is missing from XKRX" (opened 2026-10-05, still open).**
https://github.com/gerrymanoim/exchange_calendars/issues/611
Constitution Day became a public holiday again from 2026. A developer found the most widely used Python calendar library still treats it as a trading day.

**Source 2: the same pattern, every time Korea adds a holiday.**
- #420, "XKRX - October 1, 2024, Designated as a Temporary Public Holiday": https://github.com/gerrymanoim/exchange_calendars/issues/420
- #366, "XKRX - 2024 Election Day missing in calendar": https://github.com/gerrymanoim/exchange_calendars/issues/366
- #128, "Revert XKRX lunar/solar calendar based holidays to hardcoded dates" (open since 2022): https://github.com/gerrymanoim/exchange_calendars/issues/128

Different reporters, different years, one cause: temporary holidays, election days and lunar-calendar holidays are announced during the year, and a pinned library cannot know them. Each issue was filed by someone whose code produced a wrong result first.

## Why a Pocket service fits

| Need | Library | Service |
|---|---|---|
| A holiday announced last week | wrong until a new release is installed | correct as soon as the supplier updates |
| An AI agent asking in plain language | must write and run code | one HTTP call |
| Proof of where the answer came from | none | source notice and fetch time in the response |

The data is small, public and changes a few times a year, which is cheap to serve and valuable to get right.

## Proposed shape

- `GET /v1/is-open?date=2026-07-17` → `{open: false, reason: "Constitution Day", source: "...", announced: "..."}`
- `GET /v1/sessions?from=&to=` → trading days in a range, with late opens (the first trading day of the year and the college entrance exam day start late)
- `GET /v1/changes?since=` → holidays added or removed since a date, so a cached calendar can be patched
- `POST /v1/query` → the same in Korean or English

Every response would carry the official source (the government gazette notice or the KRX notice) and when it was fetched.

## Who would call it

- Backtesting and quant platforms that support Korean equities
- Broker and robo-adviser schedulers
- AI assistants answering "can I trade tomorrow?" or "when does this order settle?"
- Maintainers of the calendar libraries themselves, as a change feed

## Risks and open questions

- Low call volume per user: a calendar is read once a day. Revenue depends on many integrators, not heavy ones.
- KRX's own notices are in Korean and not machine-readable; the supplier carries the parsing risk and must show the source so errors are visible.
- Near-duplicates: none on the portal today. A general "market calendar for 50 exchanges" service would overlap, so the listing should be scoped to Korea, where the supplier can verify sources.

## Next step

I proposed this service (`kr-market-calendar-v1`) to the ambassador team on 2026-11-13 as a candidate for the "Deploy a Working Pocket Service" quest. This brief is the demand case for it.
