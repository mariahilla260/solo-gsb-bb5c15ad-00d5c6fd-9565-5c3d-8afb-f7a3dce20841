# 播客转写字幕分段 API（Podcast Subtitle Segmentation API）

接收播客转写后按时间排列的词序列，在硬约束下用**动态规划**求出总分最低的字幕分段方案，
同时返回分段 JSON 与 SRT 字幕文本；另提供**广告插播点**接口，在词间静音处选出
总静音最长、满足间隔约束的插播位置，以及**说话人校正**接口，在人工锁定约束下
以最小代价重指派说话人。基于 Python 3.12 + FastAPI。

## 一条命令启动

```bash
docker compose up --build
```

服务监听并映射到宿主机 **`http://localhost:8000`**。

- 分段接口：`POST http://localhost:8000/segments`
- 广告插播点接口：`POST http://localhost:8000/ad-breaks`
- 说话人校正接口：`POST http://localhost:8000/speaker-reconcile`
- 健康检查：`GET http://localhost:8000/health`
- 交互式 API 文档（Swagger UI）：`http://localhost:8000/docs`

> 不想用 Docker 时也可：`pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000`

## 请求

`POST /segments`，`Content-Type: application/json`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",       "speaker": "A"},
    {"start_ms": 1800, "end_ms": 6100, "text": "今天聊聊",       "speaker": "A"},
    {"start_ms": 6100, "end_ms": 7000, "text": "动态规划。",     "speaker": "A"},
    {"start_ms": 7000, "end_ms": 9100, "text": "我同意你的看法。", "speaker": "B"}
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `start_ms` | 非负整数 | 词起始时间（毫秒） |
| `end_ms` | 非负整数 | 词结束时间（毫秒，须不早于 `start_ms`） |
| `text` | 非空字符串 | 词文本；**段文本为词文本原样拼接，不插入空格** |
| `speaker` | 非空字符串 | 说话人标识 |

## 分段规则（硬约束）

每一段必须同时满足：

1. **换人必断**：段内所有词的 `speaker` 相同，说话人变化处强制断开；
2. **长度**：段文本不超过 **18 个 Unicode 字符**（按 code point 计数，`😀` 算 1 个）；
3. **时长**：首词 `start_ms` 到末词 `end_ms` 在 **1000～6000 毫秒**之间（含边界）；
4. 段至少包含一个词。

输入还必须满足：词按时间排列（`start_ms` 非倒序）、相邻词时间不重叠
（允许端点相接，即下一个词的 `start_ms` 等于上一个词的 `end_ms`），否则返回 **422**。

## 计分规则（动态规划最小化）

对**非末段**累计分数（末段不参与扣分）：

- 末词的文本**不以** `。`、`！`、`？` 结尾：**+2 分**；
- 段时长不足 **2000 毫秒**：**+1 分**。

在所有总分相同的方案中，取**断点序列字典序最小**者。断点序列只记录“词之间切开
的位置”（除第一段外每段首词的序号，不含固定的起点 0 与终点 n），按标准字典序
比较——因此同分时自然优先段数更少、首个断点更靠前的方案。例如断在词 8 与词 12
之后：断点序列 `(8,)` 小于 `(12,)`。

## 响应示例（200）

```json
{
  "segments": [
    {
      "index": 0,
      "start_word": 0,
      "end_word": 0,
      "start_ms": 0,
      "end_ms": 1800,
      "duration_ms": 1800,
      "speaker": "A",
      "text": "大家好。",
      "char_count": 4
    },
    {
      "index": 1,
      "start_word": 1,
      "end_word": 2,
      "start_ms": 1800,
      "end_ms": 7000,
      "duration_ms": 5200,
      "speaker": "A",
      "text": "今天聊聊动态规划。",
      "char_count": 9
    },
    {
      "index": 2,
      "start_word": 3,
      "end_word": 3,
      "start_ms": 7000,
      "end_ms": 9100,
      "duration_ms": 2100,
      "speaker": "B",
      "text": "我同意你的看法。",
      "char_count": 8
    }
  ],
  "total_score": 1,
  "srt": "1\n00:00:00,000 --> 00:00:01,800\n大家好。\n\n2\n00:00:01,800 --> 00:00:07,000\n今天聊聊动态规划。\n\n3\n00:00:07,000 --> 00:00:09,100\n我同意你的看法。\n"
}
```

