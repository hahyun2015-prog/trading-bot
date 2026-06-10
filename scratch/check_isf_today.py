import requests
from bs4 import BeautifulSoup
import pandas as pd
import time

headers = {'User-Agent': 'Mozilla/5.0'}

def get_stock_data(code):
    rows = []
    for page in range(1, 3):
        url = f'https://finance.naver.com/item/sise_day.naver?code={code}&page={page}'
        r = requests.get(url, headers=headers)
        soup = BeautifulSoup(r.content, 'html.parser')
        for tr in soup.select('table.type2 tr'):
            tds = tr.select('td')
            if len(tds) < 7: continue
            dstr = tds[0].text.strip()
            if not dstr or '.' not in dstr: continue
            rows.append({
                'date': dstr.replace('.', '-'),
                'close': int(tds[1].text.strip().replace(',', '')),
                'open': int(tds[3].text.strip().replace(',', '')),
                'high': int(tds[4].text.strip().replace(',', '')),
                'low': int(tds[5].text.strip().replace(',', ''))
            })
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    df.sort_index(ascending=True, inplace=True)
    return df

print("=== ISF (개별주식선물) 오늘 시뮬레이션 ===")
# 삼성전자
ss_df = get_stock_data('005930')
# SK하이닉스
sk_df = get_stock_data('000660')

def check_isf_signal(name, df, K, sl_pct, tp_pct):
    print(f"\n[{name}]")
    prev_day = df.iloc[-2] # June 4
    today = df.iloc[-1] # June 5
    
    prev_range = prev_day['high'] - prev_day['low']
    day_open = today['open']
    target_long = day_open + prev_range * K
    
    print(f"  전일 Range: {prev_range:,}원")
    print(f"  금일 시가: {day_open:,}원")
    print(f"  매수 돌파선: {target_long:,.1f}원")
    print(f"  금일 고가: {today['high']:,}원")
    print(f"  금일 저가: {today['low']:,}원")
    print(f"  금일 종가: {today['close']:,}원")
    
    if today['high'] >= target_long:
        print("  => 매수(롱) 진입 성공!")
        entry_price = target_long
        sl_price = entry_price * (1 - sl_pct/100)
        tp_price = entry_price * (1 + tp_pct/100)
        print(f"    - 진입가: {entry_price:,.1f}원")
        print(f"    - 손절가: {sl_price:,.1f}원")
        print(f"    - 익절가: {tp_price:,.1f}원")
        
        # Check exits
        if today['low'] <= sl_price:
            print("    - [결과] 손절 터치! (SL)")
            pnl_pct = -sl_pct
        elif today['high'] >= tp_price:
            print("    - [결과] 익절 터치! (TP)")
            pnl_pct = tp_pct
        else:
            print("    - [결과] 당일 청산 없음 (종가 청산)")
            pnl_pct = ((today['close'] / entry_price) - 1.0) * 100
        print(f"    - 수익률: {pnl_pct:+.2f}%")
    else:
        print("  => 금일 매수 돌파 실패 (진입 없음)")

check_isf_signal("삼성전자", ss_df, 0.35, 2.0, 2.5)
check_isf_signal("SK하이닉스", sk_df, 0.18, 1.2, 2.0)
