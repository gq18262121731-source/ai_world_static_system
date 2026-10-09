# T10 手环串口采集 —— 独立套件

把主系统里手环数据接收的全部逻辑抽成一个**只依赖 pyserial 的独立包**。目的：

- 新系统重做时直接拷走，不用从三万行代码里再剥一次
- 现场排障时不启动整个后端也能确认手环是否在发数据
- 协议细节、AT 命令、已踩过的坑，都记在这一份文档里

本目录内容：

| 文件 | 作用 |
| --- | --- |
| `parser.py` | 报文解析：识别包型、解字节、合并 A/B 包 |
| `serial_reader.py` | 串口读取：探测端口、发 AT 命令、包型轮转、断线重连 |
| `models.py` | `HealthSample` 数据类，纯标准库 |
| `collect.py` | 命令行工具：列端口 / 看原始数据 / 正式采集存 CSV |
| `selftest.py` | 解析器离线自测，不需要硬件，31 项断言 |

`parser.py` 与 `serial_reader.py` 和主系统 `iot/` 下同名文件**逻辑完全一致**，只改了 import 路径。主系统那边改了什么，同步到这里即可。

---

## 一、硬件与连接

```
T10 手环 ──BLE──> USB 采集器（CH9102 / CP210x 转串口）──USB──> 电脑 COMx
```

采集器是一块带蓝牙的 USB 转串口板，负责扫描手环广播、按 AT 命令过滤，把收到的报文按行以 HEX 文本吐到串口。**电脑不直接连手环**，所有配置都是发 AT 命令给采集器。

| 参数 | 值 |
| --- | --- |
| 波特率 | 115200 |
| 数据位 / 停止位 / 校验 | 8 / 1 / 无 |
| 当前实验室手环 MAC | `54:10:26:01:00:DC` |
| 当前实验室端口 | `COM12`（换电脑会变） |
| 采集器识别关键词 | `ch9102` `cp210` `1a86:55d4` `10c4:ea60` `silicon labs` `wch` |

### 驱动

- CH9102：Windows 10/11 一般免驱；不识别时装 WCH 官网 CH343SER
- CP210x：装 Silicon Labs CP210x VCP 驱动

设备管理器里看到"USB-Enhanced-SERIAL CH9102 (COMx)"或"Silicon Labs CP210x USB to UART Bridge (COMx)"即为正常。

---

## 二、快速开始

在仓库根目录执行（或把本目录拷到任何地方，父目录下执行）。

```powershell
pip install pyserial

# 1. 先跑离线自测，确认解析器没被改坏
python -m wristband_kit.selftest

# 2. 找采集器在哪个端口
python -m wristband_kit.collect ports

# 3. 看原始数据流（确认采集器活着、手环在发）
python -m wristband_kit.collect raw --port COM12 --scan --mac 54:10:26:01:00:DC

# 4. 正式采集，可存 CSV
python -m wristband_kit.collect run --port COM12 --mac 54:10:26:01:00:DC --csv samples.csv
```

`run` 的正常输出长这样：

```
16:01:24  54:10:26:01:00:DC  response_a   HR= 87  SpO2= 99  T=   --  BP=--/--  步数=265   电量= 90%  [部分]
16:01:28  54:10:26:01:00:DC  broadcast    HR= 87  SpO2= 97  T=36.46  BP=--/--  步数=--    电量=  0%  [完整]
16:01:29  54:10:26:01:00:DC  response_b   HR=  0  SpO2=  0  T=36.52  BP=119/79 步数=--    电量=  0%  [部分]
```

**`HR=0`、`SpO2=0`、`T=--` 是正常现象**，不是故障。每种包只带一部分字段，见下一节。

### 在自己的代码里用

```python
from wristband_kit import SerialGatewayReader, T10PacketParser, HealthSample

def on_sample(sample: HealthSample) -> None:
    if sample.sos_flag:
        print("SOS!", sample.device_mac, sample.sos_trigger)
    if sample.is_complete():
        print(sample.heart_rate, sample.blood_oxygen, sample.temperature, sample.blood_pressure)

reader = SerialGatewayReader(T10PacketParser(merge_timeout_seconds=0.5))
reader.run(
    port="COM12",                    # 留 None 则按关键词自动探测
    mac_filter="54:10:26:01:00:DC",
    collection_strategy="single_target",
    packet_type=5,
    enable_broadcast_sos_overlay=True,
    response_cycle_seconds=1.0,
    broadcast_cycle_seconds=0.5,
    on_sample=on_sample,
)                                    # 阻塞，内部无限重连；放到线程里跑
```

