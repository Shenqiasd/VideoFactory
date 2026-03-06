#!/bin/bash
#
# video-factory 一键启动脚本
# 启动所有5个服务：Groq Whisper Proxy + Edge-TTS Proxy + KlicStudio + video-factory API + Worker
#
# 用法: bash scripts/start_all.sh
# 停止: bash scripts/start_all.sh stop
#

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KLIC_DIR="/Users/enesource/Projects/KlicStudio/bin"
PID_DIR="$PROJECT_DIR/.pids"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON="/usr/local/bin/python3.11"  # 统一使用Python 3.11
FFMPEG_FULL_DIR="/usr/local/opt/ffmpeg-full/bin"
API_PORT="${VF_API_PORT:-8087}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

mkdir -p "$PID_DIR" "$LOG_DIR"

FFMPEG_BIN=""
FFMPEG_SUBTITLES="unknown"
WORKER_HEARTBEAT_FILE="$HOME/.video-factory/worker_heartbeat.json"

# ========== 停止所有服务 ==========
stop_all() {
    echo -e "${YELLOW}🛑 正在停止所有服务...${NC}"

    # 停止 video-factory API
    if [ -f "$PID_DIR/api.pid" ]; then
        PID=$(cat "$PID_DIR/api.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo -e "${GREEN}  ✅ video-factory API (PID: $PID) 已停止${NC}"
        fi
        rm -f "$PID_DIR/api.pid"
    fi

    # 停止 Groq Whisper Proxy
    if [ -f "$PID_DIR/whisper_proxy.pid" ]; then
        PID=$(cat "$PID_DIR/whisper_proxy.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo -e "${GREEN}  ✅ Whisper Proxy (PID: $PID) 已停止${NC}"
        fi
        rm -f "$PID_DIR/whisper_proxy.pid"
    fi

    # 停止 Edge-TTS Proxy
    if [ -f "$PID_DIR/tts_proxy.pid" ]; then
        PID=$(cat "$PID_DIR/tts_proxy.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo -e "${GREEN}  ✅ Edge-TTS Proxy (PID: $PID) 已停止${NC}"
        fi
        rm -f "$PID_DIR/tts_proxy.pid"
    fi

    # 停止 KlicStudio
    if [ -f "$PID_DIR/klicstudio.pid" ]; then
        PID=$(cat "$PID_DIR/klicstudio.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo -e "${GREEN}  ✅ KlicStudio (PID: $PID) 已停止${NC}"
        fi
        rm -f "$PID_DIR/klicstudio.pid"
    fi

    # 停止 Worker
    if [ -f "$PID_DIR/worker.pid" ]; then
        PID=$(cat "$PID_DIR/worker.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo -e "${GREEN}  ✅ Worker (PID: $PID) 已停止${NC}"
        fi
        rm -f "$PID_DIR/worker.pid"
    fi

    # 兜底：清理未被 PID 文件管理的遗留 Worker（例如手动 python -m workers.main 启动）
    EXTRA_WORKER_PIDS=$(ps -ax -o pid=,command= | grep -E 'workers/main.py|[Pp]ython.*-m workers.main' | grep -v grep | awk '{print $1}' | tr '\n' ' ')
    if [ -n "$EXTRA_WORKER_PIDS" ]; then
        echo -e "${YELLOW}  清理遗留 Worker 进程: $EXTRA_WORKER_PIDS${NC}"
        kill $EXTRA_WORKER_PIDS 2>/dev/null || true
    fi

    # 兜底：检查端口上残留的进程
    for PORT in 8866 8877 8888 "$API_PORT" 9000; do
        PIDS=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null || true)
        if [ -n "$PIDS" ]; then
            echo -e "${YELLOW}  清理端口 $PORT 上的残留进程: $PIDS${NC}"
            echo "$PIDS" | xargs kill 2>/dev/null || true
        fi
    done

    echo -e "${GREEN}🛑 所有服务已停止${NC}"
}

# ========== 等待服务启动 ==========
wait_for_port() {
    local PORT=$1
    local SERVICE=$2
    local MAX_WAIT=${3:-30}
    local WAIT=0

    while [ $WAIT -lt $MAX_WAIT ]; do
        if lsof -ti :$PORT >/dev/null 2>&1; then
            echo -e "${GREEN}  ✅ $SERVICE 已启动 (端口: $PORT)${NC}"
            return 0
        fi
        sleep 1
        WAIT=$((WAIT + 1))
    done

    echo -e "${RED}  ❌ $SERVICE 启动超时 (端口: $PORT)${NC}"
    return 1
}

worker_heartbeat_age_seconds() {
    $PYTHON - "$WORKER_HEARTBEAT_FILE" 2>/dev/null <<'PY' || echo "-1"
import json
import pathlib
import sys
import time

p = pathlib.Path(sys.argv[1])
if not p.exists():
    print(-1)
    raise SystemExit(0)

try:
    data = json.loads(p.read_text())
    ts = float(data.get("timestamp", 0) or 0)
    print(int(time.time() - ts) if ts else -1)
except Exception:
    print(-1)
PY
}

# ========== FFmpeg能力探测 ==========
detect_ffmpeg_capability() {
    local configured="${VF_FFMPEG_PATH:-}"
    local from_config=""

    if [ -z "$configured" ] && [ -f "$PROJECT_DIR/config/settings.yaml" ]; then
        from_config="$($PYTHON -c 'import sys, yaml; p=sys.argv[1]; d=yaml.safe_load(open(p, "r")) or {}; print(((d.get("ffmpeg") or {}).get("path")) or "")' "$PROJECT_DIR/config/settings.yaml" 2>/dev/null || true)"
        if [ -n "$from_config" ]; then
            configured="$from_config"
        fi
    fi

    if [ -n "$configured" ]; then
        FFMPEG_BIN="$configured"
    else
        FFMPEG_BIN="$(command -v ffmpeg 2>/dev/null || true)"
    fi

    if [ -z "$FFMPEG_BIN" ]; then
        FFMPEG_SUBTITLES="missing"
        echo -e "${YELLOW}  ⚠️ 未找到 ffmpeg，可通过配置 ffmpeg.path 或环境变量 VF_FFMPEG_PATH 指定${NC}"
        return 0
    fi

    if "$FFMPEG_BIN" -filters 2>/dev/null | grep -q "subtitles"; then
        FFMPEG_SUBTITLES="available"
        echo -e "${GREEN}  ✅ ffmpeg: $FFMPEG_BIN (subtitles filter 可用)${NC}"
    else
        FFMPEG_SUBTITLES="unavailable"
        echo -e "${YELLOW}  ⚠️ ffmpeg: $FFMPEG_BIN (subtitles filter 不可用，将回退软字幕)${NC}"
    fi
}

# ========== 启动所有服务 ==========
start_all() {
    local START_ERRORS=0
    local API_STARTED=0

    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  🚀 video-factory 一键启动${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════${NC}"
    echo ""

    # 先停止已有服务
    stop_all 2>/dev/null || true
    sleep 1

    echo -e "${BLUE}[0/5] 探测 FFmpeg 能力...${NC}"
    detect_ffmpeg_capability

    # ---------- 1. 启动 Groq Whisper Proxy (port 8866) ----------
    echo -e "${BLUE}[1/5] 启动 Groq Whisper Proxy...${NC}"
    cd "$PROJECT_DIR"
    nohup $PYTHON scripts/groq_whisper_proxy.py > "$LOG_DIR/whisper_proxy.log" 2>&1 &
    echo $! > "$PID_DIR/whisper_proxy.pid"
    if ! wait_for_port 8866 "Groq Whisper Proxy" 15; then
        START_ERRORS=$((START_ERRORS + 1))
        rm -f "$PID_DIR/whisper_proxy.pid"
    fi

    # ---------- 2. 启动 Edge-TTS Proxy (port 8877) ----------
    echo -e "${BLUE}[2/5] 启动 Edge-TTS Proxy...${NC}"
    cd "$PROJECT_DIR"
    nohup $PYTHON scripts/edge_tts_proxy.py > "$LOG_DIR/tts_proxy.log" 2>&1 &
    echo $! > "$PID_DIR/tts_proxy.pid"
    if ! wait_for_port 8877 "Edge-TTS Proxy" 15; then
        START_ERRORS=$((START_ERRORS + 1))
        rm -f "$PID_DIR/tts_proxy.pid"
    fi

    # ---------- 3. 启动 KlicStudio (port 8888) ----------
    echo -e "${BLUE}[3/5] 启动 KlicStudio Server...${NC}"
    cd "$KLIC_DIR"
    if [ -x "$FFMPEG_FULL_DIR/ffmpeg" ] && [ -x "$FFMPEG_FULL_DIR/ffprobe" ]; then
        echo -e "${GREEN}  ✅ KlicStudio 使用 ffmpeg-full: $FFMPEG_FULL_DIR${NC}"
        nohup env PATH="$FFMPEG_FULL_DIR:$PATH" ./KlicStudio_1.4.0_macOS_amd64 > "$LOG_DIR/klicstudio.log" 2>&1 &
    else
        echo -e "${YELLOW}  ⚠️ 未找到 ffmpeg-full，KlicStudio 将使用系统 PATH 中的 ffmpeg${NC}"
        nohup ./KlicStudio_1.4.0_macOS_amd64 > "$LOG_DIR/klicstudio.log" 2>&1 &
    fi
    echo $! > "$PID_DIR/klicstudio.pid"
    if ! wait_for_port 8888 "KlicStudio Server" 15; then
        START_ERRORS=$((START_ERRORS + 1))
        rm -f "$PID_DIR/klicstudio.pid"
    fi

    # ---------- 4. 启动 video-factory API ----------
    echo -e "${BLUE}[4/5] 启动 video-factory API...${NC}"
    cd "$PROJECT_DIR"
    nohup env VF_API_PORT="$API_PORT" $PYTHON scripts/start_server.py > "$LOG_DIR/api.log" 2>&1 &
    echo $! > "$PID_DIR/api.pid"
    if wait_for_port "$API_PORT" "video-factory API" 15; then
        API_STARTED=1
    else
        START_ERRORS=$((START_ERRORS + 1))
        rm -f "$PID_DIR/api.pid"
    fi

    # ---------- 5. 启动 Worker ----------
    echo -e "${BLUE}[5/5] 启动 Worker (Orchestrator + Scheduler)...${NC}"
    if [ "$API_STARTED" -eq 1 ]; then
        cd "$PROJECT_DIR"
        nohup $PYTHON workers/main.py > "$LOG_DIR/worker.log" 2>&1 &
        echo $! > "$PID_DIR/worker.pid"
        sleep 2
        if kill -0 "$(cat "$PID_DIR/worker.pid")" 2>/dev/null; then
            AGE=$(worker_heartbeat_age_seconds)
            if [ "$AGE" -ge 0 ] && [ "$AGE" -le 45 ]; then
                echo -e "${GREEN}  ✅ Worker 已启动 (PID: $(cat "$PID_DIR/worker.pid"), heartbeat: ${AGE}s)${NC}"
            else
                echo -e "${YELLOW}  ⚠️ Worker 已启动但心跳未就绪 (PID: $(cat "$PID_DIR/worker.pid"))${NC}"
            fi
        else
            echo -e "${RED}  ❌ Worker 启动失败，请查看: $LOG_DIR/worker.log${NC}"
            rm -f "$PID_DIR/worker.pid"
            START_ERRORS=$((START_ERRORS + 1))
        fi
    else
        echo -e "${YELLOW}  ⚠️ API 未启动，跳过 Worker 启动${NC}"
    fi

    echo ""
    if [ "$START_ERRORS" -eq 0 ]; then
        echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  ✅ 所有服务已启动！${NC}"
        echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
    else
        echo -e "${YELLOW}════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  ⚠️ 启动完成，但有 ${START_ERRORS} 个服务未启动${NC}"
        echo -e "${YELLOW}════════════════════════════════════════════════════${NC}"
    fi
    echo ""
    echo -e "  🎤 Whisper Proxy:     ${BLUE}http://localhost:8866${NC}  (Groq Whisper API代理)"
    echo -e "  🗣️  TTS Proxy:         ${BLUE}http://localhost:8877${NC}  (Edge-TTS → OpenAI API)"
    echo -e "  🎬 KlicStudio:        ${BLUE}http://localhost:8888${NC}  (翻译引擎)"
    echo -e "  🏭 video-factory API: ${BLUE}http://localhost:${API_PORT}${NC}  (主控API)"
    echo -e "  🔁 Worker:            ${BLUE}后台运行${NC}  (编排器/调度器)"
    echo -e "  📖 API文档:           ${BLUE}http://localhost:${API_PORT}/docs${NC}"
    echo ""
    echo -e "  📄 日志目录: ${LOG_DIR}"
    echo -e "  📄 PID目录:  ${PID_DIR}"
    echo ""
    echo -e "${YELLOW}  提示: 停止所有服务请运行: bash scripts/start_all.sh stop${NC}"
    echo ""

    if [ "$API_STARTED" -ne 1 ]; then
        echo -e "${RED}  ❌ API 未启动，无法访问 http://localhost:${API_PORT}${NC}"
        return 1
    fi
}

# ========== 查看状态 ==========
status_all() {
    echo ""
    echo -e "${BLUE}═══ 服务状态 ═══${NC}"
    echo ""

    for PORT_INFO in "8866:Groq Whisper Proxy" "8877:Edge-TTS Proxy" "8888:KlicStudio" "${API_PORT}:video-factory API"; do
        PORT="${PORT_INFO%%:*}"
        SERVICE="${PORT_INFO#*:}"

        if lsof -nP -iTCP:$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
            PID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
            echo -e "  ${GREEN}✅ $SERVICE${NC} (端口: $PORT, PID: $PID)"
        else
            echo -e "  ${RED}❌ $SERVICE${NC} (端口: $PORT, 未运行)"
        fi
    done

    if [ -f "$PID_DIR/worker.pid" ]; then
        WORKER_PID=$(cat "$PID_DIR/worker.pid")
        if kill -0 "$WORKER_PID" 2>/dev/null; then
            AGE=$(worker_heartbeat_age_seconds)
            if [ "$AGE" -ge 0 ] && [ "$AGE" -le 45 ]; then
                echo -e "  ${GREEN}✅ Worker${NC} (PID: $WORKER_PID, heartbeat: ${AGE}s)"
            elif [ "$AGE" -ge 0 ]; then
                echo -e "  ${YELLOW}⚠️ Worker${NC} (PID: $WORKER_PID, heartbeat stale: ${AGE}s)"
            else
                echo -e "  ${YELLOW}⚠️ Worker${NC} (PID: $WORKER_PID, heartbeat missing)"
            fi
        else
            echo -e "  ${RED}❌ Worker${NC} (PID文件存在但进程未运行)"
        fi
    else
        echo -e "  ${RED}❌ Worker${NC} (未运行)"
    fi

    detect_ffmpeg_capability
    echo ""
}

# ========== 查看日志 ==========
logs() {
    local SERVICE=${2:-all}

    case "$SERVICE" in
        whisper|proxy)
            tail -f "$LOG_DIR/whisper_proxy.log"
            ;;
        tts)
            tail -f "$LOG_DIR/tts_proxy.log"
            ;;
        klic|klicstudio)
            tail -f "$LOG_DIR/klicstudio.log"
            ;;
        api|factory)
            tail -f "$LOG_DIR/api.log"
            ;;
        worker)
            tail -f "$LOG_DIR/worker.log"
            ;;
        all|*)
            tail -f "$LOG_DIR"/*.log
            ;;
    esac
}

# ========== 主入口 ==========
case "${1:-start}" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        sleep 2
        start_all
        ;;
    status)
        status_all
        ;;
    logs)
        logs "$@"
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|logs [whisper|tts|klic|api|worker|all]}"
        exit 1
        ;;
esac
