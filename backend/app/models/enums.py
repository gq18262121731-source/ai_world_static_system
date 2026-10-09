from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"          # 平台管理员
    STAFF = "staff"          # 社区/机构服务人员
    FAMILY = "family"        # 家属


class DeviceType(StrEnum):
    WATCH = "watch"
    DOG = "dog"


class DeviceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class OutingDecisionType(StrEnum):
    ALLOW = "allow"              # 允许外出
    CONDITIONAL = "conditional"  # 有条件允许
    DENY = "deny"                # 不建议外出


class OutingTaskStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABORTED = "aborted"


class AlarmType(StrEnum):
    FALL = "fall"
    SOS = "sos"
    GEOFENCE = "geofence"
    TIMEOUT = "timeout"
    HEALTH = "health"
    FOLLOW_LOST = "follow_lost"
    DEVICE_OFFLINE = "device_offline"


class AlarmLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlarmStatus(StrEnum):
    PENDING = "pending"        # 待处理
    CONFIRMING = "confirming"  # 机器狗语音确认中
    ACKED = "acked"            # 已受理
    RESOLVED = "resolved"      # 已处置
    FALSE_ALARM = "false_alarm"


class IntentType(StrEnum):
    OUTING_REQUEST = "outing_request"
    END_OUTING = "end_outing"
    HEALTH_QUERY = "health_query"
    GENERAL_QA = "general_qa"
    SOS = "sos"
    FALL_CONFIRM = "fall_confirm"
    CHITCHAT = "chitchat"


class MessageRole(StrEnum):
    ELDER = "elder"
    SYSTEM = "system"


class FallSource(StrEnum):
    WATCH = "watch"
    DOG = "dog"
    FUSION = "fusion"