`run()` 是阻塞的死循环。主系统里用 `asyncio.to_thread(reader.run, ...)` 放到线程池，样本回调里用 `loop.call_soon_threadsafe` 投回事件循环。

---

## 三、协议

采集器每收到一条手环报文，输出一行 HEX 文本，格式为 `<MAC 12 位><载荷>`，或带 `RAW,ID=xxxx,DATA=` 之类前缀。`serial_reader._extract_payload_and_mac` 负责剥前缀、认 MAC。

手环有**三种包型**，由采集器的 `AT+TYPE` 决定收哪种：

| `AT+TYPE` | 收到的包 | 说明 |
| --- | --- | --- |
| `4` | 广播包 | 手环主动广播，含 SOS 位。所有手环都能听到 |
| `5` | 响应包 A + B | 采集器主动连接目标手环拉取，字段最全 |

### 3.1 广播包（`AT+TYPE=4`）

固定前缀 `0201061AFF4C000215`，总长 ≥ 29 字节。

| 偏移 | 长度 | 字段 | 换算 |
| --- | --- | --- | --- |
| 0–8 | 9 | 固定前缀 | — |
| 9–24 | 16 | UUID | 固定 `52616469-6F6C-616E-642D-541000000000`（"Radioland-T10"） |
| 25 | 1 | 心率 | 直接读 |
| 26 | 1 | 血氧 | 直接读 |
| 27–28 | 2 | 温度 | 大端 ÷100，如 `0E42` → 36.50 |
| 29 | 1 | SOS | bit1 = 长按，bit0 = 双击；高位可能带其他状态（0x81/0x82 也算） |

温度不在 35.0–45.0 之间视为表面/环境温度，`temperature` 置 0，原值存 `surface_temperature`。

### 3.2 响应包 A（`AT+TYPE=5`）

标记 `161803`，标记后 ≥ 20 字节。偏移相对标记起点。

| 偏移 | 长度 | 字段 | 换算 |
| --- | --- | --- | --- |
| 0–2 | 3 | 标记 `161803` | — |
| 3–4 | 2 | 环境温度 | 大端 ÷100 |
| 5 | 1 | 电量 % | 直接读 |
| 6–7 | 2 | 表面温度 | 大端 ÷100；在 35–45 内则作为体温 |
| 8–9 | 2 | 心率 | 大端 |
| 10–11 | 2 | 血氧 | 大端 |
| 12–17 | 6 | MAC | 行首没 MAC 时从这里取 |
| 18–19 | 2 | 步数 | 大端 |

### 3.3 响应包 B（`AT+TYPE=5`）

标记 `160318`，标记后 ≥ 5 字节。

| 偏移 | 长度 | 字段 | 换算 |
| --- | --- | --- | --- |
| 0–2 | 3 | 标记 `160318` | — |
| 3 | 1 | 收缩压 | 直接读 |
| 4 | 1 | 舒张压 | 直接读 |
| 5–6 | 2 | 体温（可选） | 大端 ÷100；存在时覆盖 A 的表面温 |

### 3.4 A/B 合并

A 和 B 由同一次连接先后返回，间隔通常 < 0.5s。解析器把 A 缓存起来，B 在 `merge_timeout_seconds`（默认 0.5s）内到达就合并成 `response_ab`——这是唯一一种**同时有心率、血氧、体温、血压**的样本。

B 超时才到，就单独产出 `response_b`：只有血压和体温，心率血氧为 0。主系统日志里大量的 `hr=0 spo2=0 ... bp=131/72` 就是这个。

### 3.5 包型轮转（SOS 窃听）

`AT+TYPE=5` 模式下采集器只连目标手环，**听不到广播，也就收不到 SOS 位**。所以 `enable_broadcast_sos_overlay=True` 时读取器会来回切：

```
TYPE=5 收 1.0s（response_cycle_seconds）→ TYPE=4 收 0.5s（broadcast_cycle_seconds）→ TYPE=5 ...
```

每次切换发一组 `AT+SCANSTOP / AT+MAC= / AT+TYPE= / AT+SCANSTART`，命令间隔 `command_delay_seconds`（0.02–0.12s）。代价是样本率下降约 1/3，换来 SOS 不漏。

不需要 SOS 时用 `--no-sos-overlay` 关掉。

### 3.6 旧固件（legacy）

偶尔有旧手环在偏移 12 处放 `1803` / `0318` 两字节标记（不带 `16` 前缀）。解析器保留了 `LEGACY_RESPONSE_A/B` 分支兜底，新手环用不到。

---

## 四、AT 命令

采集器识别的命令，以 `\r\n` 结尾，回显 `OK`。

