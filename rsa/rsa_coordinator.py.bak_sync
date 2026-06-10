import os
import sys
import sqlite3
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(current_dir)
sys.path.append(workspace_root)

from faa_analyst import FinancialAnalystAgent
from ira_researcher import IndustryResearcherAgent
from nsaa_sentiment import NewsSentimentAnalystAgent

try:
    import notifier
except ImportError:
    notifier = None

class RSACoordinator:
    def __init__(self):
        self.faa = FinancialAnalystAgent()
        self.ira = IndustryResearcherAgent()
        self.nsaa = NewsSentimentAnalystAgent()
        
        self.db_path = os.path.join(workspace_root, "unified_data.db")
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS research_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT,
                strategy_type TEXT,
                faa_score INTEGER,
                faa_reason TEXT,
                ira_score INTEGER,
                ira_reason TEXT,
                nsaa_score INTEGER,
                nsaa_reason TEXT,
                score INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def evaluate_stock(self, code, name, strategy_type="DAY"):
        """
        특정 종목에 대해 FAA, IRA, NSAA 분석을 종합 수행하여
        가중 취합 점수를 연산하고 DB에 기록한 뒤 최종 스코어를 반환합니다.
        """
        print(f"\n[RSA Coordinator] '{name}({code})' 정밀 리서치 개시 (유형: {strategy_type})")
        
        # 1. FAA (재무분석가) 분석 수행
        faa_score, faa_reason = self.faa.analyze_finance(code)
        print(f"  └─ [FAA] 재무 안전성 점수: {faa_score}점 | {faa_reason}")
        
        # 2. IRA (업종리서치) 분석 수행
        ira_score, ira_reason = self.ira.analyze_industry(code)
        print(f"  └─ [IRA] 업종 사이클 점수: {ira_score}점 | {ira_reason}")
        
        # 3. NSAA (뉴스분석가) 분석 수행
        nsaa_score, nsaa_reason = self.nsaa.analyze_sentiment(code)
        print(f"  └─ [NSAA] 실시간 뉴스 감성 점수: {nsaa_score}점 | {nsaa_reason}")
        
        # 4. 동적 가중치 결합
        if strategy_type == "DAY":
            # 단타: 뉴스가 압도적으로 중요 (뉴스 60% / 재무 20% / 업종 20%)
            weight_faa = 0.20
            weight_ira = 0.20
            weight_nsaa = 0.60
        else:
            # 스윙: 재무와 업종 사이클이 극히 중요 (뉴스 20% / 재무 40% / 업종 40%)
            weight_faa = 0.40
            weight_ira = 0.40
            weight_nsaa = 0.20
            
        final_score = int(
            (faa_score * weight_faa) +
            (ira_score * weight_ira) +
            (nsaa_score * weight_nsaa)
        )
        
        print(f"  => 👑 [RSA 최종 판정] 종합 리서치 점수: {final_score}점 (DAY/SWING 가중 반영)")
        
        # DB 적재
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO research_reports (
                code, name, strategy_type, 
                faa_score, faa_reason, 
                ira_score, ira_reason, 
                nsaa_score, nsaa_reason, 
                score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            code, name, strategy_type,
            faa_score, faa_reason,
            ira_score, ira_reason,
            nsaa_score, nsaa_reason,
            final_score
        ))
        conn.commit()
        conn.close()
        
        print(f"  └─ [DB 저장 완료] research_reports 테이블 적재 완료.")
        return final_score

    def _is_analyzed_today(self, code):
        """오늘 이미 분석한 종목이면 True 반환 (중복 API 호출 방지)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT id FROM research_reports WHERE code = ? AND date(timestamp) = ?",
                (code, today)
            )
            result = cursor.fetchone() is not None
            conn.close()
            return result
        except Exception:
            return False

    def run_coordinator_loop(self):
        """
        top_volume_theme(단타) 및 PENDING 스윙 시그널 종목에 대해
        RSA 정밀 분석을 수행하는 워커. 당일 이미 분석한 종목은 스킵.
        """
        print("[RSA Coordinator Worker] 백그라운드 리서치 모니터링 활성화...")
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            # 오늘 top_volume_theme에 있는 단타 후보
            cursor.execute(
                "SELECT DISTINCT code, name FROM top_volume_theme WHERE date = ? LIMIT 10",
                (today,)
            )
            day_targets = cursor.fetchall()

            # PENDING 상태의 스윙 후보 (아직 RSA 미평가)
            cursor.execute(
                "SELECT DISTINCT code, name FROM signals WHERE status = 'PENDING' AND strategy_type = 'SWING'"
            )
            swing_targets = cursor.fetchall()
        except sqlite3.OperationalError:
            day_targets = []
            swing_targets = []
        finally:
            conn.close()

        day_analyzed, swing_analyzed = [], []

        for code, name in swing_targets:
            if self._is_analyzed_today(code):
                print(f"  [스킵] {name}({code}) 오늘 이미 분석 완료.")
                continue
            self.evaluate_stock(code, name, "SWING")
            swing_analyzed.append(name)

        for code, name in day_targets:
            if self._is_analyzed_today(code):
                print(f"  [스킵] {name}({code}) 오늘 이미 분석 완료.")
                continue
            self.evaluate_stock(code, name, "DAY")
            day_analyzed.append(name)

        total = len(day_analyzed) + len(swing_analyzed)
        print(f"[RSA 완료] 단타 {len(day_analyzed)}종목, 스윙 {len(swing_analyzed)}종목 신규 분석.")

        summary = "🔬 <b>[RSA 분석 완료]</b>\n"
        if day_analyzed:
            summary += f"• 단타 {len(day_analyzed)}종목: {', '.join(day_analyzed)}\n"
        if swing_analyzed:
            summary += f"• 스윙 {len(swing_analyzed)}종목: {', '.join(swing_analyzed)}\n"
        if total == 0:
            summary += "• 분석 대상 없음 (모두 기완료 또는 후보 없음)\n"
        summary += "💡 ERA가 signals 폴링 시 70점 필터를 자동 적용합니다."

        if notifier:
            notifier.send_message(summary)

if __name__ == "__main__":
    coord = RSACoordinator()
    # 개별 종목 테스트 (SK하이닉스 - 000660)
    coord.evaluate_stock("000660", "SK하이닉스", "DAY")
    # 백그라운드 루프 1회 구동 테스트
    coord.run_coordinator_loop()
