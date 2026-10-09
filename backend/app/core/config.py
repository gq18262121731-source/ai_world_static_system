from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "基于AIoT的居家养老健康平台"
    debug: bool = True
    api_prefix: str = "/api/v1"
    secret_key: str = "dev-secret-key-change-me"
    access_token_expire_minutes: int = 720
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    database_url: str = "sqlite+aiosqlite:///./aiot_elder.db"
    redis_url: str = "redis://localhost:6379/0"

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_client_id: str = "aiot-backend"
    mqtt_topic_prefix: str = "aiot"

    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"

    asr_provider: str = "mock"
    asr_api_key: str = ""
    asr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    asr_model: str = "paraformer-realtime-v2"

    tts_provider: str = "mock"
    tts_api_key: str = ""
    tts_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    tts_model: str = "cosyvoice-v1"
    # 百炼 qwen-tts 系列的音色名，Cherry 是温和的女声
    tts_voice: str = "Cherry"

    # 讯飞开放平台：超拟人合成（机器狗音色）与语音听写（流式识别）共用一套凭据
    xfyun_app_id: str = ""
    xfyun_api_key: str = ""
    xfyun_api_secret: str = ""
    xfyun_tts_vcn: str = "x6_lingxiaoxuan_pro"
    xfyun_tts_speed: int = 50

    # mock / qweather / open-meteo
    weather_provider: str = "mock"
    weather_api_key: str = ""
    weather_cache_seconds: int = 600
    # 和风天气: API Host 与 API KEY 在控制台-设置/项目管理中获取
    qweather_api_host: str = ""
    qweather_api_key: str = ""
    # 空气质量是独立接口, 不需要 AQI 时可关掉以省调用量
    qweather_air_quality: bool = True
    default_city: str = "杭州"

    scheduler_interval_seconds: int = 15
    device_offline_seconds: int = 90

    media_dir: Path = BASE_DIR / "media"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
