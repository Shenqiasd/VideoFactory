---
planStatus:
  planId: plan-20260313-fix-subtitle-segmentation-translation
  title: Fix incomplete subtitle segmentation after translation
  status: ready-for-development
  planType: bug-fix
  priority: high
  owner: codex
  stakeholders: []
  tags:
    - subtitles
    - translation
    - srt
    - readability
    - videofactory
    - semantic-boundary
  created: "2026-03-13"
  updated: "2026-03-13T01:05:00.000Z"
  progress: 100
---
# 修复翻译后字幕断句不完整问题

## 决策结论
- 修复范围：**字幕全链路**
  - 覆盖 `target_language_srt.srt`
  - 覆盖 `bilingual_srt.srt`
  - 覆盖 `GlobalTranslationReviewer` 二次改写后的回投
- 策略偏好：**语义优先**
  - 允许少量 cue 长短不均
  - 优先避免拆坏标题、书名号内容、固定短语、引号/括号内容

## 问题概述
当前项目已经引入 `SentenceRegrouper`，会先把连续碎片 cue 合并后再统一翻译，目标是减少逐行翻译带来的语义断裂。
但实际产物里仍然会出现“翻译结果被重新切回原 cue 时切坏短语/书名/专有名词”的情况，例如：

- `名为《天堂阶`
- `梯》 [`
- 对应原文被切成 `called Stairway to` / `Heaven`

这说明问题不在“有没有先合并再翻译”，而在“翻译后如何重新投影回原始 cue”。

## 已确认的现状
### 相关代码路径
1. `src/production/pipeline.py:813`
  - 调用 `SentenceRegrouper.translate_entries(...)`
2. `src/production/sentence_regrouper.py:229`
  - 先把多个 cue 合成 group，统一送翻译
3. `src/production/sentence_regrouper.py:238`
  - 调用 `project_translation(...)` 把整句翻译重新拆回原 cue 数量
4. `src/production/global_translation_reviewer.py:1065`
  - 全局复审改写后仍复用同一个 `project_translation(...)`
5. `src/production/subtitle_repair.py:769`
  - 现有 repair 只关注漏翻、英文残留、低中文占比，不负责“短语是否被切断”

### 当前根因
`src/production/sentence_regrouper.py:173` 的 `project_translation(...)` 目前采用：
- 按原始 cue 的字符/词数计算权重
- 按目标权重大致切分翻译文本
- 仅在“空格/中文标点/逗号等边界字符”附近寻找切点

这个策略能保证：
- cue 数量不变
- 每个 cue 大致有字

但不能保证：
- 不把 `《...》`、引号、括号包裹内容切开
- 不把英文标题、专有名词、固定短语切成半句
- 不生成过短尾巴（如 `梯》`、`to`、`Heaven` 这种残片）

因此，当前残留断句是“长度拟合优先”造成的，不是翻译模型本身一定翻错。

## 为什么现有方案仍会失败
### 1. 回投阶段只做“长度近似”，不做“语义边界保护”
`project_translation(...)` 的切点搜索只看邻近边界字符，不知道某段文字是否属于：
- 书名号 `《...》`
- 引号 `“...”` / `'...'`
- 括号 `(...)` / `[...]`
- 英文标题或固定短语

所以即使整句翻译是正确的，也会在回投时被拆坏。

### 2. 全局复审阶段复用了同样的投影算法
即使 `GlobalTranslationReviewer` 把 group 文本整体润色好了，最后仍然调用相同的 `SentenceRegrouper.project_translation(...)`，因此问题会再次出现。

### 3. 后处理没有“断句完整性”校验
`SubtitleRepairer` 目前更像“漏翻/残留英文修复器”，并不会识别：
- 某行是否只剩半个标题
- 某行是否以不应独立存在的介词/连词结尾
- 某行是否把配对标点拆开了

## 修复目标
### 必须达到
- 不再把 `《...》`、成对引号、成对括号内部文本拆开
- 尽量避免把英文标题/固定短语切成无意义碎片
- 保持现有 cue 数量不变，兼容后续 SRT、双语字幕、review、QC 链路
- 不降低已有的“碎片合并翻译”收益
- 主翻译与全局复审两条回投路径统一受益

### 尽量达到
- 避免生成非常短的尾段/首段（如 1~3 个字符、单个介词、半个词）
- 若无法完美平均长度，优先保证语义完整，再做长度平衡

