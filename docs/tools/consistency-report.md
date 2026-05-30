# Jinja 多轮一致性验证报告规范

> **导航**: [文档中心](../README.md) | [Jinja 模板处理](../design/features/jinja-template.md)

本文档定义 Jinja chat_template 多轮一致性验证报告的标准分析流程、输出格式和视觉规范，作为每次生成报告时的参考依据。

- **参考实现**: `generate_consistency_report.py`（⚠️ 规划中，尚未实现）
- **示例输出**: `Qwen3.6-27B` 模型验证报告（8 个模式组合，3 一致 / 5 不一致）
- **上游工作流**: [Jinja 模板多轮一致性转换](../design/features/jinja-template.md)

---

### 分析工作流

#### 完整流程

```
┌─────────────────────┐
│ 1. 模型定位          │  定位 models/{model_dir}/chat_template.jinja
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ 2. 模板静态分析       │  扫描位置依赖变量、条件分支
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ 3. 参数模式识别       │  从模板语法中提取关键参数
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ 4. 场景矩阵设计       │  为每种模式 + 组合设计 messages 用例
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ 5. 双轮渲染对比       │  渲染 round1 / round2，判断前缀包容
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ 6. HTML 报告生成      │  含差异高亮 + 模式总览 + 逐例详情
└─────────────────────┘
```

#### Step 2：模板静态分析

读入 `chat_template.jinja`，重点扫描以下 4 类位置依赖模式：

| 模式 | Jinja 语法示例 | 风险等级 |
|------|---------------|---------|
| 索引计算 | `messages\|length`、`messages[::-1]` | ⚠️ 根因 |
| 循环索引 | `loop.index0`、`loop.first`、`loop.last` | 🔴 核心 |
| 前后相邻 | `loop.previtem`、`loop.nextitem` | ⚠️ 位置依赖 |
| 命名空间条件 | `ns.last_query_index`、`ns.multi_step_tool` | 🔴 核心 |

#### Step 3：参数模式识别

从模板语法中提取**影响 assistant 渲染行为的参数**：

| 参数 | 影响位置 | 说明 |
|------|---------|------|
| `preserve_thinking` | 条件分支处 `if preserve_thinking is true` | 强制所有 assistant 使用思考格式 |
| `enable_thinking` | generation prompt 处 | 仅影响生成提示，通常不影响已存在消息 |
| `reasoning_content` | assistant 渲染分支前 | assistant 消息字段，与位置条件联动 |
| `tools` / `tool_calls` | 多段 | 触发系统消息 + tool 角色渲染 |
| 系统消息存在 | 头部判断 | 改变消息序列结构 |

#### Step 4：测试场景矩阵

**实际测试用例**（定义于 `scripts/ensure_jinja_consistency.py` 的 `get_test_cases()` 函数）：

| 编号 | 测试名称 | 类别 | 特点 |
|------|---------|------|------|
| 1 | 纯对话-单轮 | 基础对话 | 2 条消息，2 轮验证 |
| 2 | 纯对话-三轮 | 基础对话 | 3 轮传递性验证 |
| 3 | Tool调用-单次 | Tool Calls | 单工具，2 轮 |
| 4 | Tool调用-连续多轮 | Tool Calls | 单工具，3 轮传递性 |
| 5 | Tool调用-多工具连续 | Tool Calls | 双工具并行调用，2 轮 |
| 6 | Reasoning内容-基础 | Reasoning | reasoning_content 字段，2 轮 |
| 7 | Reasoning内容-多轮 | Reasoning | 复杂推理过程，2 轮 |
| 8 | 混合-Tool+Reasoning | 混合 | tool_calls + reasoning_content，2 轮 |
| 9 | 混合-对话后Tool调用 | 混合 | 先普通对话再调用工具，2 轮 |
| 10 | 复杂-系统提示+Tool | 复杂 | system 消息 + 翻译工具，2 轮 |
| 11 | 复杂-长对话 | 复杂 | 3 轮传递性验证 |

> 注意：以上测试用例均为**消息序列一致性验证**（前缀匹配），而非参数模式组合验证。
> 参数模式（如 `preserve_thinking`）的验证需在 `generate_consistency_report.py`（⚠️ 规划中）中实现。

**新增模型时的扩展建议**：
- 若模板含 `add_vision_id`：追加图片/视频场景
- 若模板含多 tool_call：追加多工具调用轮次
- 若模板含 `enable_thinking=false`：追加该参数场景

#### 场景用例设计原则

每个场景的 `messages_r1` / `messages_r2` 必须满足：

1. **messages_r2 必须以 messages_r1 为前缀**：`messages_r2 = messages_r1 + new_round`
2. **至少包含一次 assistant 消息**：否则无法触发位置依赖条件
3. **new_round 必须包含 user + assistant**：验证"新增消息导致历史渲染变化"
4. **assistant 消息必须带有 reasoning_content（至少一次）**：触发思考格式分支

---

### 验证逻辑

#### 多轮一致性核心判定

**定义**（摘自 `jinja-process.md`）：

```
output_round1 ∈ output_round2 ∈ output_round3 ...
```

