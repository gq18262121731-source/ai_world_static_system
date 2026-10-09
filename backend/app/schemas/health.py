from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthRecordIn(BaseModel):
    elder_id: int | None = None
    device_id: str = ""
    recorded_at: datetime | None = None
    heart_rate: int | None = None
    systolic: int | None = None
    diastolic: int | None = None
    spo2: int | None = None
    temperature: float | None = None
    steps: int | None = None
    sleep_hours: float | None = None
    activity_level: str = "rest"
    battery: int | None = None
    lat: float | None = None
    lng: float | None = None
    source: str = "watch"


class HealthRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    elder_id: int
    device_id: str
    recorded_at: datetime
    heart_rate: int | None
    systolic: int | None
    diastolic: int | None
    spo2: int | None
    temperature: float | None
    steps: int | None
    sleep_hours: float | None
    activity_level: str
    battery: int | None
    # 被判为孤立坏读数的字段；原值仍返回，由前端灰显或剔除
    suspicious_fields: list[str] = Field(default_factory=list)

    @field_validator("suspicious_fields", mode="before")
    @classmethod
    def _split_fields(cls, value):
        if isinstance(value, str):
            return [item for item in value.split(",") if item]
        return value or []


class HealthTrendPoint(BaseModel):
    """一个时间桶里的可信读数汇总。桶内只有一条读数时 avg/min/max 相等。

    t_min / t_max 是最低、最高读数各自出现的时刻：前端按时间顺序把两者连成线，
    桶再宽也不会把一次 157 的心率飙升平均成 90。
    """

    t: datetime
    avg: float
    min: float
    max: float
    n: int
    t_min: datetime
    t_max: datetime


class HealthTrendOut(BaseModel):
    metric: str
    label: str
    unit: str
    hours: int
    bucket_seconds: int
    points: list[HealthTrendPoint] = Field(default_factory=list)
    # 窗口内该项的原始可信读数条数, 以及因疑似坏读数被剔除的条数
    total: int = 0
    dropped: int = 0
    latest: float | None = None
    latest_at: datetime | None = None


class HealthSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary_date: date
    hr_avg: float | None
    hr_min: int | None
    hr_max: int | None
    spo2_avg: float | None
    spo2_min: int | None
    systolic_avg: float | None
    diastolic_avg: float | None
    temperature_avg: float | None
    steps_total: int | None
    sleep_hours: float | None
    anomaly_count: int
    risk_score: float
    health_score: float | None = None
    health_grade: str | None = None
    health_confidence: float | None = None
    resting_hr: float | None = None
    spo2_median: float | None = None


class HealthScoreDimension(BaseModel):
    key: str
    label: str
    weight: int
    # None 表示该维度缺数据，不参与加权
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)


class HealthScoreOut(BaseModel):
    """0~100 的正向健康评分，越高越好。"""

    score: int | None = None
    grade: str = ""  # 优 / 良 / 中 / 差
    # 参与评分的维度数，如 "4/5"
    coverage: str = ""
    dimensions: list[HealthScoreDimension] = Field(default_factory=list)
    # 最多三条、按对总分影响排序的扣分原因
    deductions: list[str] = Field(default_factory=list)
    delta_vs_yesterday: int | None = None
    # 覆盖度 × 数据新鲜度，低于 0.6 时只作参考
    confidence: float = 1.0
    is_reference: bool = False
    # 触发 NEWS2 临床响应阈值而被压低总分时，说明原因
    cap_reason: str = ""
    # 给值守人员看的精确说法
    summary: str = ""
    # 给老人和家属看的温和说法
    friendly_text: str = ""
    computed_at: datetime | None = None


class VitalStat(BaseModel):
    latest: float | None = None
    avg: float | None = None
    min: float | None = None
    max: float | None = None
    unit: str = ""
    status: str = "normal"  # normal / warning / danger / unknown
    label: str = ""
    # 当前值本身正常、但因近期出现过异常读数而被提级时, 在这里说明原因
    note: str = ""
    # 最近一小时内出现过的最极端读数（已恢复正常时才有），供智能体准确复述"曾到多少"
    recent_extreme: float | None = None


class LiveSeriesPoint(BaseModel):
    ts: datetime
    heart_rate: int | None = None
    spo2: int | None = None
    systolic: int | None = None


class LiveElderVitals(BaseModel):
    """首页实时体征卡片所需的最小数据集。"""

    elder_id: int
    elder_name: str
    latest_at: datetime | None = None
    seconds_since_update: int | None = None
    # 超过阈值未更新视为数据陈旧, 前端会灰显提示
    is_stale: bool = True
    vitals: dict[str, VitalStat] = Field(default_factory=dict)
    steps_today: int | None = None
    watch_battery: int | None = None
    watch_online: bool = False
    dog_online: bool = False
    dog_following: bool = False
    active_task_id: int | None = None
    # normal / warning / danger / unknown
    risk_level: str = "unknown"
    # 面向展示的一组简短状态标签，如“疲劳缺觉、血氧偏低”。
    body_statuses: list[str] = Field(default_factory=list)
    abnormal_items: list[str] = Field(default_factory=list)
    series: list[LiveSeriesPoint] = Field(default_factory=list)
    # 当日健康评分（来自日摘要，随每次上报刷新）
    health_score: int | None = None
    health_grade: str = ""
    score_delta: int | None = None
    # 数据不全时分数只作参考，界面上灰显
    score_reference: bool = False


class HealthAssessment(BaseModel):
    """外出评估与语音问答共用的健康快照。"""

    elder_id: int
    has_data: bool = False
    window_hours: int = 24
    latest_at: datetime | None = None
    vitals: dict[str, VitalStat] = Field(default_factory=dict)
    abnormal_items: list[str] = Field(default_factory=list)
    trend_notes: list[str] = Field(default_factory=list)
    sleep_hours_last_night: float | None = None
    steps_today: int | None = None
    risk_score: float = 0.0
    watch_battery: int | None = None
    summary_text: str = ""
    health_score: HealthScoreOut | None = None
