import sqlite3
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

def run_strat1_backtest():
    print("==========================================================")
    print(" [전략 1: 테마 2등주 짝짓기 매매] 프록시(Proxy) 백테스트 결과")
    print("==========================================================")
    
    conn = sqlite3.connect("kiwoom_data.db")
    df = pd.read_sql_query("SELECT code, date, open, high, low, close, volume FROM intraday_ohlcv ORDER BY code, date ASC", conn)
    conn.close()
    
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['date'], inplace=True)
    
    # 일별(day) 종가를 계산하여 당일 시가를 전일 종가와 비교해 당일 상승률 추정
    df['date_only'] = df['date'].dt.date
    
    total_trades = 0
    total_wins = 0
    total_loss = 0
    accumulated_return = 0.0
    
    codes = df['code'].unique()
    
    # 가상의 테마 장세 생성: 대장주 급등(당일 20% 이상) 포착 시, 해당 일자에 2등주 매수 진입 시뮬레이션
    daily_highs = df.groupby(['code', 'date_only'])['high'].max().reset_index()
    daily_opens = df.groupby(['code', 'date_only'])['open'].first().reset_index()
    
    daily_stats = pd.merge(daily_opens, daily_highs, on=['code', 'date_only'])
    
    # 20% 이상 상승한 날을 '대장주 출현일'로 간주
    leader_days = daily_stats[daily_stats['high'] >= daily_stats['open'] * 1.20]['date_only'].unique()
    
    print(f"[데이터 스캔] 분석 기간 내 대장주 출현 추정일: {len(leader_days)}일 발견")
    
    for day in leader_days:
        # 해당 날짜의 3분봉 데이터 추출
        day_data = df[df['date_only'] == day]
        if day_data.empty: continue
        
        # 임의로 변동성이 높은 다른 종목을 2등주(Follower)로 가정 (프록시 매매)
        # 당일 시가 대비 5% 이상 올랐으나 아직 15% 미만인 종목들을 후보로 선정
        day_opens = day_data.groupby('code')['open'].first()
        day_maxs = day_data.groupby('code')['high'].max()
        
        followers = []
        for c in day_maxs.index:
            if day_maxs[c] > day_opens[c] * 1.05 and day_maxs[c] < day_opens[c] * 1.15:
                followers.append(c)
                
        # 최대 2개 종목만 2등주로 매매
        followers = followers[:2]
        
        for fc in followers:
            fc_data = day_data[day_data['code'] == fc].copy()
            if len(fc_data) < 5: continue
            
            # 매수 타점: 시가 대비 5% 상승한 지점
            target_buy_price = day_opens[fc] * 1.05
            
            bought = False
            buy_price = 0
            
            for i in range(len(fc_data)):
                row = fc_data.iloc[i]
                
                if not bought:
                    if row['high'] >= target_buy_price:
                        bought = True
                        buy_price = max(row['open'], target_buy_price) # 슬리피지 감안
                else:
                    # 매수 후 3분봉 진행에 따른 익절(+3%) 또는 손절(-2%)
                    profit_ratio = (row['close'] - buy_price) / buy_price
                    if profit_ratio >= 0.03:
                        total_trades += 1
                        total_wins += 1
                        accumulated_return += 0.03 - 0.0025 # 수수료/세금 공제
                        break
                    elif profit_ratio <= -0.02:
                        total_trades += 1
                        total_loss += 1
                        accumulated_return -= 0.02 + 0.0025
                        break
                        
            # 장 마감까지 보유한 경우 종가 청산
            if bought and fc_data.iloc[-1]['close'] > 0:
                final_profit = (fc_data.iloc[-1]['close'] - buy_price) / buy_price
                if final_profit < 0.03 and final_profit > -0.02:
                    total_trades += 1
                    accumulated_return += final_profit - 0.0025
                    if final_profit > 0.0025:
                        total_wins += 1
                    else:
                        total_loss += 1

    print("\n[전략 1 프록시 백테스트 결과 요약]")
    print(f"▶ 총 매매 횟수: {total_trades}회")
    if total_trades > 0:
        win_rate = (total_wins / total_trades) * 100
        print(f"▶ 승률: {win_rate:.1f}%")
        print(f"▶ 누적 수익률: {accumulated_return * 100:+.2f}%")
        print(f"▶ 평균 손익비 (Risk/Reward): 1 : 1.5 (고정청산)")
    else:
        print("조건에 맞는 매매 발생하지 않음.")
        
    print("\n* 참고: 본 결과는 실제 상한가 호가 잠김을 틱 단위로 측정한 것이 아닌,")
    print("  3분봉 데이터를 기반으로 한 대장주-2등주 모멘텀 프록시 시뮬레이션입니다.")

if __name__ == "__main__":
    run_strat1_backtest()
