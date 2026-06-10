import io
import sys

def main():
    file_path = r'c:\Antigravity\AI_T_Agent\era\era_order_manager.py'
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    modified = False
    new_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. Check for ISF OnReceiveChejanData lock release target
        # We look for:
        #   if code in self.isf_code_map:
        #       sc = self.isf_code_map[code]
        # and then insert the lock release
        if 'if code in self.isf_code_map:' in line and i + 1 < len(lines) and 'sc = self.isf_code_map[code]' in lines[i+1]:
            # Make sure we are in OnReceiveChejanData, not in some other place.
            # In OnReceiveChejanData, the line '# 개별주식선물(ISF) 체결 처리' is right before it.
            # Let's check if the line before or 2 lines before has '# 개별주식선물(ISF) 체결 처리' or 'isf_cfg'
            # Let's just check if 'self.isf_order_locked[sc] = False' is already there to avoid duplicates.
            new_lines.append(line)
            new_lines.append(lines[i+1])
            if 'self.isf_order_locked[sc] = False' not in lines[i+2]:
                indent = len(lines[i+1]) - len(lines[i+1].lstrip())
                new_lines.append(' ' * indent + 'self.isf_order_locked[sc] = False  # 체결되었으므로 주문 잠금 해제\n')
                print("Inserted ISF lock release logic.")
                modified = True
            i += 2
            continue
            
        # 2. Check for Futures OnReceiveChejanData lock release target
        # We look for:
        #   pos_key = "KOSPI200_NIGHT" if is_night_fill else "KOSPI200"
        # and insert the futures lock release before it.
        elif 'pos_key = "KOSPI200_NIGHT" if is_night_fill else "KOSPI200"' in line:
            if 'self.futures_order_locked = False' not in lines[i-1] and 'self.futures_order_locked = False' not in lines[i-2] and 'self.futures_order_locked = False' not in lines[i-3]:
                indent = len(line) - len(line.lstrip())
                new_lines.append(' ' * indent + '# 체결되었으므로 주문 잠금 해제\n')
                new_lines.append(' ' * indent + 'if is_night_fill:\n')
                new_lines.append(' ' * (indent + 4) + 'self.futures_night_order_locked = False\n')
                new_lines.append(' ' * indent + 'else:\n')
                new_lines.append(' ' * (indent + 4) + 'self.futures_order_locked = False\n')
                new_lines.append('\n')
                print("Inserted Futures lock release logic.")
                modified = True
            new_lines.append(line)
            i += 1
            continue

        new_lines.append(line)
        i += 1

    if modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print("File successfully modified and saved.")
    else:
        print("No modifications needed or target lines not found.")

if __name__ == '__main__':
    main()
