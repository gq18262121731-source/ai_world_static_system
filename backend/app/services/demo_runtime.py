"""托管演示用的设备模拟器进程。

演示模式下按一次快捷键，界面揭开的同时要让数据"开始流动"。模拟器是独立进程
（simulator/run_all.py，走 MQTT），这里负责把它拉起来、停掉、汇报状态。
只管自己启动的那个进程；用户手工在别的窗口跑的模拟器不归这里管。
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

from app.core.config import BASE_DIR
from app.core.logging import get_logger

logger = get_logger(__name__)

SIMULATOR_DIR = BASE_DIR.parent / "simulator"
ENTRYPOINT = SIMULATOR_DIR / "run_all.py"
LOG_PATH = BASE_DIR / "media" / "simulator_runtime.log"
# 正常停止最多等这么久，超时就强杀
STOP_TIMEOUT_SECONDS = 6.0


def find_external_simulators(exclude_pid: int | None = None) -> list[dict]:
    """找出不是本进程拉起的 run_all.py。

    两份模拟器同时跑会用同一个 MQTT client_id 互相踢下线，日志里表现为每几秒
    "已连接 MQTT" 一次、数据却断断续续。启动前先查一遍，有就沿用别人的。
    """
    try:
        if os.name == "nt":
            script = (
                "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                "Select-Object ProcessId, ParentProcessId, CommandLine | ConvertTo-Json -Compress"
            )
            output = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
            rows = json.loads(output) if output.strip() else []
            if isinstance(rows, dict):
                rows = [rows]
            found = [
                {
                    "pid": row.get("ProcessId"),
                    "parent": row.get("ParentProcessId"),
                    "command": row.get("CommandLine") or "",
                }
                for row in rows
            ]
        else:
            output = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, timeout=8
            ).stdout
            found = []
            for line in output.splitlines():
                parts = line.strip().split(None, 2)
                if len(parts) == 3 and parts[0].isdigit():
                    found.append(
                        {"pid": int(parts[0]), "parent": int(parts[1]), "command": parts[2]}
                    )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning("检测外部模拟器进程失败：%s", exc)
        return []
    # venv 里的 python.exe 是个启动器，会再拉一个真正的解释器子进程，两个都算自己的
    return [
        {"pid": item["pid"], "command": item["command"]}
        for item in found
        if "run_all.py" in item["command"]
        and item["pid"] != exclude_pid
        and item["parent"] != exclude_pid
    ]


class DemoRuntime:
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._log_handle = None
        self._started_at: datetime | None = None
        self._args: list[str] = []
        self.last_error = ""
        # 最近一次启动时发现的外部模拟器进程
        self.external: list[dict] = []

    # ---------- 状态 ----------

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def status(self) -> dict:
        exit_code = None
        if self._process is not None and not self.running:
            exit_code = self._process.returncode
        return {
            "available": ENTRYPOINT.exists(),
            "running": self.running,
            "pid": self._process.pid if self.running else None,
            "started_at": self._started_at.isoformat() if self.running and self._started_at else None,
            "uptime_seconds": (
                int((datetime.now() - self._started_at).total_seconds())
                if self.running and self._started_at
                else None
            ),
            "args": self._args if self.running else [],
            "last_exit_code": exit_code,
            "last_error": self.last_error,
            "external": self.external,
            "log_path": str(LOG_PATH),
        }

    # ---------- 启停 ----------

    def start(self, only: str = "", watch_interval: float | None = None) -> dict:
        if self.running:
            return self.status()
        if not ENTRYPOINT.exists():
            self.last_error = f"找不到模拟器入口 {ENTRYPOINT}"
            logger.warning(self.last_error)
            return self.status()
        self.external = find_external_simulators()
        if self.external:
            pids = ", ".join(str(item["pid"]) for item in self.external)
            self.last_error = f"已有模拟器在别处运行（PID {pids}），沿用它，不再重复启动"
            logger.info(self.last_error)
            return self.status()

        args = [sys.executable, "-u", str(ENTRYPOINT)]
        if only:
            args += ["--only", only]
        if watch_interval:
            args += ["--watch-interval", str(watch_interval)]

        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._close_log()
        self._log_handle = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        self._log_handle.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动 {' '.join(args[2:])} =====\n")

        popen_kwargs: dict = {
            "cwd": str(SIMULATOR_DIR),
            "stdout": self._log_handle,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            # 继承后端的环境变量，MQTT_HOST 之类的配置就不用重复写
            "env": {**os.environ, "PYTHONIOENCODING": "utf-8"},
        }
        if os.name == "nt":
            # 单独的进程组，停止时才能只给它发 Ctrl+Break 而不波及后端自己
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            self._process = subprocess.Popen(args, **popen_kwargs)
        except OSError as exc:
            self.last_error = f"启动模拟器失败：{exc}"
            logger.exception("启动模拟器失败")
            self._close_log()
            return self.status()

        self._started_at = datetime.now()
        self._args = args[2:]
        self.last_error = ""
        logger.info("模拟器进程已启动 pid=%s args=%s", self._process.pid, self._args)
        return self.status()

    def stop(self) -> dict:
        process = self._process
        if process is None or process.poll() is not None:
            self._close_log()
            return self.status()

        try:
            if os.name == "nt":
                # 模拟器接了 SIGBREAK，会先给每台设备发 offline 再退出
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
        except (OSError, ValueError) as exc:
            logger.warning("向模拟器发停止信号失败（%s），改为直接终止", exc)
            process.terminate()

        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            logger.warning("模拟器 %.0f 秒内未退出，强制结束", STOP_TIMEOUT_SECONDS)
            process.kill()
            process.wait(timeout=3)

        logger.info("模拟器进程已停止 exit=%s", process.returncode)
        self._close_log()
        return self.status()

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def log_tail(self, lines: int = 30) -> list[str]:
        if not LOG_PATH.exists():
            return []
        try:
            content = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return []
        return content[-lines:]


demo_runtime = DemoRuntime()
