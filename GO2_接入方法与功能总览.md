# Unitree Go2 EDU 接入方法、功能清单与注意事项总览

整理日期：2026-09-06
整理依据：本仓库代码与文档的实际内容（`go2_dev/go2-gateway`、`go2_dev/unitree_webrtc_connect`、`go2_dev/unitree_sdk2_python`、`health_new_p04`、`DEPLOYMENT.md` 及各阶段报告）。
证据标注：文中"已真机验证"指仓库内有对应报告或日志证据；"软件就绪"指有代码和 Mock 测试但没有真机证据；"未实现"指仅有接口契约或明文禁止。

---

## 1. 设备基本信息

| 项目 | 值 | 来源 |
| --- | --- | --- |
| 型号 | Unitree Go2 EDU，硬件版本 V2.0 | `go2-gateway/GO2_EDU_REFERENCE.md` |
| 序列号 | `B42N6000Q3PABHGC` | 同上 |
| 机器人软件版本 | V1.1.14 | 同上 |
| 手机 App | Unitree Go v1.12.7c | 同上 |
| 项目内设备 ID | `go2-edu-001`（主系统侧 `go2_edu_01`，名称"小康01"） | `.env.example` / `DEPLOYMENT.md` |
| 有线 IP | `192.168.123.161`（SDK 默认） | `app/config.py` |
| PC 有线网卡地址 | `192.168.123.99/24` 或 `192.168.123.222/24` | 报告记录 |
| 无线 STA IP | `192.168.8.252`（历史上还用过 `192.168.8.235`） | `docs/GO2_COMPETITION_MODE.md` |
| WebRTC 信令端口 | TCP 9991 | `DEPLOYMENT.md` |
| 外设 | Unitree L1 LiDAR、UWB 标签 | Phase 5/7 报告 |
| 有线 SDK | `unitree_sdk2py 1.0.1` + `cyclonedds 0.10.2`，commit `37116c5` | `HARDWARE_ENVIRONMENT_REPORT_PHASE_5_1_0.md` |
| 无线库 | `unitree_webrtc_connect 2.1.2` + `aiortc 1.15.0`，Python 3.12 | `WEBRTC_WIRELESS_MOTION_GATE_20260826.md` |

---

## 2. 系统拓扑与端口

```text
浏览器 / Flutter 移动端
    │ HTTP / WebSocket
    ▼
health_new_p04 智慧康养主系统 (8000)  ── Vue 前端 (5173)、Redis (6379)
    ├── go2-gateway HTTP 网关 (8090)          ── 链路 A：有线 DDS / SDK2
    └── Go2 统一无线 Runtime (8093)           ── 链路 B：WebRTC（视频/语音/UWB/运动）
         └── FFmpeg → MediaMTX RTSP (8554) → camera-service 视觉服务 (8011)
```

| 端口 | 服务 | 暴露范围 |
| --- | --- | --- |
| 5173 | Vue/Vite 前端 | 本机或可信局域网 |
| 8000 | 主系统 FastAPI | 本机或可信局域网 |
| 8090 | Go2 HTTP 网关 | 主系统所在可信网段 |
| 8093 | 无线 Runtime / MJPEG | 本机；跨机时仅可信网段 |
| 8554 | MediaMTX RTSP | 桥接电脑与视觉服务器之间 |
| 9991 | Go2 WebRTC 信令 | 桥接电脑到 Go2 |
| 8766/UDP | UWB follow target 转发（可选） | 指定接收主机 |
| 6379 / 5432 / 8001 / 11434 | Redis / PostgreSQL / ChromaDB / Ollama | 仅本机或容器网络 |

---

## 3. 三条接入链路

### 3.1 链路 A：有线 DDS（`unitree_sdk2_python`）

- 代码：`go2_dev/go2-gateway/app/adapters/unitree_adapter.py`
- 运行环境：Ubuntu 20.04（项目实际用 WSL2）+ Python ≥ 3.8 + CycloneDDS 0.10.x
- 初始化：`ChannelFactoryInitialize(domain_id, 网卡名)` → `SportClient` + `VideoClient`
- 订阅话题：`rt/lf/sportmodestate`、`rt/lf/lowstate`、`rt/utlidar/lidar_state`
- 使用的 SDK 方法：`StandUp` / `StandDown` / `Sit` / `StopMove` / `Move(vx,vy,wz)` / `SwitchJoystick` / `GetImageSample`
- 特殊处理：适配器会正则重写 SDK 内置 CycloneDDS 配置里的 `<Peer Address>` 为配置的机器人 IP（支持非默认 IP）
- 状态：真机验证通过（状态、相机、站立/趴下、闭环单动作）
- 限制：**无线环境下 SDK2 DDS 四种配置组合均收不到状态样本，无线 DDS 路线已冻结**，有线仅作开发、标定和兜底

