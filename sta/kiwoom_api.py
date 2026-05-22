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
            account_list = [acc for acc in account_list if acc]
            
            print(f"내 전체 계좌 목록: {account_list}")
            if account_list:
                if len(account_list) > 1:
                    self.account_no = account_list[1]
                else:
                    self.account_no = account_list[0]
                    
                print(f"주계좌번호: {self.account_no}")
                self.check_balance()
        else:
            print(f"로그인 실패 (에러코드: {err_code})")

    def _on_receive_real_data(self, sCode, sRealType, sRealData):
        """실시간 시세 데이터 수신 이벤트 처리"""
        current_price = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", sCode, 10)
        if current_price:
            current_price = current_price.strip().replace('+', '').replace('-', '')
            print(f"[실시간 시세] 종목코드: {sCode} | 현재가: {current_price}")
            
    def _on_receive_tr_data(self, sScrNo, sRQName, sTrCode, sRecordName, sPrevNext, nDataLength, sErrorCode, sMessage, sSplmMsg):
        """TR(Transaction Request) 응답 수신 이벤트 처리"""
        if sRQName in ["주식일봉차트조회", "선물일봉차트조회", "주식분봉차트조회"]:
            repeat_cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", sTrCode, sRQName)
            date_field = "체결시간" if sRQName == "주식분봉차트조회" else "일자"
            
            for i in range(repeat_cnt):
                date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, date_field).strip()
                open_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "시가").strip()
                high_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "고가").strip()
                low_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "저가").strip()
                close_p = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "현재가").strip()
                volume = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "거래량").strip()
                
                self.ohlcv_data.append([date, abs(int(open_p)), abs(int(high_p)), abs(int(low_p)), abs(int(close_p)), abs(int(volume))])
            
            self.remained_data = (sPrevNext == "2")
            print(f"[{sRQName}] {repeat_cnt}일 치 데이터 수신 완료. (추가 데이터 존재: {self.remained_data})")
            
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

    def req_historical_data_single_page(self, code, tr_code, rq_name, tick_range="3"):
        """특정 종목의 과거 데이터를 1페이지(약 600~900개)만 조회합니다."""
        self.ohlcv_data = [] # 초기화
        
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        if tr_code == "opt10081":
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        elif tr_code == "opt10080":
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "틱범위", tick_range)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", rq_name, tr_code, 0, "1005")
        
        self.tr_event_loop = QEventLoop()
        self.tr_event_loop.exec_()

    def _on_receive_chejan_data(self, sGubun, nItemCnt, sFIdList):
        pass
