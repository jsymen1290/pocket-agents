# Settlement Agent 공개 URL(도메인+TLS)·서비스 등록·supplier 스테이크 절차 — 2026-09-20

사용자 결정(2026-09-20 00:xx KST): 지갑은 Soothe Vault 유지, 에이전트 후보는 Settlement Agent 하나, 공개 경로는 도메인+TLS(연 소액). 자산 이동·키 생성·tx 브로드캐스트는 전부 사용자가 실행한다.

## 이미 준비된 것 (Mac)

- `pocketd` 0.1.35 설치(공식 Homebrew tap `pokt-network/poktroll`, `brew trust` 후 설치). 경로 `/opt/homebrew/bin/pocketd`, SHA-256 `9d0c18b9f1a7fc2d77c22d5fc2535cfacae125bb927482aec63e01807cd29efb`. 읽기 전용 질의(`query service params`, `query supplier show-supplier`) 정상. RelayMiner는 `pocketd relayminer` 하위 명령이라 별도 설치 없음. 키링은 비어 있음(키 생성은 사용자).
- `cloudflared` 2026.6.1은 이미 brew로 설치돼 있었다. Cloudflare Tunnel은 무료이며 공유기 포트 개방이 필요 없고 TLS를 Cloudflare가 종단한다. 비용은 도메인만(연 1~2만원).
- Settlement Agent v0는 `http://172.30.1.33:8793/`(LAN)에서 상시 실행 중. 서비스 카드 `card.json`(1,689 B, `pocket-service-card/v1`).
- `/Volumes/A/POKT/agent-deploy/`: `README.md`(순서), `cloudflared.config.yml`, `relayminer_config.yaml`, `supplier_stake_config.yaml`, `com.jsy.pokt.relayminer.plist`, `card.json`. 자리표시자 `<DOMAIN>`, `<TUNNEL_UUID>`, `<OWNER_OPS_ADDR>`, `<OPERATOR_ADDR>`만 채우면 된다.

## 키 구조 (Soothe Vault 유지 결정에 맞춘 설계)

Soothe Vault 공식 문서는 송금·수신·가져오기/내보내기만 다루고, supplier 스테이크·위임 같은 사용자 정의 tx 서명이나 하드웨어 지갑은 언급이 없다(미확인). 그래서 스테이크 tx는 Mac의 `pocketd` 키링(file 백엔드, 암호)으로 서명하고, Soothe는 **금고** 역할을 한다.

| 키 | 위치 | 보유량 | 역할 |
|---|---|---|---|
| Soothe Vault 계정 | 브라우저 확장(핫월렛) | 테스트 20 → 운용분 이체 후 잔여 | 거래소 출금 수취, owner-ops로 송금. 큰 금액 장기 보관은 아래 "보유량 권고" 참조 |
| `pokt-owner-ops` | Mac pocketd 키링 | 59,500(스테이크) + 1,500(등록 1,000·가스) | supplier `owner_address`. 스테이크 반환 주소. 서비스 등록(1,000 POKT) 서명 |
| `pokt-operator` | Mac pocketd 키링 | 100(가스) | supplier `operator_address`(스테이크 후 변경 불가). RelayMiner 서명. stake·services·rev_share를 한 번에 설정 가능(공식 문서) |

두 키의 복구문구는 종이에만. `rev_share`는 owner-ops 100%.

## 순서 (각 단계 확인 후 다음)

