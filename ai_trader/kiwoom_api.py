import sys
import os

# Fix PyQt5 plugin path issue for virtual environments
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path
from PyQt5.QtWidgets import QMainWindow
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop
from PyQt5.QtTest import QTest

class KiwoomAPI(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # OpenAPI COM 객체 활성화 (국내 주식/선물용 ProgID)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        
        # 이벤트 루프 및 시그널 연결 설정
        self._set_signals_slots()
        
        # 데이터 수집용 속성
        self.ohlcv_data = []
        self.remained_data = False
        self.tr_event_loop = None
        
    def _set_signals_slots(self):
        self.kiwoom.OnEventConnect.connect(self._on_event_connect)
        self.kiwoom.OnReceiveRealData.connect(self._on_receive_real_data)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        
    def login(self):
        """로그인 창을 띄웁니다."""
        print("로그인 창을 호출합니다...")
        ret = self.kiwoom.dynamicCall("CommConnect()") # 국내 OpenAPI+ 로그인 호출
        print(f"CommConnect() 호출 반환값: {ret}")
        
    def _on_event_connect(self, err_code):
        """로그인 결과 이벤트 처리"""
        if err_code == 0:
            print("로그인 성공!")
            
            # 계좌 정보 가져오기 테스트
            account_info = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            account_list = account_info.split(';')
            # 마지막 항목은 보통 빈 문자열이므로 필터링
            account_list = [acc for acc in account_list if acc]
            
            print(f"내 전체 계좌 목록: {account_list}")
            if account_list:
                # 잔고가 있는 모의계좌(두 번째 계좌) 선택
                if len(account_list) > 1:
                    self.account_no = account_list[1]
                else:
                    self.account_no = account_list[0]
                    
                print(f"주계좌번호: {self.account_no}")
                
                # 예수금(주문 가능 금액) 확인 요청
                self.check_balance()

        else:
            print(f"로그인 실패 (에러코드: {err_code})")

    def _on_receive_real_data(self, sCode, sRealType, sRealData):
        """실시간 시세 데이터 수신 이벤트 처리"""
        # GetCommRealData(종목코드, FID) -> FID 10은 현재가
        current_price = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", sCode, 10)
        
        # 현재가 문자열에 포함된 + 또는 - 기호 제거 후 공백 제거
        if current_price:
            current_price = current_price.strip().replace('+', '').replace('-', '')
            print(f"[실시간 시세] 종목코드: {sCode} | 현재가: {current_price}")
            
    def _on_receive_tr_data(self, sScrNo, sRQName, sTrCode, sRecordName, sPrevNext, nDataLength, sErrorCode, sMessage, sSplmMsg):
        """TR(Transaction Request) 응답 수신 이벤트 처리"""
        if sRQName in ["주식일봉차트조회", "선물일봉차트조회", "주식분봉차트조회"]:
            # 한 번에 수신된 데이터의 행(Row) 개수를 가져옵니다
            repeat_cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", sTrCode, sRQName)
            
            # 분봉일 경우 일자 대신 체결시간 필드 사용
            date_field = "체결시간" if sRQName == "주식분봉차트조회" else "일자"
            
            for i in range(repeat_cnt):
                date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, date_field).strip()
                open_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "시가").strip()
                high_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "고가").strip()
                low_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "저가").strip()
                close_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "현재가").strip()
                volume = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "거래량").strip()
                
                self.ohlcv_data.append([date, abs(int(open_p)), abs(int(high_p)), abs(int(low_p)), abs(int(close_p)), abs(int(volume))])
            
            # 다음 페이지 데이터가 있는지 확인 (sPrevNext == "2" 이면 더 있음)
            self.remained_data = (sPrevNext == "2")
            print(f"[{sRQName}] {repeat_cnt}일 치 데이터 수신 완료. (추가 데이터 존재: {self.remained_data})")
            
            # 대기 중인 이벤트 루프 종료
            if self.tr_event_loop and self.tr_event_loop.isRunning():
                self.tr_event_loop.exit()
                
        elif sRQName == "예수금상세현황요청":
            deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, 0, "주문가능금액").strip()
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, 0, "d+2추정예수금").strip()
            
            if deposit and deposit.lstrip('-').isdigit():
                print(f"[계좌 확인] 주문 가능 금액: {int(deposit):,}원 (D+2 추정 예수금: {int(d2_deposit):,}원)")
            else:
                print(f"[계좌 확인] 주문 가능 금액: {deposit}원")

    def check_balance(self):
        """내 계좌의 예수금(주문 가능 금액) 조회"""
        if not hasattr(self, 'account_no'):
            return
            
        print("내 계좌의 예수금(돈)을 확인합니다...")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.account_no.strip())
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "예수금상세현황요청", "opw00001", 0, "1004")

    def get_code_list_by_market(self, market_code):
        """시장 구분에 따른 종목 코드를 반환합니다. (0: 코스피, 10: 코스닥 등)"""
        code_list = self.kiwoom.dynamicCall("GetCodeListByMarket(QString)", market_code)
        return [code for code in code_list.split(';') if code]

    def get_master_code_name(self, code):
        """종목 코드에 해당하는 한글 종목명을 반환합니다."""
        return self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code)

    def req_historical_data(self, code, tr_code, rq_name):
        """특정 종목의 과거 데이터를 연속 조회합니다."""
        self.ohlcv_data = [] # 초기화
        
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        if tr_code == "opt10081":
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        # 최초 조회 (sPrevNext = 0)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", rq_name, tr_code, 0, "1005")
        
        # 이벤트 루프 생성 (데이터 응답이 올 때까지 코드 진행 정지)
        self.tr_event_loop = QEventLoop()
        self.tr_event_loop.exec_()
        
        # 추가 데이터가 남아있으면 계속해서 반복 호출
        while self.remained_data:
            # 1초에 5회 제한을 피하기 위한 0.5초 강제 대기
            QTest.qWait(500)
            
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            if tr_code == "opt10081":
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            # 연속 조회 (sPrevNext = 2)
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", rq_name, tr_code, 2, "1005")
            
            self.tr_event_loop.exec_()

    def req_historical_data_single_page(self, code, tr_code, rq_name, tick_range="3"):
        """특정 종목의 과거 데이터를 1페이지(약 600~900개)만 조회합니다."""
        self.ohlcv_data = [] # 초기화
        
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        if tr_code == "opt10081":
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        elif tr_code == "opt10080":
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "틱범위", tick_range)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            
        # 최초 조회 (sPrevNext = 0)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", rq_name, tr_code, 0, "1005")
        
        # 이벤트 루프 생성 (데이터 응답이 올 때까지 코드 진행 정지)
        self.tr_event_loop = QEventLoop()
        self.tr_event_loop.exec_()

    def send_buy_order(self, code, qty):
        """시장가 신규 매수 주문 (주식)"""
        if not hasattr(self, 'account_no'):
            print("계좌번호가 설정되지 않아 주문을 보낼 수 없습니다.")
            return
            
        print(f"[{code}] {qty}주 시장가 신규 매수 주문을 서버로 전송합니다...")
        # SendOrder(사용자구분명, 화면번호, 계좌번호, 주문유형(1:신규매수), 종목코드, 주문수량, 주문단가(시장가는 0), 호가구분(03:시장가), 원주문번호)
        ret = self.kiwoom.dynamicCall("SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                                      ["모의투자_신규매수", "1002", self.account_no.strip(), 1, code, qty, 0, "03", ""])
        if ret == 0:
            print("주문 전송 성공! (체결 여부는 OnReceiveChejanData에서 확인)")
        else:
            print(f"주문 전송 실패 (에러코드: {ret})")

    def send_future_buy_order(self, code, qty):
        """시장가 신규 매수 주문 (국내 선물)"""
        # 전체 계좌 목록 다시 가져오기
        account_info = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        account_list = [acc for acc in account_info.split(';') if acc]
        
        # 파생 계좌 찾기 (보통 8로 시작하거나, 리스트의 두 번째 이후 계좌)
        future_account = account_list[0] # 기본값
        for acc in account_list:
            if acc.startswith('8') or acc.startswith('5'):
                future_account = acc
                break
                
        print(f"[{code}] {qty}계약 시장가 신규 매수 주문(선물)을 서버로 전송합니다... (계좌: {future_account})")
        # SendOrderFO(사용자구분명, 화면번호, 계좌번호, 종목코드, 주문종류(1:신규), 매매구분(2:매수), 거래구분(3:시장가), 수량, 가격, 원주문번호)
        # 가격은 문자열로 "0" (시장가)
        args = ["모의투자_선물매수", "1003", future_account, code, 1, "2", "3", qty, "0", ""]
        ret = self.kiwoom.dynamicCall("SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)", args)
        
        if ret == 0:
            print("선물 주문 전송 성공! (체결 여부는 OnReceiveChejanData에서 확인)")
        else:
            print(f"선물 주문 전송 실패 (에러코드: {ret})")

    def send_future_sell_order(self, code, qty):
        """시장가 신규 매도 주문 (국내 선물)"""
        account_info = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        account_list = [acc for acc in account_info.split(';') if acc]

        future_account = account_list[0]
        for acc in account_list:
            if acc.startswith('8') or acc.startswith('5'):
                future_account = acc
                break

        print(f"[{code}] {qty}계약 시장가 신규 매도 주문(선물)을 서버로 전송합니다... (계좌: {future_account})")
        # SendOrderFO: 매매구분 "1" = 매도
        args = ["모의투자_선물매도", "1004", future_account, code, 1, "1", "3", qty, "0", ""]
        ret = self.kiwoom.dynamicCall("SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)", args)

        if ret == 0:
            print("선물 매도 주문 전송 성공! (체결 여부는 OnReceiveChejanData에서 확인)")
        else:
            print(f"선물 매도 주문 전송 실패 (에러코드: {ret})")

    def _on_receive_chejan_data(self, sGubun, nItemCnt, sFIdList):
        """주문 접수 및 체결 시 발생하는 이벤트"""
        if sGubun == "0":
            print("[주문접수/체결] 주문이 서버에 접수되었거나 체결이 발생했습니다!")
            # GetChejanData(9203) -> 주문번호, GetChejanData(9001) -> 종목코드
            order_no = self.kiwoom.dynamicCall("GetChejanData(int)", 9203)
            item_code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001)
            order_status = self.kiwoom.dynamicCall("GetChejanData(int)", 913) # 주문상태 (접수, 체결 등)
            print(f"  -> 주문번호: {order_no}, 종목: {item_code}, 상태: {order_status}")
        elif sGubun == "1":
            print("[잔고변경] 국내주식 잔고가 변경되었습니다.")
