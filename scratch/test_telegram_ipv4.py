import socket
import requests

print("--- Testing Telegram API connection (IPv6 preferred, default) ---")
try:
    r = requests.get("https://api.telegram.org", timeout=5)
    print("Success! Status code:", r.status_code)
except Exception as e:
    print("Failed with default settings:", e)

print("\n--- Patching socket.getaddrinfo to force IPv4 (AF_INET) ---")
orig_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo

try:
    r = requests.get("https://api.telegram.org", timeout=5)
    print("Success after patch! Status code:", r.status_code)
except Exception as e:
    print("Failed after patch:", e)