## 方案对比
### 方案 A：增强 `project_translation(...)` 的切点选择（推荐）
在保持现有 pipeline 不变的前提下，升级“回投切分器”：

1. 新增“受保护片段”识别
  - 识别 `《...》`、引号、括号、英文连续词组、数字单位组合等 span
  - 禁止切点落在 span 内部

2. 新增“切点优先级评分”
  - 优先：句末标点后
  - 次优：分句标点后
  - 再次：空格/自然短语边界
  - 最后才允许长度近似切分

3. 新增“残片兜底重平衡”
  - 若切分后某段太短、只剩配对标点一半、或疑似 dangling token，则向前/向后吞并重切

4. 让全链路自动受益
  - `pipeline` 主翻译回投受益
  - `GlobalTranslationReviewer` 回投受益
  - 双语字幕不会继续携带被切坏的中文行

**优点**
- 改动集中
- 风险低
- 兼容现有 cue 数量和下游格式
- 能同时修复主翻译和全局复审回投

**缺点**
- 仍然受“必须回写到原 cue 数量”约束，无法做到真正按阅读体验自由排版

### 方案 B：把“翻译”和“字幕排版”彻底分层
先得到 group 级最终翻译，再根据时间轴和阅读规则重新做 cue 级排版，不再严格依赖原 cue 长度比例。

**优点**
- 可得到更自然的字幕阅读体验
- 更接近专业字幕排版

**缺点**
- 影响面大
- 需要重新评估 SRT 时轴、双语字幕对齐、QC 指标
- 不适合先做热修

### 推荐结论
优先落地 **方案 A**，先解决“半句/半个标题被切开”的核心问题；若后续仍需进一步提升观感，再考虑方案 B。

## 拟修改文件
- 修改：`src/production/sentence_regrouper.py`
  - 扩展 `project_translation(...)`
  - 增加 protected span / boundary scoring / short-fragment rebalance
- 补测试：`tests/test_sentence_regrouper.py`
  - 覆盖书名号、括号、英文标题、极短残片重平衡
- 视实现情况补测试：`tests/test_subtitle_repair.py`
  - 若需要验证 repair 后链路不回退，可追加集成样例
- 视实现情况补测试：`tests/test_global_translation_reviewer.py`
  - 若已有对应测试入口，可补“review 后回投不拆坏短语”的回归测试

## 具体实施步骤
### Task 1：补充失败用例，锁定问题
- 在 `tests/test_sentence_regrouper.py` 增加回归用例：
  - `献上了一首气势磅礴的乐曲，名为《天堂阶梯》` 不应被切成 `《天堂阶` / `梯》`
  - `Stairway to Heaven` 对应的中文标题不应被拆成不完整标题
  - 若出现括号/引号，不能把开闭符号拆到不同 cue
- 若已有 reviewer 回归测试入口，再补一条 `review -> project_translation` 路径回归

### Task 2：为回投切分增加“保护 span”能力
- 在 `src/production/sentence_regrouper.py` 增加：
  - 配对标点 span 扫描
  - 英文连续词组 span 识别
  - 数字/单位/版本号保护
  - `cut` 候选点合法性过滤（span 内禁切）

### Task 3：为候选切点增加优先级评分
- 改造 `_candidate_split_indexes(...)` / `_find_split_index(...)`：
  - 给候选点打分，而不是只按“离 target 最近”
  - 优先自然语义边界，再考虑长度接近度
  - 对“标题结束后、短语结束后、闭合符号后”的切点给更高优先级

### Task 4：增加短残片/悬挂 token 重平衡
- 在 `project_translation(...)` 末尾增加检查：
  - 单个介词/冠词/连词
  - 极短中文尾巴
  - 只有闭合符号或半个标题
  - 开闭标点不配对
- 若命中，则与相邻段做局部重切

### Task 5：验证全链路兼容性
- 确认以下复用方无行为回退：
  - `src/production/pipeline.py`
  - `src/production/global_translation_reviewer.py`
  - 双语字幕输出格式
  - `target_language.txt` 聚合文本不会因重平衡出现回退

## 测试与验证
### 最小回归
- `python3.11 -m pytest -q tests/test_sentence_regrouper.py`

### 推荐补充
- `python3.11 -m pytest -q tests/test_subtitle_repair.py`
- `python3.11 -m pytest -q tests/test_global_translation_reviewer.py`

### 项目硬门槛
- `python3.11 -m pytest -q`

