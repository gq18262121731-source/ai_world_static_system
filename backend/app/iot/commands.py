from datetime import datetime
from typing import Any

from app.core.events import hub
from app.core.logging import get_logger
from app.iot import topics
from app.iot.mqtt_client import mqtt_bridge

logger = get_logger(__name__)


async def send_command(
    device_id: str,
    command: str,
    payload: dict[str, Any] | None = None,
    elder_id: int | None = None,
) -> dict:
    """向机器狗下发指令, 并同步推送到前端(模拟器面板据此播报)。"""
    message = {
        "command": command,
        "payload": payload or {},
        "ts": datetime.now().isoformat(),
    }
    delivered = mqtt_bridge.publish(topics.command_topic(device_id), message)
    message["delivered_via_mqtt"] = delivered
    message["device_id"] = device_id
    await hub.broadcast("device_command", message, elder_id=elder_id)
    logger.info("下发指令 %s -> %s (mqtt=%s)", command, device_id, delivered)
    return message


async def speak(
    device_id: str, text: str, elder_id: int | None = None, audio_url: str = ""
) -> dict:
    return await send_command(
        device_id,
        topics.CMD_SPEAK,
        {"text": text, "audio_url": audio_url},
        elder_id=elder_id,
    )


async def start_follow(
    device_id: str,
    elder_id: int,
    task_id: int,
    duration_minutes: int,
    radius_m: int,
) -> dict:
    return await send_command(
        device_id,
        topics.CMD_START_FOLLOW,
        {
            "task_id": task_id,
            "duration_minutes": duration_minutes,
            "radius_m": radius_m,
            "fall_detection": True,
        },
        elder_id=elder_id,
    )


async def stop_follow(device_id: str, elder_id: int, task_id: int) -> dict:
    return await send_command(
        device_id, topics.CMD_STOP_FOLLOW, {"task_id": task_id}, elder_id=elder_id
    )


async def return_home(device_id: str, elder_id: int) -> dict:
    return await send_command(device_id, topics.CMD_RETURN_HOME, {}, elder_id=elder_id)


async def ask_confirm(
    device_id: str, elder_id: int, text: str, alarm_id: int, timeout_seconds: int
) -> dict:
    return await send_command(
        device_id,
        topics.CMD_ASK_CONFIRM,
        {"text": text, "alarm_id": alarm_id, "timeout_seconds": timeout_seconds},
        elder_id=elder_id,
    )


async def alarm_broadcast(device_id: str, elder_id: int, text: str, level: str) -> dict:
    return await send_command(
        device_id,
        topics.CMD_ALARM_BROADCAST,
        {"text": text, "level": level},
        elder_id=elder_id,
    )


async def ping(device_id: str, nonce: str, elder_id: int | None = None) -> dict:
    """自检探活：设备收到后应在 event 主题回 pong 并带上同样的 nonce。"""
    return await send_command(device_id, topics.CMD_PING, {"nonce": nonce}, elder_id)


def send_simulator_control(device_id: str, action: str, payload: dict | None = None) -> bool:
    """控制模拟器注入异常数据 / 触发跌倒 / 模拟说话。"""
    return mqtt_bridge.publish(
        topics.simulator_control_topic(device_id),
        {"action": action, "payload": payload or {}, "ts": datetime.now().isoformat()},
    )
