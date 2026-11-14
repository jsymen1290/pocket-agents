#!/bin/zsh
# 소유 서비스 10개 CUPR 50,000 -> 100,000 (팀 승인: #ticket-0606 2026-11-13 22:59). 서명은 사용자.
# 기본은 점검만(전송 안 함). 실제 전송: RUN=1 ./cupr_to_100k.sh   (키링 비밀번호는 사용자가 직접 입력)
# 한 번에 한 서비스씩, 카드가 체인과 다르면 멈춘다(카드가 바뀌는 사고 방지).
P=/opt/homebrew/bin/pocketd
D=/Volumes/A/POKT/agent-deploy
OWNER=pokt139kcncr45rukhlzyncgua8ln2jpfjysyzu5t2c
NEW=100000
for pair in pine:pine-script-lint-v1:"Pine Script Lint" kr:kr-market-data-v1:"KR Market Data" apt:kr-apt-trades-v1:"KR Apartment Trades" dart:dart-kr-events-v1:"DART KR Events" impact:dart-correction-impact-v1:"DART Correction Impact" export:kr-export-pulse-v1:"KR Export Pulse" rates:kr-rates-fx-v1:"KR Rates and FX" law:kr-law-v1:"KR Law" figures:kr-figure-check-v1:"KR Figure Check" backtest:backtest-reconcile-v1:"Backtest Reconcile"; do
  f=${pair%%:*}; rest=${pair#*:}; s=${rest%%:*}; n=${rest#*:}
  cur=$($P query service show-service $s --network=main -o json | python3 -c '
import sys,json,base64
s=json.load(sys.stdin);s=s.get("service",s)
ok=base64.b64decode(s["metadata"]["card"])==open(sys.argv[1],"rb").read()
print(s["compute_units_per_relay"],s["owner_address"],"CARD_OK" if ok else "CARD_DIFF")' $D/$f.card.json)
  echo "$s  현재: $cur"
  case "$cur" in *"$OWNER CARD_OK") ;; *) echo "  -> 소유자/카드 불일치, 건너뜀"; continue;; esac
  case "$cur" in "$NEW "*) echo "  -> 이미 $NEW"; continue;; esac
  CMD=($P tx service add-service $s "$n" $NEW --card-file $D/$f.card.json --from pokt-owner-ops --network=main --gas 400000 --fees 400000upokt --keyring-backend file --yes)
  if [ "$RUN" = 1 ]; then
    "${CMD[@]}" || { echo "  -> 전송 실패, 중단"; exit 1; }
    sleep 40
    $P query service show-service $s --network=main -o json | python3 -c 'import sys,json;s=json.load(sys.stdin);s=s.get("service",s);print("  -> 갱신 후 CUPR",s["compute_units_per_relay"])'
  else
    echo "  (점검만) ${CMD[*]}"
  fi
done