### 人工验收样例
重点验证这类输入：
- 英文标题被切成多 cue：`called Stairway to` / `Heaven`
- 中文标题落在书名号内：`《天堂阶梯》`
- 括号说明：`(live version)` / `[Applause]`
- 引号内容：`"hello world"`
- 书名号 + 方括号 + 英文原文并存的双语字幕样例

验收标准：
- 不出现 `《天堂阶` / `梯》` 这类残片
- 不出现仅含 `to`、`the`、`and` 之类的孤立尾段
- 不出现开闭标点拆散到不同 cue
- cue 数量保持不变
- 原有通过测试不回退

## 风险与兼容性
- 若保护规则过强，可能导致个别 cue 长短不均
- 若英文词组保护过宽，可能压缩邻近 cue 可分配空间
- 因此实现时要优先局部重平衡，而不是全局硬保护

## 结论
这不是“模型没翻对”为主的问题，而是“翻译后回投到原 cue 时的切分算法过于按长度分配、缺少语义边界保护”。
最小且正确的修复点是：**增强 \****`SentenceRegrouper.project_translation(...)`**\*\*，让它在回投阶段优先保证短语完整性，而不是只追求长度均分。**


## 外部调研（2026-03-13）
### 1. 专业字幕行业并不是“按原 cue 生硬回写”
当前较成熟的做法，普遍是把“翻译”和“字幕排版/断句”分成两个问题处理：
- 先得到更完整的句子级/短语级翻译
- 再依据字幕规范重新做 subtitle segmentation / line breaking / alignment

这和我们当前“先 group 翻译，再硬按原 cue 数量切回去”的思路接近，但我们少了成熟方案里的“语法边界 + 风格约束 + 对齐惩罚”层。

### 2. 行业规范非常明确：断句要服从语义/句法边界
检索到的专业规范与研究结论高度一致：
- Netflix Timed Text Style Guide：优先在标点后断；在 conjunction / preposition 前断；不要把不该拆开的语言单位拆开
- 学术研究（eye-tracking / subtitle segmentation）总结：应保持 noun phrase、prepositional phrase、verb phrase、first name + last name 等单位完整
- 这正对应你现在遇到的问题：`Stairway to Heaven` / `《天堂阶梯》` 本质上都是“应保持完整的语言单位”

### 3. 开源工具里最成熟的是“规则集 + 自动平衡”，不是只靠长度均分
#### Subtitle Edit
- 有现成的 `Auto br` / `Auto balance selected lines` / `Fix common errors`
- 支持 `do-not-break-after` 词表
- 社区讨论也明确在处理短残片时，会参考“不要在某些词后断开”“短行并到前后句”等规则

这说明成熟工具的核心不是单一算法，而是：
- 候选切点
- 语言特定 no-break 规则
- 短残片合并/重平衡

#### Helsinki-NLP `subalign`
- `mt2srt` 明确就是“把句子级翻译重新投影到模板字幕时间轴”
- 它不是简单按长度切，而是用带约束的长度对齐算法，并对“句内结束”施加 penalty

这和我们的问题最相关：成熟方案不会默认“离长度目标最近”就是最好切点，而是会把“在句内硬切开”视为坏事并惩罚。

#### VideoLingo
- 对外文档明确写了：用 NLP + LLM 做 intelligent subtitle segmentation
- 技术文档说明有专门的 `split_sub` 步骤，把翻译字幕拆成适合显示的片段，并用 GPT 做与源字幕的对齐
- 它还强调输出是 Netflix-standard single-line subtitles only

这说明当前比较新的 AI 视频翻译项目，也普遍不会直接保留 ASR 原始碎片；而是会显式做一次“适合显示”的重切分。

## 对本项目的启发
### 可以直接借鉴的成熟思路
1. **采纳 Netflix / BBC / 学术规则作为硬约束**
   - 不拆书名号、引号、括号内容
   - 不拆介词 + 宾语、冠词 + 名词、固定短语、姓名、标题

2. **采纳 Subtitle Edit 风格的 no-break 词表 + 短残片回收**
   - 给英文 `to / the / a / an / and / or / of / in / on ...` 这类词设置 no-break 规则
   - 对 1~3 字中文残片、孤立介词、孤立闭合符号做局部重平衡

3. **把当前“长度最近优先”升级为“代价最小优先”**
   - 参考 `subalign`：
     - 长度偏差有 penalty
     - 在受保护短语内部切开有更高 penalty
     - 在非句末/非自然边界切开有 penalty
   - 最终选总代价最小的切分，而不是最近切点

