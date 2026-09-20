"""Read-only status page for the POKT collector.

render_html(state, alerts, brief_md) -> HTML string written to <data>/pokt/index.html on every cycle.
serve(data_dir, host, port) -> tiny stdlib HTTP server that serves ONLY these files from <data>/pokt:
  /            index.html        /state.json   STATE.json      /alerts.json  ALERTS.json
  /status.md   STATUS.md         /brief.md     latest daily brief (copied at render time)
No directory listing, no other paths, no query handling, GET/HEAD only. Nothing secret lives in that
directory (public addresses, chain observations, receipts) and the server never reads .env.
"""
import datetime
import html
import http.server
import json
import os
import socketserver

KST = datetime.timezone(datetime.timedelta(hours=9))
ALLOWED = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
           "/state.json": ("STATE.json", "application/json; charset=utf-8"), "/alerts.json": ("ALERTS.json", "application/json; charset=utf-8"),
           "/status.md": ("STATUS.md", "text/markdown; charset=utf-8"), "/brief.md": ("brief.md", "text/markdown; charset=utf-8"),
           "/decision.json": ("DECISION.json", "application/json; charset=utf-8"), "/decisions.ndjson": ("decisions.ndjson", "application/x-ndjson; charset=utf-8"),
           "/surveillance.json": (os.path.join("surveillance", "STATUS.json"), "application/json; charset=utf-8"),
           "/events.ndjson": (os.path.join("surveillance", "EVENTS.ndjson"), "application/x-ndjson; charset=utf-8")}


def _e(v):
    return html.escape("" if v is None else str(v))


def _num(v):
    """'100000.000000' -> '100,000.00'; None -> '–'."""
    if v is None:
        return "–"
    try:
        f = float(str(v).replace(",", ""))
    except ValueError:
        return _e(v)
    return "{:,.2f}".format(f) if abs(f) >= 1 else "{:,.6f}".format(f)