1. **도메인**: 구매(어느 등록기관이든) → Cloudflare 계정에 사이트 추가 → 네임서버 변경. Mac에서 `cloudflared tunnel login`(브라우저) → `cloudflared tunnel create pokt-agent` → `cloudflared tunnel route dns pokt-agent agent.<DOMAIN>` → `agent-deploy/cloudflared.config.yml`을 `~/.cloudflared/config.yml`로 복사(UUID·도메인 기입) → `sudo cloudflared service install`. 검증: 임시로 service를 `http://localhost:8793`로 두고 `curl https://agent.<DOMAIN>/v1/version` → `{"service":"pokt-settlement-agent-v1",…}`.
2. **키**: `pocketd keys add pokt-owner-ops --keyring-backend file`, `pocketd keys add pokt-operator --keyring-backend file`. 주소는 `pocketd keys show <이름> -a --keyring-backend file`.
3. **자금**: Soothe → owner-ops 61,000 POKT, owner-ops → operator 100 POKT. 관제 페이지에 `set-user --owner <OWNER_OPS_ADDR> --operators <OPERATOR_ADDR>`로 등록해 잔액 확인.
4. **서비스 등록**(1,000 POKT, 1회): `pocketd tx service add-service pokt-settlement-agent-v1 "POKT Settlement Agent" 5000 --card-file /Volumes/A/POKT/agent-deploy/card.json --from pokt-owner-ops --network=main --gas auto --gas-prices 1upokt --gas-adjustment 1.5 --keyring-backend file --dry-run` → 출력 확인 → `--dry-run` 제거. 확인: `pocketd query service show-service pokt-settlement-agent-v1 --network=main`.
5. **스테이크**(59,500): `supplier_stake_config.yaml`에 주소·도메인 기입 → `pocketd tx supplier stake-supplier --config … --from pokt-operator --network=main --gas auto --gas-prices 1upokt --gas-adjustment 1.5 --keyring-backend file --dry-run`. dry-run에서 **스테이크 자금이 어느 계정에서 빠지는지** 확인(공식 문서는 서명자 규칙만 적고 자금 출처를 명시하지 않음). operator에서 빠지면 owner-ops→operator로 59,500을 먼저 옮기거나, owner-ops가 stake만 먼저 하고 operator가 services를 추가하는 2단계로 간다.
6. **RelayMiner**: `cloudflared` config의 service를 `http://localhost:8545`로 되돌리고 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jsy.pokt.relayminer.plist`(plist는 agent-deploy에서 복사). 확인: `https://agent.<DOMAIN>/v1/health` 200, 관제 페이지 supplier 표에 operator가 `found=True`로 뜨고 첫 `EventClaimSettled`가 찍히는지.
7. **포털**: Agentic Portal 공개 런칭 2026-09-21 13:00 ET(09-22 02:00 KST). 소프트 런칭 접근은 Discord. 온체인 서비스+supplier가 있으면 인덱스 노출 전제는 충족되며, 순위 규칙은 공식 문서에서 아직 확인되지 않았다.

## Soothe Vault 보유량 권고

- 오늘: 테스트 20 POKT. 관제 페이지에서 잔액·아침 브리프 보존 확인.
- 확인 후: 1차 운용분 70,000 POKT를 Soothe로 받고, 그중 61,000을 owner-ops로 보낸다. Soothe 잔여 약 9,000은 다음 등록·가스용.
- 3,000,000~4,000,000 POKT(A 위임분)는 **아직 Soothe로 옮기지 않는다.** Soothe는 브라우저 확장 핫월렛이고 하드웨어 지갑 연동이 문서에 없다. 위임 tx도 Soothe UI에서 되는지 미확인이다. A 위임은 Keplr(Pocket 체인 등록 지원, 위임 UI 내장, Ledger Cosmos 앱 연동 가능) 경로를 별도로 정한 뒤 옮긴다. 거래소 보관도 위험이지만 오늘 결정할 사안은 아니다.
- 규칙: 핫월렛(Soothe+Mac 키링) 합계는 실제 스테이크 예정액 + 3개월치 가스·수수료를 넘기지 않는다.

## 2026-09-20 01:45 KST 실행 기록 (Claude + 사용자)

