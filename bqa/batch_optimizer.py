import sqlite3
import pandas as pd
import numpy as np
import json
import os
import sys
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

# 결과 저장 경로
RESULTS_FILE = os.path.join(workspace_root, "config", "active_strategy.json")

def load_data(db_path):
    try:
        conn = sqlite3.connect(db_path)
        query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10100000' ORDER BY date ASC"
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if not df.empty:
            raw_dates = df['date'].copy()
            df['date'] = pd.to_datetime(raw_dates, format='%Y%m%d%H%M%S', errors='coerce')
            if df['date'].isnull().all():
                # 포맷 미매칭 시 원본 값으로 재시도
                df['date'] = pd.to_datetime(raw_dates, errors='coerce')
            df.set_index('date', inplace=True)
            df.sort_index(inplace=True)
        return df
    except Exception as e:
        print(f"데이터 로드 에러: {e}")
        return pd.DataFrame()

def run_backtest_with_k(df, K, stop_loss_pt=2.0, take_profit_pt=5.0):
    """
    ERA 실전 로직과 동일한 백테스트:
      - 진입: K값 변동성 돌파 (high/low >= target)
      - 손절: -stop_loss_pt (기본 2.0pt)
      - 익절: +take_profit_pt (기본 5.0pt)
      - 강제 청산: 08:45~08:50
    """
    if len(df) < 50:
        return None

    df = df.copy()
    df['date_only'] = df.index.date
    daily_stats = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily_stats['range'] = daily_stats['high'] - daily_stats['low']
    daily_stats['prev_range'] = daily_stats['range'].shift(1)

    df = df.join(daily_stats[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df.rename(columns={'open_day': 'day_open'}, inplace=True)

    POINT_VALUE = 250000
    INITIAL_CAPITAL = 50_000_000
    MARGIN_RATIO = 0.10   # 위탁증거금률 10%
    MARGIN_CAP   = 0.30   # 가용 자본의 30% 한도
    SLIPPAGE     = 0.05   # 슬리피지 0.05pt

    current_capital = INITIAL_CAPITAL
    equity = [INITIAL_CAPITAL]

    position = 0
    entry_price = 0.0
    total_trades = 0
    winning_trades = 0

    # 계약 수: 초기 자본 기준 고정 (복리 폭주 방지)
    first_price = df['close'].iloc[0]
    margin_per = first_price * POINT_VALUE * MARGIN_RATIO
    safe_budget = INITIAL_CAPITAL * MARGIN_CAP
    CONTRACTS = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1

    for i in range(len(df)):
        curr_row = df.iloc[i]
        current_time = df.index[i]
        current_price = curr_row['close']
        day_open = curr_row['day_open']
        prev_range = curr_row['prev_range']

        if pd.isna(prev_range) or prev_range <= 0:
            continue

        target_long  = day_open + prev_range * K
        target_short = day_open - prev_range * K
        force_close  = (current_time.hour == 8 and 45 <= current_time.minute <= 50)

        if position != 0:
            exit_price = None

            if position == 1:  # LONG
                pnl = current_price - entry_price
                if pnl <= -stop_loss_pt:
                    exit_price = entry_price - stop_loss_pt
                elif pnl >= take_profit_pt:
                    exit_price = entry_price + take_profit_pt
                elif force_close:
                    exit_price = current_price
            else:              # SHORT
                pnl = entry_price - current_price
                if pnl <= -stop_loss_pt:
                    exit_price = entry_price + stop_loss_pt
                elif pnl >= take_profit_pt:
                    exit_price = entry_price - take_profit_pt
                elif force_close:
                    exit_price = current_price

            if exit_price is not None:
                profit_pt  = (exit_price - entry_price if position == 1 else entry_price - exit_price) - SLIPPAGE
                trade_pnl  = profit_pt * POINT_VALUE * CONTRACTS
                current_capital += trade_pnl
                equity.append(current_capital)
                total_trades += 1
                if trade_pnl > 0:
                    winning_trades += 1
                position = 0
            continue

        if position == 0 and not force_close:
            if curr_row['high'] >= target_long:
                position = 1
                entry_price = target_long
            elif curr_row['low'] <= target_short:
                position = -1
                entry_price = target_short

    # MDD 계산
    peak = equity[0]
    mdd = 0.0
    for eq in equity:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > mdd:
            mdd = dd

    win_rate   = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    profit_pct = (current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days_diff  = max(1, (df.index[-1] - df.index[0]).days)
    cagr       = ((current_capital / INITIAL_CAPITAL) ** (365 / days_diff) - 1) * 100 if current_capital > 0 else -100

    return {
        'K':        K,
        'trades':   total_trades,
        'win_rate': round(win_rate, 2),
        'cagr':     round(cagr, 2),
        'mdd':      round(mdd, 2),
        'profit':   round(profit_pct, 2),
        'final_capital': round(current_capital),
    }

def optimize():
    print(f"[{datetime.now()}] ERA 실전 로직 반영 K값 최적화 시작 (손절 2pt / 익절 5pt)...")
    db_path = os.path.join(workspace_root, "futures_data.db")
    df = load_data(db_path)
    if df.empty:
        print("데이터가 없습니다. futures_data.db에 선물 데이터를 먼저 수집하세요.")
        return

    print(f"데이터 로드: {len(df)}봉 ({df.index[0]} ~ {df.index[-1]})")

    results = []
    k_values = np.arange(0.1, 1.05, 0.05)

    print(f"\n{'K값':>6} | {'거래':>6} | {'승률%':>7} | {'CAGR%':>8} | {'MDD%':>7} | {'CAGR/MDD':>8}")
    print("-" * 60)

    for k in k_values:
        k = round(k, 2)
        res = run_backtest_with_k(df, k)
        if res and res['trades'] > 10:
            results.append(res)
            ratio = res['cagr'] / max(res['mdd'], 0.1)
            print(f"{res['K']:>6.2f} | {res['trades']:>6} | {res['win_rate']:>7.1f} | {res['cagr']:>8.2f} | {res['mdd']:>7.2f} | {ratio:>8.2f}")

    # ── 자율 피드백 고리 (Sharpe/MDD 기반 자율 핫-리로드 & 텔레그램 알림) ──
    if not results:
        print("유효한 백테스트 결과가 없습니다. 데이터를 확인하세요.")
        return

    top_results = sorted(results, key=lambda x: x['cagr'] / max(x['mdd'], 0.1), reverse=True)

    old_best_k = 0.5
    old_ratio = 0.0

    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            old_best_k = old_data.get("best_k", 0.5)
            old_top = old_data.get("top_strategies", [])
            if old_top:
                old_ratio = old_top[0].get("cagr", 0) / max(old_top[0].get("mdd", 0.1), 0.1)
        except Exception:
            pass

    new_best_k = top_results[0]['K']
    new_ratio = top_results[0]['cagr'] / max(top_results[0]['mdd'], 0.1)
    
    msg = f"📊 <b>[BQA 주말 자율 최적화 분석 완료]</b>\n🕒 완료시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    msg += f"🏆 <b>최종 도출 1위 전략:</b>\n"
    msg += f"  • 파라미터 (K값): {new_best_k}\n"
    msg += f"  • CAGR(연성장률): {top_results[0]['cagr']}%\n"
    msg += f"  • MDD(최대낙폭): {top_results[0]['mdd']}%\n"
    msg += f"  • CAGR/MDD 비율: {new_ratio:.2f}\n"
    msg += f"  • 통계적 승률: {top_results[0]['win_rate']}%\n\n"
    
    best_k = new_best_k
    # CAGR/MDD 비율이 향상되었거나 기존 파일이 깨진 경우 자율 핫-리로드 자동 승인
    if new_ratio > old_ratio or old_ratio == 0:
        approved_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg += f"🔄 <b>[자율 진화 완료]</b>\n"
        msg += f"성능이 기존 대비 우수(CAGR/MDD {old_ratio:.2f} ➡️ {new_ratio:.2f})하여, "
        msg += f"신규 K값(<b>K={new_best_k}</b>)의 무인 자동 적용을 즉시 승인하였습니다.\n"
        msg += f"ERA 매매 엔진이 다음 거래일에 새 전략으로 자동 교체 가동됩니다."
    else:
        # 기존 K값 수호 (횡보장 노이즈 방어)
        best_k = old_best_k
        top_results = old_top if 'old_top' in locals() and old_top else top_results
        approved_time = old_data.get("approved_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")) if 'old_data' in locals() else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg += f"🛡️ <b>[기존 전략 고수]</b>\n"
        msg += f"신규 분석(CAGR/MDD {new_ratio:.2f})이 기존 검증된 전략({old_ratio:.2f})보다 보합/열세에 있습니다.\n"
        msg += f"안전을 위해 검증된 기존 파라미터(<b>K={old_best_k}</b>)를 흔들림 없이 유지합니다."

    out_data = {
        'last_updated':   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'approved_at':     approved_time,
        'strategy':       'era_realworld_v1',
        'best_k':         best_k,
        'stop_loss_pt':   2.0,
        'take_profit_pt': 5.0,
        'top_strategies': top_results,
    }

    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=4)
        
    print(f"\n결과 저장 완료: {RESULTS_FILE}")
    print(f"=> ERA 최종 적용 K값: {best_k}")
    
    # 텔레그램으로 주말 진화 완료 보고서 전송!
    try:
        import notifier
        if notifier:
            notifier.send_message(msg)
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")

if __name__ == "__main__":
    optimize()