| 命令 | 作用 |
| --- | --- |
| `AT+SCANSTOP` | 停止扫描。改配置前必发 |
| `AT+SCANSTART` | 开始扫描/连接 |
| `AT+MAC=5410260100DC` | 只处理这个 MAC（12 位无冒号） |
| `AT+TYPE=4` | 收广播包 |
| `AT+TYPE=5` | 连接目标拉响应包 |
| `AT+UUID=NO` | 输出行里不带 UUID，缩短行长 |

配置单目标的标准序列：`SCANSTOP → MAC → TYPE → SCANSTART`。顺序不能乱，`SCANSTART` 之后再发 `MAC` 不生效。

---

## 五、主系统里的对应配置

`.env` 中与手环相关的项，以及对应 `run()` 参数：

| `.env` | 值 | `run()` 参数 |
| --- | --- | --- |
| `DATA_MODE` | `serial` | 决定 `runtime_mode`，必须是 `serial` |
| `SERIAL_ENABLED` | `true` | 与上一项**同时**为真才启动串口线程 |
| `SERIAL_PORT` | `COM12` | `port` |
| `SERIAL_BAUDRATE` | `115200` | `baudrate` |
| `SERIAL_MAC_FILTER` | `54:10:26:01:00:DC` | `mac_filter` |
| `SERIAL_PACKET_TYPE` | `5` | `packet_type` |
| `SERIAL_ENABLE_BROADCAST_SOS_OVERLAY` | `true` | `enable_broadcast_sos_overlay` |
| `SERIAL_RESPONSE_CYCLE_SECONDS` | `1` | `response_cycle_seconds` |
| `SERIAL_BROADCAST_CYCLE_SECONDS` | `0.5` | `broadcast_cycle_seconds` |
| `SERIAL_PACKET_MERGE_TIMEOUT_SECONDS` | `0.5` | `T10PacketParser(merge_timeout_seconds=)` |
| `SERIAL_AUTO_CONFIGURE` | `true` | `auto_configure` |
| `SERIAL_DISABLE_UUID_OUTPUT` | `true` | `disable_uuid_output` |
| `SERIAL_APPLY_MAC_FILTER` | `false` | `apply_mac_filter`（单目标模式下由 `configure_single_target` 接管，此项无效） |
| `SERIAL_FALLBACK_DEVICE_MAC` | 空 | `fallback_device_mac`，行首无 MAC 时用 |

主系统的采集入口是 `backend/main.py::_serial_stream_loop`，运行时可通过 `POST /api/v1/devices/serial-target` 切换目标手环，靠 `target_mac_provider` 回调实现。

---

## 六、可能出现的问题

按出现频率排序。

### 6.1 一条数据都收不到

**先分层定位，不要一上来改代码。**

```powershell
# 第 1 层：端口在不在
python -m wristband_kit.collect ports
#   没列出来 → USB 没插好 / 驱动没装 / 采集器坏了

# 第 2 层：端口能不能打开
python -m wristband_kit.collect raw --port COM12
#   "打不开串口" → 被占用，见 6.2

# 第 3 层：采集器有没有在吐东西
#   raw 模式打开后一直没输出 → 加 --scan 让它开始扫描
python -m wristband_kit.collect raw --port COM12 --scan --mac 54:10:26:01:00:DC
#   有 OK 回显但没数据 → 手环没电 / 太远 / MAC 写错，见 6.3

# 第 4 层：数据有但解析不出
#   raw 有 HEX 行，run 没样本 → 复制几行 HEX 去 selftest 里比对格式，见 6.5
```

### 6.2 串口被占用：`PermissionError` / `could not open port` / `Access is denied`

**最常见的原因**。Windows 上一个 COM 口同时只能被一个进程打开。

- 主系统后端在跑就会占着 COM12，此时独立工具打不开
- 串口调试助手（sscom、XCOM）忘了关
- 上一个后端进程被 `Stop-Process -Force` 强杀，句柄没释放

查谁占着：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'uvicorn|wristband_kit' } |
  Select-Object ProcessId, CommandLine
