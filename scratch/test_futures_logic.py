import sys
import os
from unittest.mock import MagicMock

# Mock QAxWidget to prevent it from trying to load the activeX control
class MockSignal:
    def connect(self, slot):
        pass

class MockQAxWidget:
    def __init__(self, *args, **kwargs):
        self.OnEventConnect = MockSignal()
        self.OnReceiveTrData = MockSignal()
        self.OnReceiveChejanData = MockSignal()
        self.OnReceiveMsg = MockSignal()
        self.OnReceiveRealData = MockSignal()
        
    def dynamicCall(self, *args, **kwargs):
        return ""

# Inject mock QAxWidget into sys.modules using a real module type
import types
import PyQt5
qax = types.ModuleType('QAxContainer')
qax.QAxWidget = MockQAxWidget
sys.modules['PyQt5.QAxContainer'] = qax
PyQt5.QAxContainer = qax

# Mock other PyQt5 classes if needed
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

# Import ERAOrderManager
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../era")))
from era.era_order_manager import ERAOrderManager

# Initialize the manager with mocked QAxWidget
# Since the constructor tries to initialize QApplication, let's make sure app exists
if not QApplication.instance():
    app = QApplication([])
else:
    app = QApplication.instance()

# Mock functions called during __init__ to avoid DB and external calls
def mock_load_config(self):
    self.environment = "live"
    self.trading_mode = "both"
    self.config_stock_acc = "12345678"
    self.config_futures_acc = "87654321"
    self.futures_prefix = "101"

ERAOrderManager.load_config = mock_load_config
ERAOrderManager.load_persisted_positions = MagicMock()
ERAOrderManager.load_futures_exit_state = MagicMock()
ERAOrderManager._load_prev_range = MagicMock()

manager = ERAOrderManager()
manager.environment = "live"
manager.real_day_code = "101V6000"
manager.real_night_code = "105V6000"
manager.futures_account = "12345678"

# 1. Test opw20007 ("선물잔고조회") Filtering
print("\n--- 1. Testing opw20007 (선물잔고조회) ---")
# Mock kiwoom dynamicCall to return data
# We have 3 rows:
# Row 0: KOSPI200 Day Futures (101V6000) - LONG, 1 contract, buy_price = 350.0, current_price = 351.0
# Row 1: KOSPI200 Option (201V3600) - LONG, 1 contract (Should be filtered out!)
# Row 2: KOSPI200 Night Futures (105V6000) - SHORT, 2 contracts, buy_price = 350.5, current_price = 349.5

mock_data = {
    ("GetRepeatCnt", "opw20007", "선물잔고조회"): 3,
    ("GetCommData", "opw20007", "선물잔고조회", 0, "종목코드"): "101V6000",
    ("GetCommData", "opw20007", "선물잔고조회", 0, "매도매수구분"): "2", # 매수
    ("GetCommData", "opw20007", "선물잔고조회", 0, "수량"): "1",
    ("GetCommData", "opw20007", "선물잔고조회", 0, "매입단가"): "350.00",
    ("GetCommData", "opw20007", "선물잔고조회", 0, "현재가"): "351.00",

    ("GetCommData", "opw20007", "선물잔고조회", 1, "종목코드"): "201V3600", # Option (should be skipped!)
    ("GetCommData", "opw20007", "선물잔고조회", 1, "매도매수구분"): "2",
    ("GetCommData", "opw20007", "선물잔고조회", 1, "수량"): "5",
    ("GetCommData", "opw20007", "선물잔고조회", 1, "매입단가"): "3.50",
    ("GetCommData", "opw20007", "선물잔고조회", 1, "현재가"): "3.60",

    ("GetCommData", "opw20007", "선물잔고조회", 2, "종목코드"): "105V6000",
    ("GetCommData", "opw20007", "선물잔고조회", 2, "매도매수구분"): "1", # 매도
    ("GetCommData", "opw20007", "선물잔고조회", 2, "수량"): "2",
    ("GetCommData", "opw20007", "선물잔고조회", 2, "매입단가"): "350.50",
    ("GetCommData", "opw20007", "선물잔고조회", 2, "현재가"): "349.50",
}

def mock_dynamic_call(signature, *args):
    sig_name = signature.split("(")[0]
    key_prefix = (sig_name,) + tuple(args)
    return mock_data.get(key_prefix, "")

