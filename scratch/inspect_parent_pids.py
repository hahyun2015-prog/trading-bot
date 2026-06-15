import psutil

print("=== Python Process Tree ===")
for proc in psutil.process_iter(['pid', 'name', 'ppid', 'cmdline']):
    try:
        info = proc.info
        name = info['name'] or ""
        cmdline = info['cmdline'] or []
        if 'python' in name.lower() or any('python' in arg.lower() for arg in cmdline):
            ppid = info['ppid']
            try:
                parent = psutil.Process(ppid)
                parent_info = f"{parent.name()} (PID: {ppid})"
            except psutil.NoSuchProcess:
                parent_info = f"Unknown (PID: {ppid})"
            print(f"PID: {info['pid']} | Name: {info['name']} | Parent: {parent_info} | CmdLine: {cmdline}")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
