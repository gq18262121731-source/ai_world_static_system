# T10 手环串口采集 —— 独立套件

把主系统里手环数据接收的全部逻辑抽成一个**只依赖 pyserial 的独立包**。目的：

- 新系统重做时直接拷走，不用从三万行代码里再剥一次
- 现场排障时不启动整个后端也能确认手环是否在发数据
- 协议细节、AT 命令、已踩过的坑，都记在这一份文档里

本目录内容：

| 文件 | 作用 |
| --- | --- |
| `parser.py` | 报文解析：识别包型、解字节、合并 A/B 包 |
| `serial_reader.py` | 串口读取：探测端口、发 AT 命令、包型轮转、多手环调度、断线重连 |
| `device_discovery.py` | 手环 MAC 注册表：自动发现、可信性校验、轮转顺序 |
| `models.py` | `HealthSample` 数据类，纯标准库 |
| `collect.py` | 命令行工具：列端口 / 看原始数据 / 单手环采集 / 多手环采集 |
| `selftest.py` | 解析器离线自测，不需要硬件，31 项断言 |

`parser.py`、`serial_reader.py`、`device_discovery.py` 与主系统 `iot/` 下同名文件**逻辑完全一致**，只改了 import 路径。主系统那边改了什么，同步到这里即可。改完用这条命令确认差异只剩 import：

```powershell
git diff --no-index -U0 wristband_kit/serial_reader.py iot/serial_reader.py
```

---

## 一、硬件与连接

```
T10 手环 ──BLE──> USB 采集器（CH9102 / CP210x 转串口）──USB──> 电脑 COMx
```

采集器是一块带蓝牙的 USB 转串口板，负责扫描手环广播、按 AT 命令过滤，把收到的报文按行以 HEX 文本吐到串口。**电脑不直接连手环**，所有配置都是发 AT 命令给采集器。