> `start_word` / `end_word` 均为 **0-based、含端点**的词序号。
> 上例总分 1：第 1 段仅 1800ms（不足 2000ms）记 1 分，它以 `。` 结尾不记标点分；
> 第 2 段为非末段但以 `。` 结尾且时长达标，记 0 分；末段不记分。
> 词 0 不能与词 1 合段（0→6100ms 共 6100ms 超上限）；若词 1 单独成段，
> “今天聊聊”不以句末标点结尾会被记 2 分，故 DP 选择把词 1、2 合为一段（总分 1）。

### curl 示例

```bash
curl -s http://localhost:8000/segments \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1800,"text":"大家好。","speaker":"A"},{"start_ms":1800,"end_ms":6100,"text":"今天聊动态规划。","speaker":"A"},{"start_ms":6100,"end_ms":8200,"text":"我同意。","speaker":"B"}]}'
```

## 广告插播点：`POST /ad-breaks`

在相邻词之间的静音处选择 `break_count` 个插播点，使选中静音的总毫秒数最大。
`words` 字段与 `/segments` 完全相同，并沿用同一套词序与时间校验（422 错误码一致）。

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",   "speaker": "A"},
    {"start_ms": 2600, "end_ms": 5200, "text": "今天聊聊",   "speaker": "A"},
    {"start_ms": 5900, "end_ms": 6800, "text": "动态规划。", "speaker": "A"},
    {"start_ms": 7600, "end_ms": 9700, "text": "下期再见。", "speaker": "B"}
  ],
  "break_count": 2,
  "min_silence_ms": 500,
  "min_spacing_ms": 1000
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `break_count` | 整数，1～5 | 需要选出的插播点个数，必须恰好选满 |
| `min_silence_ms` | 正整数 | 候选静音时长下限：相邻词间隔达到该值才成为候选 |
| `min_spacing_ms` | 非负整数 | 任意相邻选中插播点的最小时间间隔 |

### 选择规则

1. **候选**：仅取相邻词之间静音时长 `>= min_silence_ms` 的位置，插播时间为
   静音区间的**整数中点** `(silence_start_ms + silence_end_ms) // 2`；
2. **约束**：恰好选出 `break_count` 个候选，任意相邻选中时间至少相隔
   `min_spacing_ms`；
3. **目标**：选中静音的总毫秒数最大；同分时取**时间序列字典序最小**的方案；
4. 不存在满足全部约束的方案时返回 **422**（`infeasible_ad_breaks`）。

### 响应示例（200）

```json
{
  "breaks": [
    {
      "index": 0,
      "time_ms": 2200,
      "silence_start_ms": 1800,
      "silence_end_ms": 2600,
      "silence_ms": 800
    },
    {
      "index": 1,
      "time_ms": 7200,
      "silence_start_ms": 6800,
      "silence_end_ms": 7600,
      "silence_ms": 800
    }
  ],
  "total_silence_ms": 1600
}
```

> 上例三处候选静音为 800ms（中点 2200）、700ms（中点 5550）、800ms（中点 7200），
> 两两间隔均满足 `min_spacing_ms=1000`；总静音最大的组合是首尾两处（1600ms）。
> `breaks` 始终按时间升序返回，`index` 为 0-based 序号。

### curl 示例

```bash
curl -s http://localhost:8000/ad-breaks \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1800,"text":"大家好。","speaker":"A"},{"start_ms":2600,"end_ms":5200,"text":"今天聊聊","speaker":"A"},{"start_ms":5900,"end_ms":6800,"text":"动态规划。","speaker":"A"},{"start_ms":7600,"end_ms":9700,"text":"下期再见。","speaker":"B"}],"break_count":2,"min_silence_ms":500,"min_spacing_ms":1000}'
```

### 无可行插播方案

候选不足 `break_count` 个，或候选间距离无法满足 `min_spacing_ms` 时，返回 422，
并附带候选数与请求的插播点数：

```json
{
  "error": {
    "code": "infeasible_ad_breaks",
    "message": "不存在满足全部约束的插播方案",
    "break_count": 2,
    "candidate_count": 1
  }
}
```