4. **必要时加入 LLM/NLP 辅助分句，但不作为第一步热修依赖**
   - 这更像 VideoLingo 路线
   - 质量高，但成本和复杂度也高
   - 更适合作为第二阶段优化，而不是第一阶段修复

## 更新后的推荐路线
### 推荐路线 R1：规则/代价驱动的回投器（最务实）
这是最像“借成熟轮子思想、但不重型引第三方”的方案：
- 用 Netflix/BBC/学术规则定义 no-break 约束
- 用 Subtitle Edit 风格的短残片合并思路兜底
- 用 `subalign` 风格 penalty 代替现有“最近切点”

### 可选路线 R2：引入外部组件或直接参考实现
如果你坚持“尽量别自己实现”，下一步可继续评估：
- 是否能直接复用 `subalign` 的对齐逻辑
- 是否能参考/移植 VideoLingo 的字幕重切分策略
- 是否能导入 Subtitle Edit 的语言规则/词表思路

但就工程落地看，**直接整合完整外部工具的成本和耦合风险，通常高于把它们的成熟规则抽取到本项目现有 `SentenceRegrouper` 中。**

## 深挖结论：`subalign` 与 `VideoLingo` 哪些值得直接移植
### `subalign`：最值得移植的是“代价函数 + 动态规划”，不是整工具
已确认 `subalign` 的 `mt2srt` 核心特点：
- 输入是句子级翻译 + 模板字幕时间轴
- 输出是重新对齐到模板 SRT 的目标字幕
- 不是按长度最近切点，而是用动态规划做最优对齐
- 明确存在这些参数：
  - `non-eos-penalty`：惩罚“在句中结束”的切分
  - `soft length` / `hard length`：长度软/硬约束
  - `length penalty`：超长惩罚
- README 和源码都说明：算法是 length-based alignment with additional constraints

对本项目的直接价值：
1. 可以直接借鉴其“总代价最小”思想，替代当前最近切点启发式
2. `non-eos-penalty` 非常契合我们当前问题：
   - 可以把“切在短语内部 / 标题内部 / 成对标点内部”抽象为更高 penalty
3. 其动态规划框架天然适合“固定输出 cue 数量”的约束场景

不建议直接整合的点：
- `subalign` 是 Perl 工具，直接引入工程成本和维护成本偏高
- 它更偏“句段对齐”，不直接包含我们需要的中文书名号 / 英文标题 / 孤立介词等细规则
- 如果整工具接入，反而会把我们现有 Python 流程复杂化

**结论**：
- **值得直接移植的不是源码整体，而是它的 scoring / dynamic-programming 思路**
- 特别适合把当前 `project_translation(...)` 从“最近切点”升级为“最小总代价切分”

### `VideoLingo`：最值得移植的是“两阶段切分 + 目标语重对齐”流程，不是它的 LLM 依赖本身
已确认 `VideoLingo` 的公开实现是明显的两阶段：
1. `_3_1_split_nlp.py`
   - 先用 spaCy / NLP 规则做第一轮断句
2. `_3_2_split_meaning.py`
   - 对长句再用 LLM 按语义切分
3. `_5_split_sub.py`
   - 如果字幕仍超长，再对源句先切，再让 LLM 把目标语按这些切点重新对齐

其中最值得关注的实现细节：
- `split_sentence(...)`
  - 先让模型给出多个带 `[br]` 的候选分法
  - 再从中选最好方案
- `find_split_positions(...)`
  - 不直接相信 LLM 给出的切点位置，而是把建议映射回原文字符位置
- `align_subs(...)`
  - 源语已切后，再让模型生成目标语各片段的对齐版本
  - 必要时允许轻微改写，以保证观感和对齐
- `split_for_sub_main()`
  - 会多轮重复切分，直到长度约束满足

对本项目的直接价值：
1. **流程价值很高**：
   - 先做源语语义切分
   - 再做目标语对齐切分
   - 最后检查长度是否满足，不满足继续重切
2. **局部思想可移植**：
   - “源句先切，再让目标语按切点对齐” 比我们现在“目标语自己按长度回切”更稳
3. **可作为第二阶段增强**：
   - 当规则法解决 80% 问题后，再加 LLM 对齐补偿剩余复杂案例

