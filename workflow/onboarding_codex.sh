#!/bin/bash
#
# Codex (Cursor) 入职引导脚本
# 第一次使用 Cursor 时运行此脚本,显示协作规则
#

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "=========================================="
echo "  Welcome to video-factory, Codex! 🚀"
echo "=========================================="
echo ""
echo "你即将参与一个 Claude + Codex 协同开发的项目。"
echo "在开始编码之前,请花 5 分钟了解协作规则。"
echo ""

# 检查 .cursorrules 是否存在
if [ ! -f "$PROJECT_DIR/.cursorrules" ]; then
    echo "❌ 错误: .cursorrules 文件不存在"
    echo "请确保项目根目录有 .cursorrules 文件"
    exit 1
fi

echo "📋 Step 1: 阅读协作规则"
echo "----------------------------------------"
echo "核心文档:"
echo "  1. workflow/CODEX_GUIDE.md     - 你的完整操作手册 (必读)"
echo "  2. .cursorrules                 - Cursor AI 配置 (已自动加载)"
echo "  3. workflow/COLLABORATION_GUIDE.md - 完整协作规范"
echo ""

read -p "是否现在打开 CODEX_GUIDE.md? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if command -v code &> /dev/null; then
        code "$PROJECT_DIR/workflow/CODEX_GUIDE.md"
    else
        cat "$PROJECT_DIR/workflow/CODEX_GUIDE.md"
    fi
fi

echo ""
echo "📊 Step 2: 检查当前工作流状态"
echo "----------------------------------------"

if [ -f "$PROJECT_DIR/workflow/state/current_step.json" ]; then
    echo "当前状态:"
    cat "$PROJECT_DIR/workflow/state/current_step.json" | jq .
    echo ""

    CURRENT_STEP=$(cat "$PROJECT_DIR/workflow/state/current_step.json" | jq -r '.step')
    CURRENT_OWNER=$(cat "$PROJECT_DIR/workflow/state/current_step.json" | jq -r '.owner')

    if [ "$CURRENT_STEP" = "step3_implementation" ] && [ "$CURRENT_OWNER" = "codex" ]; then
        echo "✅ 状态正常: 你可以开始实现代码"
        echo ""
        echo "下一步: 读取设计文档"
        echo "  cat workflow/steps/step2_design.md"
    elif [ "$CURRENT_STEP" = "idle" ]; then
        echo "ℹ️  当前无活跃任务,等待开发者提出需求"
    else
        echo "⚠️  当前不是你的工作阶段"
        echo "   - 当前步骤: $CURRENT_STEP"
        echo "   - 负责人: $CURRENT_OWNER"
        echo ""
        echo "   请等待 Claude 完成设计并获得批准"
    fi
else
    echo "❌ 找不到 current_step.json,请检查 workflow/state/ 目录"
fi

echo ""
echo "🧪 Step 3: 测试环境检查"
echo "----------------------------------------"

# 检查 Python 版本
if command -v python3.11 &> /dev/null; then
    PYTHON_VERSION=$(python3.11 --version)
    echo "✅ Python: $PYTHON_VERSION"
else
    echo "❌ Python 3.11 未安装"
fi

# 检查 pytest
if command -v pytest &> /dev/null; then
    PYTEST_VERSION=$(pytest --version | head -1)
    echo "✅ Pytest: $PYTEST_VERSION"
else
    echo "❌ Pytest 未安装,请运行: pip install pytest"
fi

# 检查 jq
if command -v jq &> /dev/null; then
    JQ_VERSION=$(jq --version)
    echo "✅ jq: $JQ_VERSION"
else
    echo "⚠️  jq 未安装 (可选,用于更新状态文件)"
    echo "   安装: brew install jq (macOS) 或 sudo apt install jq (Linux)"
fi

echo ""
echo "📚 Step 4: 快速命令参考"
echo "----------------------------------------"
cat << 'EOF'
# 检查当前状态
cat workflow/state/current_step.json

# 读取设计文档
cat workflow/steps/step2_design.md

# 运行测试
python3.11 -m pytest -q

# 记录进度
echo "- $(date +%H:%M) [Codex] 完成 src/xxx.py" >> workflow/progress.md

# 标记完成
jq '.status = "completed" | .owner = "claude"' \
   workflow/state/current_step.json > tmp && mv tmp workflow/state/current_step.json
EOF

echo ""
echo "🎯 Step 5: 核心原则 (必记)"
echo "----------------------------------------"
echo "1. 设计驱动: 先读 step2_design.md,再写代码"
echo "2. 测试优先: 每改一个文件,立即运行测试"
echo "3. 文档同步: 改代码的同时,更新 progress.md"
echo ""

echo "=========================================="
echo "  Onboarding 完成! 准备开始协作 🎉"
echo "=========================================="
echo ""
echo "下一步:"
echo "  1. 等待开发者分配任务"
echo "  2. 检查 current_step.json 确认轮到你"
echo "  3. 读取 step2_design.md 了解设计"
echo "  4. 按设计实现代码"
echo ""
echo "有问题? 参考:"
echo "  - workflow/CODEX_GUIDE.md (你的操作手册)"
echo "  - workflow/QUICKSTART.md (快速启动)"
echo "  - workflow/COLLABORATION_GUIDE.md (完整规范)"
echo ""
