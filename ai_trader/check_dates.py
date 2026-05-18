import sqlite3
import pandas as pd

def check_dates():
    # Stocks
    try:
        conn = sqlite3.connect(r"c:\antigravity\노트븍활용\ai_trader\kiwoom_data.db")
        df = pd.read_sql_query("SELECT MIN(date) as start, MAX(date) as end FROM intraday_ohlcv", conn)
        print("Stocks Data Range:", df.iloc[0]['start'], "to", df.iloc[0]['end'])
        conn.close()
    except Exception as e:
        print("Stock DB Error:", e)

    # Futures
    try:
        conn = sqlite3.connect(r"c:\antigravity\노트븍활용\futures_trader\futures_data.db")
        df = pd.read_sql_query("SELECT MIN(date) as start, MAX(date) as end FROM futures_ohlcv", conn)
        print("Futures Data Range:", df.iloc[0]['start'], "to", df.iloc[0]['end'])
        conn.close()
    except Exception as e:
        print("Futures DB Error:", e)

if __name__ == "__main__":
    check_dates()