### 3.2 链路 B：无线 WebRTC（`unitree_webrtc_connect`）

- 代码：`go2_dev/go2-gateway/app/webrtc/go2_wireless_runtime.py`、`tools/go2_wireless_runtime.py`
- 运行环境：Windows 10/11 + Python 3.12（固定路径 `go2_dev/unitree_webrtc_connect/.venv312`）
- 协议：与手机 App 同一套 WebRTC 协议；固件 ≥ 1.1.15 需要每台设备独立的 AES-128 密钥
- 密钥存放：`go2_dev/go2-wireless-camera/wireless_collector/.go2_aes_key.dpapi`，用 Windows DPAPI 加密，**只能由目标电脑上实际运行 Runtime 的 Windows 用户生成**（`setup_wireless.ps1`）
- 单进程只维护一个 PeerConnection，同时承载：
  - DataChannel：`rt/api/sport/request`（Move / StopMove / BalanceStand / Euler / BodyHeight）、`rt/api/audiohub/request`（音频上传与播放）
  - 视频轨：解码后转 MJPEG 供 8093 端口
  - 音频轨：麦克风 PCM 采集
  - 订阅：`rt/lf/sportmodestate`、`rt/uwbstate`、`rt/lf/lowstate`、`rt/multiplestate`
- 状态：真机验证通过（只读连接、Stop/零速度、0.23 m/s × 0.4 s 前进脉冲、姿态、扬声器、麦克风）
- 限制：`WebRTCMotionBackend` **不支持** StandUp / StandDown / Sit（返回 `TASK_NOT_SUPPORTED`）；不提供电量读取

### 3.3 链路 C：ROS 2 / LiDAR / SLAM

- 环境：Ubuntu 22.04 + ROS 2 Humble（虚拟机必须桥接网卡并验证组播）
- 内容：TF 坐标链、L1 点云、IMU 语义、Point-LIO 离线验证（`phase5*`、`phase6*` 目录）
- 状态：全部为只读实验，产物是 bag、截图和报告，**未接入网关运动控制**
- 注意：不应与比赛无线 Runtime 共用同一个 Go2 WebRTC 会话

---

## 4. 部署与启动步骤

### 4.1 部署模式选择（必须按顺序推进）

| 模式 | 用途 | 关键配置 |
| --- | --- | --- |
| A 无机器狗开发 | 前后端联调 | `GO2_MODE=mock`，主系统 `USE_MOCK_DATA=true` |
| B 只读联调 | 验证状态、相机、DDS/WebRTC、UWB | `GO2_CONTROL_ENABLED=false`、`GO2_READ_ONLY_MODE=true` |
| C 受控真实运动 | 现场验收 | 分阶段开启 `GO2_CONTROL_ENABLED` → `FOLLOW_EXECUTION_ENABLED` → `PHASE7_MOTION_EXECUTION_ENABLED` |
| D ROS 2 / SLAM | 传感器验证 | 独立 Ubuntu 22.04 主机 |

### 4.2 主系统 `health_new_p04`

```powershell
Set-Location .\health_new_p04
conda create -n helth python=3.11 -y
conda run -n helth python -m pip install -r requirements.txt
Copy-Item .env.example .env          # 填 DASHSCOPE_API_KEY、JWT_SECRET 等
docker compose -f .\docker\docker-compose.yml up -d redis
powershell -ExecutionPolicy Bypass -File .\scripts\start_server.ps1 -CondaEnv helth -ListenHost 0.0.0.0 -Port 8000
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1 -ListenHost 127.0.0.1 -Port 5173
```

验证：`Invoke-RestMethod http://127.0.0.1:8000/healthz` 应返回 `"status": "ok"`。

### 4.3 有线网关 8090（Ubuntu / WSL2）

```bash
sudo ip addr add 192.168.123.99/24 dev <Go2专用网卡>
cd go2_dev/go2-gateway && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ../unitree_sdk2_python && pip install -e . && cd ../go2-gateway
cp .env.example .env                 # GO2_MODE=real，UNITREE_NETWORK_INTERFACE=<网卡名>
GO2_MODE=real python scripts/check_environment.py --strict-real
GO2_MODE=real GO2_CONTROL_ENABLED=false GO2_READ_ONLY_MODE=true \
  uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

只读验收：`/health`、`/api/connection`、`/api/robot/diagnostics/dds`、`/api/status`，以及 `python scripts/verify_preflight.py --base-url http://127.0.0.1:8090 --allow-readonly`。