def render_html(state, alerts, brief_md=None, decision=None):
    now = state.get("generated_at_kst") or ""
    decision = decision or {}
    sev_color = {"HIGH": "#b91c1c", "WARN": "#b45309", "INFO": "#1d4ed8"}
    st = state.get("status") or "?"
    st_color = {"NORMAL": "#15803d", "CAUTION": "#b45309", "DEGRADED": "#b91c1c"}.get(st, "#374151")
    ch = state.get("chain") or {}
    d = (state.get("params") or {}).get("derived") or {}
    u = state.get("user") or {}
    m = state.get("market") or {}
    parts = []
    parts.append("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                 "<meta http-equiv='refresh' content='300'><title>POKT 관제</title><style>"
                 "body{font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;margin:0;background:#f6f7f9;color:#111}"
                 "header{background:#111827;color:#fff;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}"
                 "main{padding:16px 20px;max-width:1100px;margin:0 auto}section{background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:14px;box-shadow:0 1px 2px rgba(0,0,0,.06)}"
                 "h2{font-size:15px;margin:0 0 10px;color:#374151}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}"
                 "td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.big{font-size:28px;font-weight:700}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}"
                 ".kpi{background:#f9fafb;border-radius:8px;padding:10px 12px}.kpi small{display:block;color:#6b7280;font-size:12px}.badge{display:inline-block;padding:2px 8px;border-radius:999px;color:#fff;font-size:12px;font-weight:600}"
                 ".muted{color:#6b7280;font-size:12px}pre{white-space:pre-wrap;font-family:inherit;font-size:13px;line-height:1.5;margin:0}</style></head><body>")
    parts.append("<header><div><strong>POKT 관제</strong> <span class='muted' style='color:#cbd5e1'>Mac 상시 수집 · 30분 갱신 · 읽기 전용</span></div>"
                 "<div><span class='badge' style='background:%s'>%s</span> <span style='color:#cbd5e1;font-size:13px'>%s KST · height %s</span></div></header><main>"
                 % (st_color, _e(st), _e(now), _e(ch.get("height"))))
    # header KPIs (master map desired_dashboard.header)
    hd = decision.get("header") or {}
    if hd:
        parts.append("<section><h2>자본 KPI (③ Opportunity/Decision)</h2><div class='grid'>")
        for key, label in (("TOTAL_POKT", "총 보유 (선언)"), ("ALLOCATED", "배분 의향"), ("RESERVED", "예비"), ("ACTUALLY_STAKED", "실제 스테이크 (체인)"),
                           ("ACTUAL_REWARD_RECEIVED", "실제 수령 보상 7d (체인)"), ("WALLET_LIQUID", "owner 유동 잔액"), ("ACTUAL_CASH_REALIZED", "현금화 실현")):
            k = hd.get(key) or {}
            parts.append("<div class='kpi'><small>%s</small><span class='big'>%s</span><small>%s</small></div>" % (_e(label), _num(k.get("value")) if k.get("value") is not None else "–", _e(k.get("evidence"))))
        parts.append("</div><p><b>다음 사용자 행동:</b> %s</p>" % _e((hd.get("NEXT_USER_ACTION") or {}).get("value")))
        imp = decision.get("impacts") or {}
        col = {"NONE": "#6b7280", "INFO": "#1d4ed8", "REVIEW": "#b45309", "HIGH": "#b91c1c"}
        parts.append("<table><tr><th>트랙</th><th>영향</th><th>이유</th></tr>")
        for t, name in (("A", "A Validator"), ("B", "B Supplier capital"), ("C", "C Direct Supplier"), ("D", "D AI/API")):
            i = imp.get(t) or {}
            parts.append("<tr><td>%s</td><td><span class='badge' style='background:%s'>%s</span></td><td class='muted'>%s</td></tr>" % (
                _e(name), col.get(i.get("level"), "#6b7280"), _e(i.get("level")), _e("; ".join((i.get("reasons") or [])[:3])[:200])))
        parts.append("</table></section>")
    # agents row (master map: 3 agents)
    sv = state.get("surveillance") or {}
    parts.append("<section><h2>에이전트 3종 상태</h2><div class='grid'>")
    parts.append("<div class='kpi'><small>① Surveillance 상태 관찰</small><span class='big'>%s</span><small>출처 %s · 변경 %s · 실패 %s · %s</small></div>" % (
        "ON" if sv.get("available") else "OFF", _e(sv.get("sources")), _e(sv.get("changed_last_run")), _e(sv.get("failed_last_run")), _e(sv.get("at_kst"))))
    svcs_ok = [s for s in (state.get("services") or []) if s.get("found")]
    parts.append("<div class='kpi'><small>② Settlement Intelligence</small><span class='big'>%s</span><small>서비스 %s · 공개 URL agent.pokt-agent.com</small></div>" % (
        "REGISTERED" if svcs_ok else "LOCAL", _e(",".join(s["service_id"] for s in svcs_ok) or "–")))
    parts.append("<div class='kpi'><small>③ Opportunity / Decision</small><span class='big'>%s</span><small>결정 기록 %s건</small></div>" % (
        "ON" if hd else "OFF", _e(len(decision.get("recent_decisions") or []))))
    parts.append("</div>")
    if sv.get("recent_events"):
        parts.append("<table><tr><th>시각</th><th>출처</th><th>변화</th><th class='n'>+</th><th class='n'>−</th><th>등급</th></tr>")
        for ev in sv["recent_events"]:
            parts.append("<tr><td class='muted'>%s</td><td>%s</td><td>%s</td><td class='n'>%s</td><td class='n'>%s</td><td>%s</td></tr>" % (
                _e(str(ev.get("at_utc"))[:16]), _e(ev.get("source_id")), _e(ev.get("change")), _e(ev.get("added_lines")), _e(ev.get("removed_lines")), _e(ev.get("classification"))))
        parts.append("</table>")
    parts.append("</section>")
    # user
    parts.append("<section><h2>내 자산 (owner 주소 기준, 공개 체인 조회)</h2>")
    if u.get("configured"):
        parts.append("<div class='muted'>%s</div><div class='grid'>" % _e(u.get("owner_address")))
        for label, val in (("지갑 잔액 POKT", u.get("balance_pokt")), ("위임 중 POKT", u.get("delegated_pokt")),
                           ("미수령 위임 보상 POKT", (u.get("rewards") or {}).get("total_pokt")), ("언본딩 건수", len(u.get("unbonding") or [])),
                           ("소유 supplier", len(u.get("owned_suppliers") or []))):
            parts.append("<div class='kpi'><small>%s</small><span class='big'>%s</span></div>" % (_e(label), _num(val) if "POKT" in label else _e(val)))
        parts.append("</div>")
        if u.get("delegations"):
            parts.append("<table><tr><th>검증자</th><th class='n'>POKT</th></tr>" + "".join(
                "<tr><td>%s</td><td class='n'>%s</td></tr>" % (_e(x.get("validator")), _num(x.get("pokt"))) for x in u["delegations"]) + "</table>")
    else:
        parts.append("<p>지갑 주소 미설정. 자가보관 지갑을 만든 뒤 Mac에서 <code>python -m pocket_agents set-user --owner pokt1...</code> 를 실행하면 다음 주기부터 여기에 잔액·위임·소유 supplier가 표시됩니다.</p>")
    parts.append("</section>")
    # alerts
    parts.append("<section><h2>알림 (활성 %d, 신규 %d)</h2>" % (len(alerts), sum(1 for a in alerts if a.get("new"))))
    if alerts:
        parts.append("<table><tr><th>등급</th><th>코드</th><th>대상</th><th>내용</th><th>최초</th></tr>")
        for a in alerts:
            parts.append("<tr><td><span class='badge' style='background:%s'>%s</span></td><td>%s</td><td>%s</td><td>%s%s</td><td class='muted'>%s</td></tr>" % (
                sev_color.get(a.get("severity"), "#374151"), _e(a.get("severity")), _e(a.get("code")), _e(str(a.get("subject"))[:18]), _e(a.get("message")),
                " <b>(NEW)</b>" if a.get("new") else "", _e(str(a.get("first_seen_utc"))[:16])))
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>없음</p>")
    parts.append("</section>")
    # services (track D)
    svcs = state.get("services") or []
    if svcs:
        parts.append("<section><h2>D 등록 서비스 (온체인)</h2><table><tr><th>service id</th><th>상태</th><th>owner</th><th class='n'>CU/relay</th><th class='n'>card</th></tr>")
        for sv in svcs:
            parts.append("<tr><td>%s</td><td>%s</td><td class='muted'>%s</td><td class='n'>%s</td><td class='n'>%s B</td></tr>" % (
                _e(sv.get("service_id")), "<span class='badge' style='background:#15803d'>REGISTERED</span>" if sv.get("found") else "<span class='badge' style='background:#b45309'>NOT FOUND</span>",
                _e(sv.get("owner_address")), _e(sv.get("compute_units_per_relay")), _e(sv.get("card_bytes"))))
        parts.append("</table></section>")
    # suppliers
    parts.append("<section><h2>Supplier 정산 관측 (C 직접 운영 · B 위탁)</h2><table><tr><th>label</th><th>operator</th><th class='n'>stake</th><th>services</th><th>unbonding</th><th class='n'>24h POKT</th><th class='n'>7d POKT</th><th>마지막 정산</th><th>scan</th></tr>")
    for s in state.get("suppliers", []):
        r24 = (s.get("rewards") or {}).get("24h") or {}
        r7 = (s.get("rewards") or {}).get("7d") or {}
        last = s.get("last_settlement") or {}
        parts.append("<tr><td>%s</td><td class='muted'>%s…</td><td class='n'>%s</td><td>%s</td><td>%s</td><td class='n'>%s <span class='muted'>(%s)</span></td><td class='n'>%s <span class='muted'>(%s)</span></td><td class='muted'>%s</td><td class='muted'>%s</td></tr>" % (
            _e(s.get("label")), _e((s.get("operator_address") or "")[:12]), _num(s.get("stake_pokt")), _e(",".join(s.get("service_ids") or [])[:48]), _e(s.get("unbonding")),
            _num(r24.get("reward_pokt")), _e(r24.get("settlements")), _num(r7.get("reward_pokt")), _e(r7.get("settlements")), _e(str(last.get("block_time_utc") or "")[:16]), _e((s.get("scan") or {}).get("coverage"))))
    parts.append("</table><p class='muted'>표본(PUBLIC_SAMPLE)은 다른 운영자의 공개 정산이며 내 수익이 아닙니다. 파라미터: supplier min_stake %s POKT · supplier 언본딩 %s블록 · validator 언본딩 %ss</p></section>" % (
        _num(d.get("supplier_min_stake_pokt")), _e(d.get("supplier_unbonding_blocks")), _e(d.get("validator_unbonding_seconds"))))
    # validators
    v = state.get("validators") or {}
    sm = v.get("summary") or {}
    parts.append("<section><h2>A Validator 후보</h2><p class='muted'>활성 %s/%s · 총 본딩 %s POKT · 활성집합 컷오프 %s POKT · jailed %s</p><table><tr><th>후보</th><th>상태</th><th>jailed</th><th class='n'>토큰 POKT</th><th class='n'>수수료</th><th class='n'>순위</th></tr>" % (
        _e(sm.get("bonded_count")), _e(sm.get("max_validators")), _num(sm.get("total_bonded_pokt")), _num(sm.get("active_set_cutoff_pokt")), _e(sm.get("jailed_count"))))
    for c in v.get("candidates", []):
        parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>" % (
            _e(c.get("moniker")), _e(str(c.get("status") or "").replace("BOND_STATUS_", "")), _e(c.get("jailed")), _num(c.get("tokens_pokt")), _e(c.get("commission_rate")), _e(c.get("rank"))))
    parts.append("</table></section>")
    # network supplier economics (mobile-friendly KPI row + inline sparkline)
    ne = state.get("network_economics") or {}
    if ne and not ne.get("error"):
        sp = ne.get("supplier_stake_pokt") or {}
        sup = ne.get("suppliers") or {}
        yl = ne.get("observed_network_average_yield") or {}
        se = ne.get("settlement") or {}
        series = [pt.get("stakedPokt") for pt in (sp.get("series_60d") or []) if pt.get("stakedPokt")]
        svg = ""
        if len(series) >= 2:
            lo, hi = min(series), max(series)
            w, h = 300, 60
            pts = " ".join("%.1f,%.1f" % (i * w / (len(series) - 1), h - ((v - lo) / (hi - lo) * (h - 6) if hi > lo else h / 2) - 3) for i, v in enumerate(series))
            svg = "<svg viewBox='0 0 %d %d' width='100%%' height='60' preserveAspectRatio='none' style='display:block;max-width:420px'><polyline fill='none' stroke='#2563eb' stroke-width='2' points='%s'/></svg>" % (w, h, pts)
        parts.append("<section><h2>네트워크 서플라이어 경제 (analytics.pocket.network, %s)</h2><div class='grid'>" % _e(str(ne.get("as_of") or "")[:10]))
        parts.append("<div class='kpi'><small>총 서플라이어 스테이크 POKT</small><span class='big'>%s</span><small>7d %s%% · 30d %s%%</small></div>" % (_num(sp.get("total")), _e(sp.get("change_7d_pct")), _e(sp.get("change_30d_pct"))))
        parts.append("<div class='kpi'><small>서플라이어 수</small><span class='big'>%s</span><small>7d %s%% · unstaking %s</small></div>" % (_e(sup.get("count")), _e(sup.get("count_change_7d_pct")), _e(sup.get("unstaking"))))
        parts.append("<div class='kpi'><small>네트워크 평균 관측 수익률 (서플라이어 몫, 연환산, 비용 전)</small><span class='big'>%s%%</span><small>앱 지불 기준 %s%% × 서플라이어 배분 0.77 · 60일 평균 %s POKT/일 ÷ 총 스테이크 · 예측 아님</small></div>" % (_e(yl.get("supplier_share_annualised_pct")), _e(yl.get("annualised_pct")), _num(se.get("claimed_pokt_per_day_60d_avg"))))
        parts.append("<div class='kpi'><small>릴레이 24h</small><span class='big'>%s</span><small>변화 %s%% · claims/일 %s</small></div>" % (_num(se.get("relays_24h")), _e(round(se["relays_24h_change_pct"], 2) if isinstance(se.get("relays_24h_change_pct"), (int, float)) else None), _e(se.get("claims_last_day"))))
        parts.append("</div><div class='muted' style='margin-top:8px'>최근 30일 총 서플라이어 스테이크</div>%s</section>" % svg)
    # market
    parts.append("<section><h2>시장 (기존 Gate/Bithumb 모니터 파일)</h2>")
    if m.get("available"):
        g, b = m.get("gate") or {}, m.get("bithumb") or {}
        parts.append("<div class='grid'><div class='kpi'><small>Gate POKT/USDT</small><span class='big'>%s</span><small>24h %s · %s</small></div><div class='kpi'><small>Bithumb POKT/KRW</small><span class='big'>%s</span><small>24h %s · %s</small></div><div class='kpi'><small>프리미엄 %%</small><span class='big'>%s</span><small>%s · %s</small></div></div>" % (
            _e(g.get("live_price")), _e(g.get("ret24")), _e(g.get("quality")), _e(b.get("live_price")), _e(b.get("ret24")), _e(b.get("quality")),
            _e((m.get("cross") or {}).get("premium_pct")), _e(m.get("data_health")), _e((m.get("monitor_state") or {}).get("status"))))
    else:
        parts.append("<p class='muted'>%s</p>" % _e(m.get("reason")))
    parts.append("</section>")
    # brief
    parts.append("<section><h2>오늘 브리프 (08:40 자동)</h2><pre>%s</pre></section>" % (_e(brief_md) if brief_md else "<span class='muted'>아직 없음</span>"))
    parts.append("<p class='muted'>모든 수치는 공개 REST/RPC GET 관측이며 사용자 수익·비용 후 순수익·매도 가능액이 아닙니다. 이 페이지는 키·비밀값을 다루지 않습니다. 원본: <a href='/state.json'>state.json</a> · <a href='/alerts.json'>alerts.json</a> · <a href='/status.md'>status.md</a></p></main></body></html>")
    return "".join(parts)


def latest_brief(data_dir):
    root = os.path.join(data_dir, "results", "pokt_daily_brief")
    if not os.path.isdir(root):
        return None
    days = sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "brief.md")))
    if not days:
        return None
    with open(os.path.join(root, days[-1], "brief.md"), encoding="utf-8") as fh:
        return fh.read()


class _Handler(http.server.SimpleHTTPRequestHandler):
    root = None

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, head_only=False):
        path = self.path.split("?", 1)[0]
        entry = ALLOWED.get(path)
        if not entry:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        fp = os.path.join(self.root, entry[0])
        if not os.path.isfile(fp):
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            body = b"not generated yet; run pokt collect"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return
        with open(fp, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", entry[1])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self):
        self._send()

    def do_HEAD(self):
        self._send(head_only=True)


def serve(data_dir, host="0.0.0.0", port=8792):
    _Handler.root = os.path.join(data_dir, "pokt")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()
