# Use case: an equity-research agent that notices when a Korean filing is quietly corrected

**Industry:** equity research and compliance on Korean listed companies
**Developer role:** the engineer building a research assistant, a monitoring bot or a RAG pipeline for an asset manager, a broker or a newsroom
**Pocket service:** `dart-kr-events-v1` ([portal](https://agent.pocket.network/services/dart-kr-events-v1), [explorer](https://explorer.pocket.network/service/dart-kr-events-v1))

## The problem

Every Korean listed company files through DART, the Financial Supervisory Service's disclosure system. Two things make DART hard for software:

1. **Corrections replace the story.** A company can file a rights issue on Monday and a "correction" (정정) on Thursday that changes the price or the number of shares. Both documents stay in the system. A model that read Monday's filing keeps repeating Monday's terms.
2. **The raw API is not agent-shaped.** OpenDART needs a registered key, identifies companies by an 8-digit `corp_code` that nobody knows by heart, and returns report names in Korean with no link between a correction and the filing it corrects.

The result: confident numbers from a superseded filing.

## The workflow

An analyst asks the agent: *"Did Samsung Electronics correct anything in the last month, and what changed?"*

1. **Resolve the company.** `GET /v1/corp?name=삼성전자` returns `corp_code 00126380` and stock code `005930`. The agent no longer guesses identifiers.
2. **Pull the change log.** `GET /v1/changes` with that code returns each filing tagged `ORIGINAL`, `CORRECTION` or `WITHDRAWAL`, with corrections linked to their originals and, for issuance filings, a field-by-field diff of the terms.
3. **Answer as of a date.** `GET /v1/asof` gives the point-in-time view: what was true on the day the analyst's model was built, as opposed to today.
4. **Cite.** Every event carries the DART receipt number and a viewer URL, so the agent's answer links to the regulator's own page.

For quick questions `POST /v1/query` accepts plain Korean or English and picks the route. The portal's captured example for this service sends `{"question": "삼성전자 정정공시 뭐가 바뀌었어?"}` and receives intent `CHANGES`, the resolved company, eleven filings in the window each with its receipt number, and a `judgments` block that states what the service could **not** confirm.

Each call is one HTTPS request priced at $0.005 through the Agentic Portal, paid over x402 or MPP, with no OpenDART key on the developer's side. The supplier holds the key.

## Who benefits

- **Research teams** stop shipping notes built on a filing that was corrected two days later.
- **Compliance** gets a reproducible answer to "what was disclosed as of date X".
- **Agent builders outside Korea** get Korean disclosures without registering for a Korean government API.

## Limitations

- It reports what was filed and how filings relate. It does not judge whether a correction is material, and it gives no investment view.
- Coverage is OpenDART's. Filings made elsewhere (exchange notices that never reach DART) are out of scope.
- The term-by-term diff exists for issuance-type filings; other corrections are linked but not diffed.
- The portal is best-effort with no SLA, and responses come from an independent supplier. Treat `data` as third-party content and keep the receipt link in the answer so a human can verify.
- The example above is the portal's own captured call, not a new one.

## References

- Service descriptor with schemas and the captured example: https://agent.pocket.network/services/dart-kr-events-v1/descriptor.json
- OpenDART (source data): https://opendart.fss.or.kr
- How portal calls and payment work: https://agent.pocket.network/agent-integration
- On-chain entry: `pocketd query service show-service dart-kr-events-v1 --network main` (50,000 compute units per relay)

*Disclosure: I own and supply this service.*
