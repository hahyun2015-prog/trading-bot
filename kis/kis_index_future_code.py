"""
한국투자증권 지수선물옵션 종목코드 마스터파일(fo_idx_code_mts.mst)을 내려받아
코스피200 선물 중 현재 최근월물(가장 이른 만기) 코드를 찾아준다.
(원본: koreainvestment/open-trading-api의 stocks_info/domestic_index_future_code.py를
 최근월물 자동 선택 로직만 추가해 재구성)
"""
import os
import ssl
import zipfile
import urllib.request

import pandas as pd


def download_master(dest_dir):
    ssl._create_default_https_context = ssl._create_unverified_context
    zip_path = os.path.join(dest_dir, "fo_idx_code_mts.mst.zip")
    urllib.request.urlretrieve(
        "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip", zip_path
    )
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    return os.path.join(dest_dir, "fo_idx_code_mts.mst")


def load_master_df(dest_dir):
    mst_path = download_master(dest_dir)
    columns = ['상품종류', '단축코드', '표준코드', '한글종목명',
               'ATM구분', '행사가', '월물구분코드', '기초자산단축코드', '기초자산명']
    df = pd.read_table(mst_path, sep='|', encoding='cp949', header=None)
    df.columns = columns
    return df


def find_kospi200_front_month(dest_dir, product_type='1'):
    """코스피200 선물 중 최근월물의 단축코드를 반환.
    한글종목명 컬럼은 파일 자체에 인코딩이 일관되지 않은 행이 섞여 있어(사례: 미니/변동성
    선물명이 깨짐) 순수 ASCII인 상품종류/기초자산명 컬럼만으로 필터링한다.
    product_type: 상품종류 컬럼 값. '1' = 코스피200 선물(표준, 기본값), 'B' = 미니,
    '7' = 변동성(VKOSPI). 실제 매매 시스템(era_order_manager.py)은 미니선물을 쓰므로
    실거래/백테스트용 데이터를 모으려면 'B'를 지정해야 한다."""
    df = load_master_df(dest_dir)
    kospi200_futs = df[
        (df['기초자산명'].astype(str).str.strip() == 'KOSPI200')
        & (df['상품종류'].astype(str).str.strip() == product_type)
    ].copy()
    if kospi200_futs.empty:
        raise RuntimeError(f"마스터파일에서 코스피200 선물(상품종류={product_type}) 종목을 찾지 못했습니다.")
    kospi200_futs = kospi200_futs.sort_values('월물구분코드')
    row = kospi200_futs.iloc[0]
    label = {"1": "표준", "B": "미니", "7": "변동성"}.get(product_type, product_type)
    return str(row['단축코드']).strip(), f"KOSPI200 선물({label}) {row['표준코드']}"


if __name__ == '__main__':
    code, name = find_kospi200_front_month(os.path.dirname(os.path.abspath(__file__)))
    print(f"최근월물: {name} (코드: {code})")
