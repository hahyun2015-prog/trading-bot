"""ERA 에러 진단 래퍼 - 어떤 에러든 잡아서 출력"""
import sys, os, traceback

print("=" * 50)
print("  ERA 에러 진단 모드")
print("=" * 50)

try:
    # ERA 경로 설정
    era_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "era")
    root_dir = os.path.dirname(os.path.abspath(__file__))
    
    print(f"[1] Python: {sys.executable}")
    print(f"[2] 버전: {sys.version}")
    print(f"[3] 비트: {8 * __import__('struct').calcsize('P')}bit")
    print(f"[4] ERA 경로: {era_dir}")
    print()

    # step-by-step import
    print("[5] PyQt5 import...")
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QAxContainer import QAxWidget
    from PyQt5.QtCore import QTimer
    print("    OK")
    
    print("[6] QApplication 생성...")
    app = QApplication(sys.argv)
    print("    OK")
    
    print("[7] Kiwoom OCX 로드...")
    kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
    print("    OK")
    
    print("[8] config.json 로드...")
    import json
    config_path = os.path.join(root_dir, "config", "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    print(f"    OK - environment={config.get('environment')}, trading_mode={config.get('trading_mode')}")
    
    print("[9] notifier import...")
    sys.path.append(root_dir)
    try:
        import notifier
        print(f"    OK - notifier={notifier}")
    except ImportError as e:
        print(f"    notifier 없음 (정상): {e}")
    
    print("[10] ERAOrderManager import...")
    sys.path.insert(0, root_dir)
    os.chdir(era_dir)
    from era.era_order_manager import ERAOrderManager
    print("     OK")
    
    print("[11] ERAOrderManager() 생성...")
    manager = ERAOrderManager()
    print("     OK - ERA 정상 시작!")
    
    print("\n[ERA 정상 구동] 이벤트 루프 시작...")
    sys.exit(app.exec_())

except SystemExit as e:
    print(f"\n[SystemExit] code={e.code}")
except Exception as e:
    print(f"\n{'='*50}")
    print(f"[에러 발생] {type(e).__name__}: {e}")
    print(f"{'='*50}")
    traceback.print_exc()

print("\n" + "=" * 50)
input("이 창을 닫으려면 Enter 키를 누르세요...")