不建议直接整合的点：
- VideoLingo 当前这套切分/对齐高度依赖 LLM prompt + JSON 输出稳定性
- 项目自己也承认弱模型时会因为 JSON 不稳而出错
- 直接照搬会引入额外成本、时延、模型依赖和失败模式
- 它的目标是“Netflix 单行字幕”，而我们当前链路首先要解决的是“回投不切坏”，不是全面重建整套字幕生产系统

**结论**：
- **值得移植的是它的流程结构，不值得直接移植的是它的 LLM 强依赖实现**
- 对我们最现实的用法，是把它作为二阶段策略：
  - 第一阶段：规则/代价驱动修复当前回投器
  - 第二阶段：对仍然高风险的复杂句，增加可选 LLM 对齐重切

## 最终判断
### 可直接移植（高价值、低风险）
1. `subalign` 的 penalty-based dynamic programming
2. `subalign` 的 soft/hard length + non-eos penalty 思路
3. `VideoLingo` 的“两阶段：先源语切分，再目标语对齐”流程思想
4. `VideoLingo` 的“多轮切分直到长度满足”兜底机制

### 不建议直接移植（高成本或高耦合）
1. 直接引入 `subalign` Perl 工具到现有 Python 主链路
2. 直接照搬 `VideoLingo` 的 LLM prompt / JSON 强依赖切分实现
3. 直接把当前项目改造成完整的 Netflix 单行字幕系统

## 更新后的推荐落地方案
### Phase 1（建议先做）
- 在现有 `SentenceRegrouper.project_translation(...)` 内引入：
  - `subalign` 风格的 dynamic programming / penalty scoring
  - no-break 语义约束（书名号、括号、固定短语、介词短语等）
  - short fragment rebalance

### Phase 2（可选增强）
- 对 Phase 1 后仍命中的复杂案例，引入 `VideoLingo` 风格的“源语先切、目标语重对齐”补偿器
- 仅在高风险句子启用，避免把整条主链路变成强 LLM 依赖

## 具体实现设计（供开发直接执行）

### 设计目标
在 **不改变现有主链路接口** 的前提下，重写 `SentenceRegrouper.project_translation(...)` 的“回投切分”内部实现：
- 输入仍然是：
  - `translated_text: str`
  - `source_lines: Sequence[str]`
- 输出仍然是：
  - `List[str]`，长度必须等于 `len(source_lines)`
- 保持 `pipeline`、`GlobalTranslationReviewer`、双语字幕写出逻辑无需改接口即可受益

---

### 一、总体架构
将当前的“贪心找最近切点”替换为 **两阶段切分器**：

#### Stage A：候选切点预处理
在 `translated_text` 上先计算：
1. **token / char 索引信息**
2. **合法切点集合**
3. **受保护 span**
4. **切点特征分数**

输出一个 `CandidateBoundaryMap`，供后续 DP 使用。

#### Stage B：最小总代价切分
在固定要切成 `N=len(source_lines)` 段的约束下，用动态规划求总代价最低的切分方案：
- 代价 = 长度偏差代价 + 非自然边界代价 + 保护区切分代价 + 残片代价
- 最终返回 N 段文本

#### Stage C：局部重平衡兜底
对 DP 结果再做一次轻量 post-pass：
- 处理极短尾巴
- 处理孤立介词/冠词/连词
- 处理未闭合的书名号/括号/引号
- 必要时只在相邻两段间微调边界

---

### 二、拟新增的数据结构
文件：`src/production/sentence_regrouper.py`

#### 1. 边界特征
```python
@dataclass
class BoundaryFeature:
    index: int
    allowed: bool
    strong_break: bool
    medium_break: bool
    weak_break: bool
    inside_protected_span: bool
    after_closing_punct: bool
    before_opening_punct: bool
    ends_with_function_word: bool
    starts_with_function_word: bool
```

用途：描述 `translated_text[:index] | translated_text[index:]` 这个切点是否适合切。

#### 2. 保护 span
```python
@dataclass
class ProtectedSpan:
    start: int
    end: int
    kind: str  # title, quote, bracket, english_phrase, number_unit
```
```

#### 3. DP 结果节点
```python
@dataclass
class SegmentationStep:
    prev_index: int
    cost: float
```
```

---

### 三、受保护 span 设计

#### 1. 必须保护的 span
优先实现这些：
- `《...》`
- `“...”`、`‘...’`
- `(...)`、`[...]`、`【...】`
- 英文连续标题短语，如 `Stairway to Heaven`
- 数字 + 单位 / 版本号，如 `3.5 mm`、`v2.1`

