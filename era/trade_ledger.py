# -*- coding: utf-8 -*-
"""선물 매매 측정 원장 (2026-08-12 신규).

목적
----
로그·텔레그램의 손익이 슬리피지와 수수료를 반영하지 않아 실제보다 좋게 보인다.
2026-08-12 실계좌 첫 3건에서 로그 -0.50pt vs 계좌 실제 -1.162pt로 0.66pt(33,117원)
차이가 났고, 이를 확인하려고 잔고를 역산해야 했다. 어떤 개선을 해도 효과를 측정할
수 없는 상태라 이 모듈을 먼저 만든다.

설계 원칙 (실계좌 운용 중이므로 엄수)
------------------------------------
1. **매매 로직은 절대 건드리지 않는다.** 이 모듈은 읽기만 하고 아무것도 되돌리지 않는다.
2. **모든 공개 함수는 예외를 밖으로 내보내지 않는다.** 기록이 실패해도 매매는 계속된다.
3. 호출부(era_order_manager.py)는 `try/except`로 한 번 더 감싼다 (이중 방어).
4. 파일 쓰기는 append 1회(≈1KB)로 서브밀리초. 버퍼링/비동기 없이도 매매 루프를 막지 않는다.

출력
----
era/ledger/fills_YYYYMMDD.jsonl    : 레그(주문·체결) 단위 원자료
era/ledger/trades_YYYYMMDD.jsonl   : 진입-청산이 짝지어진 거래 단위 손익
era/ledger/daily_YYYYMMDD.json     : 일일 요약

되돌리는 법
----------
era_order_manager.py의 `_LEDGER` 관련 4개 호출 블록을 삭제하고 이 파일을 지우면 된다.
호출부를 남겨둔 채 이 파일만 지워도 import 실패가 흡수되어 매매는 정상 동작한다.
"""
import os
import json
import time
from datetime import datetime

_WARNED = set()


def _warn_once(key, msg):
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        print("[TradeLedger] %s" % msg)
    except Exception:
        pass