### 4.4 无线统一 Runtime 8093（Windows）

```powershell
py -3.12 -m venv .\go2_dev\unitree_webrtc_connect\.venv312
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe -m pip install -e .\go2_dev\unitree_webrtc_connect
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe -m pip install -r .\go2_dev\go2-gateway\requirements.txt

Set-Location .\go2_dev\go2-wireless-camera\wireless_collector
.\setup_wireless.ps1                 # 在目标电脑生成 DPAPI 密钥

Test-NetConnection <GO2_IP> -Port 9991
Set-Location ..\..\go2-gateway
.\scripts\Start-Go2WirelessRuntime.ps1 -RobotIp <GO2_IP> -ListenHost 127.0.0.1 `
  -HealthNewUrl http://127.0.0.1:8000 -ElderId <ELDER_ID> -NoOpenBrowser
```

验收：`http://127.0.0.1:8093/status` 中 `connected=true`、`hasFrame=true`、`frameCount` 5 秒内增长、`frameAgeMs` < 1000。

Runtime 控制台命令：

| 命令 | 作用 |
| --- | --- |
| `STATUS` | 连接/状态/视频计数 |
| `UWB_GATE` | 15 s 只读 UWB 观察 |
| `MIC_GATE` | 录 5 s 麦克风 WAV，不发运动命令 |
| `VOICE_INTENT_GATE` | 一句话 ASR + 意图，只看不动 |
| `VOICE_CONTROL` | 一句话 ASR → 白名单意图 → Lifecycle 执行 |
| `GATE` | 视频 + 0.20 m 前进 + Stop 联合门禁 |
| `POSE_GATE` / `AUDIO_GATE` | 单独验证姿态 / 扬声器 |
| `START_DEMO` | 运行 `phone_demo.yaml` 完整序列 |
| `START` / `STOP` / `RESUME` | 伴随会话生命周期 |
| `FOLLOW_3MIN` | 180 s 受监督 UWB 跟随 |
| `MANUAL` | 人工接管（WASD/QE 键盘） |
| `NO_RESPONSE` / `RESET_DEMO` | 紧急流程记录 / 演示重置 |
| `EXIT` | StopMove + 关闭 HTTP + 断开 WebRTC（**必须用它退出**） |

### 4.5 推荐启动顺序

1. 检查电源、网络、场地、急停、Go2 电量
2. 启动 Redis
3. 启动主系统后端，确认 `/healthz`
4. 启动 Vue 前端
5. 以 Mock 或只读模式启动 8090 网关，确认 `/api/preflight`
6. 启动 8093 Runtime，确认帧序号持续增长
7. 如需视觉链，启动 MediaMTX、FFmpeg、camera-service
8. 验证主系统能看到机器人、视频和视觉服务的真实状态
9. 完成 30 分钟只读稳定性测试
10. 现场负责人授权后，分阶段开启真实运动

推荐停止顺序：停止新任务派发 → 急停并确认静止 → `EXIT` 退出 8093 → 停 8090 → 停视觉链和前端 → 停主系统后端。

---

## 5. 已具备的接入功能清单

### 5.1 状态读取（已真机验证）

- 在线判断：必须收到新鲜 `SportModeState` 或 `LowState`，ping 通不算在线
- 运动模式、步态类型、速度 vx/vy/yawSpeed、机身高度
- IMU 姿态 roll/pitch/yaw；位置 x/y（用于闭环）
- 电量 SOC、电压、电流（仅有线 LowState）
- LiDAR 状态：点云频率、丢包率、固件版本、IMU 频率
- 接口：`GET /api/status`、`/api/robot/status`、`/api/connection`、`/api/readiness`、`/api/robot/diagnostics/dds`、`/api/lidar/status`

### 5.2 基础运动（有线已真机验证）

| 接口 | 说明 |
| --- | --- |
| `POST /api/robot/stand` | 站立 |
| `POST /api/robot/lie-down` | 趴下（先 StopMove） |
| `POST /api/robot/sit` | 坐下（先 StopMove） |
| `POST /api/robot/stop` | 停止 |
| `POST /api/robot/emergency-stop` | 急停 |
| `POST /api/robot/move` | 短时低速移动，`{"vx","vy","wz","duration"}`，结束自动 StopMove |