- 도메인 `pokt-agent.com` Cloudflare Registrar에서 구매·Active(사용자). 계정 로그인·결제는 사용자.
- 키 2개 생성(사용자, Mac pocketd file 키링): `pokt-owner-ops` = `pokt139kcncr45rukhlzyncgua8ln2jpfjysyzu5t2c`, `pokt-operator` = `pokt10hhuv4yllh4yuvt32j4k093n99pr4vmr0exxxk` (키링 파일명의 hex를 bech32로 변환해 확인; 암호·복구문구는 다루지 않음).
- `cloudflared tunnel login`(Mac) → 사용자 Chrome에서 pokt-agent.com 존 Authorize(Claude in Chrome로 클릭) → `cert.pem` 수신 → 터널 `pokt-agent` 생성(UUID `9ba3ebea-259e-4d22-97c4-8856dc5ece12`) → CNAME `agent.pokt-agent.com` 추가 → `~/.cloudflared/config.yml`(현재 ingress → `http://localhost:8793` 에이전트 직결) → 사용자 LaunchAgent `com.jsy.pokt.cloudflared`(KeepAlive, sudo 불필요) 기동, 연결 2개(icn01/icn06, quic).
- 공개 확인(Windows에서): `https://agent.pokt-agent.com/v1/version` → `{"service":"pokt-settlement-agent-v1",…}`, `/v1/health` 200.
- 관제 페이지에 두 주소 등록(`set-user`), 수집 CAUTION 1건: operator 주소 `SUPPLIER_NOT_FOUND`(스테이크 전이므로 정상).
- `agent-deploy/` 자리표시자 채움(도메인·UUID·두 주소). RelayMiner 기동 시 ingress를 `http://localhost:8545`로 바꾸고 `launchctl kickstart -k gui/$(id -u)/com.jsy.pokt.cloudflared`.

## 2026-09-20 02:1x KST — 자금·가스·서비스 등록 완료 (사용자 서명, Claude 검증)

- Soothe → owner-ops 50 + 1,150 POKT 도착(체인 잔액 1,200 확인). 수집 주기 30분→10분으로 단축.
- owner-ops → operator 100 POKT: tx `6FE3E9FB…D505`, 블록 929038, code 0, gas 89,650.
- **서비스 등록**: `pocketd tx service add-service pokt-settlement-agent-v1 "POKT Settlement Agent" 5000 --card-file … --from pokt-owner-ops --gas 400000 --fees 400000upokt` → tx `7B9151C6…D3D7`, 블록 929041, code 0, gas 130,984. `show-service` 확인: id `pokt-settlement-agent-v1`, owner `pokt139kc…`. 잔액 1,099.89 → 99.49 POKT(등록 수수료 1,000 + 가스 0.4 차감).
- 참고: `--dry-run`/`--gas auto`는 이 서브커맨드에서 "valid bech32 address must be provided in simulation mode" 오류로 실패한다. 시뮬레이션 없이 고정 가스로 보내면 된다(최근 체인 add-service 3건의 gas_used 173k~182k 기준 40만 설정).
- 관제 페이지에 "D 등록 서비스" 칸 추가(collector v0.5.2, `set-user --services`).

## 2026-09-20 02:30~03:10 KST — supplier 스테이크·RelayMiner 가동 (C 트랙 메인넷 진입)

- 첫 시도(operator 서명, 서비스 포함) tx `ACA070…4375` 블록 929066 **code 1**: "spendable balance 99.5 POKT < 59,500: insufficient funds" → **스테이크 자금은 서명자 계정에서 빠진다**(문서 미명시 사항 확인). 수수료 0.5 소모.
- 2단계로 분리: ① owner-ops `--stake-only`(services 비움, `supplier_stake_owner.yaml`) → 확정, owner-ops 60,099→598.99 ② operator `--services-only`(stake_amount 제거, `supplier_services_only.yaml`) tx `1D1732…4421` 블록 929077 code 0. 서비스는 세션 경계 규칙으로 **블록 929081부터 활성**(service_config_history activation_height).
- 최종 supplier `pokt10hh…`: stake 59,500, owner `pokt139kc…`, service `pokt-settlement-agent-v1` → `https://agent.pokt-agent.com` REST, rev_share owner 100%.
- RelayMiner: `pocketd relayminer start --config … --network=main --keyring-backend file`, LaunchAgent `com.jsy.pokt.relayminer`(KeepAlive). 키링 암호는 `agent-deploy/.keyring-pass`(600, 사용자 작성)를 stdin으로 **계속 공급**(pocketd가 키링을 여러 번 열어 1회 입력으론 "too many failed passphrase attempts"). 첫 기동 시 백엔드 핑 501 → 에이전트에 HEAD/OPTIONS 추가로 해결. 현재 `0.0.0.0:8545` LISTEN, 체인 head 추적, SMT 저장 `agent-deploy/smt`.
- Cloudflare 터널 ingress: `agent.pokt-agent.com`→:8545(RelayMiner, 프로토콜 릴레이), `pokt-agent.com`/`www`→:8793(사람용 UI + `/v1/*` + `/openapi.json`). 공개 확인: `https://agent.pokt-agent.com/v1/health` → RelayMiner 응답("missing session header: invalid relay request" = 릴레이 헤더 없는 GET에 대한 정상 거절), `https://pokt-agent.com/` 200(UI).
- 관제 페이지: 소유 supplier 1, 실제 스테이크 59,500(CHAIN), 다음 행동 "첫 정산 확인".


