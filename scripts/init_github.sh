#!/bin/bash
#
# GitHub 初始化脚本
# 一键完成 Git 初始化和首次提交
#

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==========================================
  VideoFactory GitHub 初始化
==========================================
"

# Step 1: 检查敏感信息
echo "📋 Step 1: 检查敏感信息..."
if [ -f "config/settings.yaml" ]; then
    echo "⚠️  发现 config/settings.yaml"
    echo "   正在备份到 config/settings.local.yaml..."
    cp config/settings.yaml config/settings.local.yaml
    echo "✅ 备份完成"
else
    echo "✅ 未发现敏感配置文件"
fi

# Step 2: 检查 .gitignore
echo ""
echo "📋 Step 2: 检查 .gitignore..."
if [ -f ".gitignore" ]; then
    echo "✅ .gitignore 已存在"
else
    echo "❌ .gitignore 不存在，请先创建"
    exit 1
fi

# Step 3: 初始化 Git
echo ""
echo "📋 Step 3: 初始化 Git 仓库..."
if [ -d ".git" ]; then
    echo "⚠️  Git 仓库已存在，跳过初始化"
else
    git init
    git branch -M main
    echo "✅ Git 仓库初始化完成"
fi

# Step 4: 首次提交
echo ""
echo "📋 Step 4: 创建首次提交..."
git add .
git commit -m "Initial commit: VideoFactory v0.1.0

- 完整的翻译配音流程（YouTube → ASR → 翻译 → TTS）
- ASR 路由（YouTube字幕/Whisper/火山引擎）
- TTS 路由（KlicStudio/火山引擎）
- 二次创作能力（长视频、短切片、封面、元数据）
- 多平台发布调度器
- Web 管理界面（任务/存储/发布/设置）
- 30+ 测试用例
- 工程化 workflow 流程

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

echo "✅ 首次提交完成"

# Step 5: 提示关联远程仓库
echo ""
echo "==========================================
  ✅ 初始化完成！
==========================================
"
echo ""
echo "下一步："
echo "1. 在 GitHub 创建仓库: https://github.com/new"
echo "   - 仓库名: video-factory"
echo "   - 可见性: Private（推荐）"
echo "   - 不要勾选任何初始化选项"
echo ""
echo "2. 关联远程仓库并推送:"
echo "   git remote add origin git@github.com:YOUR_USERNAME/video-factory.git"
echo "   git push -u origin main"
echo ""
echo "3. 创建 develop 分支:"
echo "   git checkout -b develop"
echo "   git push -u origin develop"
echo ""