#### 2. 识别方式
##### A. 配对符号 span
对这些成对符号做一次扫描：
- `《》`
- `“”`
- `‘’`
- `()`
- `[]`
- `【】`

规则：
- 找到开闭配对后，记录 `[start, end)` 为 `ProtectedSpan`
- 禁止切点落在 span 内部
- 切在 span 结束后是加分项

##### B. 英文标题/固定短语 span
对连续英文 token 序列识别 span：
- token regex 可复用现有 `_LATIN_WORD_PATTERN`
- 当连续 2+ 个英文词相邻，中间只夹空格/撇号/连字符时，标记为 `english_phrase`

示例：
- `Stairway to Heaven`
- `New York`
- `rock and roll`

注意：
- 这里不是所有英文串都绝对禁切
- 初版可先做“高 penalty”，不是完全 forbid
- 避免太强规则导致整句无法分配

##### C. 数字单位 span
用 regex 标记：
- `\d+(\.\d+)?\s*(mm|cm|kg|%|fps|Hz|MB|GB|TB)`
- `v\d+(\.\d+)+`

---

### 四、合法切点与边界强度

#### 1. 候选切点来源
只考虑这些位置作为候选切点：
- 空格后
- 中文/英文标点后
- 闭合书名号、引号、括号后
- 连字符后（低优先级）

#### 2. 边界强度分级
##### Strong break
代价最低，优先切：
- 句末标点后：`。！？.!?`
- 分号、冒号后：`；;：:`
- 闭合 `》”’)]】` 后

##### Medium break
可接受：
- 逗号、顿号后：`，,、`
- 明显并列分句边界

##### Weak break
最后兜底：
- 普通空格边界
- 连字符边界

##### Forbidden / heavy penalty
- 保护 span 内部
- 打开符号后立即切开
- function word 后断开，如 `to`, `the`, `a`, `an`, `and`, `or`, `of`, `in`, `on`, `for`, `with`

---

### 五、代价函数设计（核心）

#### 总体公式
对于一个切分段 `text[left:right]`，第 `k` 段的局部代价：

```text
segment_cost =
    length_cost
  + boundary_cost(right)
  + fragment_cost(segment)
  + syntax_penalty(segment, boundary)
```

总成本：

```text
total_cost = sum(segment_cost_k)
```

#### 1. `length_cost`
目标：参考原 cue 权重，但不强行均分。

做法：
- 复用现有 `_source_weights(source_lines)` 作为目标长度比例
- 令 `expected_len_k = total_length * cumulative_weight_ratio`
- 某段实际长度过短/过长时给 soft penalty
- 超过上限时给 hard penalty

建议：
```python
soft_deviation = abs(actual_len - expected_len)
length_cost = soft_deviation * alpha
if actual_len < min_len:
    length_cost += short_penalty
if actual_len > max_len:
    length_cost += overlong_penalty
```

#### 2. `boundary_cost(right)`
根据切点特征打分：
- strong break: `0`
- medium break: `+2`
- weak break: `+6`
- inside protected span: `+1000`（近似禁止）
- after function word: `+30`
- before closing punctuation only fragment: `+20`

#### 3. `fragment_cost(segment)`
检测该段是否是糟糕残片：
- 只有 1~3 个中文字符
- 只有 1 个英文词且该词是 function word
- 只有闭合符号或引号尾巴
- 只有 `]`、`》`、`)`、`”` 之类

命中则高 penalty，如 `+40 ~ +120`

#### 4. `syntax_penalty`
初版用轻规则替代 NLP：
- 段尾是 function word：`+35`
- 段首是 function word 且前段可并：`+25`
- 段内出现未闭合成对符号：`+80`
- 切断 `《...》` / `(...)` / `"..."`：`+1000`

---

### 六、动态规划设计

#### 状态定义
设：
- `cuts` = 所有候选切点位置，包含 `0` 和 `len(text)`
- `m = len(cuts)`
- `n = len(source_lines)`

DP 状态：
```text
dp[i][j] = 把前 cuts[i] 个字符切成 j 段的最小代价
```

转移：
```text
dp[i][j] = min(dp[p][j-1] + cost(segment=cuts[p]:cuts[i], part=j))
```
其中：
- `p < i`
- `cuts[p]:cuts[i]` 必须是合法片段
- 保留 `prev[i][j]` 回溯路径

