import os
import sys
import json
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
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
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
        """FAA + IRA + NSAA 종합 분석 → 가중 점수 반환"""
        print(f"\n[RSA] '{name}({code})' 분석 중... (유형: {strategy_type})")

        faa_score, faa_reason = self.faa.analyze_finance(code)
        print(f"  └─ [재무] {faa_score}점 | {faa_reason}")

        ira_score, ira_reason = self.ira.analyze_industry(code)
        print(f"  └─ [업종] {ira_score}점 | {ira_reason}")

        nsaa_score, nsaa_reason = self.nsaa.analyze_sentiment(code, name)
        print(f"  └─ [뉴스] {nsaa_score}점 | {nsaa_reason}")

        if strategy_type == "DAY":
            # 단타: 뉴스 60% / 재무 20% / 업종 20%
            final_score = int(faa_score * 0.20 + ira_score * 0.20 + nsaa_score * 0.60)
        else:
            # 스윙: 재무 40% / 업종 40% / 뉴스 20%
            final_score = int(faa_score * 0.40 + ira_score * 0.40 + nsaa_score * 0.20)

        print(f"  => 👑 종합 점수: {final_score}점")

        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO research_reports (
                code, name, strategy_type,
                faa_score, faa_reason,
                ira_score, ira_reason,
                nsaa_score, nsaa_reason,
                score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (code, name, strategy_type,
              faa_score, faa_reason,
              ira_score, ira_reason,
              nsaa_score, nsaa_reason,
              final_score))
        conn.commit()
        conn.close()
        return final_score

    def _is_analyzed_today(self, code):
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
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
        top_volume_theme 후보(최대 50개) + PENDING 스윙 신호를 RSA 분석 후
        점수 기준으로 정렬하여 오늘의 추천 종목 랭킹을 알림으로 발송합니다.
        """
        print("[RSA] 분석 워커 시작...")
        self.nsaa.reset_errors()   # 오류 추적 초기화
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        try:
            # signals 테이블 미존재 시 자동 생성 (스윙 신호 누락 방지)
            cursor.execute("""CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT, name TEXT, strategy_type TEXT,
                price REAL, open_price REAL,
                status TEXT DEFAULT 'PENDING',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit()

            cursor.execute(
                "SELECT DISTINCT code, name FROM top_volume_theme WHERE date = ?",
                (today,)
            )
            day_targets = cursor.fetchall()

            cursor.execute(
                "SELECT DISTINCT code, name FROM signals WHERE status = 'PENDING' AND strategy_type = 'SWING'"
            )
            swing_targets = cursor.fetchall()
        except sqlite3.OperationalError:
            day_targets = []
            swing_targets = []
        finally:
            conn.close()

        # ISF 감시 종목(삼성전자/SK하이닉스 등)은 테마에 없어도 항상 분석
        isf_cfg_path = os.path.join(workspace_root, "config", "config_local.json")
        existing_codes = {code for code, _ in day_targets}
        if os.path.exists(isf_cfg_path):
            try:
                with open(isf_cfg_path, "r", encoding="utf-8") as f:
                    lcfg = json.load(f)
                for item in lcfg.get("individual_stock_futures", []):
                    sc = item.get("stock_code", "")
                    nm = item.get("name", sc)
                    if sc and sc not in existing_codes:
                        day_targets.append((sc, nm))
                        existing_codes.add(sc)
                        print(f"[RSA ISF] 감시 종목 분석 대상 추가: {nm}({sc})")
            except Exception as e:
                print(f"[RSA ISF] config 로드 오류: {e}")

        print(f"[RSA] 단타 후보 {len(day_targets)}개 (ISF 포함), 스윙 후보 {len(swing_targets)}개")

        # ── 스윙 우선 분석 ─────────────────────────────────────────────
        swing_results = []
        for code, name in swing_targets:
            if self._is_analyzed_today(code):
                continue
            score = self.evaluate_stock(code, name, "SWING")
            swing_results.append((score, name, code))

        # ── 단타 분석 (중복 제외) ─────────────────────────────────────
        day_results = []
        analyzed_codes = {code for _, _, code in swing_results}
        for code, name in day_targets:
            if self._is_analyzed_today(code) and code not in analyzed_codes:
                # 오늘 이미 분석됐으면 DB에서 기존 점수 로드
                try:
                    conn2 = sqlite3.connect(self.db_path, timeout=30)
                    conn2.execute("PRAGMA journal_mode=WAL;")
                    c2 = conn2.cursor()
                    c2.execute(
                        "SELECT score FROM research_reports WHERE code=? AND date(timestamp)=? ORDER BY id DESC LIMIT 1",
                        (code, today)
                    )
                    row = c2.fetchone()
                    conn2.close()
                    if row:
                        day_results.append((row[0], name, code))
                except Exception:
                    pass
                continue
            if code in analyzed_codes:
                continue
            score = self.evaluate_stock(code, name, "DAY")
            day_results.append((score, name, code))
            analyzed_codes.add(code)

        # ── 점수 기준 내림차순 정렬 ───────────────────────────────────
        day_results.sort(key=lambda x: x[0], reverse=True)
        swing_results.sort(key=lambda x: x[0], reverse=True)

        total_analyzed = len(day_results) + len(swing_results)
        print(f"[RSA 완료] 단타 {len(day_results)}개, 스윙 {len(swing_results)}개 분석")

        # ── 텔레그램 추천 종목 랭킹 발송 ─────────────────────────────
        msg = "🔬 <b>[RSA 분석 완료 — 오늘의 추천 종목]</b>\n\n"

        if day_results:
            msg += "📈 <b>단타 후보 (뉴스·업종 강도 순위)</b>\n"
            for rank, (score, name, code) in enumerate(day_results[:10], 1):
                grade = "⭐⭐⭐" if score >= 80 else ("⭐⭐" if score >= 70 else "⭐")
                status = "✅ 매수 허용" if score >= 70 else "❌ 70점 미달"
                msg += f"  {rank}. {grade} <b>{name}</b> ({code}) — {score}점 {status}\n"
            msg += "\n"

        if swing_results:
            msg += "📊 <b>스윙 후보 (재무·업종 안정성 순위)</b>\n"
            for rank, (score, name, code) in enumerate(swing_results[:5], 1):
                grade = "⭐⭐⭐" if score >= 80 else ("⭐⭐" if score >= 70 else "⭐")
                status = "✅ 매수 허용" if score >= 70 else "❌ 70점 미달"
                msg += f"  {rank}. {grade} <b>{name}</b> ({code}) — {score}점 {status}\n"
            msg += "\n"

        if total_analyzed == 0:
            msg += "• 분석 대상 없음\n"

        # 1위 강조 메시지
        if day_results and day_results[0][0] >= 70:
            top = day_results[0]
            msg += f"💡 <b>오늘의 RSA 1위: {top[1]} ({top[2]}) — {top[0]}점</b>\n"
        elif day_results:
            msg += f"⚠️ 오늘 단타 후보 중 70점 이상 종목 없음 — 자동 매수 신호 비활성\n"

        msg += "\n<i>* 70점 이상 종목만 자동 매수 신호 허용</i>"

        if notifier:
            notifier.send_message(msg)

        # ── 개별주식선물(ISF) 방향 결정 및 파일 저장 ─────────────────────
        self._write_isf_directions(day_results, swing_results)

        # ── RSA 오류 요약 알림 ────────────────────────────────────────
        errors = self.nsaa.errors
        if errors and notifier:
            # 오류 유형별 그룹핑
            by_type = {}
            for e in errors:
                by_type.setdefault(e["type"], []).append(
                    e["name"] if e["name"] else e["code"]
                )

            err_msg = "⚠️ <b>[RSA 분석 오류 요약]</b>\n\n"
            quota_hit = False
            for err_type, names in by_type.items():
                err_msg += f"• <b>{err_type}</b>: {', '.join(names)}\n"
                if "quota" in err_type.lower() or "quota" in err_type:
                    quota_hit = True

            if quota_hit:
                err_msg += (
                    "\n📌 <b>Gemini quota 초과 해결 방법:</b>\n"
                    "1. 잠시 후 자동으로 재시도됩니다 (분당 한도 초과 시)\n"
                    "2. 하루 한도 초과라면 내일 자동 해제됩니다\n"
                    "3. 키워드 백업 분석기로 대체 진행됐습니다 (정확도 다소 낮음)"
                )
            else:
                err_msg += (
                    "\n📌 해당 종목은 <b>키워드 백업 분석기</b>로 대체 평가됐습니다.\n"
                    "네트워크 상태를 확인하거나 ERA 재시작 후 다시 시도하세요."
                )

            notifier.send_message(err_msg)

        # ── Gemini API 미설정 경고 ────────────────────────────────────
        if not self.nsaa.api_key and notifier:
            notifier.send_message(
                "⚠️ <b>[RSA 뉴스 분석 경고]</b>\n"
                "Gemini API 키가 미설정 상태입니다.\n"
                "현재 키워드 기반 백업 분석기를 사용 중 (정확도 낮음).\n\n"
                "👉 config.json → <code>\"gemini_api_key\"</code> 에 키를 입력하면\n"
                "AI 뉴스 감성 분석이 활성화됩니다. (무료 키: aistudio.google.com)"
            )

    def _write_isf_directions(self, day_results, swing_results):
        """
        개별주식선물(ISF) 감시 종목의 오늘 방향을 NSAA 점수 기반으로 결정하고
        config/isf_direction.json 에 저장합니다.
        ERA는 09:00에 이 파일을 읽어 ISF 진입 방향을 설정합니다.
        """
        isf_direction_path = os.path.join(workspace_root, "config", "isf_direction.json")
        isf_config_path = os.path.join(workspace_root, "config", "config_local.json")

        # config_local.json에서 ISF 감시 종목 목록 읽기
        isf_stocks = []
        try:
            with open(isf_config_path, "r", encoding="utf-8") as f:
                local_cfg = json.load(f)
            isf_stocks = local_cfg.get("individual_stock_futures", [])
        except Exception:
            pass

        if not isf_stocks:
            return

        # 모든 분석 결과를 code → nsaa_score 매핑
        today = datetime.now().strftime("%Y-%m-%d")
        code_nsaa = {}
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            for isf_cfg in isf_stocks:
                sc = isf_cfg.get("stock_code", "")
                cursor.execute(
                    "SELECT nsaa_score FROM research_reports WHERE code=? AND date(timestamp)=? ORDER BY id DESC LIMIT 1",
                    (sc, today)
                )
                row = cursor.fetchone()
                if row:
                    code_nsaa[sc] = row[0]
            conn.close()
        except Exception as e:
            print(f"[RSA ISF] NSAA 조회 오류: {e}")

        # 방향 결정
        directions = {}
        isf_msg_lines = []
        for isf_cfg in isf_stocks:
            sc = isf_cfg.get("stock_code", "")
            name = isf_cfg.get("name", sc)
            nsaa = code_nsaa.get(sc)
            long_min = isf_cfg.get("nsaa_long_min", 72)
            short_max = isf_cfg.get("nsaa_short_max", 35)

            if nsaa is None:
                direction = "NEUTRAL"
                reason = "RSA 미분석 (오늘 후보 미포함)"
            elif nsaa >= long_min:
                direction = "LONG"
                reason = f"뉴스감성 {nsaa}점 ≥ {long_min}점 → LONG"
            elif nsaa <= short_max:
                direction = "SHORT"
                reason = f"뉴스감성 {nsaa}점 ≤ {short_max}점 → SHORT"
            else:
                direction = "NEUTRAL"
                reason = f"뉴스감성 {nsaa}점 중립 ({short_max}~{long_min}점 범위)"

            directions[sc] = {"direction": direction, "nsaa_score": nsaa, "reason": reason, "name": name}

            icon = "📈" if direction == "LONG" else ("📉" if direction == "SHORT" else "⏸️")
            isf_msg_lines.append(f"  {icon} <b>{name}</b>: {direction} ({reason})")

        directions["_date"] = today
        directions["_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with open(isf_direction_path, "w", encoding="utf-8") as f:
                json.dump(directions, f, ensure_ascii=False, indent=2)
            print(f"[RSA ISF] 방향 결정 파일 저장 완료: {isf_direction_path}")
        except Exception as e:
            print(f"[RSA ISF] 방향 파일 저장 오류: {e}")

        # 텔레그램 알림
        if isf_msg_lines and notifier:
            notifier.send_message(
                "📊 <b>[ISF 오늘의 방향 결정]</b>\n\n"
                + "\n".join(isf_msg_lines) +
                "\n\n<i>* LONG/SHORT만 09:00 이후 자동 진입 허용</i>"
            )


if __name__ == "__main__":
    coord = RSACoordinator()
    coord.run_coordinator_loop()
