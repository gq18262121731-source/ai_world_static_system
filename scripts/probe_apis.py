"""逐项探测 .env 里配置的外部 API 是否真的可用。

会发起真实调用（用量极小），用来确认密钥、网络与模型名是否正确。

用法: python scripts/probe_apis.py [--env .env]
"""

import argparse
import base64
import json
import sys
import time
import wave
from io import BytesIO
from pathlib import Path

import httpx

DASH_NATIVE = "https://dashscope.aliyuncs.com/api/v1"

# env 里配的是 WebSocket 实时模型，HTTP 多模态接口上实测可用的是这个
ASR_HTTP_MODEL = "qwen3-asr-flash"


def load_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class Probe:
    def __init__(self) -> None:
        self.rows = []

    def record(self, name, ok, detail, elapsed=None, note=""):
        self.rows.append(
            {"name": name, "ok": ok, "detail": detail, "ms": elapsed, "note": note}
        )
        flag = "可用" if ok else "不可用"
        timing = f"{elapsed}ms" if elapsed is not None else "--"
        print(f"[{flag:^4}] {name:<22} {timing:>8}  {detail}")
        if note:
            print(f"{'':>36}{note}")

    def run(self, name, fn, note=""):
        started = time.perf_counter()
        try:
            detail = fn()
            self.record(name, True, detail, int((time.perf_counter() - started) * 1000), note)
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:160].replace("\n", " ")
            self.record(
                name, False, f"HTTP {exc.response.status_code} {body}",
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            self.record(
                name, False, f"{type(exc).__name__}: {str(exc)[:150]}",
                int((time.perf_counter() - started) * 1000),
            )


def silent_wav() -> bytes:
    """生成一段 1 秒的静音 WAV，只用于验证接口是否接受音频入参。"""
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env")
    args = parser.parse_args()

    env = load_env(Path(args.env))
    if not env:
        print(f"没有找到 {args.env}")
        return 1

    key = env.get("DASHSCOPE_API_KEY") or env.get("QWEN_API_KEY", "")
    base = env.get("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    tavily = env.get("TAVILY_API_KEY", "")
    ollama = env.get("OLLAMA_BASE_URL", "")

    print("=" * 86)
    print("外部 API 可用性探测")
    print("=" * 86)
    print(f"DashScope 密钥: {key[:8]}…{key[-4:]}" if key else "DashScope 密钥: 未配置")
    print(f"Tavily 密钥:    {tavily[:10]}…" if tavily else "Tavily 密钥:    未配置")
    print("-" * 86)

    probe = Probe()
    auth = {"Authorization": f"Bearer {key}"}
    client = httpx.Client(timeout=45)

    # ---------- 1. 对话大模型 ----------
    def chat():
        resp = client.post(
            f"{base}/chat/completions",
            headers=auth,
            json={
                "model": env.get("QWEN_MODEL", "qwen-plus"),
                "messages": [{"role": "user", "content": "用一句话说明你能做什么"}],
                "max_tokens": 60,
                # 思考模式会把耗时从 0.7 秒拉到 13 秒以上，语音场景必须关掉
                "enable_thinking": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        return f"{env.get('QWEN_MODEL')} → {text[:44]}… (tokens {usage.get('total_tokens', '?')})"

    probe.run("对话大模型 LLM", chat, "已关闭思考模式；开启时同一请求要 13 秒以上")

    # ---------- 2. 文本向量 ----------
    def embedding():
        resp = client.post(
            f"{base}/embeddings",
            headers=auth,
            json={
                "model": env.get("QWEN_EMBEDDING_MODEL", "text-embedding-v3"),
                "input": "老人今天血压偏高能出门吗",
            },
        )
        resp.raise_for_status()
        vector = resp.json()["data"][0]["embedding"]
        return f"{env.get('QWEN_EMBEDDING_MODEL')} → {len(vector)} 维向量"

    probe.run("文本向量 Embedding", embedding, "可用于健康知识库检索")

    # ---------- 3. 重排序 ----------
    def rerank():
        resp = client.post(
            env.get("QWEN_RERANK_API", f"{DASH_NATIVE}/services/rerank/text-rerank/text-rerank"),
            headers=auth,
            json={
                "model": env.get("QWEN_RERANK_MODEL", "gte-rerank-v2"),
                "input": {
                    "query": "高血压老人能不能外出散步",
                    "documents": [
                        "高血压患者应避免剧烈运动，散步是合适的选择",
                        "机器狗的电池续航为 4 小时",
                        "血压超过 160 时不建议外出",
                    ],
                },
                "parameters": {"top_n": 2, "return_documents": False},
            },
        )
        resp.raise_for_status()
        results = resp.json()["output"]["results"]
        top = results[0]
        return f"{env.get('QWEN_RERANK_MODEL')} → 命中第 {top['index']} 条 (score {top['relevance_score']:.3f})"

    probe.run("检索重排 Rerank", rerank, "可提升知识库问答的准确率")

    # ---------- 4. 语音合成 ----------
    def tts():
        resp = client.post(
            f"{DASH_NATIVE}/services/aigc/multimodal-generation/generation",
            headers=auth,
            json={
                "model": env.get("QWEN_TTS_MODEL", "qwen-tts"),
                "input": {
                    "text": "今天天气不错，可以出去走走。",
                    "voice": env.get("QWEN_TTS_VOICE", "Cherry"),
                },
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        audio = payload.get("output", {}).get("audio", {})
        url = audio.get("url", "")
        if not url:
            raise ValueError(f"未返回音频地址: {json.dumps(payload)[:160]}")
        return f"{env.get('QWEN_TTS_MODEL')} → 音频 {url[:52]}…"

    probe.run("语音合成 TTS", tts, "机器狗播报可换成真实音色")

    # ---------- 5. 语音识别 ----------
    def asr():
        audio_b64 = base64.b64encode(silent_wav()).decode()
        resp = client.post(
            f"{DASH_NATIVE}/services/aigc/multimodal-generation/generation",
            headers=auth,
            json={
                "model": ASR_HTTP_MODEL,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"audio": f"data:audio/wav;base64,{audio_b64}"}],
                        }
                    ]
                },
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if "output" not in payload:
            raise ValueError(f"未返回识别结果: {json.dumps(payload)[:160]}")
        return f"{ASR_HTTP_MODEL} → 接口接受音频并返回结果（送入静音，内容为空属正常）"

    probe.run(
        "语音识别 ASR",
        asr,
        f"env 里的 {env.get('QWEN_ASR_MODEL', '')} 是 WebSocket 实时模型，HTTP 侧用 {ASR_HTTP_MODEL}",
    )

    # ---------- 6. 全模态 ----------
    def omni():
        resp = client.post(
            f"{base}/chat/completions",
            headers=auth,
            json={
                "model": env.get("QWEN_OMNI_MODEL", "qwen2.5-omni-7b"),
                "messages": [{"role": "user", "content": "你好"}],
                "max_tokens": 30,
                "modalities": ["text"],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
        resp.raise_for_status()
        chunks = [line for line in resp.text.splitlines() if line.startswith("data:")]
        return f"{env.get('QWEN_OMNI_MODEL')} → 流式返回 {len(chunks)} 个分片"

    probe.run("全模态 Omni", omni, "支持音频/图像直接输入，可省掉单独的 ASR")

    # ---------- 7. 联网搜索 ----------
    def search():
        resp = client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": tavily,
                "query": "杭州今天天气",
                "max_results": 3,
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return f"Tavily → 返回 {len(results)} 条结果，首条：{results[0]['title'][:36]}…"

    if tavily:
        probe.run("联网搜索 Tavily", search, "可用于天气、生活常识类问答")
    else:
        probe.record("联网搜索 Tavily", False, "未配置密钥", None)

    # ---------- 8. 实时天气 ----------
    # 和风天气的配置在 backend/.env，与本脚本默认读取的 env 文件不是同一份
    weather_env = env if env.get("QWEATHER_API_KEY") else load_env(Path("backend/.env"))
    qw_host = weather_env.get("QWEATHER_API_HOST", "").strip().rstrip("/").split("://")[-1]
    qw_key = weather_env.get("QWEATHER_API_KEY", "").strip()
    # 杭州市中心，与 Elder.home_lat/home_lng 的默认值一致
    qw_point = "30.27/120.15"

    def weather_now():
        resp = client.get(
            f"https://{qw_host}/weather/v1/current/{qw_point}",
            headers={"X-QW-Api-Key": qw_key},
            params={"lang": "zh-hans"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return (
            f"和风天气 → {data['condition']['text']}，"
            f"{round(data['temperature']['value'])}℃，"
            f"{round(data['humidity'] * 100)}% 湿度，{data['wind']['scale']} 级风"
        )

    def weather_air():
        resp = client.get(
            f"https://{qw_host}/airquality/v1/current/{qw_point}",
            headers={"X-QW-Api-Key": qw_key},
            params={"lang": "zh-hans"},
            timeout=10,
        )
        resp.raise_for_status()
        indexes = {i["code"]: i for i in resp.json().get("indexes", [])}
        index = indexes.get("cn-mee") or next(iter(indexes.values()))
        return f"和风天气 → {index['name']} {index['aqiDisplay']}（{index.get('category', '')}）"

    if qw_host and qw_key:
        probe.run("实时天气 QWeather", weather_now, "外出评估的天气事实来源")
        probe.run("空气质量 QWeather", weather_air, "对应规则引擎里的 AQI 判定")
    else:
        missing = "API Host" if qw_key else "API KEY"
        probe.record("实时天气 QWeather", False, f"backend/.env 缺少 {missing}", None)

    # ---------- 9. 本地大模型 ----------
    def local_llm():
        resp = client.get(f"{ollama}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return f"Ollama 在线，本地模型：{', '.join(models) or '无'}"

    if ollama:
        probe.run("本地大模型 Ollama", local_llm, "离线兜底，需本机已安装并启动")

    client.close()

    print("-" * 86)
    usable = [row for row in probe.rows if row["ok"]]
    print(f"探测完成：{len(usable)}/{len(probe.rows)} 项可用")
    for row in probe.rows:
        if not row["ok"]:
            print(f"  不可用 · {row['name']}：{row['detail'][:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
