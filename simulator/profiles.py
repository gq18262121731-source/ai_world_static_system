"""健康数据档位, 与后端 app/services/simulator_service.py 保持一致。"""

import random

HEALTH_PROFILES: dict[str, dict] = {
    "normal": {
        "label": "健康平稳",
        "heart_rate": (68, 82),
        "systolic": (118, 132),
        "diastolic": (72, 84),
        "spo2": (96, 99),
        "temperature": (36.3, 36.9),
        "sleep_hours": (6.5, 8.0),
    },
    "tired": {
        "label": "疲劳(睡眠不足)",
        "heart_rate": (78, 92),
        "systolic": (128, 140),
        "diastolic": (80, 90),
        "spo2": (94, 96),
        "temperature": (36.4, 37.0),
        "sleep_hours": (3.5, 5.0),
    },
    "hypoxia": {
        "label": "血氧偏低",
        "heart_rate": (88, 102),
        "systolic": (124, 138),
        "diastolic": (78, 88),
        "spo2": (85, 89),
        "temperature": (36.4, 36.9),
        "sleep_hours": (5.0, 7.0),
    },
    "hypertension": {
        "label": "血压偏高",
        "heart_rate": (76, 90),
        "systolic": (168, 182),
        "diastolic": (104, 112),
        "spo2": (95, 98),
        "temperature": (36.3, 36.8),
        "sleep_hours": (5.0, 7.0),
    },
    "tachycardia": {
        "label": "心率过快",
        "heart_rate": (124, 142),
        "systolic": (138, 150),
        "diastolic": (86, 96),
        "spo2": (93, 96),
        "temperature": (36.6, 37.2),
        "sleep_hours": (5.0, 7.0),
    },
    "fever": {
        "label": "发热",
        "heart_rate": (95, 110),
        "systolic": (126, 140),
        "diastolic": (80, 90),
        "spo2": (94, 97),
        "temperature": (38.2, 38.9),
        "sleep_hours": (4.5, 6.5),
    },
    "hypertension_tachycardia": {
        "label": "血压偏高、心率过快",
        "heart_rate": (126, 140),
        "systolic": (168, 182),
        "diastolic": (106, 112),
        "spo2": (95, 98),
        "temperature": (36.4, 36.9),
        "sleep_hours": (6.0, 7.5),
    },
    "tired_hypoxia_hypertension": {
        "label": "疲劳缺觉、血氧偏低、血压偏高",
        "heart_rate": (82, 96),
        "systolic": (168, 182),
        "diastolic": (106, 112),
        "spo2": (85, 89),
        "temperature": (36.4, 36.9),
        "sleep_hours": (3.5, 4.8),
    },
    "fever_hypoxia_hypertension": {
        "label": "发热、血氧偏低、血压偏高",
        "heart_rate": (88, 98),
        "systolic": (168, 182),
        "diastolic": (106, 112),
        "spo2": (85, 89),
        "temperature": (38.2, 38.9),
        "sleep_hours": (6.0, 7.5),
    },
}


def generate_vitals(profile_key: str) -> dict:
    profile = HEALTH_PROFILES.get(profile_key, HEALTH_PROFILES["normal"])
    return {
        "heart_rate": round(random.uniform(*profile["heart_rate"])),
        "systolic": round(random.uniform(*profile["systolic"])),
        "diastolic": round(random.uniform(*profile["diastolic"])),
        "spo2": round(random.uniform(*profile["spo2"])),
        "temperature": round(random.uniform(*profile["temperature"]), 1),
        "sleep_hours": round(random.uniform(*profile["sleep_hours"]), 1),
    }
