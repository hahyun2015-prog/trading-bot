import os
import winreg
import getpass
import socket
import sys

def set_reg_value(path, name, value, value_type=winreg.REG_SZ):
    try:
        # Create key if not exists
        key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, path)
        winreg.SetValueEx(key, name, 0, value_type, value)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"Error setting registry {name}: {e}")
        return False

def main():
    print("====================================================")
    print("  AMATS Windows 자동 로그인 설정 도우미 (Local Script)")
    print("====================================================")
    
    # Check admin privileges
    try:
        # Try to open HKLM key with write permission
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE", 0, winreg.KEY_WRITE)
        winreg.CloseKey(key)
    except PermissionError:
        print("❌ [오류] 관리자 권한이 없습니다. 이 스크립트는 관리자 권한이 필요합니다.")
        print("   앞선 단계에서 사용자 계정을 '관리자'로 변경하고 재부팅한 뒤 다시 실행해 주세요.")
        input("\n종료하려면 엔터를 누르세요...")
        sys.exit(1)
    
    # 1. Windows Hello 비밀번호 없는 로그인 비활성화 (netplwiz 옵션 활성화용)
    path_passwordless = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device"
    set_reg_value(path_passwordless, "DevicePasswordLessBuildVersion", 0, winreg.REG_DWORD)
    
    # 2. 자동 로그인 설정 정보 입력
    username = os.getlogin()
    domain = socket.gethostname()
    
    print(f"• 현재 사용자명: {username}")
    print(f"• 컴퓨터 이름: {domain}")
    print("\n* 중요: 자동 로그인을 설정하려면 Windows 계정의 실제 비밀번호가 필요합니다.")
    print("  (PIN 번호 4자리가 아닌 원래 계정 암호)")
    
    password = getpass.getpass("• Windows 비밀번호를 입력하세요: ")
    confirm_password = getpass.getpass("• 비밀번호를 다시 입력하세요: ")
    
    if password != confirm_password:
        print("❌ 비밀번호가 일치하지 않습니다. 다시 실행해 주세요.")
        input("\n종료하려면 엔터를 누르세요...")
        sys.exit(1)
        
    # 3. Winlogon 레지스트리 작성
    path_winlogon = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    
    success = True
    success &= set_reg_value(path_winlogon, "AutoAdminLogon", "1")
    success &= set_reg_value(path_winlogon, "DefaultUserName", username)
    success &= set_reg_value(path_winlogon, "DefaultPassword", password)
    success &= set_reg_value(path_winlogon, "DefaultDomainName", domain)
    
    if success:
        print("\n====================================================")
        print("✅ Windows 자동 로그인 설정이 성공적으로 완료되었습니다!")
        print("   이제 컴퓨터를 재부팅하시면 암호 입력 없이 바탕화면으로 진입합니다.")
        print("====================================================")
    else:
        print("\n❌ 레지스트리 설정 중 일부 오류가 발생했습니다.")
    
    input("\n종료하려면 엔터를 누르세요...")

if __name__ == "__main__":
    main()