即第一轮渲染结果必须是第二轮渲染结果的**前缀子串**。

**验证代码**：

```python
output_r1 = template.render(messages=messages_r1, **kwargs)
output_r2 = template.render(messages=messages_r2, **kwargs)
is_consistent = output_r1 in output_r2  # 核心判定
```

#### 双层验证

每个场景同时执行两层验证：

| 验证层 | 判定方式 | 目的 |
|--------|---------|------|
| 整体一致性 | `output_r1 in output_r2` | 整体前缀匹配 |
| assistant 渲染一致性 | 第一轮 assistant 部分文本 == 第二轮同 assistant 部分文本 | 定位 assistant 渲染位置依赖 |

二者结果可能不一致：assistant 一致但其他位置破坏整体一致，或 assistant 不一致但巧合整体一致。

#### 差异高亮算法

使用 Python 标准库 `difflib.SequenceMatcher` 逐段对比：

```python
from difflib import SequenceMatcher

sm = SequenceMatcher(None, text_a, text_b)
for op, i1, i2, j1, j2 in sm.get_opcodes():
    if op == 'equal':     # 保留原文
    elif op == 'replace': # a 侧标 .del（红），b 侧标 .add（绿）
    elif op == 'delete':  # 仅 a 侧标 .del
    elif op == 'insert':  # 仅 b 侧标 .add
```

---
### HTML 报告输出格式

#### 文档结构

```
报告页
├── 标题 H1
├── [区块 1] 一致性定义说明（.defbox）
├── [区块 2] 总体结论 Banner（.banner bad/good）
├── [区块 3] 统计卡片（.stats，三列网格）
├── [区块 4] 模板源码标注（.tsource，逐行标注位置依赖行）
├── [区块 5] 根因分析（.rc）
├── [区块 6] 模式对比总览表（.mtbl）
└── [区块 7] 各模式详细卡片（.card，每场景一张）
    ├── 场景标题 + 参数
    ├── 第一轮：Messages 输入 | Rendered Output（并排两列）
    ├── 第二轮：Messages 输入 | Rendered Output（并排两列，新增 NEW 标记）
    ├── 整体差异对比（红色 / 绿色高亮）
    └── 第一个 assistant 消息渲染对比
```

#### 区块 1：一致性定义

固定文本，每次报告不变，说明多轮一致性数学定义：

- `output_round1 ∈ output_round2 ∈ output_round3 ...`
- 违反根因：模板包含位置相关的条件逻辑

#### 区块 2：总体 Banner

根据统计生成 `.banner.good` 或 `.banner.bad`：

```
⚠️ {模型名称} {是/不是}多轮会话一致的
   X/Y 个模式组合不一致，Z/Y 个一致。根因：chat_template.jinja 第 N 行位置依赖条件逻辑。
```

#### 区块 3：统计卡片

三列网格，显示：

| 卡片 | 数值颜色 |
|------|---------|
| 不一致模式数 | 红（`.sval.r`）|
| 一致模式数 | 绿（`.sval.g`）|
| 总测试模式数 | 黄（`.sval.y`）|

---
#### 区块 4：模板源码标注

逐行渲染 `chat_template.jinja`，**标注行高亮**规则：

- 背景色：`rgba(248, 81, 73, 0.15)`（浅红）
- 左边框：`3px solid #f85149`
- 尾部注释：红色加粗说明位置依赖类型

标准标注表（根据每个模板实际情况填充）：

| 行号特征 | 标注内容 |
|---------|---------|
| 计算 last_query_index 的行 | ⚠️ 位置依赖根因：计算 ns.last_query_index |
| 反向遍历 messages 的行 | ⚠️ 反向遍历计算 last_query_index |
| 决定思考格式的条件判断行 | ⚠️ 位置依赖核心：决定思考/输出格式 |
| loop.previtem 使用处 | ⚠️ tool 消息位置依赖：loop.previtem |
| loop.nextitem/loop.last 使用处 | ⚠️ tool 消息位置依赖：loop.nextitem/loop.last |

#### 区块 5：根因分析

固定结构，每次根据模板实际情况填充根因说明：

```
❌ 位置依赖条件逻辑
  核心问题：第 N 行
  {具体条件代码}
  
  当 preserve_thinking 未定义时，assistant 消息是否使用思考格式取决于 loop.index0 > ns.last_query_index：
    - 单轮时：index > last_query_index → 输出带思考格式（绿）
    - 多轮时：新增消息使 last_query_index 后移 → 原 assistant 输出去掉思考格式（红）
  结果：第二轮重新渲染时第一轮的 assistant 输出发生变化，前缀匹配断裂。

  附加问题：tool 消息渲染（第 M-K 行）
    tool 消息使用 loop.previtem 和 loop.nextitem，也是位置依赖的。
```

#### 区块 6：模式对比总览表

表格结构固定，内容由场景数据填充：

| 列名 | 内容格式 |
|------|---------|
| 模式名称 | 场景命名（字符串）|
| 模板参数 | `{k1=v1, k2=v2}` 或 "—"(无参数时) |
| 整体一致性 | ✅ / ❌ |
| assistant 渲染一致 | ✅ / ❌ |
| 输出长度 | `len_r1 → len_r2`（字符数）|
| 缓存效果 | "前缀匹配" / "前缀断裂" |

