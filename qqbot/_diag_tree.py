import psutil
import os
# 列出所有 python 进程, 按 parent 排序
all_p = []
for p in psutil.process_iter(['pid', 'ppid', 'name', 'exe', 'create_time']):
    name = (p.info.get('name') or '').lower()
    if 'python' not in name:
        continue
    try:
        e = p.info.get('exe') or ''
        all_p.append((p.info['pid'], p.info['ppid'], e, p.info['create_time'], p.cmdline()[:3]))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

# 找所有 powershell
ps = [p for p in psutil.process_iter(['pid', 'name', 'exe']) if (p.info.get('name') or '').lower() in ('powershell.exe', 'pwsh.exe')]
for p in ps:
    try:
        e = p.info.get('exe') or ''
        all_p.append((p.pid, p.ppid(), e, p.create_time(), ['POWERSHELL'] + p.cmdline()[:2]))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

# 排序: 按 create_time
all_p.sort(key=lambda x: x[3])

for pid, ppid, exe, ct, cmd in all_p:
    kind = 'venv' if 'skill_qqbot' in exe else 'anaconda' if 'anaconda' in exe else 'powershell' if 'powershell' in exe.lower() else 'other'
    cmd_short = ' '.join(str(x) for x in cmd)[:80]
    print(f'pid={pid:>6} ppid={ppid:>6} {kind:<11} cmd={cmd_short}')
