"""python -m pocket_agents <subcommand>

  collect                       one bounded read-only cycle -> data/pokt/STATE.json (launchd runs this every 30 min)
  status                        print data/pokt/STATUS.md
  alerts [--all]                print active (or active+resolved) alerts
  settlements <operator> [--n]  last N settlement rows for a watched operator
  prepare-keys                  operator key commands (user runs them)
  prepare-delegate --validator poktvaloper1... --pokt N [--key NAME]
  prepare-supplier --owner pokt1... --operator pokt1... --pokt N --service ID=URL[,RPC_TYPE] [...]
  set-user --owner pokt1... [--operators a,b]   write data/pokt/watch.local.json (public addresses only)
  serve [--host 0.0.0.0] [--port 8792]         read-only LAN status page (index.html/state.json/alerts.json/status.md/brief.md)
  prepare-service --id ID --name NAME --cu N --card card.json   add-service command (1,000 POKT fee on MainNet, 2026-09-19)
  agent-serve [--port 8793] [--no-llm]         POKT Settlement Agent v0 HTTP service (GET /v1/version, /v1/health, POST /v1/settlement)
  watch-serve [--port 8794]                    POKT Network Watch HTTP service (GET /v1/network/status, /v1/sources, /v1/events, POST /v1/watch)
  krmarket-serve [--port 8795]                 KR Market Data HTTP service (Upbit/Bithumb KRW ticker, orderbook, premium, fx)
  pinelint-serve [--port 8796]                 Pine Script Lint HTTP service (POST /v1/lint)
  krexport-serve [--port 8797]                 KR Export Pulse (Korea Customs 10-day / HS; needs DATA_GO_KR_SERVICE_KEY)
  dart-serve [--port 8798]                     DART KR Events (OpenDART corrections/terms/as-of; needs OPENDART_API_KEY)
  agent-card [--out path]                      write the pocket-service-card/v1 JSON for the agent + add-service command
  ask --operator pokt1... --q "질문" [--from H --to H | --hours N] [--no-llm]   one local answer (same code path as the service)
"""
import argparse
import json
import os
import sys

from . import prepare
from .chain import is_address, is_valoper
from .collect import _read_json, _read_ndjson, run_cycle


def _data_dir():
    return os.environ.get("LLM_DATA_DIR") or os.path.join(_runtime_root(), "data")