默认限幅：`GO2_MAX_VX=0.30 m/s`、`GO2_MAX_VY=0.0`、`GO2_MAX_WZ=0.30 rad/s`、`GO2_MAX_MOVE_DURATION=1.0 s`、`GO2_MIN_MOVE_DURATION=0.05 s`。

键盘手动控制（`app/motion/manual_control.py`）：前进 0.35、后退 0.30、横移 0.20 m/s、转向 0.55 rad/s，5 Hz 刷新，0.30–0.50 s 死区自动停车。

### 5.3 闭环脚本动作编排（有线单动作已真机验证）

- 代码：`app/motion/scripted_motion.py`，配置 `configs/scripted_motion.yaml`、`configs/phone_demo.yaml`
- 支持动作：forward / backward / move_left / move_right / turn_clockwise / turn_counterclockwise / wait / pose / speak
- 闭环依据：`SportModeState.position` 做平移、`imu.rpy[2]` 做旋转，5 Hz 刷新，禁用纯时间估算
- 已验证精度：前进 0.2/0.8 m、后退 0.1 m、横移 0.2 m、左转 30°/90°，误差 ≤ 2.5 cm / 2°
- 有效参数：前进 0.30 m/s、后退 0.18 m/s、横移 0.30 m/s、旋转 0.60 rad/s；近目标 0.23 m/s、0.50 rad/s
- 完整 `phone_demo` 连续序列截至报告日尚未整段真机验收

### 5.4 无线 WebRTC 运动与姿态（已真机通过三级 Gate）

- Move / StopMove 通过 DataChannel 发送，`api_id` 来自 `unitree_webrtc_connect` 的 `SPORT_CMD`
- 姿态：`StopMove → 静置 0.25 s → BalanceStand → Euler + BodyHeight → 保持 → 归零 → StopMove`
- 机身高度硬限 `[-0.18, +0.03] m`，角度 YAML 用度、后端转弧度
- 重连后运动锁定，直到 StopMove 被确认

### 5.5 视频

| 方式 | 接口 |
| --- | --- |
| 有线 JPEG 快照 | `GET /api/camera/snapshot`、`/api/robot/camera/snapshot` |
| 有线 MJPEG 流 | `GET /api/camera/stream`，间隔 `GO2_CAMERA_STREAM_INTERVAL_SECONDS=0.5` |
| 无线 MJPEG | `http://<A电脑IP>:8093/stream.mjpg`、`/snapshot`、`/status` |
| 视频边缘网关契约 | `GET /healthz`、`/api/v1/video/status`、`/api/v1/robot/video`，mDNS `_robot-video._tcp.local` / `robot-gateway.local` |
| RTSP 转发 | Go2 8093 → FFmpeg H.264 → MediaMTX `rtsp://<SERVER>:8554/go2_front` → camera-service |

### 5.6 语音对话与语音控制（WebRTC，软件完整，Gate 文档声称通过）

管线：

```text
Go2 麦克风 → WebRTC 音轨 (PCM16) → 本地能量 VAD (300 ms 尾静音)
  → 降采样 16 kHz 单声道 → health_new /api/v1/voice/asr (DashScope qwen3-asr-flash)
  → 本地固定短语路由（命中即返回意图，不走 LLM）
  → 未命中 → health_new /api/v1/go2-companion/text-turn (Qwen，JSON 输出 reply + intent)
  → VoiceIntentAdapter 授权（仅 6 个高层意图，LLM 永不产生速度）
  → 控制意图播放预录 WAV；普通对话走 /api/v1/voice/tts (qwen3-tts-flash) 生成 WAV 并本地缓存
  → WebRTC AudioHub：WAV → base64 → 4 KB 分块上传 → play_by_uuid 播放
```

- 唤醒词：小康（容忍同音：小仓/小汤/晓康/小刚），须与指令在同一句
- 白名单意图：`START_COMPANION` / `STOP_COMPANION` / `RESUME_COMPANION` / `REQUEST_HELP` / `CALL_FAMILY` / `I_AM_OK`
- 预录语音：`data/voice/presets/current/*.wav` 共 17 段，启动时批量预加载到机器狗
- 紧急语音检查：确认跌倒后播放 `VOICE_CHECK.wav`，最多录两次回应，两次无效进入 `ESCALATED_EMERGENCY`
- 相关环境变量：`GO2_VOICE_CAPTURE_SECONDS=8.0`、`GO2_VOICE_VAD_TRAILING_SILENCE_SECONDS=0.3`、`GO2_WEBRTC_ENABLE_AUDIO`
- Go2 有线 SDK2 **没有**麦克风/TTS 接口（`AudioClient.TtsMaker` 仅 G1/A2 有，Go2 只有 `VuiClient` 音量/LED），因此语音只能走 WebRTC

