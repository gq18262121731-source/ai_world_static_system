"""一次性启动演示所需的全部设备模拟器。

用法:
    python run_all.py                      # 启动种子数据里的两位老人的设备
    python run_all.py --only LJG           # 只启动张三的设备
"""

import argparse
import threading

from common import install_signal_handlers
from dog_sim import DogSimulator
from watch_sim import WatchSimulator

DEVICE_SETS = [
    {
        "key": "LJG",
        "elder": "张三",
        "watch": "WATCH-LJG-001",
        "dog": "DOG-LJG-001",
        "profile": "normal",
        "lat": 30.2795,
        "lng": 120.1330,
    },
    {
        "key": "ZXL",
        "elder": "李四",
        "watch": "WATCH-ZXL-001",
        "dog": "DOG-ZXL-001",
        "profile": "hypertension_tachycardia",
        "lat": 30.3120,
        "lng": 120.1420,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="批量启动设备模拟器")
    parser.add_argument("--only", default="", help="只启动指定老人的设备(LJG/ZXL)")
    parser.add_argument("--watch-interval", type=float, default=10.0)
    parser.add_argument("--dog-interval", type=float, default=5.0)
    args = parser.parse_args()

    simulators = []
    for item in DEVICE_SETS:
        if args.only and args.only.upper() != item["key"]:
            continue
        simulators.append(
            WatchSimulator(
                item["watch"],
                item["lat"],
                item["lng"],
                args.watch_interval,
                item["profile"],
            )
        )
        simulators.append(
            DogSimulator(item["dog"], item["lat"], item["lng"], args.dog_interval)
        )

    install_signal_handlers(*simulators)
    threads = [threading.Thread(target=sim.run, daemon=True) for sim in simulators]
    for thread in threads:
        thread.start()
    print(f"已启动 {len(simulators)} 个设备模拟器，按 Ctrl+C 停止")
    # 不能用不带超时的 join：Windows 上它会一直阻塞在锁上，Ctrl+Break 的信号处理器
    # 永远轮不到执行，后端就只能等超时后强杀，设备也来不及发 offline
    while any(thread.is_alive() for thread in threads):
        for thread in threads:
            thread.join(timeout=0.5)


if __name__ == "__main__":
    main()