## 说话人校正：`POST /speaker-reconcile`

在**严格满足人工锁定**、且不改词序/文本/时间的前提下，为每个词重新指派说话人，
使总成本最小。`words` 字段与 `/segments` 完全相同，并沿用同一套词序与时间校验
（422 错误码一致）。

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",   "speaker": "A"},
    {"start_ms": 1800, "end_ms": 3600, "text": "我是小明。", "speaker": "A"},
    {"start_ms": 3600, "end_ms": 5400, "text": "谢谢。",     "speaker": "B"},
    {"start_ms": 5400, "end_ms": 7200, "text": "下期再见。", "speaker": "A"}
  ],
  "locks": [{"word_index": 2, "speaker": "B"}],
  "switch_cost": 2
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `locks` | 数组，可空/可省略 | 人工锁定项，每项含 `word_index`（0-based 词序号）与非空 `speaker`；该词必须指派为该说话人 |
| `switch_cost` | 整数，0～10 | 相邻换人的单位代价 |

### 校正规则

1. **候选说话人**：取原词 `speaker` 与锁定值的并集，**至多 8 个**，超限返回 422
   （`too_many_speakers`）；锁定可以引入原序列中不存在的新说话人；
2. **硬约束**：锁定词严格取锁定值；词序、文本、时间一律不变；
3. **目标**：最小化 `改动原 speaker 的词数 + switch_cost × 相邻换人次数`；
4. **同成本裁决**：取整条说话人序列**字典序最小**者；
5. `locks` 为空数组或省略时即对原标注做平滑（例如开关成本较高时，孤立的
   单人插话会被并入相邻说话人）；
6. 锁定索引越界（含负数）、同一 `word_index` 重复锁定均返回 422。

