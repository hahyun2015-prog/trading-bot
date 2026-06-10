import sys
import os

# Forward execution to tca_controller.py
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
sys.path.append(os.path.join(current_dir, "tca"))

from tca.tca_controller import TCAController

if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        print("[TDA] 윈도우 절전 방지 활성화 완료.")
    except Exception as e:
        print(f"[TDA] 절전 방지 실패: {e}")

    controller = TCAController()
    controller.run_controller()
