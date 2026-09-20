# Pocket 에이전트 전수조사 (2026-09-20 10:20 KST) + GPT 추천 반영 (12:30 KST)

목적: 포털에 이미 올라갔거나 심사 중인 다른 사람들의 에이전트를 전부 보고, 우리가 선점할 빈 자리를 고른다.
출처: `pocketd query service all-services`(beta 58 / main 180), 카드 58건 디코드(`docs/portal-refs/beta-cards.json`),
포털 감사 API 58건(`docs/portal-refs/beta-audit.json`), 포털 MCP 참조문서 3편(`docs/portal-refs/*.md`).
"볼 수 있나"에 대한 답: **볼 수 있다.** 서비스·카드·서플라이어·정산은 전부 온체인이고, 포털 감사 API가 verdict를 공개한다.
비공개인 것은 PNF 내부의 "수락 manifest"(어느 서비스를 포털에 실었는지)뿐이다.

## 1. 경쟁 구도 (베타 = 포털이 보는 네트워크)

| 구분 | 수 | 비고 |
|---|---|---|
| 베타 서비스 | 58 (10:20) → 61 (12:30, 우리 3개 추가) | 소유자 12명 |
| 카드 있음 | 52 | 카드 없는 6개는 테스트/PNF 인프라 |
| 감사 PASS (9/9) | 17 (10:20) → 우리 4개 중 2 PASS + 2 정산 대기 | 한 빌더 40개 중 15 + agentsearch |
| 감사 FAIL | 41 | 대부분 A4(서플라이어 미스테이크) 또는 A7(정산 없음) |

한 소유자(`pokt1hqlr3zqlq…`)가 40개를 한 번에 올렸다(미국 규제·금융·컴플라이언스·EVM 데이터). 그 외 AgentSearch, text-generation/qwen3-embedding, pretty-charts/example-charts, **pocket-data-mcp**(우리와 같은 영역, 10:20 시점 FAIL: TLS 없음·정산 17일 전·스펙 없음 — 시점 한정 관측, 영구 상태 아님).

## 2. 빈 자리 → GPT 재해석 (2026-09-20, `POKT_AGENT_OPPORTUNITIES_RESEARCH_FULL_v1.0_20260920.json`)

GPT 핵심 논지: **"빈 카테고리의 단순 데이터 전달"보다 "다른 에이전트가 결과를 내기 전에 반복 호출하는 검증·분석 기능"을 선점한다.** 등록 목록의 빈자리 ≠ 시장 경쟁 부재·지불 의사. 빗썸 공식 MCP·업비트 Skills/CLI/SDK도 경쟁이며, 업비트 약관은 API 활용 프로그램의 유상 양도·배포를 제한한다(키 불필요 ≠ 유료 재판매 허용).

| GPT 순위 | 후보 | 우리 반영 상태 (12:30) |
|---|---|---|
| 1 | `pocket-operator-watch-v1` (D 확장: 정산 공백·설정 변경·수수료 잔액/claim·proof) | **구현** → `pokt-settlement-agent-v1`의 `GET /v1/supplier/{op}/ops` (별도 에이전트 아님, 카드 apis에 `-ops` 추가) |
| 2 | `kr-export-pulse-v1` (관세청 10일 수출 잠정치 + HS 월간, 수정 이력) | **키 대기** — data.go.kr serviceKey(무료, 자동승인) 필요 |
| 3 | `dart-kr-events-v1` (정정공시 차이·발행조건·시점별 유효 정보) | **키 대기** — OpenDART 인증키 필요 |
| 4 | `pine-integrity-v1` (재도색·미래정보·백테스트 왜곡) | **구현** → `pine-script-lint-v1`에 `POST /v1/integrity` + 규칙 P201~P212(서비스 ID는 불변이라 유지) |
| 5 | `agent-evidence-quality-v1` | 미착수 (자체 4개 서비스 품질검사에 먼저 적용 검토) |
| 6 | `api-semantic-drift-v1` | 미착수 (①의 출처 감시가 부분 커버) |
| 7 | `kr-market-integrity-v1` (자산 식별·시차·체결가능량·수수료) | **구현** → `kr-market-data-v1`에 `GET /v1/executable/{SYMBOL}?qty&side&fee_pct` + 티커 `data_lag_seconds` + 자산 식별 한계 명시. 유료 제공 권리는 미확인(GPT 조건부) |
| 8 | `material-evidence-check-v1` | 장기 |
| 장기 | kr-token-identity, data-asof-check, pocket-validator-evidence | ①·②·D의 기능으로 흡수 검토 |

GPT의 개발기간 추정 미채택, 500만 POKT 배분 변경 없음, 지속조사 루프는 제안 상태(활성화 안 함).

## 3. 포털이 요구하는 것 (참조문서 요약)
- 수락 규칙 A1~A9: 등록, 카드 스키마(4KiB), apis 고유, 서플라이어 스테이크, https 호스트명, TLS 응답, **정산 1건 이상(7일 내)**, 식별 프로브+기능 프로브+스펙 URL, 가격 95퍼센타일 이내.
- 설계 규칙 12개: 응답은 항상 JSON 객체, 입력은 본문으로만, 인증 없음, 성공 본문 앞 2KB에 error/fail 단어 금지, 세션 안에 응답, 프로브 3종, **결정성**, 요청별 식별.
- 순위/카탈로그: 가격과 감사 verdict로 배치. 순위 산식은 비공개(미확인).

## 4. 우리 서비스 현황 (12:30 KST, 베타)

| 서비스 | 공개 URL | 상태 |
|---|---|---|
| pokt-settlement-agent-v1 (+economics, +ops) | pokt-agent.com | 9/9 PASS, 메인넷도 등록(ops 카드 재서명은 사용자) |
| pokt-network-watch-v1 | watch.pokt-agent.com | 9/9 PASS, 메인넷 등록은 사용자 서명 대기 |
| kr-market-data-v1 (+executable) | kr.pokt-agent.com | 릴레이·claim·proof 완료, 정산 대기 |
| pine-script-lint-v1 (+integrity) | pine.pokt-agent.com | 릴레이·claim·proof 완료, 정산 대기 |

## 5. 다음 행동
1. 사용자: data.go.kr·OpenDART 키 발급 → Mac `.env`에 `DATA_GO_KR_SERVICE_KEY`, `OPENDART_API_KEY` → ②·③ 구현.
2. 지속조사: 매주 이 문서 기준으로 등록 목록·감사·경쟁자 상태 diff만 기록(의미 있는 변경 없으면 커밋 안 함). 자동화는 별도 승인.
3. 공개 저장소: https://github.com/jsymen1290/pocket-agents (2026-09-20 생성). GPT가 언급한 `Jayanng/PocketAgent`는 제3자(Johnson Jayanng)의 무관한 프로젝트로 확인됨.

## 6. 미확인
- 포털 순위 산식, PNF 수락 manifest, pocket-data-mcp 재정비 여부, 업비트·빗썸 데이터의 유료 제공 권리, 관세청·DART API의 실제 호출 조건.