#### 复杂度控制
因为 group 一般不长（现有默认 `max_cues_per_group=4`，文本长度也有限），DP 足够轻：
- 候选切点数通常几十个以内
- `O(m^2 * n)` 可以接受

#### 剪枝
可加轻量剪枝：
- 若剩余字符不足以分配给剩余段数，则跳过
- 若当前段长度已明显超过 hard limit，则跳过
- 若切点在强禁切 span 内，则跳过

---

### 七、局部重平衡 post-pass
即使 DP 完成，也建议保留一个轻 post-pass。

#### 处理规则
对相邻两段 `A | B` 检查：
1. `B` 是否仅为 `梯》` / `to` / `the` / `]` 这类残片
2. `A` 是否以 function word 结尾
3. `A` 是否包含未闭合书名号/括号，而 `B` 正好补闭合

若命中：
- 仅在 `A+B` 内重新搜索一个更优局部边界
- 优先移动到：
  - 闭合符号后
  - 标点后
  - 非 function word 边界后

这一步可最大限度避免一次 DP 因局部 tie-break 不理想而留下坏尾巴。

---

### 八、与现有链路的集成点

#### 1. `pipeline`
文件：`src/production/pipeline.py:813`
- 无需改调用接口
- `SentenceRegrouper.translate_entries(...)` 内部改进即可

#### 2. `GlobalTranslationReviewer`
文件：`src/production/global_translation_reviewer.py:1065`
- 已复用 `SentenceRegrouper.project_translation(...)`
- 因此无需额外改 reviewer 主流程
- 只要 `project_translation(...)` 提升，review 后字幕同步受益

#### 3. `SubtitleRepairer`
文件：`src/production/subtitle_repair.py:769`
- 初版不建议把“断句完整性修复”塞进 repairer
- repairer 仍保持“漏翻/残留英文修复器”职责
- 断句问题应在回投层解决，职责更清晰

---

### 九、测试设计
文件：`tests/test_sentence_regrouper.py`

#### 第一组：现有回归
1. `《天堂阶梯》` 不能被拆成 `《天堂阶` / `梯》`
2. `Stairway to Heaven` 对应中文标题不能拆坏
3. `rock and roll` 这类英文固定短语不能把 `and` 留成尾巴

#### 第二组：成对标点
4. `“这是一个测试”` 不能拆成未闭合引号
5. `(live version)` 不能拆成 `(live` / `version)`
6. `[Applause]` 不能拆成 `[` / `Applause]`

#### 第三组：短残片
7. 不产生仅含 `to` / `the` / `and` 的单独段
8. 不产生仅含 `》` / `]` / `)` 的单独段
9. 不产生仅 1~2 个中文字符的悬挂尾段（允许极少数不可避免情况时放宽）

#### 第四组：长度兼容
10. 输出段数必须等于输入 `source_lines` 数
11. 所有段非空
12. 对原有测试样例不回退

#### 可选补测
文件：`tests/test_global_translation_reviewer.py`
- 增加 reviewer 改写后仍不拆坏标题/短语的回归用例

---

### 十、开发顺序建议
1. 先补失败测试（书名号/括号/function word）
2. 实现 `ProtectedSpan` 扫描
3. 实现 `BoundaryFeature` 提取
4. 用 DP 替换现有贪心切分
5. 加 post-pass 重平衡
6. 让 reviewer 回归测试通过
7. 再看是否需要 Phase 2 的“源语先切、目标语重对齐”增强

---

### 十一、Phase 2 预留接口（暂不实现）
若后续要引入 `VideoLingo` 风格增强，建议预留：

```python
class SentenceProjectionStrategy(Protocol):
    def project(self, translated_text: str, source_lines: Sequence[str]) -> List[str]: ...
```

然后：
- `PenaltyBasedProjectionStrategy`（Phase 1 默认实现）
- `LLMRealignmentProjectionStrategy`（Phase 2 可选实现）

这样后续升级不会再次重写主流程。

---

### 十二、最终建议
这次实现不要直接接外部工具，而是：
- **算法上借 `subalign`**：DP + penalty
- **规则上借 Netflix / Subtitle Edit**：语义 no-break + 短残片回收
- **流程上预留 `VideoLingo` 升级口**：后续对极难案例再做 LLM 重对齐

这是当前项目里“成熟方法借鉴最多、实现风险最低、收益最直接”的设计方案。