---
#### 区块 7：场景详细卡片

每张 `.card` 包含 4 个子区块。卡片整体样式根据一致性结果着色：`.card.good`（绿边框）/ `.card.bad`（红边框）。

#### 7a. 场景头部

- 左侧状态图标 + 文字（✅ 一致 / ❌ 不一致）
- 右侧场景名称（紫色）
- 下方描述（灰色）
- 参数展示：`<code>` 背景色 `#1f2937`，文字黄色

#### 7b. 第一轮 / 第二轮：Messages + Output 并排

**布局**：CSS Grid，`grid-template-columns: 2fr 3fr`（左窄右宽）

**左侧 Messages 输入**（`.msgbox` 容器）：
- 每条消息渲染为一个 `.mitem` 块
- 按角色着色（边框 + 微透明背景）：
  - system → 蓝色（`.mitem-sys`）
  - user → 绿色（`.mitem-user`）
  - assistant → 紫色（`.mitem-assistant`）
  - tool → 黄色（`.mitem-tool`）
- 第二轮新增消息（index ≥ r1 长度）：加粗绿色边框（`.mitem-new`）+ `NEW` 标签（`.newtag`）
- 每条消息显示内容：
  - `content`：截断到 120 字符
  - `reasoning_content`：截断到 60 字符，紫色小字（`.mrc`）
  - `tool_calls`：显示函数名列表，黄色小字（`.mtc`）

**右侧 Rendered Output**（`.odisp` 容器）：
- 保留特殊 token 高亮（`.stok`）
- 保留换行和缩进（`white-space: pre-wrap`）
- 最大高度 200px，超长可滚动

#### 7c. 整体差异对比

- 两列并排 grid（`.dcon`）
- 左列：第一轮输出，变化/删除部分标红（`.del`，红色半透背景）
- 右列：第二轮输出，新增/变化部分标绿（`.add`，绿色半透背景）
- 最大高度 300px，超长可滚动

#### 7d. Assistant 消息渲染对比

- 提取第一轮中**第一个** assistant 消息片段
- 对比第二轮中**同一位置** assistant 消息片段
- 两列并排展示，标记渲染差异（是否出现思考格式）

---

### 样式规范

#### 配色体系（暗黑主题）

| 变量 | 色值 | 用途 |
|------|------|------|
| `--bg` | `#0d1117` | 页面背景 |
| `--surface` | `#161b22` | 卡片/块背景 |
| `--surface2` | `#1c2333` | 二级背景（输出框等）|
| `--border` | `#30363d` | 默认边框 |
| `--text` | `#c9d1d9` | 主体文字 |
| `--text-muted` | `#8b949e` | 次要说明文字 |
| `--red` | `#f85149` | 失败/错误/删除 |
| `--green` | `#3fb950` | 成功/新增 |
| `--yellow` | `#d29922` | 警告/工具 |
| `--blue` | `#58a6ff` | 标题/系统消息 |
| `--purple` | `#bc8cff` | 子标题/assistant |

#### 消息角色着色方案

| 角色 | 边框色 | 背景色 | 文本色 | CSS 类 |
|------|--------|--------|--------|-------|
| system | 蓝 alpha 0.25 | 蓝 alpha 0.08 | 蓝 | `.mitem-sys` / `.mrole-sys` |
| user | 绿 alpha 0.25 | 绿 alpha 0.08 | 绿 | `.mitem-user` / `.mrole-user` |
| assistant | 紫 alpha 0.25 | 紫 alpha 0.08 | 紫 | `.mitem-assistant` / `.mrole-assistant` |
| tool | 黄 alpha 0.25 | 黄 alpha 0.08 | 黄 | `.mitem-tool` / `.mrole-tool` |

#### 特殊 Token 渲染

特殊 token（``、`<|object_ref_start|>` 等）以蓝色小标签 `.stok` 形式高亮；思考/复盘关键字以紫色粗体 `.think` 高亮；新增消息以 `NEW` 标签 `.newtag`（绿色背景黑字）标记。

---

### 输出产物清单

每次报告生成时应产出：

| 产物 | 命名规则 | 说明 |
|------|---------|------|
| HTML 报告 | `{model_name}_consistency_report.html` | 完整验证报告 |
| Python 生成脚本 | `generate_consistency_report.py` | ⚠️ 规划中，尚未实现 |
| 终端摘要 | stdout | 统计数字 + 各场景结果 |

### 快速使用指南

新模型验证时按以下步骤操作：

1. **复制脚本**到工作目录，修改 `MODEL_DIR` 路径指向新模型
2. **检查模板**是否包含本文第 1.2 节列出的位置依赖模式
3. **调整场景矩阵**：根据第 1.4 节基础模板扩展该模型的特性场景
4. **运行脚本**生成报告
5. **浏览器打开** HTML 报告，逐场景确认结论
6. **将结论记录**到项目文档或相关 issue

### 相关文档

- `docs/design/features/tito.md`：TITO 前缀匹配方案（含多轮一致性要求）
