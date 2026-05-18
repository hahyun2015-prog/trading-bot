import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import os
import joblib

class MacroAnalyzer:
    def __init__(self):
        self.model_path = 'macro_kmeans_model.pkl'
        self.scaler_path = 'macro_scaler.pkl'
        
    def fetch_macro_data(self):
        """
        API(FRED, BOK 등)를 연동하여 거시 경제 지표를 수집하는 모듈.
        현재는 아키텍처 구현을 위해 임의의 시뮬레이션 데이터를 생성합니다.
        (실제 연동 시 pykrx KOSPI 지수, 원/달러 환율, VIX 등 활용)
        """
        print("[Macro] 거시 경제 지표(금리, 환율, 변동성) 데이터를 수집합니다...")
        
        # 1년(252일) 치 가상의 거시 지표 생성
        np.random.seed(42)
        dates = pd.date_range(end=pd.Timestamp.today(), periods=252, freq='B')
        
        df = pd.DataFrame({
            'interest_rate': np.linspace(1.5, 4.0, 252) + np.random.normal(0, 0.1, 252),
            'usd_krw': np.linspace(1150, 1350, 252) + np.random.normal(0, 10, 252),
            'vix': np.random.normal(20, 5, 252)
        }, index=dates)
        
        # 최신 데이터에 약간의 변동성 부여
        df.iloc[-30:, 2] = np.random.normal(25, 8, 30)
        return df

    def train_regime_model(self, df):
        """수집된 데이터를 바탕으로 비지도학습(K-Means) 국면 분류기를 학습합니다."""
        print("[Macro] K-Means Clustering 기반 시장 국면(Regime) 분류 모델을 학습합니다...")
        
        scaler = StandardScaler()
        scaled_data = scaler.fit_transform(df)
        
        # 3가지 국면으로 분류: 상승장(리스크 온), 하락장(리스크 오프), 횡보장
        kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
        df['regime'] = kmeans.fit_predict(scaled_data)
        
        # 모델 저장
        joblib.dump(kmeans, self.model_path)
        joblib.dump(scaler, self.scaler_path)
        
        print(f" => 모델 학습 완료. (저장 완료: {self.model_path})")
        return df

    def predict_current_regime(self):
        """최신 데이터를 바탕으로 현재 시장 국면을 추론합니다."""
        df = self.fetch_macro_data()
        
        if not os.path.exists(self.model_path) or not os.path.exists(self.scaler_path):
            self.train_regime_model(df)
            
        kmeans = joblib.load(self.model_path)
        scaler = joblib.load(self.scaler_path)
        
        latest_data = df[['interest_rate', 'usd_krw', 'vix']].iloc[[-1]]
        scaled_latest = scaler.transform(latest_data)
        regime = kmeans.predict(scaled_latest)[0]
        
        regime_labels = {
            0: "리스크 온 (Risk-On) / 성장주 우위 국면",
            1: "리스크 오프 (Risk-Off) / 가치주 우위, 현금 비중 확대 국면",
            2: "횡보장 (Sideways) / 변동성 축소 국면"
        }
        
        print("\n=== [AI Macro Regime Analysis] ===")
        print(f" - 현재 금리 추정치: {latest_data['interest_rate'].values[0]:.2f}%")
        print(f" - 현재 환율(USD/KRW): {latest_data['usd_krw'].values[0]:.0f}원")
        print(f" - 변동성 지수(VIX): {latest_data['vix'].values[0]:.2f}")
        print(f" => AI 판단 현재 시장 국면: [{regime_labels.get(regime, '알 수 없음')}]")
        print("====================================\n")
        
        return regime_labels.get(regime, 'Unknown')

if __name__ == "__main__":
    analyzer = MacroAnalyzer()
    analyzer.predict_current_regime()