def _runtime_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv):
    p = argparse.ArgumentParser(prog="pocket_agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    sub.add_parser("status")
    a = sub.add_parser("alerts")
    a.add_argument("--all", action="store_true")
    s = sub.add_parser("settlements")
    s.add_argument("operator")
    s.add_argument("--n", type=int, default=20)
    sub.add_parser("prepare-keys")
    d = sub.add_parser("prepare-delegate")
    d.add_argument("--validator", required=True)
    d.add_argument("--pokt", required=True)
    d.add_argument("--key", default="pokt-owner")
    ps = sub.add_parser("prepare-supplier")
    ps.add_argument("--owner", required=True)
    ps.add_argument("--operator", required=True)
    ps.add_argument("--pokt", required=True)
    ps.add_argument("--service", action="append", required=True, help="ID=URL[,RPC_TYPE]")
    ps.add_argument("--rev-share", default=None, help="addr:pct,addr:pct (sum 100)")
    ps.add_argument("--out", default=None)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8792)
    pv = sub.add_parser("prepare-service")
    pv.add_argument("--id", required=True)
    pv.add_argument("--name", required=True)
    pv.add_argument("--cu", required=True, help="compute units per relay")
    pv.add_argument("--card", required=True, help="path to card.json")
    pv.add_argument("--key", default="pokt-owner")
    ws = sub.add_parser("watch-serve")                # (1) as a product: POKT Network Watch HTTP service
    ws.add_argument("--host", default="0.0.0.0"); ws.add_argument("--port", type=int, default=8794)
    wc = sub.add_parser("watch-card"); wc.add_argument("--out"); wc.add_argument("--base", default="https://watch.pokt-agent.com")
    ks = sub.add_parser("krmarket-serve")             # kr-market-data-v1 HTTP service
    ks.add_argument("--host", default="0.0.0.0"); ks.add_argument("--port", type=int, default=8795)
    kc = sub.add_parser("krmarket-card"); kc.add_argument("--out"); kc.add_argument("--base", default="https://kr.pokt-agent.com")
    pl = sub.add_parser("pinelint-serve")             # pine-script-lint-v1 HTTP service
    pl.add_argument("--host", default="0.0.0.0"); pl.add_argument("--port", type=int, default=8796)
    pc = sub.add_parser("pinelint-card"); pc.add_argument("--out"); pc.add_argument("--base", default="https://pine.pokt-agent.com")
    ke = sub.add_parser("krexport-serve"); ke.add_argument("--host", default="0.0.0.0"); ke.add_argument("--port", type=int, default=8797)
    kec = sub.add_parser("krexport-card"); kec.add_argument("--out"); kec.add_argument("--base", default="https://export.pokt-agent.com")
    de = sub.add_parser("dart-serve"); de.add_argument("--host", default="0.0.0.0"); de.add_argument("--port", type=int, default=8798)
    dec = sub.add_parser("dart-card"); dec.add_argument("--out"); dec.add_argument("--base", default="https://dart.pokt-agent.com")
    ag = sub.add_parser("agent-serve")
    ag.add_argument("--host", default="0.0.0.0")
    ag.add_argument("--port", type=int, default=8793)
    ag.add_argument("--no-llm", action="store_true")
    ac = sub.add_parser("agent-card")
    ac.add_argument("--out", default=None)
    ak = sub.add_parser("ask")
    ak.add_argument("--operator", required=True)
    ak.add_argument("--q", required=True)
    ak.add_argument("--from", dest="from_h", type=int, default=None)
    ak.add_argument("--to", dest="to_h", type=int, default=None)
    ak.add_argument("--hours", type=int, default=None)
    ak.add_argument("--no-llm", action="store_true")
    sub.add_parser("surveil")                      # ① REFRESH_OBSERVATION (+ BUILD_REVIEW unless --no-llm)
    sub.add_parser("events")                       # ① GET_HISTORY
    vf = sub.add_parser("verify-event")            # ① VERIFY_EVIDENCE
    vf.add_argument("event_id")
    sub.add_parser("decision")                     # ③ compute + print
    dc = sub.add_parser("decide")                  # ③ RECORD_DECISION
    dc.add_argument("--track", required=True, choices=["A", "B", "C", "D", "ALL"])
    dc.add_argument("--text", required=True)
    cp = sub.add_parser("capital")                 # ③ user-declared totals/intent
    cp.add_argument("--total", type=int, default=None)
    cp.add_argument("--allocate", default=None, help="A=..,B=..,C=..,D=..")
    cp.add_argument("--reserve", type=int, default=None)
    su = sub.add_parser("set-user")
    su.add_argument("--owner", required=True)
    su.add_argument("--operators", default="")
    su.add_argument("--services", default="", help="comma-separated on-chain service ids you own (track D)")
    args = p.parse_args(argv)
    data_dir = _data_dir()
    root = os.path.join(data_dir, "pokt")

    if args.cmd == "collect":
        rec = run_cycle(_runtime_root(), data_dir)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0 if rec.get("status") in ("NORMAL", "CAUTION", "SKIPPED") else 1
    if args.cmd == "status":
        path = os.path.join(root, "STATUS.md")
        if not os.path.isfile(path):
            print("no STATUS.md yet; run `pokt collect` first")
            return 1
        print(open(path, encoding="utf-8").read())
        return 0
    if args.cmd == "alerts":
        d = _read_json(os.path.join(root, "ALERTS.json"), {}) or {}
        print(json.dumps({"active": d.get("active", []), "resolved": d.get("resolved", []) if args.all else "(use --all)"}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "settlements":
        rows = _read_ndjson(os.path.join(root, "settlements", "%s.ndjson" % args.operator), limit=args.n)
        for r in rows:
            print(json.dumps({k: r.get(k) for k in ("height", "block_time_utc", "service_id", "num_relays", "settled_upokt", "reward_to_owner_pokt", "reward_to_operator_pokt", "reward_recipients")}, ensure_ascii=False))
        print("%d rows" % len(rows), file=sys.stderr)
        return 0
    if args.cmd == "prepare-keys":
        print(prepare.operator_key_commands())
        return 0
    if args.cmd == "prepare-delegate":
        if not is_valoper(args.validator):
            print("validator must be poktvaloper1...", file=sys.stderr)
            return 2
        print(prepare.delegate_command(args.validator, args.pokt, key_name=args.key))
        return 0
    if args.cmd == "prepare-supplier":
        services = []
        for item in args.service:
            sid, rest = item.split("=", 1)
            url, _, rpc = rest.partition(",")
            services.append({"service_id": sid.strip(), "url": url.strip(), "rpc_type": (rpc or "JSON_RPC").strip()})
        rev = None
        if args.rev_share:
            rev = {}
            for pair in args.rev_share.split(","):
                addr, pct = pair.split(":")
                rev[addr.strip()] = int(pct)
        yaml_text = prepare.supplier_stake_config(args.owner, args.operator, args.pokt, services, rev)
        out = args.out or os.path.join(root, "prepared", "supplier_stake_config.yaml")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(yaml_text)
        print("# wrote %s\n%s" % (out, yaml_text))
        print(prepare.supplier_stake_commands(out))
        return 0
    if args.cmd == "serve":
        from .web import serve
        print("serving %s on http://%s:%d/ (read-only)" % (root, args.host, args.port), file=sys.stderr)
        serve(data_dir, args.host, args.port)
        return 0
    if args.cmd == "prepare-service":
        print(prepare.add_service_commands(args.id, args.name, args.cu, args.card, key_name=args.key))
        return 0
    if args.cmd == "agent-serve":
        from .agent import serve as agent_serve
        print("POKT Settlement Agent on http://%s:%d/ (llm=%s)" % (args.host, args.port, not args.no_llm), file=sys.stderr)
        agent_serve(data_dir, _runtime_root(), args.host, args.port, use_llm=not args.no_llm)
        return 0
    if args.cmd == "watch-serve":
        from .watch import serve as watch_serve
        print("POKT Network Watch on http://%s:%d/" % (args.host, args.port), file=sys.stderr)
        watch_serve(data_dir, args.host, args.port)
        return 0
    if args.cmd == "watch-card":
        from .watch import SERVICE_ID as WATCH_ID, service_card as watch_card
        out = args.out or os.path.join(root, "prepared", "%s.card.json" % WATCH_ID)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(watch_card(args.base), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
        print(prepare.add_service_commands(WATCH_ID, "POKT Network Watch", "5000", out))
        return 0
    if args.cmd == "krmarket-serve":
        from .krmarket import serve as kr_serve
        print("KR Market Data on http://%s:%d/" % (args.host, args.port), file=sys.stderr)
        kr_serve(args.host, args.port)
        return 0
    if args.cmd == "krmarket-card":
        from .krmarket import SERVICE_ID as KR_ID, service_card as kr_card
        out = args.out or os.path.join(root, "prepared", "%s.card.json" % KR_ID)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(kr_card(args.base), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
        print(prepare.add_service_commands(KR_ID, "KR Market Data", "5000", out))
        return 0
    if args.cmd == "pinelint-serve":
        from .pinelint import serve as pl_serve
        print("Pine Script Lint on http://%s:%d/" % (args.host, args.port), file=sys.stderr)
        pl_serve(args.host, args.port)
        return 0
    if args.cmd == "pinelint-card":
        from .pinelint import SERVICE_ID as PL_ID, service_card as pl_card
        out = args.out or os.path.join(root, "prepared", "%s.card.json" % PL_ID)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(pl_card(args.base), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
        print(prepare.add_service_commands(PL_ID, "Pine Script Lint", "5000", out))
        return 0
    if args.cmd in ("krexport-serve", "dart-serve"):
        mod = __import__("pocket_agents." + ("krexport" if args.cmd == "krexport-serve" else "dartevents"), fromlist=["serve"])
        print("%s on http://%s:%d/" % (mod.SERVICE_ID, args.host, args.port), file=sys.stderr)
        mod.serve(data_dir, args.host, args.port)
        return 0
    if args.cmd in ("krexport-card", "dart-card"):
        mod = __import__("pocket_agents." + ("krexport" if args.cmd == "krexport-card" else "dartevents"), fromlist=["service_card"])
        out = args.out or os.path.join(root, "prepared", "%s.card.json" % mod.SERVICE_ID)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(mod.service_card(args.base), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
        print(prepare.add_service_commands(mod.SERVICE_ID, "KR Export Pulse" if args.cmd == "krexport-card" else "DART KR Events", "5000", out))
        return 0
    if args.cmd == "agent-card":
        from .agent import SERVICE_ID, service_card
        out = args.out or os.path.join(root, "prepared", "%s.card.json" % SERVICE_ID)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(service_card(), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
        print(prepare.add_service_commands(SERVICE_ID, "POKT Settlement Agent", "5000", out))
        return 0
    if args.cmd == "ask":
        from .agent import answer
        res = answer(args.q, args.operator, args.from_h, args.to_h, args.hours, data_dir=data_dir, runtime_root=_runtime_root(), use_llm=not args.no_llm)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "surveil":
        from . import surveillance
        st, events = surveillance.refresh(_runtime_root(), data_dir)
        reviews = surveillance.review(_runtime_root(), data_dir, events) if os.environ.get("POKT_SURVEIL_LLM", "1") == "1" else []
        print(json.dumps({"run_id": st["run_id"], "sources": st["sources"], "changed": st["changed"], "first": st["first"], "failed": st["failed"],
                          "events": [e["event_id"] for e in events], "reviews": reviews}, ensure_ascii=False, indent=2))
        return 0 if st["failed"] < st["sources"] else 1
    if args.cmd == "events":
        from . import surveillance
        for e in surveillance.history(data_dir, limit=30):
            print(json.dumps({k: e.get(k) for k in ("at_utc", "event_id", "source_id", "change", "added_lines", "removed_lines", "classification")}, ensure_ascii=False))
        return 0
    if args.cmd == "verify-event":
        from . import surveillance
        evs = [e for e in surveillance.history(data_dir, limit=10000, only_changes=False) if e.get("event_id") == args.event_id]
        if not evs:
            print("event not found", file=sys.stderr)
            return 1
        print(json.dumps(surveillance.verify_receipt(data_dir, evs[-1]), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "decision":
        from .decision import compute
        print(json.dumps(compute(data_dir), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "decide":
        from .decision import record_decision
        print(json.dumps(record_decision(data_dir, args.track, args.text), ensure_ascii=False))
        return 0
    if args.cmd == "capital":
        from .decision import save_capital
        alloc = None
        if args.allocate:
            alloc = {}
            for pair in args.allocate.split(","):
                k, v = pair.split("=")
                alloc[k.strip().upper()] = int(v)
        print(json.dumps(save_capital(data_dir, args.total, alloc, args.reserve), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "set-user":
        if not is_address(args.owner):
            print("owner must be pokt1...", file=sys.stderr)
            return 2
        ops = [o.strip() for o in args.operators.split(",") if o.strip()]
        bad = [o for o in ops if not is_address(o)]
        if bad:
            print("bad operator address(es): %s" % bad, file=sys.stderr)
            return 2
        path = os.path.join(root, "watch.local.json")
        cur = _read_json(path, {}) or {}
        svcs = [x.strip() for x in args.services.split(",") if x.strip()]
        cur["user"] = {"owner_address": args.owner, "operator_addresses": ops, "service_ids": svcs}
        os.makedirs(root, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cur, fh, indent=2)
            fh.write("\n")
        print("wrote %s (public addresses only)" % path)
        return 0
    return 2