### 5.7 UWB 伴随跟随（软件闭环完成，真机只读验证，真实连续跟随受限批准）

- 状态机：`IDLE → FOLLOWING → PERSON_STOPPED / VIEW_ADJUST / HOLD / TARGET_LOST / SAFE_STOP / OBSTACLE_STOP / FALL_SUSPECTED / EMERGENCY_STOP / VOICE_CHECK / MONITORING / WAIT_RESUME / MANUAL_CONTROL`
- HTTP 接口：`GET /api/v1/robot/companion/status`、`POST .../start`、`.../stop`、`.../resume`（不能提交原始速度）
- UWB 标定：bearing 源 `orientation_est`，弧度，左正右负，零点偏移 +0.55 rad，约 5 Hz
- 目标位姿：老人右后方，`companion_follow_real.yaml` 中距离 1.35 m、方位 18.4°，启动 1.20 m、停止 1.05 m
- 速度：vx 0.24–0.42 m/s，wz ≤ 0.55 rad/s（demo 配置为 0.30/0.30）
- 最低有效步态速度 0.20 m/s，启停双阈值滞回（0.25 m 启动 / 0.10 m 停止）
- UWB 超时 1–2 s 即停车；恢复必须人工 `RESUME`
- 掉线真机记录：标签断连 22.157 s 期间 LowState 正常，证明是 UWB 断连而非 DDS 中断

### 5.8 LiDAR 前向避障（真机只读验证：功能转换 PASS，绝对距离 HOLD）

- 三态 CLEAR / SLOW / STOP，ROI 前方 0.10–2.00 m、宽 0.90 m、高 -0.25–0.65 m
- 阈值：SLOW 0.70 m、STOP 0.50 m、SLOW 降速比 0.35；连续 3 帧空场才解除 SLOW，STOP 立即锁存
- 点云 `rt/utlidar/cloud_base`，约 15.4 Hz
- 真机测距正偏差 +0.08 到 +0.17 m（拟合 `observed = 1.043 × physical + 0.076`），候选阈值未写入生产配置

### 5.9 主系统任务编排（Mock 全覆盖，真机部分验证）

- 跌倒事件 → `confirm_fall` 任务：`receive_event → moving → arrived → robot_camera → voice_check → finished`
- 入口：`POST /api/events/fall`、`POST /api/tasks/confirm-fall`、`POST /api/tasks/target-move`
- 幂等键：`source_event_id`（及 camelCase 别名）、`external_task_id`
- 中文位置别名：卧室 / 卫生间 / 客厅 / 厨房 → 固定动作计划（`GO2_LOCATION_MOTION_PLANS_JSON`），不是导航
- 任务队列 FIFO、单调 `revision`、审计 JSONL（`logs/task-events.jsonl`）、回调重放、取消、语音结果回写
- 回调：`HEALTH_NEW_CALLBACK_URL` + bearer token，单 worker FIFO 发送
- 能力发现：`GET /api/capabilities`；非运动预检：`GET /api/preflight`；运维摘要：`GET /health`

### 5.10 诊断与验证脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/check_environment.py --strict-real` | SDK、网卡、路由、ping 检查 |
| `scripts/verify_preflight.py` | 对运行中网关做非运动预检 |
| `scripts/verify_real_acceptance.py` | 分级现场验收（可选 `--exercise-camera`、`--allow-motion --dispatch-fall`） |
| `scripts/verify_health_new_contract.py` | Mock 模式离线合同验证 |
| `scripts/verify_release.py` | 编译 + 合同 + 全量 pytest |
| `scripts/verify_gateway_readonly.sh` | 只读模式下所有运动请求应返回 `CONTROL_DISABLED` |
| `tools/dds_diagnostics.py` | 只订阅 SportModeState / LowState 的只读 DDS 诊断 |
| `tools/go2_webrtc_motion_gate.py` | WebRTC 三级运动 Gate |
| `tools/go2_webrtc_stability_ab.py` | WebRTC 四组订阅矩阵稳定性 A/B |
| `tools/go2_motion_demo.py` | 有线脚本动作演示 |

---

## 6. 明确未实现或禁止的能力

- **SLAM、自主导航、建图、巡逻**：`/api/navigation/**` 全部为 Mock，响应固定带 `"provider":"mock","real_motion_enabled":false`
- `/api/tasks/follow`、`/api/tasks/patrol` 返回 `TASK_NOT_SUPPORTED`
- **明文禁止**：跳跃、翻转、倒立、特技动作、低层电机/关节控制
- 机器狗侧真实语音识别未实现，依赖主系统云端 ASR
- `camera-service/` 目录只有接口文档，无可运行代码
- WebRTC 后端不提供电量，也不支持站立 / 趴下 / 坐下
- 自动充电、通用跟随任务未实现

