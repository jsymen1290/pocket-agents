# Community feedback, October to November 2026: three concerns worth acting on

*Collected from public channels of the Pocket Network Discord. Each item links to the thread, describes the people by role, and ends with one concrete action. I have not edited anyone's meaning; quotes are short and exact.*

## 1. "I staked a supplier and nothing happens", and the answer took three days

**Where:** `#node-chat`, 1 to 4 October
[question](https://discord.com/channels/553741558869131266/564836328202567725/1555149782291324960) · [follow-up](https://discord.com/channels/553741558869131266/564836328202567725/1555553104664068107) · [answer](https://discord.com/channels/553741558869131266/564836328202567725/1556102441123053590)

**What was said:** a new operator with a 60k stake on `solana`, online for two weeks, reported two settlements of one relay each at 0.00 POKT. They had already done real diagnosis (looked up a live session, saw they were not seated). The next day they asked whether anybody was there. A team member answered on day three: the node also needs an application stake delegated to a gateway.

**Why it matters:** this is the first experience of the people Pocket most needs, operators who have already locked up the minimum stake. The question is predictable and the checks are mechanical.

**Action:** add a "Staked but no relays" page to the node-operator troubleshooting docs with the four checks in order (is an application staked on the service, am I in the session, is my endpoint an HTTPS hostname, how many relays per settlement), and pin it in `#node-chat`. A pinned link answers in seconds what took three days.

## 2. Holders do not know where POKT goes when the network is used

**Where:** `#community-chat`, 11 to 12 November
[question](https://discord.com/channels/553741558869131266/553757870123122708/1570161223994183802) · [reply](https://discord.com/channels/553741558869131266/553757870123122708/1570437378135302175)

**What was said:** a holder asked, in frustration, whether node operators simply sell what they earn. A team member replied that "Pocket isn't printing, it's deflationary" and that 2.5% is burned.

**Why it matters:** the reply is right but unsourced, and the same question returns every few weeks. The numbers behind it are public and small enough to fit in one picture: of every 100 POKT burned from an application, 97.5 are minted (79% supplier, 14% validators, 4.5% DAO, 2.5% service owner) and 2.5 leave supply.

**Action:** one canonical "Where 100 POKT goes" graphic on the tokenomics docs page, generated from `pocketd query tokenomics params` so it cannot go stale, and a saved reply in Discord that links to it. This is about making the mechanism checkable. It should say nothing about price.

## 3. Services exist on chain that nobody can call, and owners do not know what is missing

**Where:** `#new-services`, 19 October
[team comment](https://discord.com/channels/553741558869131266/1557427049507856414/1561741574487023689) · [listing rule](https://discord.com/channels/553741558869131266/1557427049507856414/1561741949311127583)

**What was said:** a Foundation member noted "we have a bunch of services created a while back, but we don't have the hardware to support them." A team member restated the rule for the portal: "No card, no listing."

**Why it matters:** two groups are looking for each other. Service IDs with no supplier are demand without supply; operators with spare hardware (see concern 1) are supply without demand. Meanwhile owners of new services learn about the service card only when the audit fails.

**Action:**
- Publish a short "suppliers wanted" list: registered service IDs that currently have zero staked suppliers, with the backend each one needs. The data is already on chain.
- Have the audit page link each failing rule to the exact doc section and a copy-paste card template, so "No card, no listing" comes with the card.

## Summary

| # | Concern | Who is affected | Smallest fix |
|---|---|---|---|
| 1 | Staked, no traffic, slow answer | New suppliers | One troubleshooting page, pinned |
| 2 | Token flow not understood | Holders | One sourced graphic and a saved reply |
| 3 | Unserved services, unclear listing steps | Service owners, idle operators | "Suppliers wanted" list; audit links to templates |

All three are documentation and signposting, not protocol changes, and none needs governance.

*Collected by jsymen. Threads read on 13 November 2026.*
