import sys
from pathlib import Path

# venv 子进程补丁: uvicorn / multiprocessing 会用 sys._base_executable 派生 worker,
# 把它锁到当前 venv 解释器, 避免派生出去的子进程拿不到依赖。
# 详细环境说明见根目录 manage.bat / scripts/bat/launch_dashboard.ps1。
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

# 私密配置路径：统一在 private/ 下
_PRIVATE_ENV = Path(__file__).resolve().parent.parent / "private" / ".env"
if _PRIVATE_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_PRIVATE_ENV)

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 显式传 _env_file 给 NoneBot2 init，让它从 private/.env 读取
# （NoneBot2 默认从 CWD 的 .env / .env.prod 读，路径写死无法用 pyproject.toml 改）
nonebot.init(_env_file=str(_PRIVATE_ENV) if _PRIVATE_ENV.exists() else None)

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
