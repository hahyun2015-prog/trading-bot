# -*- coding: utf-8 -*-
import sqlite3, requests, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('unified_data.db')
conn.execute("DELETE FROM nsaa_cache WHERE reason IS NULL OR reason = ''")
deleted = conn.execute("SELECT changes()").fetchone()[0]
conn.commit()
remaining = conn.execute("SELECT COUNT(*) FROM nsaa_cache").fetchone()[0]
print(f'캐시 삭제: {deleted}건, 남은 캐시: {remaining}건')
conn.close()

headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)'}
for code, name in [('005930','삼성전자'), ('000660','SK하이닉스')]:
    dates_seen = set()
    total = 0
    for page in range(1, 500):
        url = f'https://m.stock.naver.com/api/news/stock/{code}?pageSize=20&page={page}'
        try:
            r = requests.get(url, headers=headers, timeout=8)
            data = r.json()
            if isinstance(data, list) and data:
                items = data[0].get('items', [])
            elif isinstance(data, dict):
                items = data.get('items', [])
            else:
                items = []
            if not items:
                print(f'{name}: {page-1}페이지까지, 총 {total}건, {len(dates_seen)}개 날짜')
                break
            for item in items:
                dt = item.get('datetime','')[:8]
                if dt:
                    dates_seen.add(dt)
                    total += 1
        except Exception as e:
            print(f'{name}: {page}페이지 오류 {e}')
            break
    if dates_seen:
        ds = sorted(dates_seen)
        print(f'  날짜 범위: {ds[0]} ~ {ds[-1]}')