### 响应示例（200）

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",   "speaker": "A"},
    {"start_ms": 1800, "end_ms": 3600, "text": "我是小明。", "speaker": "A"},
    {"start_ms": 3600, "end_ms": 5400, "text": "谢谢。",     "speaker": "B"},
    {"start_ms": 5400, "end_ms": 7200, "text": "下期再见。", "speaker": "B"}
  ],
  "changed_word_indices": [3],
  "total_cost": 3
}
```

> 上例锁定词 2 为 `B`。保留原标注 `AABA` 需 2 次相邻换人（成本 4）；把词 3 也
> 改为 `B`（`AABB`）只需 1 次改动 + 1 次换人（成本 3）；全部改为 `B`（`BBBB`）
> 需 3 次改动（成本也是 3）——同成本取字典序最小的 `AABB`。
> `changed_word_indices` 为说话人被改动的词序号（0-based，升序）。

### curl 示例

```bash
curl -s http://localhost:8000/speaker-reconcile \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1800,"text":"大家好。","speaker":"A"},{"start_ms":1800,"end_ms":3600,"text":"我是小明。","speaker":"A"},{"start_ms":3600,"end_ms":5400,"text":"谢谢。","speaker":"B"},{"start_ms":5400,"end_ms":7200,"text":"下期再见。","speaker":"A"}],"locks":[{"word_index":2,"speaker":"B"}],"switch_cost":2}'
```

## 错误响应（HTTP 422）

统一形如：`{"error": {"code": "...", "message": "...", ...}}`

| `code` | 触发条件 | 附加字段 |
| --- | --- | --- |
| `invalid_request` | 请求体结构/字段类型不合法（含空 `words`、`switch_cost` 越界） | `errors` |
| `invalid_time_range` | 某个词 `end_ms < start_ms` | `word_index` |
| `time_out_of_order` | 相邻词 `start_ms` 倒序 | `word_index` |
| `overlapping_time_range` | 某词 `start_ms` 早于前词 `end_ms`（重叠） | `word_index` |
| `infeasible_segmentation` | 不存在满足全部约束的分段 | `last_word_index` |
| `infeasible_ad_breaks` | 不存在满足全部约束的插播方案 | `break_count`、`candidate_count` |
| `lock_index_out_of_range` | 锁定词序号越界（含负数） | `word_index` |
| `duplicate_lock` | 同一词序号被重复锁定 | `word_index` |
| `too_many_speakers` | 候选说话人（原标注 ∪ 锁定值）超过 8 个 | `speaker_count` |

### 无可行分段

当任何切分方式都无法满足硬约束时，返回 422，并在 `last_word_index` 中给出
**最早无法到达的前缀末词序号（0-based）**：即最小的满足“前 k 个词无法被任何合法
分段完整覆盖”的 k，其末词序号为 `k-1`。

```bash
curl -s http://localhost:8000/segments \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":800,"text":"太短了","speaker":"A"}]}'
```

```json
{
  "error": {
    "code": "infeasible_segmentation",
    "message": "不存在满足全部约束的分段方案",
    "last_word_index": 0
  }
}
```

（该词仅 800ms 且无法与前后词组合成 ≥1000ms 的段。）

## 算法说明

两趟动态规划 + 贪心重建，**O(n) 时间、O(n) 空间**：

1. **段可行性枚举**：每段至多 18 个词（每词至少 1 个字符），以任一词为段首时
   可行段末词至多 18 个，且利用字符数、时长随段长单调的性质提前剪枝；
2. **前向 DP**：`fwd[t]` = 恰好覆盖前 t 个词的最小总分；
3. **后向 DP**：`bwd[i]` = 覆盖后缀 `[i,n)` 的最小总分；
4. **贪心重建**：最优总分为 `fwd[n]`。从边界 0 出发，在每个当前边界枚举可行下一段：
   - 若该段直达末尾（成为末段）且总分为最优，则直接结束——它的断点序列是任何
     “继续切分”方案的严格前缀，标准字典序下最小；
   - 否则在所有能以最优总分补全后缀的候选中，取**最小的下一个断点**。
   每步贪心都在保持总分最优的前提下让断点序列的当前元素最小，归纳可得全局
   字典序最小；无任何候选则该前缀不可达。

> 早期版本曾把整条断点序列保存在 DP 状态里，会退化为 O(n²)；当前实现每状态
> 只存一个分数，字典序裁决全部推迟到重建阶段，10 万词约数秒、内存约 60MB。

广告插播点选择（`ad_breaks.py`）同样为动态规划，**O(m·k) 时间/空间**
（m 为候选数、k = break_count ≤ 5）：候选静音区间的中点随词序严格递增，
先用双指针扫出每个候选之后第一个满足 `min_spacing_ms` 的下标，再按
“恰选 j 个”分层做后缀最大 DP，最后从左向右贪心重建——每步取仍能达成
最优总静音的最小下标，即得字典序最小的时间序列。

说话人校正（`speaker_reconcile.py`）也是动态规划，**O(n·m) 时间/空间**
（m 为候选说话人数，≤ 8）：`bwd[i][k]` 表示词 i 指派为第 k 个候选说话人时
后缀 `[i, n)` 的最小代价（含词 i 的改动代价与其后全部换人代价），锁定词只
允许取锁定值。转移利用“不换人代价为 0、换人统一为 `switch_cost`”的结构，
每行预求最小值与次小值即可 O(m) 完成；重建阶段从左向右贪心，每步在仍能
达到最优总成本的候选中取字典序最小的说话人，归纳可得整条序列字典序最小。

## 测试

核心算法只依赖 Python 标准库，无需安装任何第三方包即可运行：

```bash
python3 -m unittest discover -s tests -v
```

在容器内（含 fastapi/httpx）会额外执行 HTTP 接口层测试：

```bash
docker compose run --rm api python -m unittest discover -s tests -v
```

## 项目结构

```
app/
  __init__.py
  main.py               # FastAPI 路由、异常处理（422 统一错误体）
  schemas.py            # 请求/响应 Pydantic 模型
  segmentation.py       # 输入校验 + 动态规划核心（纯标准库）
  ad_breaks.py          # 广告插播点选择（纯标准库，复用同一套输入校验）
  speaker_reconcile.py  # 说话人校正（纯标准库，复用同一套输入校验）
  srt.py                # SRT 渲染
  errors.py             # DomainError
tests/             # unittest：核心算法、暴力对拍、HTTP 接口层
  test_segmentation.py
  test_ad_breaks.py
  test_speaker_reconcile.py
  test_differential.py
  test_api.py
Dockerfile
docker-compose.yml
requirements.txt
```