manager.kiwoom.dynamicCall = mock_dynamic_call
manager.export_status = MagicMock()

# Run the callback handler
# Internally, rqname is "선물잔고조회", trcode is "opw20007"
manager._on_receive_tr_data("2002", "선물잔고조회", "opw20007", "", "0")

print("Futures positions after sync:")
print(manager.futures_positions)
# Assert that Option position was skipped
assert "KOSPI200" in manager.futures_positions, "KOSPI200 day position should be present"
assert "KOSPI200_NIGHT" in manager.futures_positions, "KOSPI200 night position should be present"
assert len(manager.futures_positions) == 2, f"Expected 2 positions, got {len(manager.futures_positions)}"
assert manager.futures_positions["KOSPI200"]["qty"] == 1, "KOSPI200 day qty should be 1"
assert manager.futures_positions["KOSPI200_NIGHT"]["qty"] == 2, "KOSPI200 night qty should be 2"
print("=> 1. opw20007 Filtering Test PASSED")


# 2. Test is_exiting check and duplicate exit prevention
print("\n--- 2. Testing is_exiting and duplicate exit prevention ---")
# Mock SendOrderFO to return success (0)
# We set up a LONG position
manager.futures_positions = {
    "KOSPI200": {
        'type': 'LONG',
        'qty': 1,
        'price': 350.0,
        'current_price': 350.0
    }
}
manager.futures_day_entry_price = 350.0
manager.futures_order_locked = False

order_calls = 0
def mock_send_order_fo(signature, args):
    global order_calls
    order_calls += 1
    return 0  # Success

manager.kiwoom.dynamicCall = lambda signature, args: mock_send_order_fo(signature, args)

# Call exit order
manager._execute_futures_direct("LONG_EXIT", 348.0, "101V6000", "KOSPI200")
print(f"First Exit Order placed. Order calls count: {order_calls}")
assert order_calls == 1, f"Expected 1 order call, got {order_calls}"
assert manager.futures_positions["KOSPI200"].get("is_exiting") is True, "Position should be marked is_exiting = True"

# Try calling exit order again (e.g. on next tick)
manager._execute_futures_direct("LONG_EXIT", 348.0, "101V6000", "KOSPI200")
print(f"Second Exit Order attempt. Order calls count: {order_calls}")
# Since is_exiting is True, the monitor should skip it
# Let's test if tick monitor skips it:
# Reset order_calls to 0
order_calls = 0
# Run _process_day_tick with current_price = 345.0 (which triggers stop-loss if not is_exiting)
# If it doesn't skip, it would call _execute_futures_direct
manager._process_day_tick("101V6000", 345.0, MagicMock(hour=10, minute=0))
print(f"Tick monitor with is_exiting=True. Order calls count: {order_calls}")
assert order_calls == 0, "No exit order should be sent since is_exiting is True!"
print("=> 2. duplicate exit prevention Test PASSED")


# 3. Test 08:45 Liquidation Code Routing
print("\n--- 3. Testing 08:45 Liquidation Code Routing ---")
manager.futures_positions = {
    "KOSPI200": {
        'type': 'LONG',
        'qty': 1,
        'price': 350.0,
        'current_price': 350.0
    },
    "KOSPI200_NIGHT": {
        'type': 'SHORT',
        'qty': 1,
        'price': 350.0,
        'current_price': 350.0
    }
}
manager.futures_order_locked = False
manager.futures_night_order_locked = False

sent_codes = []
def mock_send_order_fo_capture(signature, args):
    sent_codes.append(args[3])
    return 0

manager.kiwoom.dynamicCall = lambda signature, args: mock_send_order_fo_capture(signature, args)

# Call 08:45 liquidation (tick at 08:45)
now_time = MagicMock(hour=8, minute=45)
manager._process_day_tick("101V6000", 350.0, now_time)

print(f"Sent orders codes at 08:45: {sent_codes}")
assert "101V6000" in sent_codes, "Should send liquidation for KOSPI200 using day code"
assert "105V6000" in sent_codes, "Should send liquidation for KOSPI200_NIGHT using night code"
print("=> 3. 08:45 Liquidation Code Routing Test PASSED")

print("\nAll Tests PASSED successfully!")
