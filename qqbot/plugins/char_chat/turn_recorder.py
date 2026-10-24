"""
对话 turn 记录器（2026-06-27 引入）

设计目标：
- 每个 QQ 消息 → 一个 turn，turn_id = uuid4
- 每个 turn 内有多个 step（MSG / PROBE / LLM_CALL / TOOL_CALL / TOOL_RESULT / STREAM_CHUNK / REPLY / ERROR）
- 内存 ring buffer 默认保留 200 条 turn（最近），磁盘持久化到 logs/turns/{date}/{turn_id}.json
- 实时事件流追加到 logs/turns_stream.jsonl,dashboard 进程通过 tail 该文件做 SSE 推流
- 与 chat_logger 解耦：chat_logger 继续写 chat.log，turn_recorder 只写结构化事件
"""
import asyncio
import json
import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

from nonebot import logger


# ── 配置 ──
# turn_recorder.py 位于 qqbot/plugins/char_chat/turn_recorder.py
# parents[0]=char_chat/, parents[1]=plugins/, parents[2]=qqbot/, parents[3]=项目根
RING_BUFFER_SIZE = 200
TURNS_DISK_DIR = Path(__file__).resolve().parents[2] / "logs" / "turns"
TURNS_STREAM_FILE = Path(__file__).resolve().parents[2] / "logs" / "turns_stream.jsonl"
# 板块落盘开关（关闭后只走内存 ring，重启丢历史）
PERSIST_TO_DISK = True


# 模块 import 时立刻确保 stream 文件存在（dashboard 进程 tail 它）
try:
    TURNS_DISK_DIR.mkdir(parents=True, exist_ok=True)
    TURNS_STREAM_FILE.touch(exist_ok=True)
except Exception as _e:
    import logging as _logging
    _logging.getLogger("char_chat.events").warning(f"[turn_recorder] init file touch failed: {_e}")


