import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import json
import time

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

portfolio = {
    "001420": {"name": "태원물산", "strategy": "SWING", "qty": 160, "buy_price": 2486},
    "001820": {"name": "삼화콘덴서", "strategy": "SWING", "qty": 9, "buy_price": 73767},
    "049550": {"name": "잉크테크", "strategy": "SWING", "qty": 119, "buy_price": 3218},
    "053980": {"name": "오상자이엘", "strategy": "DAY", "qty": 177, "buy_price": 2751},
    "131030": {"name": "옵투스제약", "strategy": "DAY", "qty": 62, "buy_price": 6260},
    "171010": {"name": "램테크놀러지", "strategy": "SWING", "qty": 74, "buy_price": 3380},
    "240810": {"name": "원익IPS", "strategy": "DAY", "qty": 3, "buy_price": 119567},
    "241790": {"name": "티이엠씨씨엔에스", "strategy": "SWING", "qty": 62, "buy_price": 6190},
    "265520": {"name": "AP시스템", "strategy": "DAY", "qty": 10, "buy_price": 24650},
    "272290": {"name": "이녹스첨단소재", "strategy": "DAY", "qty": 8, "buy_price": 29794},
    "285800": {"name": "진영", "strategy": "SWING", "qty": 188, "buy_price": 1253},
    "440110": {"name": "파두", "strategy": "SWING", "qty": 8, "buy_price": 96800}
}

def get_naver_ohlcv(code, pages=3):
    rows = []
    for page in range(1, pages + 1):
        url = f'https://finance.naver.com/item/sise_day.naver?code={code}&page={page}'
        try:
            r = requests.get(url, headers=headers, timeout=10)
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
                    'low': int(tds[5].text.strip().replace(',', '')),
                    'volume': int(tds[6].text.strip().replace(',', ''))
                })
            time.sleep(0.1)
        except Exception as e:
            print(f"Error crawling {code}: {e}")
            break
            
    df = pd.DataFrame(rows)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(ascending=True, inplace=True)
    return df

print("=== 포트폴리오 종목 2026-06-05 데이터 크롤링 ===")
results = []
for code, info in portfolio.items():
    print(f"크롤링 중: {info['name']}({code})...")
    df = get_naver_ohlcv(code, pages=3)
    if df.empty:
        print(f"  {info['name']} 데이터 없음")
        continue
    
    # Calculate MAs
    df['ma_5'] = df['close'].rolling(5).mean()
    df['ma_10'] = df['close'].rolling(10).mean()
    df['ma_20'] = df['close'].rolling(20).mean()
    
    # Get today's row (June 5, 2026)
    today_str = '2026-06-05'
    if today_str in df.index.strftime('%Y-%m-%d'):
        today_row = df.loc[today_str]
        if isinstance(today_row, pd.DataFrame):
            today_row = today_row.iloc[-1]
            
        prev_row = df.iloc[-2] # June 4, 2026
        
        results.append({
            'code': code,
            'name': info['name'],
            'strategy': info['strategy'],
            'qty': info['qty'],
            'buy_price': info['buy_price'],
            'open': int(today_row['open']),
            'high': int(today_row['high']),
            'low': int(today_row['low']),
            'close': int(today_row['close']),
            'ma_5': float(today_row['ma_5']),
            'ma_10': float(today_row['ma_10']),
            'ma_20': float(today_row['ma_20']),
            'prev_close': int(prev_row['close']),
            'prev_ma_5': float(prev_row['ma_5']),
            'prev_ma_10': float(prev_row['ma_10'])
        })
        print(f"  {info['name']}: Close={today_row['close']}, 5MA={today_row['ma_5']:.1f}, 10MA={today_row['ma_10']:.1f}")
    else:
        print(f"  {info['name']}: 6월 5일 데이터 찾을 수 없음. 최신 날짜: {df.index[-1].strftime('%Y-%m-%d')}")

# Save to json
with open('scratch/today_stock_prices.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=4)
print("완료!")