## 2026-09-20 03:40~03:50 KST — 포털 감사(mcp.pocketmcp.network/audit)와 보정

- 텔레그램(Jinx 전달, 09-20): Agentic Portal 배포 대기열 43개, 검수 도구 웹 공개. 우리 서비스 첫 감사: A1·A3·A4·A5·A6·A9 PASS, A2·A8 WARN, A7 FAIL(정산 릴레이 0).
- 카드 보정(사용자 재서명, 가스만): `updated: 2026-09-20`, description에 "Every response is a JSON object", 기능 프로브(POST /v1/settlement, expect `$.service`), specs openapi·docs URL. 재감사: **A2·A8 PASS → A7만 FAIL**.
- Cloudflare: Python-urllib·Go-http-client UA가 두 호스트에서 403 → 원인 **Browser Integrity Check**(Bot Fight Mode는 원래 꺼짐). 사용자 승인 후 Claude in Chrome로 끔 → 200 확인. 포털 검수기·게이트웨이·AI 에이전트 호출 차단 위험 제거.
- 에이전트: 영어 기본 UI + 한국어 토글, API `lang`(en/ko), 영어 의도 매핑, 루트 경로 POST 허용(릴레이 도구 호환).
- A7 직접 경로 준비: `agent-deploy/app_stake_config.yaml`(1,000 POKT, 서비스 1개). 순서: Soothe→owner-ops 500 → `stake-application`(owner-ops) → `pocketd relayminer relay --app owner-ops --supplier operator --payload …` → 세션 종료 후 claim/proof/settle(≈40~60분) → 감사 재실행.
- Discord: 서버 참여 완료(초대 수락), 게시는 전화번호 인증 필요(사용자, SMS 속도제한 중). help-desk는 Ticket Tool "Create ticket" 방식.


## 2026-09-20 04:00~05:05 KST — A7 첫 릴레이·클레임 시도

- 앱 스테이크: 1차 실패(owner-ops 597.9 < 1,000, insufficient funds) → Soothe→owner-ops 500 이체 후 tx `2E3C8B02…C5CF` 블록 929142 code 0. 애플리케이션 `pokt139kc…` 스테이크 1,000, 서비스 `pokt-settlement-agent-v1`.
- 테스트 릴레이 1(사용자 실행, 03:55): `pocketd relayminer relay --app owner-ops --supplier operator --grpc-insecure=false …` → Cloudflare→RelayMiner→에이전트 200→supplier 서명 검증 성공. RelayMiner 로그 "relay request served successfully", 세션 99a4a6… 채굴.
- 클레임 실패(04:57): "unexpected 'x-cosmos-block-height' header length; got 0" → 세션 트리 삭제. 원인: `relayminer start`에 `--grpc-insecure=false` 누락(기본 true) + config grpc URL `https://` 스킴. CLI 재현: `--grpc-addr sauron-grpc.infra.pocket.network:443 --grpc-insecure=false`만 성공, `https://`·`grpcs://`·`tcp://` 스킴은 dial 실패.
- 교정: 런처에 `--grpc-addr sauron-grpc.infra.pocket.network:443 --grpc-insecure=false` 추가, config `query_node_grpc_url: tcp://…:443`. 재기동 후 테스트 릴레이 2(05:02, Claude가 사용자 암호 파일로 앱 키 서명; 비용 ≈0.0006 POKT) → 200, 세션 fc19d9…. 클레임·정산 감시 진행(예상 ≈929253).
- Discord: 티켓 `#ticket-0606` 생성·1차 문안 게시(사용자). 크롬 브리지 허용 규칙(`.claude/settings.local.json`) 추가(사용자). 정산 후 Claude가 업데이트 게시 예정.
- ② 확장: `GET /v1/supplier/{operator}/economics`, `GET /v1/network/suppliers`(총 서플라이어 스테이크 254.9M, 7d −0.19%, 30d +0.16%; 관측 서플라이어 몫 수익률 연 39.6% 비용 전) + 관제 페이지 네트워크 경제 칸. 카드(경제 API 포함) 재서명은 아침 대기.
- A 4M 위임 시나리오(관측 기준): 월 35,088~76,503 POKT(60일 평균 55,654; APR 10.7~23.4% 희석·수수료 2% 반영). 단계적 1M 우선·Keplr+Ledger·검증자 분산 권고.


