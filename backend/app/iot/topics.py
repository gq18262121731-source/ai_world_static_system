"""MQTT 主题约定。

真机接入时只要按同样的主题与报文格式收发, 即可无缝替换模拟器。

上行 (设备 -> 平台):
    aiot/watch/{device_id}/telemetry   手表健康遥测
    aiot/watch/{device_id}/event       手表事件 (fall / sos / low_battery)
    aiot/dog/{device_id}/telemetry     机器狗状态与位置
    aiot/dog/{device_id}/event         机器狗事件 (fall_detected / follow_lost)
    aiot/dog/{device_id}/speech        机器狗语音识别文本
    aiot/{type}/{device_id}/status     上下线状态 (含 LWT)

下行 (平台 -> 设备):
    aiot/dog/{device_id}/cmd           机器狗指令
    aiot/sim/{device_id}/control       模拟器控制指令 (仅模拟环境使用)
"""

from app.core.config import settings

PREFIX = settings.mqtt_topic_prefix

TELEMETRY = "telemetry"
EVENT = "event"
SPEECH = "speech"
STATUS = "status"
CMD = "cmd"


def telemetry_topic(device_type: str, device_id: str) -> str:
    return f"{PREFIX}/{device_type}/{device_id}/{TELEMETRY}"


def event_topic(device_type: str, device_id: str) -> str:
    return f"{PREFIX}/{device_type}/{device_id}/{EVENT}"


def speech_topic(device_id: str) -> str:
    return f"{PREFIX}/dog/{device_id}/{SPEECH}"


def status_topic(device_type: str, device_id: str) -> str:
    return f"{PREFIX}/{device_type}/{device_id}/{STATUS}"


def command_topic(device_id: str) -> str:
    return f"{PREFIX}/dog/{device_id}/{CMD}"


def simulator_control_topic(device_id: str) -> str:
    return f"{PREFIX}/sim/{device_id}/control"


def gateway_status_topic(gateway_id: str) -> str:
    return f"{PREFIX}/gateway/{gateway_id}/{STATUS}"


SUBSCRIPTIONS: list[str] = [
    f"{PREFIX}/watch/+/{TELEMETRY}",
    f"{PREFIX}/watch/+/{EVENT}",
    f"{PREFIX}/watch/+/{STATUS}",
    f"{PREFIX}/dog/+/{TELEMETRY}",
    f"{PREFIX}/dog/+/{EVENT}",
    f"{PREFIX}/dog/+/{SPEECH}",
    f"{PREFIX}/dog/+/{STATUS}",
    # 采集网关的心跳，用于判断真机数据通道是否还活着
    f"{PREFIX}/gateway/+/{STATUS}",
]


def parse(topic: str) -> tuple[str, str, str] | None:
    """topic -> (device_type, device_id, kind)"""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != PREFIX:
        return None
    _, device_type, device_id, kind = parts
    return device_type, device_id, kind


# 机器狗支持的下行指令
CMD_SPEAK = "tts_speak"
CMD_START_FOLLOW = "start_follow"
CMD_STOP_FOLLOW = "stop_follow"
CMD_RETURN_HOME = "return_home"
CMD_ALARM_BROADCAST = "alarm_broadcast"
CMD_ASK_CONFIRM = "ask_confirm"
# 自检用：下发后设备应在 event 主题回 pong，用来确认它真的还能收指令
CMD_PING = "ping"
