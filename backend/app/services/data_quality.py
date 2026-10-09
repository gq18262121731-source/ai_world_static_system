"""孤立坏读数识别。

手环在手腕移动、没贴紧皮肤的瞬间会给出一次离谱的读数（实测：心率 82 的人
突然冒出一条 214，下一秒又回到 82）。这种值落在生理可能区间内，网关侧的
范围校验挡不住，进了库就会让外出评估多出一条"最近曾出现 214"的提醒、
触发假告警、把 24 小时范围拉成 62~214。

判定原则只有一条：**真实的生理变化会持续，坏读数不会。**
一个读数与最近几分钟的基线差得太多，且紧邻的下一个读数没有跟上来，就是孤立坏读数；
只要下一个读数也跟着偏离，就认为是真的变化，同时把前一条撤回可信。

被判可疑的字段原值照常入库，只是打上标记；评估、告警、统计一律跳过。
"""

from datetime import datetime, timedelta
from statistics import median

from app.models import HealthRecord

# 与近期基线相差超过这个值才算"跳变"。取得比正常波动宽得多：
# 上楼梯心率从 80 到 115 是常见的，不能误伤；但 80 到 214 不可能在几秒内发生。
JUMP_THRESHOLDS: dict[str, float] = {
    "heart_rate": 40,
    "spo2": 8,
    "systolic": 45,
    "diastolic": 30,
    "temperature": 1.5,
}
# 基线取这么久以内、未被标记的读数的中位数
BASELINE_MINUTES = 10
# 少于这么多个基线点就不下判断——刚开机、刚换手环时宁可放过
MIN_BASELINE_POINTS = 3
# 紧邻的前一个读数在这么久以内且与本次一致，就认为变化是持续的
CONFIRM_SECONDS = 120
# 血压是一次测量给出的一对数，任一项可疑就整对都不可信
PAIRED_FIELDS = {"systolic": "diastolic", "diastolic": "systolic"}
# 合成数据没有传感器，谈不上坏读数，而且演示面板会把 6 个点回填到过去 25 分钟里，
# 与"读数按时间顺序到达"的前提不符——一律不判定，演示注入的异常必须原样生效
SYNTHETIC_SOURCES = {"simulator", "manual"}


def evaluate(
    record: HealthRecord, history: list[HealthRecord]
) -> tuple[set[str], list[tuple[HealthRecord, str]]]:
    """判定一条新记录。

    history 是同一位老人在 record 之前、按时间升序的记录（至少覆盖 BASELINE_MINUTES）。
    返回 (record 上要标记的可疑字段, 需要撤销标记的 [(历史记录, 字段)])。
    """
    suspicious: set[str] = set()
    cleared: list[tuple[HealthRecord, str]] = []
    source = record.source or ""
    if source in SYNTHETIC_SOURCES:
        return suspicious, cleared
    window_start = record.recorded_at - timedelta(minutes=BASELINE_MINUTES)
    # 只拿同一来源的读数做基线：真机串口流和别的设备流交错入库时不能互相比对，
    # 否则一条别处来的读数就能把真机的孤立坏值"确认"成真
    history = [r for r in history if (r.source or "") == source]

    for key, jump in JUMP_THRESHOLDS.items():
        value = getattr(record, key, None)
        if value is None:
            continue
        value = float(value)

        baseline_values = [
            float(r.trusted(key))
            for r in history
            if r.recorded_at >= window_start and r.trusted(key) is not None
        ]
        if len(baseline_values) < MIN_BASELINE_POINTS:
            continue
        if abs(value - median(baseline_values)) <= jump:
            continue

        # 跳变了。看紧邻的前一个读数是否与本次一致（不管它当时是否被标可疑）
        previous = _previous_with_field(history, key, record.recorded_at)
        if previous is not None and abs(value - float(getattr(previous, key))) <= jump:
            if key in previous.suspicious():
                cleared.append((previous, key))
            continue
        suspicious.add(key)

    for key in list(suspicious):
        partner = PAIRED_FIELDS.get(key)
        if partner and getattr(record, partner, None) is not None:
            suspicious.add(partner)
    return suspicious, cleared


def _previous_with_field(
    history: list[HealthRecord], key: str, before: datetime
) -> HealthRecord | None:
    cutoff = before - timedelta(seconds=CONFIRM_SECONDS)
    for candidate in reversed(history):
        if candidate.recorded_at < cutoff:
            return None
        if getattr(candidate, key, None) is not None:
            return candidate
    return None


def apply(record: HealthRecord, history: list[HealthRecord]) -> set[str]:
    """就地更新 record 与历史记录的可疑标记，返回本条被标记的字段。"""
    suspicious, cleared = evaluate(record, history)
    for previous, key in cleared:
        remaining = previous.suspicious() - {key}
        partner = PAIRED_FIELDS.get(key)
        if partner:
            remaining.discard(partner)
        previous.suspicious_fields = ",".join(sorted(remaining))
    record.suspicious_fields = ",".join(sorted(suspicious))
    return suspicious


def reflag(records: list[HealthRecord]) -> dict[str, int]:
    """对一段按时间升序的历史记录从头重新判定，用于给旧数据补标记。"""
    for record in records:
        record.suspicious_fields = ""
    for index, record in enumerate(records):
        window_start = record.recorded_at - timedelta(minutes=BASELINE_MINUTES)
        start = index
        while start > 0 and records[start - 1].recorded_at >= window_start:
            start -= 1
        apply(record, records[start:index])
    # 后来的读数可能撤销了前面的标记，所以要等整趟跑完再数
    counts: dict[str, int] = {}
    for record in records:
        for key in record.suspicious():
            counts[key] = counts.get(key, 0) + 1
    return counts