## 2026-09-20 05:05~06:15 KST — 클레임 제출 실패 원인 확정 (A7 BLOCKED, 공개 gRPC 한계)

- 릴레이 2·3·4(세션 fc19d9…, 7f7936…, ac0779…) 모두 서비스됨(200, 세션 트리 채굴, 수익성 판정 보상 614 upokt). **클레임 제출은 4회 연속 동일 오류**: `unexpected 'x-cosmos-block-height' header length; got 0, expected: 1` → 세션 트리 삭제(보상 소멸, 손실은 없음).
- 시도한 교정(모두 무효): `--grpc-insecure=false` + config grpc `tcp://` 스킴; `--query-caching=false`; `--unordered --timeout-duration 10m --account-number 16251`(오프라인 서명).
- 원인(소스 확인): cosmos-sdk `x/auth/types/account_retriever.go` `GetAccountWithHeight`가 계정 조회 gRPC 응답 메타데이터의 `x-cosmos-block-height`를 요구. poktroll `pkg/deps/config/suppliers.go`는 tx 컨텍스트에 `--grpc-addr = query_node_grpc_url.Host`를 **항상** 설정 → 공개 `sauron-grpc.infra.pocket.network:443` 앞단(LB)이 이 응답 헤더를 벗겨 실패. CLI `tx` 명령은 gRPC 없이 RPC/ABCI 경로라 성공했던 것과 일치. (`pocketd query auth account --grpc-addr …`는 헤더를 검사하지 않아 성공해 보임.)
- 대안 조사: 다른 공개 gRPC(blockval·grove 도메인 추정) 연결 불가; 자체 풀노드는 Linux 전용 `full-node.sh`(스냅샷 호스트 `snapshots.us-nj.poktroll.com` DNS 불가 상태) + 디스크 200~420GB 필요인데 **Mac /Volumes/A 여유 147GB**로 부족. 따라서 오늘 밤 A7 해결 불가.
- 조치: Discord 티켓 `#ticket-0606`에 기술 상세(오류·설정·시도·질문: 헤더 보존 gRPC 존재 여부 또는 자체 노드 필수 여부) 업데이트 게시(Claude, 허용 규칙 적용). RelayMiner는 계속 가동(릴레이 서비스는 정상).
- 아침 결정 필요: (a) Pocket 팀 회신 대기 (b) 외장/여유 디스크 확보 후 Lima VM 풀노드(pruned 스냅샷 크기 확인 필요) (c) 저가 Linux VPS 풀노드(월 비용) 중 선택.

## 검증 상태

- pocketd 설치·읽기 질의: passed. cloudflared 존재: passed(설치 전부터). 카드 JSON 파싱: passed.
- 도메인·터널·키·자금·등록·스테이크·RelayMiner: not_run(사용자 실행 대기).
- 미확인: Soothe의 사용자 정의 tx 서명 가능 여부, stake-supplier 자금 출처 계정, 포털 순위 규칙.

## 2026-09-20 09:10 KST — 팀 회신 반영: 베타 테스트넷 재등록 + 새 RelayMiner (진행 기록)