---

## 7. 注意事项与安全规则

### 7.1 总原则

- 第一次部署必须从 Mock 或只读模式开始
- 只有场地清空、机器狗架起或进入受控区、急停可用、操作员明确授权后才允许真实运动
- 禁止一次性打开全部运动开关；顺序为 `GO2_CONTROL_ENABLED` → `FOLLOW_EXECUTION_ENABLED` → `PHASE7_MOTION_EXECUTION_ENABLED`
- 真实运动前先做至少 30 分钟只读稳定性测试
- 不要在无人监护时运行 `verify_motion.py`，也不要绕过它的人工确认
- 真机操作需输入确认口令，例如 `EXCLUSIVE_MOTION_WRITER`、`UNITREE_APP_CLOSED`、`OPEN_AREA_REMOTE_READY`、`PHONE_DEMO_APPROVED`、`POSE_AUDIO_REAL_APPROVED`；启动器不会静默绕过
- 原厂遥控器必须始终在现场操作员手中

### 7.2 连接与并发

- 网关必须 `--workers 1`，**不要**对同一台机器狗启动多个网关实例
- WebRTC 只允许一个 PeerConnection：启动前关闭手机 Unitree App、停止旧 8093 桥、确认 Companion 为 `IDLE`
- 不得同时运行 SDK2 运动工具、Companion Follow、其他 WebRTC 控制器
- 不要在默认 Docker bridge 网络里运行 DDS 网关
- 不要对承载 SSH 或互联网的网卡执行 `ip addr flush`
- WSL2 需 mirrored 网络模式；虚拟机需桥接网卡并验证组播

### 7.3 状态判定

- ping 通 ≠ DDS 就绪；必须收到新鲜 `SportModeState` / `LowState` 且时间戳持续更新
- 状态超过 `GO2_STATE_STALE_SECONDS=2` 秒视为陈旧，拒绝运动
- `/api/readiness` 返回码：403 `CONTROL_DISABLED`、503 `SDK_NOT_INITIALIZED` / `ROBOT_OFFLINE` / `ROBOT_STATE_STALE`、409 `CONTROL_BUSY`
- 断流时主系统必须显示降级，不能把旧的 `NON_FALL` 结果当作当前安全状态

### 7.4 停止与退出

- 每次 Move 后自动 StopMove；watchdog 0.5 s；网关关闭时先取消活动任务再 StopMove
- 8093 Runtime **必须用 `EXIT` 退出**，直接杀进程可能跳过 StopMove 和 WebRTC 清理；`stop_wireless_video.cmd` 故意拒绝强杀
- 网关重启后默认回到未授权 / 停止状态，不恢复上一次跟随会话
- 网络瞬断或进程崩溃时 Stop 可能送不到，遥控器兜底不可省略

### 7.5 运动参数

- 阈值来源：Go2 存在最低有效步态速度，横移 < 0.30 m/s、旋转 < 0.50 rad/s、前进 < 0.20 m/s 只会扭身不成步态
- 不要盲目降低速度追求"平稳"
- Companion/Follow 保持 `vy=0`；scripted motion 硬限 VX 0.30 / VY 0.30 / WZ 0.60
- WebRTC 前进脉冲硬边界：0.20–0.23 m/s、0.20–0.50 s、y=0、yaw=0

### 7.6 密钥与凭证

- `.env`、DPAPI 密钥文件、Token、账号密码不得进入 Git
- 不得将明文 AES Key 写入仓库、文档、聊天或日志
- 不得复制其他电脑 / 其他 Windows 用户生成的 DPAPI 文件
- `JWT_SECRET` 使用随机值；共享演示要改默认账户密码或关闭 `SEED_DEFAULT_ACCOUNTS`
- RTSP 分发布账号和只读账号；MediaMTX API 只监听 `127.0.0.1`
- 不要把 Redis、数据库、Ollama、无鉴权 RTSP、Vite 开发服务暴露到公网

### 7.7 语音链路专项