class TradeLedger:
    """건별 체결·거래 단위 손익 기록기. 어떤 메서드도 예외를 밖으로 던지지 않는다."""

    # 실측 기본값 (2026-08-12 실계좌 3건, 잔고 역산). 왕복 3,039원/계약 = 명목의 0.0030%
    DEFAULT_COMMISSION_RATE = 0.00003

    def __init__(self, workspace_root, point_value_getter=None, commission_rate=None,
                 emit=None):
        self.root = workspace_root
        self.dir = os.path.join(workspace_root, "era", "ledger")
        self._pv_getter = point_value_getter          # 승수를 엔진에서 가져오는 콜백
        self._emit = emit                             # 텔레그램 발신 콜백(선택)
        self.commission_rate = (commission_rate if commission_rate is not None
                                else self.DEFAULT_COMMISSION_RATE)
        self.pending_order = None                     # 직전에 전송한 주문의 의도 정보
        self.open_trade = None                        # 진입 체결 후 청산 전까지의 거래 상태
        try:
            os.makedirs(self.dir, exist_ok=True)
        except Exception as e:
            _warn_once("mkdir", "디렉터리 생성 실패(기록 비활성): %s" % e)

    # ── 내부 유틸 ────────────────────────────────────────────────────────
    def _path(self, kind, day=None):
        d = day or datetime.now().strftime("%Y%m%d")
        return os.path.join(self.dir, "%s_%s.jsonl" % (kind, d))

    def _append(self, kind, rec):
        try:
            rec.setdefault("ts", datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
            with open(self._path(kind), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            _warn_once("append_" + kind, "%s 기록 실패: %s" % (kind, e))

    def _pv(self):
        try:
            if self._pv_getter:
                v = self._pv_getter()
                if v:
                    return float(v)
        except Exception:
            pass
        return 50000.0

    @staticmethod
    def _snap(engine):
        """체결 시점 전략 내부 상태 스냅샷. 없는 값은 None으로 남긴다(추정하지 않음)."""
        g = lambda n, d=None: getattr(engine, n, d)
        try:
            return {
                "sar_value": g("sar_value"), "sar_ep": g("sar_ep"),
                "sar_af": g("sar_af"), "sar_bull": g("sar_bull"),
                "atr14": g("futures_atr_14"), "std_error": g("futures_std_error"),
                "ma200": g("futures_ma_filter_value"), "ma_prev_close": g("futures_ma_filter_close"),
                "bb_mid": g("current_bb_mid"), "bb_bandwidth": g("current_bb_bandwidth"),
                "bb_squeeze_limit": g("current_bb_squeeze_limit"),
                "bb_upper": g("bb_upper"), "bb_lower": g("bb_lower"),
                "strategy": g("futures_strategy_type"),
                "balance": g("futures_available_balance"),
                "daily_loss": g("futures_daily_loss"), "daily_halted": g("futures_daily_halted"),
                "trade_count": g("futures_day_trade_count"),
                "consec_losses": g("futures_day_consecutive_losses"),
            }
        except Exception:
            return {}

    # ── 훅 A: 주문 전송 직전 ─────────────────────────────────────────────
    def on_order(self, engine, signal_type, intended_price, qty, order_code, pos_key):
        try:
            self.pending_order = {
                "order_time": time.time(),
                "order_ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "signal_type": signal_type, "intended_price": float(intended_price),
                "qty": int(qty), "code": order_code, "pos_key": pos_key,
                "fills": [], "state": self._snap(engine),
            }
            self._append("fills", {
                "event": "ORDER", "signal_type": signal_type,
                "intended_price": float(intended_price), "qty": int(qty),
                "code": order_code, "pos_key": pos_key,
                "state": self.pending_order["state"],
            })
        except Exception as e:
            _warn_once("on_order", "주문 기록 실패: %s" % e)

    # ── 훅 B: 체결 통보 ──────────────────────────────────────────────────
    def on_fill(self, engine, code, exec_price, exec_qty, order_gubun, order_no,
                is_entry=None, commission=None):
        try:
            po = self.pending_order
            intended = po["intended_price"] if po else None
            signal_type = po["signal_type"] if po else None
            slip = None
            if intended is not None:
                buy = ("매수" in (order_gubun or "")) or ("환매" in (order_gubun or ""))
                slip = (exec_price - intended) if buy else (intended - exec_price)
            if po is not None:
                po["fills"].append({"price": float(exec_price), "qty": int(exec_qty)})
            rec = {
                "event": "FILL", "code": code, "order_no": order_no,
                "order_gubun": order_gubun, "exec_price": float(exec_price),
                "exec_qty": int(exec_qty), "intended_price": intended,
                "slippage_pt": (round(slip, 4) if slip is not None else None),
                "signal_type": signal_type,
                "order_ts": (po["order_ts"] if po else None),
                "fill_seq": (len(po["fills"]) if po else None),
                "latency_sec": (round(time.time() - po["order_time"], 3) if po else None),
                "commission_raw": commission,
                "state": self._snap(engine),
            }
            self._append("fills", rec)
            self._pair(engine, signal_type, exec_price, exec_qty, commission)
        except Exception as e:
            _warn_once("on_fill", "체결 기록 실패: %s" % e)

    def _pair(self, engine, signal_type, exec_price, exec_qty, commission):
        """진입 체결이면 거래를 열고, 청산 체결이면 닫으며 손익을 확정한다."""
        try:
            if not signal_type:
                return
            pv = self._pv()
            po = self.pending_order or {}
            if "ENTER" in signal_type:
                self.open_trade = {
                    "entry_ts": po.get("order_ts"), "entry_time": time.time(),
                    "direction": "LONG" if "LONG" in signal_type else "SHORT",
                    "entry_intended": po.get("intended_price"),
                    "entry_fill": float(exec_price), "qty": int(exec_qty),
                    "entry_state": po.get("state", {}),
                    "entry_balance": getattr(engine, "futures_available_balance", None),
                    "entry_comm": commission,
                    "mfe_pt": 0.0, "mae_pt": 0.0, "tick_samples": 0,
                }
                return
            if "EXIT" not in signal_type or not self.open_trade:
                return
            ot = self.open_trade
            d = 1 if ot["direction"] == "LONG" else -1
            gross_pt = (po.get("intended_price", exec_price) - ot["entry_intended"]) * d \
                if ot.get("entry_intended") is not None else None
            net_pt = (float(exec_price) - ot["entry_fill"]) * d
            slip_pt = (net_pt - gross_pt) * -1 if gross_pt is not None else None
            qty = ot["qty"]
            comm = 0.0
            for c in (ot.get("entry_comm"), commission):
                if c:
                    comm += float(c)
            if comm == 0.0:
                nominal = (ot["entry_fill"] + float(exec_price)) / 2.0 * pv * qty
                comm = nominal * self.commission_rate * 2
                comm_src = "추정(실측요율 %.5f%%)" % (self.commission_rate * 100)
            else:
                comm_src = "체결통보"
            rec = {
                "event": "TRADE", "direction": ot["direction"], "qty": qty,
                "entry_ts": ot["entry_ts"], "exit_ts": po.get("order_ts"),
                "hold_sec": round(time.time() - ot["entry_time"], 3),
                "entry_intended": ot["entry_intended"], "entry_fill": ot["entry_fill"],
                "exit_intended": po.get("intended_price"), "exit_fill": float(exec_price),
                "gross_pt": (round(gross_pt, 4) if gross_pt is not None else None),
                "slippage_pt": (round(slip_pt, 4) if slip_pt is not None else None),
                "fill_pt": round(net_pt, 4),
                "gross_won": (round(gross_pt * pv * qty) if gross_pt is not None else None),
                "slippage_won": (round(slip_pt * pv * qty) if slip_pt is not None else None),
                "commission_won": round(comm),
                "commission_src": comm_src,
                "net_won": round(net_pt * pv * qty - comm),
                "net_pt": round(net_pt - comm / pv / qty, 4),
                "mfe_pt": round(ot.get("mfe_pt", 0.0), 4),
                "mae_pt": round(ot.get("mae_pt", 0.0), 4),
                "tick_samples": ot.get("tick_samples", 0),
                "entry_balance": ot.get("entry_balance"),
                "exit_balance": getattr(engine, "futures_available_balance", None),
                "entry_state": ot.get("entry_state", {}), "exit_state": self._snap(engine),
            }
            self._append("trades", rec)
            self.open_trade = None
            self.last_trade = rec
            # 기존 청산 알림은 그대로 두고(주문 시점·의도가 기준), 체결이 확정된 뒤
            # gross/net을 나란히 보여주는 알림을 '추가'로 하나 더 보낸다.
            # 기존 알림 코드를 건드리지 않으므로 매매 경로 diff가 0이다.
            try:
                # %-포맷은 %,d 를 지원하지 않는다(조용히 예외 → 알림 누락). format()으로 통일.
                line = ("📒 <b>[체결 확정 손익]</b> {} {}계약\n"
                        "• gross(의도가) {:+.2f}pt ({:+,}원)\n"
                        "• 슬리피지 {:+.2f}pt ({:+,}원)\n"
                        "• 수수료 −{:,}원 [{}]\n"
                        "• <b>net(계좌) {:+.2f}pt ({:+,}원)</b>\n"
                        "• 보유 {:.1f}초 | MFE {:+.2f}pt / MAE {:+.2f}pt"
                        .format(rec["direction"], rec["qty"],
                                rec.get("gross_pt") or 0, int(rec.get("gross_won") or 0),
                                rec.get("slippage_pt") or 0, int(rec.get("slippage_won") or 0),
                                int(rec["commission_won"]), rec["commission_src"],
                                rec["net_pt"], int(rec["net_won"]),
                                rec["hold_sec"], rec["mfe_pt"], rec["mae_pt"]))
                print(line.replace("<b>", "").replace("</b>", ""))
                if self._emit:
                    self._emit(line)
            except Exception:
                pass
        except Exception as e:
            _warn_once("pair", "거래 확정 실패: %s" % e)

    # ── 훅 C: 보유 중 틱 (MFE/MAE) ───────────────────────────────────────
    def on_tick(self, price):
        try:
            ot = self.open_trade
            if not ot or not price:
                return
            d = 1 if ot["direction"] == "LONG" else -1
            pnl = (float(price) - ot["entry_fill"]) * d
            if pnl > ot["mfe_pt"]:
                ot["mfe_pt"] = pnl
            if pnl < ot["mae_pt"]:
                ot["mae_pt"] = pnl
            ot["tick_samples"] += 1
        except Exception:
            pass   # 틱마다 도는 경로 — 경고조차 남기지 않는다

    # ── 훅 D: 일일 요약 ──────────────────────────────────────────────────
    def daily_summary(self, day=None, emit=None):
        try:
            d = day or datetime.now().strftime("%Y%m%d")
            p = self._path("trades", d)
            if not os.path.exists(p):
                return None
            rows = []
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            if not rows:
                return None
            n = len(rows)
            g = sum(r.get("gross_won") or 0 for r in rows)
            s = sum(r.get("slippage_won") or 0 for r in rows)
            c = sum(r.get("commission_won") or 0 for r in rows)
            net = sum(r.get("net_won") or 0 for r in rows)
            pv = self._pv()
            cost = s + c
            edge_pt = (g / pv / n) if n else 0.0
            cost_pt = (cost / pv / n) if n else 0.0
            summ = {
                "date": d, "trades": n,
                "gross_won": g, "slippage_won": s, "commission_won": c,
                "total_cost_won": cost, "net_won": net,
                "avg_edge_pt": round(edge_pt, 4), "avg_cost_pt": round(cost_pt, 4),
                "breakeven": ("수익가능" if edge_pt > cost_pt else "비용>엣지"),
                "wins": sum(1 for r in rows if (r.get("net_won") or 0) > 0),
                "avg_hold_sec": round(sum(r.get("hold_sec") or 0 for r in rows) / n, 1),
            }
            try:
                with open(os.path.join(self.dir, "daily_%s.json" % d), "w", encoding="utf-8") as f:
                    json.dump(summ, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            # 주의: %-포맷은 정수 천단위 콤마(%,d)를 지원하지 않는다. format()을 쓴다.
            line = ("[측정원장 {}] {}건 | gross {:+,}원 | 슬리피지 -{:,} | 수수료 -{:,} | "
                    "net {:+,}원 | 건당엣지 {:+.3f}pt vs 건당비용 {:.3f}pt → {}"
                    .format(d, n, int(g), int(s), int(c), int(net),
                            edge_pt, cost_pt, summ["breakeven"]))
            print(line)
            if emit:
                try:
                    emit(line)
                except Exception:
                    pass
            return summ
        except Exception as e:
            _warn_once("summary", "일일 요약 실패: %s" % e)
            return None

    # ── 로그·텔레그램 표시용 보조 ────────────────────────────────────────
    def net_suffix(self):
        """직전 확정 거래의 net을 짧은 문자열로. 기존 표시를 지우지 않고 덧붙이는 용도."""
        try:
            r = getattr(self, "last_trade", None)
            if not r:
                return ""
            return (" | net {:+.2f}pt ({:+,}원, 슬립 {:.2f}·수수료 {:,})"
                    .format(r["net_pt"], int(r["net_won"]),
                            abs(r.get("slippage_pt") or 0), int(r["commission_won"])))
        except Exception:
            return ""