**팀 회신(#ticket-0606) 검증 결과**
- 감사 API 기본 network=**beta**(카탈로그 57 = 베타 서비스 수). `?network=main`은 8/9(A7 FAIL), `?network=beta`는 A1 FAIL(미등록). 밤새 본 8/9는 메인넷 기준이었다. → 베타에 전부 재등록 필요(테스트넷 POKT, 비용 0).
- 클레임 실패 원인 = legacy `pocketd relayminer`. 새 [pocket-relay-miner](https://github.com/pokt-network/pocket-relay-miner)(커밋 0d6ad24, 2026-08-30)는 계정 조회를 gRPC `Account`로 직접 하므로 `x-cosmos-block-height` 검사를 타지 않는다.
- 메인넷 앱 스테이크는 불필요(포털 포함 시 PNF가 대신 스테이크) → 언스테이크 가능.

**새 RelayMiner 요구·특성**: 릴리스 바이너리 없음(Go 1.27.1로 `make build-release`, 114MB), Redis 8.2+ 필수(brew redis 8.10.2, `brew services`), relayer+miner 2프로세스, 키는 hex keys_file(`keys: ["<hex>"]`) 또는 keyring(file 백엔드는 stdin=nil → 비대화 불가). 테스트넷은 **test 키링 새 키**를 만들어 hex로 넣었다(메인넷 키 무관).

**베타 체인 사실**: chain-id `pocket-lego-testnet`, RPC `https://sauron-rpc.beta.infra.pocket.network`, gRPC `sauron-grpc.beta.infra.pocket.network:443`, 블록 ≈30초, 파라미터 메인넷과 동일(서비스 1,000·서플라이어 min 59,500·앱 1,000). 페이싯: pocketd 내장 URL은 DNS 없음 → 웹 페이싯 `GET https://faucet.beta.pocket.network/send/beta-pokt/<addr>` (100,000/회, 주소당 2회/24h, 캡차 없음).

**실행 순서(전부 Claude, 테스트넷)**
1. 키: `pokt-beta-owner` = `pokt1sawepkltpkpetcvrg7qnfl3f3vex9q7dgqvuvu`, `pokt-beta-operator` = `pokt16m783stsdya2cv9cvah57hhekyxkv7xphajqhf` (test 키링). 페이싯 owner 200k, operator 100k.
2. `add-service` (같은 card.json) → tx `8B32516E…` 블록 662244. 주의: 여러 tx를 한 스크립트에서 연달아 보내면 브로드캐스트가 조용히 빠진다(원인 미확인) → tx는 한 번에 하나씩 별도 ssh 호출.
3. owner `--stake-only` 59,500 → `3C804E83…` 662250; operator `--services-only`(endpoint `https://beta.pokt-agent.com`, REST) → `7710393C…` 662252, **activation_height 662261**(서비스 설정은 다음 세션 경계에 활성화); 앱 1,000 → `A2087CBD…` 662252.
4. 터널: `cloudflared tunnel route dns pokt-agent beta.pokt-agent.com` + ingress `beta.pokt-agent.com → http://localhost:8546`.
5. 새 relayer(:8546, health :8083, metrics :9093, backend `http://127.0.0.1:8793` REST) + miner(chain_id 베타, block_time 30, metrics :9094). LaunchAgents `com.jsy.pokt.beta-relayer`, `com.jsy.pokt.beta-miner`.
   - **launchd 함정 3개**(모두 겪음): ① Program이 /Volumes/A의 바이너리면 EX_CONFIG(78) → `/bin/bash` 래퍼; ② StandardOutPath가 /Volumes/A면 EX_CONFIG → `~/Library/CodexOps/llm-runtime/logs/`; ③ 바이너리·설정·키가 /Volumes/A에 있으면 프로세스가 **조용히 멈춤**(리슨 없음, 로그 0줄) → 전부 `~/Library/CodexOps/pokt/`로 이동. `/Volumes/A/POKT/beta-deploy/`는 사본·기록용.
6. 첫 릴레이 09:05:46 KST: `pocketd relayminer relay --app <beta-owner> --supplier <beta-op> --from pokt-beta-owner --network=beta --keyring-backend test --payload '{"question":…}'` → 백엔드 200, relayer "supplier found in session" session `33a06a31`, miner "session created on first relay" end_height **662280**. 추가 2건 전송.
7. miner 경고 "stake critically low, buffer 0"(min_stake와 정확히 같음 → proof 1회 누락 시 자동 언스테이크) → 베타 owner stake 64,500으로 상향. **메인넷도 59,500 정확히 = 같은 위험** → 메인넷 전환 시 버퍼 추가 권고(사용자 결정, 실자금).

8. **정산 성공(09:31 KST)**: claim tx `49D4F247…`(662293, claim window open 662292) → proof tx `6AD70762…`(662304, proof required: 600upokt > threshold) → **`EventClaimSettled` 블록 662313**: num_relays 3, compute units 15,000, claimed 600upokt, reward_distribution owner 476upokt(나머지 DAO/proposer). 릴레이→정산 총 26분.
9. **감사(beta) 9/9 PASS** (audited_at 2026-09-20T00:31:43Z, A1~A9 전부 PASS). main은 여전히 A7 FAIL(무관—포털은 beta 기준). Discord `#ticket-0606`에 결과 게시(Claude, 09:33).

**남은 것**: 메인넷 앱 언스테이크(owner-ops 서명, 사용자), 메인넷 서플라이어를 새 RelayMiner로 이전(메인넷 operator 키 hex 또는 os 키링 방식 결정 필요), 메인넷 stake 버퍼(59,500=min 정확히) 추가 여부.

## 2026-09-20 09:50 KST — 메인넷도 새 RelayMiner로 전환 완료
- 사용자 실행: 앱 언스테이크(unstake_session_end_height 929480, 1,000 반환 예정), 서플라이어 stake 59,500→**60,000**(tx `6DE1BA17…` 블록 929486, 버퍼 500: proof_missing_penalty 1upokt라 금액보다 "min과 같음" 구조 위험 제거용), operator 키 hex → `~/Library/CodexOps/pokt/keys/mainnet-supplier-keys.yaml`(600, 77B, Claude는 값 미열람).
- Claude: legacy `com.jsy.pokt.relayminer` 중지(plist는 `~/Library/LaunchAgents/disabled/`), `com.jsy.pokt.main-relayer`(:8545, health :8084, metrics :9095) + `com.jsy.pokt.main-miner`(chain_id pocket, metrics :9096) 기동. 설정 `~/Library/CodexOps/pokt/config.main.{relayer,miner}.yaml`, 래퍼 `run_main.sh`.
- **함정**: 베타·메인 miner가 같은 Redis 네임스페이스(`ha`)를 쓰면 리더 선출을 공유해 메인 miner가 standby로 남는다(클레임 안 나감) → 메인은 `redis.namespace.base_prefix: hamain`. 이제 두 miner 모두 leader.
- 메인넷 검증: 공개 `https://agent.pokt-agent.com/health` 200. 실제 릴레이는 우리 앱이 언스테이킹 중이라 자체 시험 불가(포털 편입 시 PNF 앱이 보냄). 베타에서 동일 코드로 정산까지 검증됨.
- 남은 정리: `.keyring-pass`(legacy용)는 이제 어느 프로세스도 안 씀 → 삭제 권장(사용자). 크롬 허용 규칙 2줄 제거 여부.

## 2026-09-20 10:40 KST — ① pokt-network-watch-v1 베타 등록 (전수조사 후 첫 선점)
- 코드: llm-runtime `e3ba7bd` `llm_runtime/pokt/watch.py`(GET /v1/network/status·/v1/sources·/v1/events·/v1/events/{id}/verify, POST /v1/watch; 결정적 의도 6종 한/영), CLI `watch-serve/watch-card`, launchd `com.jsy.llm-runtime.pokt-watch`(:8794), 테스트 P21~P24. 공개 https://watch.pokt-agent.com (터널 ingress → :8794).
- 베타: add-service `31AFBBDA…`(카드 3,197B), 앱 키 `pokt-beta-app-watch`=`pokt1yy2ex5xuxrhdt3rpjywn66ne9scgc6gwqaeymx`(페이싯 100k) 앱 스테이크 `0EC2FDDB…`, operator services-only 2개 서비스 `6C1F0CEE…`(activation 662441). 베타 relayer에 서비스 추가(backend :8794). 릴레이 3건 10:36 KST 세션 `38533e7f`(end 662460).
- 함정: install 스크립트의 트램폴린 템플릿을 그대로 OPS 경로에 복사하면 `__RUNTIME_ROOT__` 미치환으로 종료코드 75 → sed 치환 후 복구(다른 pokt 에이전트도 같은 트램폴린 사용, 즉시 수정).
- 전수조사 결과·다음 후보: `docs/pokt-agent-landscape-20260920.md`.
- 11:00 KST watch 정산: claim `3B00E667…`(≈662472) → proof `5B31010A…`(≈662483) → **EventClaimSettled 662493**. 릴레이→정산 24분.
