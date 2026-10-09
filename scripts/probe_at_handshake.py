"""抓取 AT 命令握手过程，看清采集器到底回了什么。

用来分辨两件事：
  1. AT+MAC 后面那几行短十六进制，是命令回显，还是其它手环的数据行
  2. 命令间隔太短会不会导致回显异常

用法: python scripts/probe_at_handshake.py --port COM12
"""

import argparse
import re
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wristband_gateway.parser import (  # noqa: E402
    BROADCAST_PREFIX,
    T10_NAME_HEX,
    T10_UUID_HEX,
)

TARGET_MAC = "5410260100DC"
HEX_ONLY = re.compile(r"^[0-9A-F]+$")


def classify(line: str) -> str:
    """把一行输出归类，判断它是回显还是空中数据。"""
    text = line.strip().upper()
    if not text:
        return "空行"
    if text.startswith("AT+") or text.startswith("OK"):
        return "命令回显"
    if not HEX_ONLY.match(text):
        return "其它文本"
    # 只认 T10 自己的 UUID 或名称。0201061AFF4C000215 是通用 iBeacon 前缀，
    # 现场的信标也用它，不能拿来判定手环
    if T10_UUID_HEX in text or T10_NAME_HEX in text:
        return "T10 手环"
    if BROADCAST_PREFIX in text:
        return f"iBeacon 类广播({len(text)}字符)"
    if len(text) <= 16:
        # BLE 广播帧最短也有 20 多字节，12 个字符装不下一条完整广播
        return f"短十六进制({len(text)}字符)"
    return f"其它设备数据({len(text)}字符)"


def run_once(port: str, delay: float, listen_seconds: float) -> None:
    print(f"\n{'=' * 76}")
    print(f"命令间隔 {delay:.2f} 秒，发送后监听 {listen_seconds:.0f} 秒")
    print("=" * 76)

    sequence = [
        "AT+SCANSTOP",
        "AT+UUID=NO",
        f"AT+MAC={TARGET_MAC}",
        "AT+TYPE=5",
        "AT+SCANSTART",
    ]

    with serial.Serial(port, 115200, timeout=0.2) as conn:
        conn.reset_input_buffer()
        started = time.monotonic()
        records: list[tuple[float, str, str]] = []

        def drain(seconds: float) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                raw = conn.readline()
                if not raw:
                    continue
                text = raw.decode("utf-8", errors="ignore").strip()
                if text:
                    records.append((time.monotonic() - started, text, classify(text)))

        # 先停扫描并等空气安静下来，否则命令回显会被空中数据淹没
        conn.write(b"AT+SCANSTOP\r\n")
        conn.flush()
        quiet_until = time.monotonic() + 2.0
        while time.monotonic() < quiet_until:
            raw = conn.readline()
            if raw:
                text = raw.decode("utf-8", errors="ignore").strip()
                if text:
                    records.append((time.monotonic() - started, text, classify(text)))
                    quiet_until = time.monotonic() + 0.4
        print(f"  {time.monotonic() - started:6.2f}s  扫描已停，空中安静")

        for command in sequence[1:]:
            marker = time.monotonic() - started
            print(f"  {marker:6.2f}s  发送 -> {command}")
            conn.write(f"{command}\r\n".encode())
            conn.flush()
            drain(delay)

        drain(listen_seconds)

    print(f"\n  收到 {len(records)} 行：")
    for offset, text, kind in records[:40]:
        shown = text if len(text) <= 60 else text[:57] + "…"
        print(f"    {offset:6.2f}s  [{kind:<18}] {shown}")
    if len(records) > 40:
        print(f"    …… 其余 {len(records) - 40} 行省略")

    # 重点：短十六进制行是否恰好等于我们发出去的 MAC
    shorts = [text for _, text, kind in records if kind.startswith("短十六进制")]
    print(f"\n  短十六进制行 {len(shorts)} 条：{shorts[:6]}")
    if shorts:
        exact = sum(1 for item in shorts if item == TARGET_MAC)
        print(f"    与发送的 {TARGET_MAC} 完全一致的有 {exact}/{len(shorts)} 条")
        if exact == len(shorts):
            print("    => 是命令回显，且内容正确")
        elif exact:
            print("    => 是命令回显，但部分被干扰")
        else:
            print("    => 都对不上，需要结合上下文判断是回显异常还是其它设备的数据")

    kinds: dict[str, int] = {}
    for _, _, kind in records:
        key = kind.split("(")[0]
        kinds[key] = kinds.get(key, 0) + 1
    print(f"  行类型统计：{kinds}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM12")
    parser.add_argument("--listen", type=float, default=8.0)
    args = parser.parse_args()

    print("AT 握手抓取：对比不同命令间隔下采集器的回显")
    print(f"目标 MAC：{TARGET_MAC}")

    # 0.05 是网关当前用的间隔，0.12 是指南给的上限
    for delay in (0.05, 0.12):
        try:
            run_once(args.port, delay, args.listen)
        except serial.SerialException as exc:
            print(f"  串口打不开：{exc}")
            return 1
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