多只手环时推荐两块板分工，见[第五节](#五多手环采集)：

```
手环 A ┐
手环 B ┼─BLE 广播─> 采集器①（AT+TYPE=4 不设 MAC 过滤）─> COM12  心率/血氧/体温/SOS，全部手环
手环 C ┘
       └─BLE 连接─> 采集器②（AT+TYPE=5 轮转 AT+MAC=）──> COM13  血压/步数/电量，一次一只
```

| 参数 | 值 |
| --- | --- |
| 波特率 | 115200 |
| 数据位 / 停止位 / 校验 | 8 / 1 / 无 |
| 当前实验室手环 MAC | `54:10:26:01:00:DC`、`54:10:26:01:00:DF`、`54:10:26:01:00:E7` |
| 当前实验室端口 | `COM12`（广播板）、`COM13`（响应板），换电脑会变 |
| T10 出厂 MAC 前缀 | `54:10:26` |
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

# 4a. 单手环采集，可存 CSV
python -m wristband_kit.collect run --port COM12 --mac 54:10:26:01:00:DC --csv samples.csv

# 4b. 多手环采集（两块板分工，MAC 全自动发现）
python -m wristband_kit.collect multi --broadcast-port COM12 --response-port COM13 --csv samples.csv
```

`run` 的正常输出长这样：

```
16:01:24  54:10:26:01:00:DC  response_a   HR= 87  SpO2= 99  T=   --  BP=--/--  步数=265   电量= 90%  [部分]
16:01:28  54:10:26:01:00:DC  broadcast    HR= 87  SpO2= 97  T=36.46  BP=--/--  步数=--    电量=  0%  [完整]
16:01:29  54:10:26:01:00:DC  response_b   HR=  0  SpO2=  0  T=36.52  BP=119/79 步数=--    电量=  0%  [部分]
```

**`HR=0`、`SpO2=0`、`T=--` 是正常现象**，不是故障。每种包只带一部分字段，见下一节。

### 在自己的代码里用

单手环：

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

多手环（两块板，一线程一块）：

```python
import threading
from wristband_kit import SerialGatewayReader, T10PacketParser, WristbandRegistry

registry = WristbandRegistry()        # 默认按 54:10:26 前缀 + T10 UUID 收人
parser = T10PacketParser(merge_timeout_seconds=0.5)   # 内部有锁，两个线程可共用

def on_sample(sample):
    record, is_new = registry.observe(sample.device_mac, device_uuid=sample.device_uuid)
    if record is None:
        return                        # 不是可信手环，忽略
    if is_new:
        print("发现新手环", record.mac_address)
    print(record.mac_address, sample.packet_type, sample.heart_rate, sample.blood_pressure)

for role, port in [("broadcast", "COM12"), ("response", "COM13")]:
    threading.Thread(
        target=lambda r=role, p=port: SerialGatewayReader(parser).run(
            port=p,
            collection_strategy="multi_target",
            collector_role=r,
            target_macs_provider=registry.target_macs,   # 响应板据此轮转
            response_dwell_seconds=1.0,
            on_sample=on_sample,
        ),
        daemon=True,
    ).start()
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

**`AT+MAC=` 只能设不能清**——固件没有取消过滤的命令。所以广播监听绝不能先设 MAC 过滤，一旦设过就只能靠 `AT+MAC=000000000000` 归零（`DEFAULT_MAC_FILTER_RESET_COMMAND`，未经固件文档确认，换固件要重新验证）。双板方案里广播板从头到尾不发 `AT+MAC=`，天然没有这个问题。

---

## 五、多手环采集

`single_target` 策略只认一个 MAC，并且会**主动丢弃**非目标 MAC 的报文——多只手环时只有一只能进系统。`multi_target` 策略把这两处都去掉：收到什么都交给解析器，由解析器按 MAC 分流。解析器的 A/B 合并缓存本来就是按 MAC 分桶的（`_partials[mac]`），所以三只手环并发不会串包。

### 5.1 三种采集角色

| `collector_role` | 行为 | 拿到的字段 |
| --- | --- | --- |
| `broadcast` | 常驻 `AT+TYPE=4`，**不发 `AT+MAC=`** | 心率、血氧、体温、SOS，**全部手环同时** |
| `response` | 轮转 `AT+MAC=` + `AT+TYPE=5`，每只停留 `response_dwell_seconds` | 血压、步数、电量、环境温，一次一只 |
| `rotate` | 上面两件事在一块板上时间片轮转 | 全字段，但每只手环占空比约 `1/(n+1)` |

只有 `broadcast` 角色能发现未知 MAC——`AT+TYPE=5` 要先知道 MAC 才能连。

`response` 角色靠下发 `AT+MAC=` 切换目标，而下发 `AT+MAC=` 是有代价的，可能远超预期，见 [9.3](#93-atmac-的代价未在本仓库复测)。上生产前先按那一节实测一次吞吐。

### 5.2 MAC 自动发现

`device_discovery.WristbandRegistry` 维护"值得轮询的手环"列表。一个 MAC 要进列表，必须满足其一：

- 在配置里预置（`SERIAL_TARGET_MACS`），永不被拒也永不被淘汰
- MAC 前缀命中 `54:10:26`（T10 出厂前缀）
- 广播包带 T10 的固定 UUID `52616469-6F6C-616E-642D-541000000000`

都不满足就当环境噪声忽略，不进轮转也不进设备台账。上限 `SERIAL_MAX_DISCOVERED_DEVICES`（默认 16），防止 BLE 拥挤的现场把台账刷爆。

注册表是**插入有序**的，不是 set——响应板按下标轮转，顺序一变会在停留中途跳到别的手环上。

### 5.3 主系统里的表现

广播板发现新手环后，`ingest_sample` 会自动把它注册进设备台账（`ingest_mode=serial`、`bind_status=unbound`），响应板下一轮就开始拉它的血压。**自动注册出来的设备是未绑定的**，家属端看不到，需要值守人员在设备页把它绑到某位老人。

自动注册刻意不接管"当前串口目标"（`set_active_serial_target=False`）：未绑定设备一旦被设为目标，`_set_active_serial_target_locked` 会把目标清成 None，反而搞坏 `POST /api/v1/devices/serial-target` 的单目标切换。

三只手环是不是都在收，看这里，不用翻日志：

```powershell
(Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/info).serial_runtime.known_wristbands
```

每只手环带 `sample_count` / `last_seen_at` / `packet_types`，哪只没数据一眼能看出来。

### 5.4 只有一块板时

把 `SERIAL_DUAL_COLLECTOR_ENABLED` 设为 `false`，单板会自动用 `rotate` 角色。代价是每只手环的采样率降到约 `1/(n+1)`（三只手环时约四分之一），且广播窗口要靠 `AT+MAC=000000000000` 清过滤，可靠性不如双板。**能凑出两块板就用两块。**

---

## 六、主系统里的对应配置

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

多手环相关项：

| `.env` | 值 | 说明 |
| --- | --- | --- |
| `SERIAL_MULTI_DEVICE_ENABLED` | `true` | 策略切到 `multi_target`。为 `false` 时下面各项全部无效 |
| `SERIAL_DUAL_COLLECTOR_ENABLED` | `true` | 两块板分工。两个端口必须不同，否则自动降级为单板 |
| `SERIAL_BROADCAST_PORT` | `COM12` | 广播板，`collector_role=broadcast` |
| `SERIAL_RESPONSE_PORT` | `COM13` | 响应板，`collector_role=response` |
| `SERIAL_RESPONSE_DWELL_SECONDS` | `1.0` | 响应板在每只手环上停留的秒数，低于 0.6 会被 AT 往返吃光 |
| `SERIAL_AUTO_DISCOVER_DEVICES` | `true` | 关掉就只采集 `SERIAL_TARGET_MACS` 里列出的手环 |
| `SERIAL_TARGET_MACS` | `54:10:26:01:00:DC,54:10:26:01:00:DF,54:10:26:01:00:E7` | 现场三只手环，排在轮转队首；留空则完全靠自动发现 |
| `SERIAL_DISCOVERY_MAC_PREFIXES` | `54:10:26` | 可信 MAC 前缀，把其他 BLE 设备挡在台账外 |
| `SERIAL_MAX_DISCOVERED_DEVICES` | `16` | 自动发现上限 |

主系统的采集入口是 `backend/main.py::_serial_stream_loop`，起几个线程由 `_resolve_serial_collector_plan()` 决定。单目标模式下可通过 `POST /api/v1/devices/serial-target` 切换目标手环，靠 `target_mac_provider` 回调实现；多手环模式下轮转列表由注册表接管，这个接口只影响界面上显示的"当前目标"。

---

## 七、可能出现的问题

按出现频率排序。

### 7.1 一条数据都收不到

**先分层定位，不要一上来改代码。**

```powershell
# 第 1 层：端口在不在
python -m wristband_kit.collect ports
#   没列出来 → USB 没插好 / 驱动没装 / 采集器坏了

# 第 2 层：端口能不能打开
python -m wristband_kit.collect raw --port COM12
#   "打不开串口" → 被占用，见 7.3

# 第 3 层：采集器有没有在吐东西
#   raw 模式打开后一直没输出 → 加 --scan 让它开始扫描
python -m wristband_kit.collect raw --port COM12 --scan --mac 54:10:26:01:00:DC
#   有 OK 回显但没数据 → 手环没电 / 太远 / MAC 写错，见 7.4

# 第 4 层：数据有但解析不出
#   raw 有 HEX 行，run 没样本 → 复制几行 HEX 去 selftest 里比对格式，见 7.6
```

### 7.2 只收到一只手环的数据

**先做两件事再往下查**，实际事故里问题都出在这两步（详见 [9.1](#91-只有一只手环有数据先怀疑软件白名单别先怀疑硬件)、[9.2](#92-先确认你看的那个界面是哪套后端)）：

- 确认你看的界面属于哪套后端。同机器多套系统抢同一块采集器时，改错一边的配置毫无效果。
- 确认没有别处的软件白名单在上报前把包丢掉。硬件全好、包也收到了，仍可能一条都不上报。

确认完再按下面顺序查：

1. **策略还是 `single_target`**。`single_target` 会主动丢弃非目标 MAC 的报文，多手环必须开 `SERIAL_MULTI_DEVICE_ENABLED=true`。确认生效：

   ```powershell
   (Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/info).serial_runtime |
     Select-Object collection_strategy, multi_device_enabled, dual_collector_active
   ```

   `collection_strategy` 必须是 `multi_target`。

2. **看注册表认出了几只**：

   ```powershell
   (Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/info).serial_runtime.known_wristbands
   ```

   - 一只都没有 → 广播板没在收，回到 7.1 分层定位
   - 只有一只，且是 `SERIAL_TARGET_MACS` 里那只 → 自动发现没生效，见下条
   - 三只都在但 `sample_count` 差距悬殊 → 正常，广播板对所有手环公平，响应板轮转有先后

3. **自动发现被挡掉了**。别的手环 MAC 前缀不是 `54:10:26` 时会被当噪声忽略。先用独立工具看真实 MAC：

   ```powershell
   python -m wristband_kit.collect multi --port COM12 -v
   ```

   `-v` 会打印被忽略的 MAC。确认前缀后加进 `SERIAL_DISCOVERY_MAC_PREFIXES`，或直接写进 `SERIAL_TARGET_MACS`（预置 MAC 永不被拒）。

4. **手环没电、没戴、或被别的设备连着**。`AT+TYPE=5` 是 BLE 一对一连接，手环同时只能被一个采集器连。三只手环都在广播里出现但响应包只有一只，多半是另外两只被手机 App 占着。

5. **只有一块板且没配双板**，这时是 `rotate` 角色，每只手环占空比约 `1/(n+1)`，看起来像"时断时续"，属正常。要稳就上第二块板。

注意：自动发现出来的手环是**未绑定**状态，家属端看不到，需要在设备页绑定到老人。台账里有数据但界面上没人，先查绑定。

### 7.3 串口被占用：`PermissionError` / `could not open port` / `Access is denied`

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

### 7.4 有 `OK` 回显，但没有数据行

采集器活着，但没收到手环。

- 手环没电或休眠：按一下侧键亮屏
- 距离太远：BLE 有效距离约 5–10 米，隔墙更短
- MAC 写错：`AT+MAC=` 要 12 位无冒号大写；`collect` 会自动转，手发 AT 时注意
- 手环被另一台采集器/手机 App 连着：BLE 一对一，先断开别的连接
- `AT+TYPE=5` 模式下手环不在扫描范围：先用 `--no-sos-overlay` 换 `AT+TYPE=4` 纯广播模式看能否收到

### 7.5 `HR=0` `SpO2=0` `T=--`，数据"一会有一会没"

**这是正常轮转，不是故障。** 单个包只带部分字段（第三节）。看 `[完整]` 标记的行，或在代码里用 `sample.is_complete()` 筛。

主系统里对应的日志是 `Dropping invalid health sample before alarm evaluation: ... reasons=heart_rate,blood_oxygen`——这些包被校验拦下不进告警评估，但**原始样本仍然记录**。

如果连续 30 秒以上一条 `[完整]` 都没有，才需要排查：多半是 B 包一直超时没合并上，试试把 `--merge-timeout` 调到 1.0。

### 7.6 有 HEX 行但解析不出样本

```powershell
python -m wristband_kit.collect raw --port COM12 --scan --mac 54:10:26:01:00:DC
```

抄几行原始输出，对照第三节的包结构检查：

- 广播包是否以 `0201061AFF4C000215` 开头
- 响应包里能否找到 `161803` 或 `160318`
- 行首是否有 12 位 MAC，或被 `RAW,ID=` 之类前缀包着

如果固件升级换了格式，改 `parser.py` 里的 `PacketLayout`，然后在 `selftest.py` 里加一条新报文断言。

### 7.7 温度一直是 0 或 --

采集到的温度不在 35.0–45.0 范围就被置 0。原因：

- 手环刚戴上，表面还没热起来（等 2–3 分钟）
- 戴得松，传感器没贴皮肤
- 广播包里的是环境温度而非体温（固件差异），这时看响应包 B 的体温

原值保存在 `surface_temperature` / `ambient_temperature`，没丢。

### 7.8 SOS 收不到

- `enable_broadcast_sos_overlay` 被关了（`--no-sos-overlay`）：SOS 只在广播包里，`AT+TYPE=5` 听不到
- 按键方式不对：长按 3 秒（`long_press`）或快速双击（`double_click`）
- 15 秒去重窗口（`sos_window_seconds`）内重复触发只算一次

### 7.9 自动探测选错端口

多个 USB 转串口设备同时插着时，按关键词探测取第一个。显式传 `--port` / `SERIAL_PORT` 避免。

**实验室这台电脑当前就插着两块 CH9102**（盘点时 `collect ports` 的输出）：

```
COM13  <== 是  USB-Enhanced-SERIAL CH9102 (COM13)  [USB VID:PID=1A86:55D4 SER=588F051527]
COM12  <== 是  USB-Enhanced-SERIAL CH9102 (COM12)  [USB VID:PID=1A86:55D4 SER=588F051539]
```

手环配的是 COM12（序列号 `588F051539`）。留空端口让它自动探测，有一半概率选到 COM13 然后一条数据都收不到。**`.env` 里 `SERIAL_PORT=COM12` 不要删。** 换 USB 口后端口号可能变，用序列号认。

### 7.10 `SerialException: device reports readiness to read but returned no data`

USB 线接触不良或采集器复位。读取器会自动重连（2s 退避），持续出现就换线。

### 7.11 换电脑后端口号变了

`COM12` 是这台电脑的。换机器先 `collect ports` 看，再改 `.env` 的 `SERIAL_PORT`。

### 7.12 PowerShell 脚本乱码或解析失败

与手环无关但常一起遇到：PowerShell 5.1 把无 BOM 的 UTF-8 脚本当 ANSI 读，中文注释会导致解析失败。`scripts/*.ps1` 都保持纯 ASCII 是有意为之。

---

## 八、迁移到新系统的建议

- `parser.py` + `models.py` + `device_discovery.py` 整体搬，零改动
- `serial_reader.py` 的 `run()` 死循环模型可以保留，也可以改成 `async` 用 `pyserial-asyncio`；但**AT 命令序列和轮转逻辑不要动**，那是调出来的
- 多手环时解析器实例若被多个采集线程共用，`feed()` 的锁不能去掉：`_partials` 在遍历中被另一线程插入会抛 `dictionary changed size during iteration`，直接搞死一个采集线程
- 新系统的健康检查**一定要**反映"最近一次样本时间"，否则串口线程死了没人知道
- 多手环时健康检查还要**按手环**判断，不能只看"整体最近一次样本"：三只里断一只，全局时间戳仍在刷新，`/healthz` 照样返回 `ok`。断流盲点从"整体"缩小到"单只"，但依然是盲点
- 演示用的虚拟数据必须和真实串口数据**物理隔离**（不同表、不同标记），否则虚拟数据会掩盖真实断流
- 把 `selftest.py` 接进 CI，改解析器就跑

---

## 九、现场实测心得

这一节记的是真机排障中付出代价才拿到的结论，与具体代码结构无关，**换系统也适用**。写在这里是因为下一个人一定会再踩一次。

一句话总结这次的教训：**"收不到数据"从来不是一个问题，而是四个**——串口有没有这行、解析器有没有出样本、样本有没有被上报、设备有没有绑定。这四段任何一处都能静默丢数据，而界面上看起来一模一样。先定位在哪一段，再谈原因。

### 9.1 只有一只手环有数据？先怀疑软件白名单，别先怀疑硬件

真实事故：三只手环都在设备台账里、都绑好了老人，界面上却只有一只在线，另外两只的"最后上报"永远是 `--`。硬件全好、三只手环都在广播、采集器也确实收到了它们的包。

原因是网关启动时带了单个 MAC，代码在**上报前**做了一次软件白名单过滤：

```python
# 启动命令：run --port COM12 --mac 54:10:26:01:00:DC
accept = {args.mac.upper()} if args.mac and not args.accept_all else None
...
if accept_macs and sample.device_mac not in accept_macs:
    self.stats["filtered_out"] += 1      # ← 数据死在这里
    continue
```

包已经收到、已经解析成样本、已经计进 `seen_devices`，只是没被上报。**所以"收不到数据"要分成三个问题问**：串口有没有这行？解析器有没有出样本？样本有没有被上报？三者中间任何一环都能悄悄丢数据，而只看界面是分不出来的。

排查动作：看网关的 `filtered_out` 和 `seen_devices` 两个计数器。`seen_devices` 里有它、`filtered_out` 在涨，就是被白名单挡了，跟射频、距离、电量全都无关。

修法通常不用改代码——这类网关一般都留了"不过滤"的开关，只是默认没开。上面那份代码里就是 `--accept-all`：

```powershell
Stop-Process -Id <网关PID>        # 不加 -Force，让串口句柄正常释放
Start-Sleep -Seconds 3
python -u -m wristband_gateway.main run --port COM12 --accept-all
```

去掉 `--mac` 或加上 `--accept-all`，白名单就变成 `None`，所有手环都会上报。**换系统时先找有没有这个开关，再考虑改代码。**

### 9.2 先确认你看的那个界面是哪套后端

同一台机器上很容易同时跑着多套系统，各占不同端口、各带自己的网关，而它们抢的是同一块采集器。本次就是：本仓库后端在 8000（当时根本没启动），另一套系统在 8080 并独占 COM12——界面来自 8080，改本仓库的配置自然一点效果都没有。

动手改之前先花十秒确认：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CommandLine | Format-List
```

命令行里会直接写明是哪个目录、哪个模块、监听哪个端口、串口参数是什么。这一步能省掉几小时白工。

### 9.3 `AT+MAC=` 的代价（未在本仓库复测）

另一套系统的代码里留了这样一条现场注释：

> 默认不下发 `AT+MAC`：实测它会让采集器把时间花在建连上，串口吞吐掉到七分之一，而且并没有真的挡住无关设备。软件过滤更可靠。

它的解析循环里也再做了一次软件过滤，注释说明是"采集器的 `AT+MAC` 过滤实测不总是生效"。

**如果这个结论成立，影响很大**：本套件的 `response` 角色正是每秒轮转下发 `AT+MAC=`，真机上响应包（血压/步数/电量）的速率会明显低于按停留时长估算的值。而 `broadcast` 角色从不下发 `AT+MAC=`、纯靠软件按 MAC 分流，恰好与那套系统的做法一致。

**这条结论来自另一份代码的注释，本仓库没有独立复测过**，别当既定事实。上生产前实测一次：分别用 `--response-dwell 1.0` 和纯广播模式跑同样时长，比较每只手环的样本数。若吞吐确实掉得厉害，就把架构改成"广播板全量收 + 软件分流为主，`AT+MAC` 轮转只在需要血压时短时启用"。

### 9.4 从设备台账反推故障位置

台账里的几个字段组合起来能直接区分故障类型，比翻日志快：

| 现象 | 含义 |
| --- | --- |
| 设备名就是 MAC 字符串、`last_seen` 为 `--`、电量是默认值 | 手工注册，**从未收到过任何包**。自动发现注册出来的名字会是 `T10-WATCH`，不会是 MAC |
| `last_seen` 有值但很旧 | 曾经通过，现在断流。查串口占用、手环电量、距离 |
| 网关 `seen_devices` 里有它，但台账 `last_seen` 仍是 `--` | 收到了却没上报——白名单或校验挡下了，见 9.1 |
| 有数据但界面上看不到人 | 设备未绑定到老人。数据在库里，只是没有归属 |

### 9.5 串口占用的判定与解除

一个 COM 口同时只能被一个进程打开，被占时报 `PermissionError(13, '拒绝访问')`（英文环境是 `Access is denied`）。**这条报错不代表板子坏了，只代表你不是第一个打开它的人。**

找占用者时别只匹配 `uvicorn`——网关往往是独立进程，模块名可能叫 `wristband_gateway`、`gateway`、`collector` 之类，按目录路径匹配更稳。停的时候用不带 `-Force` 的 `Stop-Process`，再等 3 秒让句柄释放；`-Force` 强杀会留下不释放的句柄，只能拔插 USB。

### 9.6 改完 CLI 一定要真跑一次

本次教训：给 `collect.py` 加 `multi` 子命令时，函数体里用了 `args.csv`，但忘了在 `add_argument` 里注册这个参数。`--help` 正常、单元测试全绿，真机一跑立刻 `AttributeError: 'Namespace' object has no attribute 'csv'`。

argparse 的参数注册和使用分在两处，天然容易漏，而这类错误只在真正执行到那一行时才暴露。**改完命令行入口，哪怕没有硬件，也至少把每个子命令空跑一次到报错为止。**
