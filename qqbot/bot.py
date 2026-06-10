import sys

# 修复 venv 子进程问题：venv 基于 anaconda 创建，sys._base_executable 指向
# anaconda python，导致 uvicorn/multiprocessing 生成 anaconda 子进程（缺少依赖）。
# 将 _base_executable 修正为当前 venv python，避免产生无法工作的子进程。
if hasattr(sys, "_base_executable") and sys._base_executable != sys.executable:
    sys._base_executable = sys.executable

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
