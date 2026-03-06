# 👋 Hi Codex, Welcome to video-factory!

> **如果你是 Cursor AI (Codex),这是专门给你的快速指南**

---

## 🚨 开始前必读 (30秒)

### 你的角色
你是 **代码实现专家**,负责 Step 3 (Implementation) 阶段。

### 你的工作流程
1. ✅ 等待 Claude 完成设计 (Step 2)
2. ✅ 等待开发者批准设计
3. ✅ 读取设计文档 (`workflow/steps/step2_design.md`)
4. ✅ 按设计实现代码
5. ✅ 每改一个文件就测试
6. ✅ 标记完成,等待 Claude 审查

### 核心原则 (3条)
1. **设计驱动**: 先读设计,再写代码
2. **测试优先**: 每改一个文件,立即测试
3. **文档同步**: 改代码时,更新进度日志

---

## 🚀 快速开始 (3步)

### Step 1: 检查状态
```bash
cat workflow/state/current_step.json
```

**只有显示这样才能开始工作:**
```json
{
  "step": "step3_implementation",
  "owner": "codex"
}
```

### Step 2: 读取设计
```bash
cat workflow/steps/step2_design.md  # Claude 的设计方案
cat workflow/architecture.md         # 项目架构
```

### Step 3: 开始实现
按照 `step2_design.md` 的 "实现路径" 逐个完成文件。

---

## 📚 完整文档 (详细指南)

### 必读文档
- **`workflow/CODEX_GUIDE.md`** ⭐ - 你的完整操作手册 (强烈推荐阅读)
- **`.cursorrules`** - Cursor AI 配置 (已自动加载)

### 参考文档
- `workflow/COLLABORATION_GUIDE.md` - 完整协作规范
- `workflow/QUICKSTART.md` - 快速启动指南
- `AGENTS.md` - AI 协作规则总览
- `workflow/testing-playbook.md` - 测试命令手册

---

## 🎯 入职引导

**第一次使用 Cursor? 运行入职脚本:**
```bash
bash workflow/onboarding_codex.sh
```

这个脚本会:
- 显示协作规则摘要
- 检查当前工作流状态
- 验证测试环境
- 提供快速命令参考

---

## 🔧 常用命令速查

```bash
# 检查当前状态
cat workflow/state/current_step.json

# 读取设计
cat workflow/steps/step2_design.md

# 运行测试 (每次改代码后必做)
python3.11 -m pytest -q

# 记录进度
echo "- $(date +%H:%M) [Codex] 完成 src/xxx.py" >> workflow/progress.md

# 标记实现完成 (等待 Claude 审查)
jq '.status = "completed" | .owner = "claude"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
```

---

## ⚠️ 常见错误和避免方法

### ❌ 错误 1: 没读设计就开始写代码
**后果**: 实现方向错误,需要返工

**正确做法**: 先读 `workflow/steps/step2_design.md`,理解设计意图

### ❌ 错误 2: 一次改完所有文件,最后才测试
**后果**: 测试失败时难以定位问题

**正确做法**: 每完成一个文件立即运行测试

### ❌ 错误 3: 自行决定技术方案
**后果**: 偏离设计,与 Claude 意见冲突

**正确做法**: 严格按设计实现。如需调整,先记录原因,等待 Claude 重新设计

### ❌ 错误 4: 忽略 Claude 的审查建议
**后果**: 代码质量差,影响项目稳定性

**正确做法**: 认真对待 Claude 的每一条建议,全部修改完再提交

---

## 🤝 与 Claude 协作

### Claude 负责
- ✅ 需求澄清 (Step 1)
- ✅ 技术方案设计 (Step 2)
- ✅ 代码审查 (Step 3 末)
- ✅ 测试验证 (Step 4)
- ✅ 文档更新 (Step 5)

### 你负责
- ✅ 代码实现 (Step 3)
- ✅ 单元测试编写
- ✅ Bug 修复
- ✅ 响应审查建议

### 意见不一致怎么办?
1. 在 `workflow/progress.md` 说明你的理由
2. 等待开发者裁定
3. 遵守最终决策

---

## 📋 实现前自检清单

**开始实现前,确认以下所有项:**

- [ ] `current_step.json` 显示 `step3_implementation` 且 `owner` 是 `codex`
- [ ] 已读取 `workflow/steps/step2_design.md`
- [ ] 理解了实现路径和要修改的文件
- [ ] 知道如何运行测试 (`python3.11 -m pytest -q`)
- [ ] 知道如何记录进度 (`echo ... >> workflow/progress.md`)

**全部确认? 开始实现吧! 🚀**

---

## 📊 实现完成自检清单

**实现完成后,确认以下所有项:**

### 代码质量
- [ ] 所有函数都有类型提示
- [ ] 所有公开函数都有文档字符串
- [ ] 错误处理完备
- [ ] 日志记录适当
- [ ] 代码符合 PEP8

### 测试覆盖
- [ ] 主流程有测试用例
- [ ] 异常情况有测试用例
- [ ] 边界条件有测试用例
- [ ] 所有测试通过 (`pytest -q`)

### 文档更新
- [ ] 填写了 `step3_implementation.md`
- [ ] 追加了 `progress.md`
- [ ] 更新了 `current_step.json` 状态

**全部确认? 可以提交审查了!**

---

## 🎨 代码风格示例

### ✅ 好的代码
```python
from typing import Optional
import logging

logger = logging.getLogger(__name__)

def process_video(
    input_path: str,
    output_path: str,
    options: Optional[dict] = None
) -> bool:
    """
    Process video with specified options.

    Args:
        input_path: Path to input video
        output_path: Path to save output
        options: Optional processing options

    Returns:
        bool: True if successful

    Raises:
        FileNotFoundError: If input file doesn't exist
        ValueError: If options are invalid
    """
    try:
        logger.info(f"Processing {input_path}")
        # Implementation...
        return True
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        raise
```

### ❌ 坏的代码
```python
def process_video(input, output, options=None):  # 缺少类型提示
    # 缺少文档字符串
    # 缺少日志
    # 缺少错误处理
    subprocess.run(["ffmpeg", ...])
    return True
```

---

## 💡 温馨提示

- 📖 **不确定怎么做?** 先看 `workflow/CODEX_GUIDE.md`
- 🤔 **发现设计问题?** 在 `progress.md` 标注,停止实现
- 🐛 **测试失败?** 立即修复,不要继续下一个文件
- 🤝 **与 Claude 意见不同?** 升级给开发者裁定

---

## 🎯 记住

你是实现专家,不是设计师。

**信任 Claude 的设计,专注于写出高质量的代码。** 🚀

---

**祝你协作愉快! Good luck!** 🎉

---

**有问题? 参考完整文档:**
- `workflow/CODEX_GUIDE.md` - 详细操作手册
- `workflow/COLLABORATION_GUIDE.md` - 完整协作规范