- 三个云端环节（ASR / LLM / TTS）都依赖阿里云 DashScope，同一个 `DASHSCOPE_API_KEY`（或 `QWEN_API_KEY`），**现场必须有公网**，否则只剩预录 WAV
- ASR 要求 16 kHz；Go2 麦克风为 48 kHz 双声道，Runtime 自动降采样
- AudioHub 上传是 base64 + 4 KB 分块 + 每块 0.1 s，几秒 WAV 需 1–3 s 传输；依靠预加载和哈希缓存降低延迟
- 每次只处理一句话，唤醒词与指令须在同一句；无持续监听
- LLM 只允许输出白名单意图，解析失败降级为 `NONE`；"我没事"只能是 `I_AM_OK`，不会恢复运动
- Qwen 对话每 session 只保留最近 3 轮历史，回复限 2–3 短句、120 token

### 7.8 Git LFS 与版本

- `.tar.gz`、`.npz`、`.exe`、bag、模型等是 LFS 指针（约 130 字节），需 `git lfs install && git lfs pull`
- 不要手工编辑 LFS 指针
- `unitree_sdk2_python` 工作树带一处未提交的 `channel_config.py` 修改（加了 `<Peer Address>` 发现），不能直接用上游 commit 覆盖
- 更新前记录 `git rev-parse HEAD`，备份 `.env`、数据库、模型；不要用 `git reset --hard` 覆盖现场未备份配置

### 7.9 文档与代码不一致处

- `.env.example` 中 `UNITREE_NETWORK_INTERFACE=eth0`，`app/config.py` 默认值为 `enp3s0`，现场必须显式设置
- 主系统 Conda 环境名文档写 `helth`，启动脚本历史默认 `health`，始终显式传 `-CondaEnv`
- `docker-compose.yml` 包含 PostgreSQL / ChromaDB / Ollama，但后端默认仍用 SQLite 和本地 Chroma，不要因为容器存在就假定已切换
- `Start-Go2WirelessRuntime.ps1` 会设置真实模式和控制变量，不是通用只读启动器

---

## 8. 关键配置项速查

### 8.1 `go2_dev/go2-gateway/.env`

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GO2_MODE` | `mock` | `mock` / `real` |
| `UNITREE_ROBOT_IP` | `192.168.123.161` | 机器人 IP（别名 `GO2_ROBOT_IP`） |
| `UNITREE_NETWORK_INTERFACE` | `enp3s0` | Go2 专用网卡（别名 `GO2_NETWORK_INTERFACE`） |
| `UNITREE_DOMAIN_ID` | `0` | DDS Domain |
| `UNITREE_REQUIRE_DDS_STATE` | `true` | 必须收到 DDS 状态才算在线 |
| `GO2_CONTROL_ENABLED` | `true`（示例文件 `false`） | 总控制开关 |
| `GO2_READ_ONLY_MODE` | `false` | 只读保护 |
| `GO2_MAX_VX` / `GO2_MAX_VY` / `GO2_MAX_WZ` | `0.30` / `0.0` / `0.30` | 速度限幅 |
| `GO2_MAX_MOVE_DURATION` | `1.0` | 单次移动上限秒 |
| `GO2_STATE_STALE_SECONDS` | `2.0` | 状态陈旧阈值 |
| `GO2_CONTROL_WATCHDOG_SECONDS` | `0.5` | 运动看门狗 |
| `FOLLOW_SIMULATION` | `true` | 跟随仅软件模拟 |
| `FOLLOW_EXECUTION_ENABLED` | `false` | 跟随真实执行门 |
| `PHASE7_MOTION_EXECUTION_ENABLED` | `false` | Phase 7 仲裁器到机器人路径 |
| `PHASE7_REQUIRE_EXTERNAL_RISK_FEED` | `true` | 需要外部风险心跳 |
| `GO2_COMPANION_CONFIG` | `configs/companion_follow_demo.yaml` | 伴随配置 |
| `UWB_BEARING_SOURCE` / `UNIT` / `SIGN` / `ZERO_OFFSET_RAD` | `orientation_est` / `radians` / `1` / `0.55` | UWB 标定 |
| `GO2_WEBRTC_ENABLE_SPORT_STATE` / `UWB` / `LOW_STATE` / `MULTIPLE_STATE` / `AUDIO` | `false` | WebRTC 订阅开关（按需激活） |
| `GO2_WEBRTC_RECONNECT_*` | 2/2/15/30 s | 重连退避 |
| `GO2_VOICE_CAPTURE_SECONDS` | `8.0` | 语音最长录音 |
| `GO2_VOICE_VAD_TRAILING_SILENCE_SECONDS` | `0.3` | 尾部静音截断 |
| `GO2_VOICE_MODE` / `GO2_VOICE_PROMPT_URL` | `mock` / 空 | 跌倒任务语音桥 |
| `GO2_TASK_AUDIT_LOG_PATH` | `logs/task-events.jsonl` | 任务审计 |
| `HEALTH_NEW_CALLBACK_URL` / `TOKEN` | 空 | 主系统回调 |

### 8.2 `health_new_p04/.env`（与机器狗相关部分）

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `ROBOT_GATEWAY_ENABLED` | `true` | 启用网关对接 |
| `ROBOT_GATEWAY_BASE_URL` | `http://127.0.0.1:8090` | 有线网关 |
| `COMPANION_GATEWAY_BASE_URL` | `http://127.0.0.1:8093` | 无线 Runtime |
| `COMPANION_BOUND_ELDER_ID` / `ROBOT_ID` / `ROBOT_NAME` / `ROBOT_MODEL` | — | 伴随绑定 |
| `DASHSCOPE_API_KEY` / `QWEN_API_KEY` | — | ASR / LLM / TTS 共用 |
| `QWEN_MODEL` | `qwen-plus` | 对话模型 |
| `QWEN_ASR_MODEL` | `qwen3-asr-flash-realtime-2026-02-10` | 语音识别 |
| `QWEN_TTS_MODEL` | `qwen3-tts-flash` | 语音合成 |
| `VISION_SERVICE_BASE_URL` | `http://127.0.0.1:8011` | 视觉服务 |
| `FALL_DETECTION_ENABLED` | `false` | 视觉链验收前保持关闭 |