class TurnRecorder:
    """单例：进程内唯一,所有 dashboard 订阅者共用同一 ring buffer"""

    def __init__(self):
        self._buffer: deque = deque(maxlen=RING_BUFFER_SIZE)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        # 跨 turn 关联: 当前活跃 turn_id(由 chat_handler 在 handle_chat 入口设置)
        self._current_turn_id: Optional[str] = None
        if PERSIST_TO_DISK:
            TURNS_DISK_DIR.mkdir(parents=True, exist_ok=True)
        # 确保 stream 文件存在,dashboard tail 它
        try:
            TURNS_STREAM_FILE.touch(exist_ok=True)
        except Exception:
            pass

    def set_current_turn(self, turn_id: Optional[str]):
        """chat_handler 在每条消息入口处设置,让其它模块(l lm / api_client)能关联 step"""
        self._current_turn_id = turn_id

    def current_turn_id(self) -> Optional[str]:
        return self._current_turn_id

    def start_turn(self, meta: dict) -> str:
        """开新 turn,返回 turn_id"""
        turn_id = uuid.uuid4().hex[:12]
        turn = {
            "turn_id": turn_id,
            "started_at": time.time(),
            "meta": meta,
            "steps": [],
            "status": "running",
        }
        self._buffer.append(turn)
        self._current_turn_id = turn_id
        # 同步广播一条 turn_started 事件,前端用它开新卡片
        self._broadcast_sync({
            "type": "turn_started",
            "turn": self._snapshot_turn(turn),
        })
        return turn_id

    def add_step(self, step: dict, turn_id: Optional[str] = None):
        """追加一条 step 到指定 turn(默认当前 turn)"""
        tid = turn_id or self._current_turn_id
        if not tid:
            # 没有 turn 上下文:作为孤儿 step 暂存到"__orphan__"虚拟 turn
            tid = "__orphan__"
        turn = self._find_turn(tid)
        if turn is None:
            # turn 已不在 ring(超过 200 条被淘汰):仍写磁盘但不入 ring
            if PERSIST_TO_DISK:
                self._write_step_to_disk(tid, step)
            return
        step.setdefault("ts", time.time())
        turn["steps"].append(step)
        self._broadcast_sync({
            "type": "step_added",
            "turn_id": tid,
            "step": step,
        })

    def end_turn(self, status: str = "ok", turn_id: Optional[str] = None):
        """结束 turn,标记 status"""
        tid = turn_id or self._current_turn_id
        if not tid:
            return
        turn = self._find_turn(tid)
        if turn is None:
            return
        turn["status"] = status
        turn["ended_at"] = time.time()
        self._current_turn_id = None
        # 落盘
        if PERSIST_TO_DISK:
            try:
                self._write_turn_to_disk(turn)
            except Exception as e:
                logger.warning(f"[turn_recorder] 落盘失败 turn={tid}: {e}")
        # 广播 turn_ended
        self._broadcast_sync({
            "type": "turn_ended",
            "turn_id": tid,
            "status": status,
            "duration": turn["ended_at"] - turn["started_at"],
        })

    def snapshot(self) -> list[dict]:
        """返回 ring buffer 的快照(供 dashboard 历史加载)"""
        return [self._snapshot_turn(t) for t in self._buffer]

    def subscribe(self) -> asyncio.Queue:
        """注册 SSE 订阅者"""
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    # ── 内部 ──

    def _find_turn(self, turn_id: str) -> Optional[dict]:
        # deque 不支持 random access,只能遍历
        for t in self._buffer:
            if t["turn_id"] == turn_id:
                return t
        return None

    def _snapshot_turn(self, turn: dict) -> dict:
        # 深拷贝避免前端读到时被改
        return json.loads(json.dumps(turn, default=str, ensure_ascii=False))

    def _broadcast_sync(self, event: dict):
        """同步广播(可能从非 async 上下文调用)"""
        # 1) 进程内 SSE 订阅者
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
            except Exception as e:
                logger.debug(f"[turn_recorder] broadcast error: {e}")
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)
        # 2) 跨进程:dashboard 进程通过 tail turns_stream.jsonl 来订阅
        # 这里把每个事件追加一行 JSON,dashboard SSE 推流
        try:
            with TURNS_STREAM_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[turn_recorder] stream file append failed: {e}")

    def _write_turn_to_disk(self, turn: dict):
        date_dir = TURNS_DISK_DIR / time.strftime("%Y%m%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        path = date_dir / f"{turn['turn_id']}.json"
        path.write_text(
            json.dumps(self._snapshot_turn(turn), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_step_to_disk(self, turn_id: str, step: dict):
        # turn 已被淘汰,补写磁盘(append 模式)
        # 简化:按 turn_id 当文件,没有就建,有就追加 step
        path = TURNS_DISK_DIR / "_overflow" / f"{turn_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(step, default=str, ensure_ascii=False) + "\n")


# 全局单例
_recorder: Optional[TurnRecorder] = None


def get_recorder() -> TurnRecorder:
    global _recorder
    if _recorder is None:
        _recorder = TurnRecorder()
        # bot 启动时清理上一轮残留的僵尸 turn
        # （上一进程被 kill -9 时没机会写 turn_finished 事件，导致 status=running 永远卡住）
        try:
            _cleanup_zombie_turns()
        except Exception as e:
            logger.warning(f"[turn_recorder] zombie cleanup failed: {e}")
    return _recorder


def _cleanup_zombie_turns():
    """bot 启动时扫描 turns_stream.jsonl,把「turn_started 但无 turn_finished」的 turn 标为 error

    时序:turn_started 一定在 turn_finished 之前,且每个 turn_id 只能各出现一次
    """
    if not TURNS_STREAM_FILE.exists():
        return
    # 1. 扫描所有 turn_id 出现的事件
    started: dict[str, dict] = {}   # turn_id -> 最后一次 turn_started 的事件
    ended: set[str] = set()
    try:
        with TURNS_STREAM_FILE.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                et = ev.get("type")
                if et == "turn_started":
                    tid = ev.get("turn", {}).get("turn_id")
                    if tid:
                        started[tid] = ev
                elif et == "turn_ended":
                    tid = ev.get("turn_id")
                    if tid:
                        ended.add(tid)
    except Exception as e:
        logger.warning(f"[turn_recorder] scan stream failed: {e}")
        return

    # 2. 找出 started 但 not ended 的 turn（僵尸）
    zombies = [tid for tid in started if tid not in ended]
    if not zombies:
        return

    # 3. 追加 turn_ended(status=error) 事件，让 dashboard 显示为「失败」
    #    而不是永远卡在「进行中」
    try:
        from nonebot import get_driver
        boot_ts = time.time()
        try:
            boot_ts = get_driver().start_time or time.time()
        except Exception:
            pass
        with TURNS_STREAM_FILE.open("a", encoding="utf-8") as f:
            for tid in zombies:
                started_ev = started[tid].get("turn", {})
                started_at = started_ev.get("started_at") or boot_ts
                ev = {
                    "type": "turn_ended",
                    "turn_id": tid,
                    "status": "error",
                    "duration": boot_ts - started_at,
                    "note": "bot 重启前未正常结束（被 kill -9 / 异常退出）",
                }
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        logger.warning(f"[turn_recorder] 清理了 {len(zombies)} 条僵尸 turn: {zombies[:5]}{'...' if len(zombies) > 5 else ''}")
    except Exception as e:
        logger.warning(f"[turn_recorder] append zombie cleanup failed: {e}")


# ── 便捷函数(给业务模块用)──

def begin_turn(meta: dict) -> str:
    return get_recorder().start_turn(meta)


def finish_turn(status: str = "ok", turn_id: Optional[str] = None):
    get_recorder().end_turn(status, turn_id)


def add_step(step: dict, turn_id: Optional[str] = None):
    """step 格式约定:
        {
          "kind": "msg" | "probe" | "llm_call" | "llm_chunk" | "tool_call" | "tool_result"
                | "reply" | "stream_segment" | "thinking" | "error" | "warning",
          "title": 简短一句话,
          "data":   dict 任意结构化详情,
        }
    """
    get_recorder().add_step(step, turn_id)