```

正确停法是不带 `-Force` 的 `Stop-Process -Id <pid>`，等 3 秒再开。实在释放不了就拔插一次 USB。

**历史事故**：主系统曾因此断流三天而无人察觉——进程健康、页面上虚拟老人的数据照常刷新，只有真实手环没数据。详见 `docs/troubleshooting/手表数据断流.md`。现在 `/healthz` 会在 120 秒无串口样本时报 `degraded`。

### 6.3 有 `OK` 回显，但没有数据行

采集器活着，但没收到手环。

- 手环没电或休眠：按一下侧键亮屏
- 距离太远：BLE 有效距离约 5–10 米，隔墙更短
- MAC 写错：`AT+MAC=` 要 12 位无冒号大写；`collect` 会自动转，手发 AT 时注意
- 手环被另一台采集器/手机 App 连着：BLE 一对一，先断开别的连接
- `AT+TYPE=5` 模式下手环不在扫描范围：先用 `--no-sos-overlay` 换 `AT+TYPE=4` 纯广播模式看能否收到

### 6.4 `HR=0` `SpO2=0` `T=--`，数据"一会有一会没"

**这是正常轮转，不是故障。** 单个包只带部分字段（第三节）。看 `[完整]` 标记的行，或在代码里用 `sample.is_complete()` 筛。

主系统里对应的日志是 `Dropping invalid health sample before alarm evaluation: ... reasons=heart_rate,blood_oxygen`——这些包被校验拦下不进告警评估，但**原始样本仍然记录**。

如果连续 30 秒以上一条 `[完整]` 都没有，才需要排查：多半是 B 包一直超时没合并上，试试把 `--merge-timeout` 调到 1.0。

### 6.5 有 HEX 行但解析不出样本

```powershell
python -m wristband_kit.collect raw --port COM12 --scan --mac 54:10:26:01:00:DC
```

抄几行原始输出，对照第三节的包结构检查：

- 广播包是否以 `0201061AFF4C000215` 开头
- 响应包里能否找到 `161803` 或 `160318`
- 行首是否有 12 位 MAC，或被 `RAW,ID=` 之类前缀包着

如果固件升级换了格式，改 `parser.py` 里的 `PacketLayout`，然后在 `selftest.py` 里加一条新报文断言。

### 6.6 温度一直是 0 或 --

采集到的温度不在 35.0–45.0 范围就被置 0。原因：

- 手环刚戴上，表面还没热起来（等 2–3 分钟）
- 戴得松，传感器没贴皮肤
- 广播包里的是环境温度而非体温（固件差异），这时看响应包 B 的体温

原值保存在 `surface_temperature` / `ambient_temperature`，没丢。

### 6.7 SOS 收不到

- `enable_broadcast_sos_overlay` 被关了（`--no-sos-overlay`）：SOS 只在广播包里，`AT+TYPE=5` 听不到
- 按键方式不对：长按 3 秒（`long_press`）或快速双击（`double_click`）
- 15 秒去重窗口（`sos_window_seconds`）内重复触发只算一次

### 6.8 自动探测选错端口

多个 USB 转串口设备同时插着时，按关键词探测取第一个。显式传 `--port` / `SERIAL_PORT` 避免。

**实验室这台电脑当前就插着两块 CH9102**（盘点时 `collect ports` 的输出）：

```
COM13  <== 是  USB-Enhanced-SERIAL CH9102 (COM13)  [USB VID:PID=1A86:55D4 SER=588F051527]
COM12  <== 是  USB-Enhanced-SERIAL CH9102 (COM12)  [USB VID:PID=1A86:55D4 SER=588F051539]
```

手环配的是 COM12（序列号 `588F051539`）。留空端口让它自动探测，有一半概率选到 COM13 然后一条数据都收不到。**`.env` 里 `SERIAL_PORT=COM12` 不要删。** 换 USB 口后端口号可能变，用序列号认。

### 6.9 `SerialException: device reports readiness to read but returned no data`

USB 线接触不良或采集器复位。读取器会自动重连（2s 退避），持续出现就换线。

### 6.10 换电脑后端口号变了

`COM12` 是这台电脑的。换机器先 `collect ports` 看，再改 `.env` 的 `SERIAL_PORT`。

### 6.11 PowerShell 脚本乱码或解析失败

与手环无关但常一起遇到：PowerShell 5.1 把无 BOM 的 UTF-8 脚本当 ANSI 读，中文注释会导致解析失败。`scripts/*.ps1` 都保持纯 ASCII 是有意为之。

---

## 七、迁移到新系统的建议

- `parser.py` + `models.py` 整体搬，零改动
- `serial_reader.py` 的 `run()` 死循环模型可以保留，也可以改成 `async` 用 `pyserial-asyncio`；但**AT 命令序列和轮转逻辑不要动**，那是调出来的
- 新系统的健康检查**一定要**反映"最近一次样本时间"，否则串口线程死了没人知道
- 演示用的虚拟数据必须和真实串口数据**物理隔离**（不同表、不同标记），否则虚拟数据会掩盖真实断流
- 把 `selftest.py` 接进 CI，改解析器就跑