---

## 9. 常见故障排查

| 现象 | 处理 |
| --- | --- |
| `SDK_NOT_INITIALIZED` | 检查 SDK 安装、CycloneDDS、网卡名、机器狗是否开机 |
| `CONTROL_DISABLED` | `GO2_CONTROL_ENABLED=false`，只读模式拒绝运动 |
| `ROBOT_OFFLINE` / `ROBOT_STATE_STALE` | 未收到或状态陈旧；查 `/api/robot/diagnostics/dds`、网卡、路由、Domain ID |
| 能 ping 但网关离线 | ping 只证明 IP 可达，运行 `check_environment.py --strict-real` 核对 DDS 样本 |
| 8093 无画面 | 关手机 App → `Test-NetConnection <IP> -Port 9991` → DPAPI 是否当前用户生成 → `.venv312` 依赖 → 8093 是否被占 → `/status` 里 `lastErrorCode` → IP 是否变了 |
| `INVALID_MOTION_PARAMETER` | 速度或时长超限 |
| `TASK_STATE_CONFLICT` | 向非 `confirm_fall` 或已终态任务写语音结果 |
| `CAMERA_DECODE_FAILED` | 相机返回非 JPEG |
| LFS 文件只有指针 | `git lfs install && git lfs pull && git lfs checkout` |
| 语音、Qwen、天气不可用 | 检查 `.env` Provider、API Key、模型名；不要在截图或日志里暴露 Key |
| 机器狗原地扭动不走 | 速度低于有效步态区间，参考 5.3 参数 |

---

## 10. 各能力验证状态一览

| 能力 | 有线 DDS | 无线 WebRTC | 备注 |
| --- | --- | --- | --- |
| 状态读取 | 已真机验证 | 已真机验证 | WebRTC 无电量 |
| 前置相机 | 已真机验证 | 已真机验证 | 无线为 MJPEG 桥 |
| 站立 / 趴下 / 坐下 | 已真机验证 | 不支持 | — |
| 短时 Move / Stop | 已真机验证 | 已真机验证 | 无线仅 0.23 m/s × 0.4 s 脉冲 |
| 闭环单动作 | 已真机验证 | 软件就绪 | 无线未逐项验收 |
| 完整 phone_demo 序列 | 未整段验收 | 未整段验收 | — |
| 姿态 Euler / BodyHeight | 有线无契约（跳过） | 已真机验证 | — |
| 扬声器播放 | 无接口 | 已真机验证 | Go2 有线 SDK 无 TTS |
| 麦克风采集 | 无接口 | 文档声称通过 | 未见独立报告 |
| 语音对话 / 控制 | 无接口 | 软件完整 | 依赖公网 DashScope |
| UWB 输入 | 已真机只读验证 | 已真机只读验证 | — |
| UWB 连续跟随 | 受限批准 | 受限批准 | 未见连续跟随通过报告 |
| LiDAR 避障 | 功能 PASS / 距离 HOLD | 未验证 | 候选阈值未入生产 |
| 跌倒任务闭环 | Mock 全通过 | — | 真机需现场联调 |
| SLAM / 导航 | 未实现 | 未实现 | 仅 Mock 契约 |
