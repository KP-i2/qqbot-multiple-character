import psutil
ps = list(psutil.process_iter(['pid', 'ppid', 'name', 'exe']))
for p in ps:
    name = (p.info.get('name') or '').lower()
    if 'python' not in name:
        continue
    try:
        exe = p.info.get('exe') or ''
        exe_kind = 'venv' if 'skill_qqbot' in exe else 'anaconda' if 'anaconda' in exe else 'other'
        cmd = ' '.join(p.cmdline())[:160]
        ppid = p.info.get('ppid')
        pid = p.info.get('pid')
        print(f'pid={pid:>5} ppid={ppid:>5} exe={exe_kind:<8} cmd={cmd}')
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
