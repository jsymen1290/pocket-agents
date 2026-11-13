# For AI developers: let your agent check its own numbers before it answers

*A Pocket use case for people building LLM assistants and report writers that touch Korean finance. It states plainly what I ran and what I did not.*

## The problem every finance assistant has

A language model writes "Samsung Electronics' 2024 revenue was 258 trillion won and the base rate is 3.5%" with total confidence. The sentence is fluent and the units are plausible. The revenue is the previous year's and the rate is out of date. Retrieval helps, but it does not catch the model mixing 조 and 억, writing % where it means percentage points, or quoting last year's base rate.

What the agent needs is a second opinion that is not another language model: a tool that takes the draft and compares each number against the official record.

## The service

`kr-figure-check-v1` on the Agentic Portal ([page](https://agent.pocket.network/services/kr-figure-check-v1)). One operation, `POST /v1/check`, takes Korean text and returns every checkable number as `MATCH` or `MISMATCH` with the official value and its source:

- listed-company revenue and profit (OpenDART annual accounts)
- Bank of Korea base rate and treasury yields (ECOS)
- won exchange rates (Korea Eximbank)
- unit and notation errors: 조/억/만, % versus %p

Numbers it has no source for are left as unverified. It does not guess.

## The workflow

```
user question → agent drafts answer → call_service(kr-figure-check-v1, {text: draft})
   → MISMATCH? rewrite with the official value and cite the source → answer
```

With the portal's MCP server the agent finds and reads the tool by itself:

```json
{"name": "search_services", "arguments": {"query": "figure check"}}
```

I ran this against the hosted endpoint (`POST https://agent.pocket.network/mcp`) on 13 November 2026. It returned one service, `kr-figure-check-v1`, price `0.005000`. `describe_service` then gives the input schema: `text` (required, up to 20,000 characters), and optional `as_of`, `corp` and `year`.

## What a result looks like

I sent the draft sentence above to the service on 13 November 2026 (see the note below on how).

Request:
```json
{"text": "삼성전자의 2024년 연간 매출은 258조 원이었다. 한국은행 기준금리는 연 3.5%다.", "as_of": "2026-11-12"}
```

Response, shortened:
```json
{"verdict": "MISMATCH", "counts": {"MISMATCH": 2},
 "claims": [
  {"type": "RATE", "name": "한국은행 기준금리", "claimed": 3.5, "official": 3.0,
   "official_date": "2026-11-12", "status": "MISMATCH",
   "source": "Bank of Korea ECOS 722Y001/0101000"},
  {"type": "DART", "corp": "삼성전자", "year": "2024", "account": "매출액",
   "text": "258조 원", "claimed": 258000000000000,
   "official": {"CFS": 300870903000000, "OFS": 209052241000000},
   "status": "MISMATCH",
   "source": "OpenDART fnlttSinglAcnt 2024 annual report (11011)"}],
 "scope": "… other numbers are left unverified"}
```

Both numbers are caught, each with the value on record and where it comes from. The agent can now rewrite: consolidated 2024 revenue of about 300.9 trillion won, base rate 3.0% as of 12 November.

## Why this suits Pocket

- **The agent pays for itself.** $0.005 per check over x402, no API key to provision per deployment.
- **It is a guardrail, so it is called once per answer.** Per-call pricing fits; a subscription would not.
- **The checker is independent of the writer.** A different operator, a deterministic method, an official source on every line.

## What I tested and what I did not

- **Ran today:** `search_services` and `describe_service` on the hosted MCP endpoint (both free), and the check above.
- **How the check was run:** I own and supply this service, so I sent the request straight to my own backend, the same process that answers portal relays. It did not go through the portal's paid route.
- **Did not run:** a paid `call_service`. Through the portal the same JSON arrives under `data`, inside the envelope shown in the descriptor's captured example.

## Limits

- Korean text and Korean official sources only.
- Annual key accounts from DART only. It has no quarterly figures, forecasts or analyst estimates, so state the fiscal year in the text or pass `year`.
- `MATCH` means the number agrees with the record within a stated tolerance. It does not mean the sentence around it is true.
- The portal is best-effort with no SLA. An agent should answer without the check, and say so, when the call fails.

## References

- Descriptor: https://agent.pocket.network/services/kr-figure-check-v1/descriptor.json
- MCP server: https://github.com/pokt-network/agentic-portal-mcp
- Integration guide: https://agent.pocket.network/agent-integration
