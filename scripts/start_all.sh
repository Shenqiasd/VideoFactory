#!/bin/bash
#
# video-factory 一键启动脚本
# 启动本地依赖与主服务：Local LLM(可选) + Groq Whisper Proxy + Edge-TTS Proxy + video-factory API + Worker
#
# 用法: bash scripts/start_all.sh
# 停止: bash scripts/start_all.sh stop
#

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/config/settings.yaml"
PID_DIR="$PROJECT_DIR/.pids"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON=""
PYTHON_LABEL=""
API_PORT="${VF_API_PORT:-9000}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

mkdir -p "$PID_DIR" "$LOG_DIR"

FFMPEG_BIN=""
FFMPEG_SUBTITLES="unknown"
CFG_FFMPEG_PATH=""
CFG_TRANSLATION_PROVIDER=""
CFG_LOCAL_LLM_ENABLED=""
CFG_LOCAL_LLM_BASE_URL=""
CFG_LOCAL_LLM_MODEL=""
RUNTIME_CONFIG_LOADED=0
LOCAL_LLM_ENABLED=0
LOCAL_LLM_SKIPPED=0
LOCAL_LLM_BASE_URL=""
LOCAL_LLM_MODEL=""
LOCAL_LLM_HOST="127.0.0.1"
LOCAL_LLM_PORT="1234"
WORKER_HEARTBEAT_FILE="$HOME/.video-factory/worker_heartbeat.json"

python_minor_version() {
    local candidate="$1"
    "$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true
}

resolve_python_3_11() {
    local candidates=()
    local candidate=""
    local version=""

    if [ -n "${VF_PYTHON_BIN:-}" ]; then
        candidates+=("${VF_PYTHON_BIN}")
    fi
    if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
        candidates+=("$PROJECT_DIR/.venv/bin/python")
    fi
    candidate="$(command -v python3.11 2>/dev/null || true)"
    if [ -n "$candidate" ]; then
        candidates+=("$candidate")
    fi
    candidate="$(command -v python3 2>/dev/null || true)"
    if [ -n "$candidate" ]; then
        candidates+=("$candidate")
    fi

    PYTHON=""
    PYTHON_LABEL=""

    for candidate in "${candidates[@]}"; do
        [ -n "$candidate" ] || continue
        version="$(python_minor_version "$candidate")"
        if [ "$version" = "3.11" ]; then
            PYTHON="$candidate"
            PYTHON_LABEL="$candidate (Python 3.11)"
            return 0
        fi
    done

    return 1
}

ensure_python_3_11() {
    local fallback_python=""
    local fallback_version=""

    if [ -n "$PYTHON" ]; then
        return 0
    fi

    if resolve_python_3_11; then
        return 0
    fi

    fallback_python="$(command -v python3 2>/dev/null || true)"
    if [ -n "$fallback_python" ]; then
        fallback_version="$(python_minor_version "$fallback_python")"
    fi

    echo -e "${RED}  ❌ 未找到可用的 Python 3.11 解释器${NC}"
    echo -e "${YELLOW}  需要的解析顺序: VF_PYTHON_BIN -> $PROJECT_DIR/.venv/bin/python -> python3.11${NC}"
    if [ -n "$fallback_version" ]; then
        echo -e "${YELLOW}  当前 python3 版本: $fallback_version${NC}"
    fi
    echo -e "${YELLOW}  建议先执行:${NC}"
    echo "     python3.11 -m venv .venv"
    echo "     ./.venv/bin/python -m pip install -r requirements.txt"
    return 1
}

config_lookup_path() {
    local keys=("$@")

    if [ -z "$PYTHON" ] || [ ! -f "$CONFIG_FILE" ]; then
        return 0
    fi

    "$PYTHON" - "$CONFIG_FILE" "${keys[@]}" <<'PY'
import pathlib
import sys

import yaml

config_path = pathlib.Path(sys.argv[1])
keys = sys.argv[2:]

try:
    data = yaml.safe_load(config_path.read_text()) or {}
except Exception:
    print("")
    raise SystemExit(0)

node = data
for key in keys:
    if isinstance(node, dict):
        node = node.get(key)
    else:
        node = ""
        break
    if node is None:
        node = ""
        break

if isinstance(node, bool):
    print("true" if node else "false")
elif isinstance(node, (dict, list)):
    print("")
else:
    print(node or "")
PY
}

config_lookup() {
    local section="$1"
    local key="$2"
    config_lookup_path "$section" "$key"
}

load_runtime_config() {
    if [ "$RUNTIME_CONFIG_LOADED" -eq 1 ]; then
        return 0
    fi
    RUNTIME_CONFIG_LOADED=1

    if [ -z "$PYTHON" ] && ! resolve_python_3_11; then
        return 0
    fi

    CFG_FFMPEG_PATH="$(config_lookup "ffmpeg" "path")"
    CFG_TRANSLATION_PROVIDER="$(config_lookup_path "translation" "provider")"
    CFG_LOCAL_LLM_ENABLED="$(config_lookup_path "translation" "local_llm" "enabled")"
    CFG_LOCAL_LLM_BASE_URL="$(config_lookup_path "translation" "local_llm" "base_url")"
    CFG_LOCAL_LLM_MODEL="$(config_lookup_path "translation" "local_llm" "model")"
}

resolve_local_llm_runtime() {
    local enabled=""
    local provider=""

    load_runtime_config

    LOCAL_LLM_ENABLED=0
    LOCAL_LLM_SKIPPED=0
    LOCAL_LLM_HOST="127.0.0.1"
    LOCAL_LLM_PORT="1234"
    LOCAL_LLM_BASE_URL="${VF_TRANSLATION_LOCAL_LLM_BASE_URL:-${CFG_LOCAL_LLM_BASE_URL:-}}"
    LOCAL_LLM_MODEL="${VF_TRANSLATION_LOCAL_LLM_MODEL:-${CFG_LOCAL_LLM_MODEL:-}}"
    enabled="${VF_TRANSLATION_LOCAL_LLM_ENABLED:-${CFG_LOCAL_LLM_ENABLED:-}}"
    provider="${VF_TRANSLATION_PROVIDER:-${CFG_TRANSLATION_PROVIDER:-}}"

    if [ "$provider" = "local_llm" ] && { [ "$enabled" = "true" ] || [ "$enabled" = "1" ]; }; then
        LOCAL_LLM_ENABLED=1
    else
        return 0
    fi

    if [ -n "$LOCAL_LLM_BASE_URL" ] && [ -n "$PYTHON" ]; then
        read -r LOCAL_LLM_HOST LOCAL_LLM_PORT <<EOF
$("$PYTHON" - "$LOCAL_LLM_BASE_URL" <<'PY'
import sys
from urllib.parse import urlparse

raw = sys.argv[1].strip()
parsed = urlparse(raw if "://" in raw else f"http://{raw}")
host = parsed.hostname or "127.0.0.1"
port = parsed.port or (443 if parsed.scheme == "https" else 80)
print(host, port)
PY
)
EOF
    fi
}

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

    # 停止 Worker
    if [ -f "$PID_DIR/worker.pid" ]; then
        PID=$(cat "$PID_DIR/worker.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo -e "${GREEN}  ✅ Worker (PID: $PID) 已停止${NC}"
        fi
        rm -f "$PID_DIR/worker.pid"
    fi

    # 停止本地翻译 LLM
    if [ -f "$PID_DIR/local_llm.pid" ]; then
        PID=$(cat "$PID_DIR/local_llm.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null && echo -e "${GREEN}  ✅ Local LLM (PID: $PID) 已停止${NC}"
        fi
        rm -f "$PID_DIR/local_llm.pid"
    fi

    # 兜底：清理未被 PID 文件管理的遗留 Worker（例如手动 python -m workers.main 启动）
    EXTRA_WORKER_PIDS=$(ps -ax -o pid=,command= | grep -E 'workers/main.py|[Pp]ython.*-m workers.main' | grep -v grep | awk '{print $1}' | tr '\n' ' ')
    if [ -n "$EXTRA_WORKER_PIDS" ]; then
        echo -e "${YELLOW}  清理遗留 Worker 进程: $EXTRA_WORKER_PIDS${NC}"
        kill $EXTRA_WORKER_PIDS 2>/dev/null || true
    fi

    # 兜底：检查端口上残留的进程
    for PORT in 1234 8866 8877 "$API_PORT" 9000; do
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
    if [ -z "$PYTHON" ]; then
        echo "-1"
        return 0
    fi
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

    load_runtime_config
    if [ -z "$configured" ] && [ -n "$CFG_FFMPEG_PATH" ]; then
        configured="$CFG_FFMPEG_PATH"
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

    if ! ensure_python_3_11; then
        return 1
    fi
    load_runtime_config
    resolve_local_llm_runtime

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

    # ---------- 1. 启动 Local LLM Translation Server (port 1234) ----------
    echo -e "${BLUE}[1/5] 启动 Local LLM Translation Server...${NC}"
    if [ "$LOCAL_LLM_ENABLED" -ne 1 ]; then
        LOCAL_LLM_SKIPPED=1
        echo -e "${YELLOW}  ⚠️ translation.provider 不是 local_llm，或 local_llm.enabled 未开启，本次跳过${NC}"
    elif [ -z "$LOCAL_LLM_MODEL" ]; then
        echo -e "${RED}  ❌ local_llm 已启用，但未配置 translation.local_llm.model${NC}"
        START_ERRORS=$((START_ERRORS + 1))
    else
        cd "$PROJECT_DIR"
        nohup "$PYTHON" -m mlx_lm server \
            --model "$LOCAL_LLM_MODEL" \
            --host "$LOCAL_LLM_HOST" \
            --port "$LOCAL_LLM_PORT" \
            --temp 0.2 \
            --max-tokens 1024 > "$LOG_DIR/local_llm.log" 2>&1 &
        echo $! > "$PID_DIR/local_llm.pid"
        if ! wait_for_port "$LOCAL_LLM_PORT" "Local LLM Translation Server" 900; then
            START_ERRORS=$((START_ERRORS + 1))
            rm -f "$PID_DIR/local_llm.pid"
        fi
    fi

    # ---------- 2. 启动 Groq Whisper Proxy (port 8866) ----------
    echo -e "${BLUE}[2/5] 启动 Groq Whisper Proxy...${NC}"
    cd "$PROJECT_DIR"
    nohup "$PYTHON" scripts/groq_whisper_proxy.py > "$LOG_DIR/whisper_proxy.log" 2>&1 &
    echo $! > "$PID_DIR/whisper_proxy.pid"
    if ! wait_for_port 8866 "Groq Whisper Proxy" 15; then
        START_ERRORS=$((START_ERRORS + 1))
        rm -f "$PID_DIR/whisper_proxy.pid"
    fi

    # ---------- 3. 启动 Edge-TTS Proxy (port 8877) ----------
    echo -e "${BLUE}[3/5] 启动 Edge-TTS Proxy...${NC}"
    cd "$PROJECT_DIR"
    nohup "$PYTHON" scripts/edge_tts_proxy.py > "$LOG_DIR/tts_proxy.log" 2>&1 &
    echo $! > "$PID_DIR/tts_proxy.pid"
    if ! wait_for_port 8877 "Edge-TTS Proxy" 15; then
        START_ERRORS=$((START_ERRORS + 1))
        rm -f "$PID_DIR/tts_proxy.pid"
    fi

    # ---------- 4. 启动 video-factory API ----------
    echo -e "${BLUE}[4/5] 启动 video-factory API...${NC}"
    cd "$PROJECT_DIR"
    nohup env VF_API_PORT="$API_PORT" "$PYTHON" scripts/start_server.py > "$LOG_DIR/api.log" 2>&1 &
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
        nohup "$PYTHON" workers/main.py > "$LOG_DIR/worker.log" 2>&1 &
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
    if [ "$LOCAL_LLM_SKIPPED" -eq 1 ]; then
        echo -e "  🧠 Local LLM:         ${YELLOW}未启用（已跳过）${NC}"
    else
        echo -e "  🧠 Local LLM:         ${BLUE}http://${LOCAL_LLM_HOST}:${LOCAL_LLM_PORT}${NC}  (本地翻译 OpenAI 兼容接口)"
    fi
    echo -e "  🎤 Whisper Proxy:     ${BLUE}http://localhost:8866${NC}  (Groq Whisper API代理)"
    echo -e "  🗣️  TTS Proxy:         ${BLUE}http://localhost:8877${NC}  (Edge-TTS → OpenAI API)"
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
    resolve_python_3_11 >/dev/null 2>&1 || true
    load_runtime_config

    echo ""
    echo -e "${BLUE}═══ 服务状态 ═══${NC}"
    echo ""

    resolve_local_llm_runtime

    if lsof -nP -iTCP:${LOCAL_LLM_PORT} -sTCP:LISTEN -t >/dev/null 2>&1; then
        PID=$(lsof -nP -iTCP:${LOCAL_LLM_PORT} -sTCP:LISTEN -t 2>/dev/null | head -1)
        echo -e "  ${GREEN}✅ Local LLM${NC} (端口: ${LOCAL_LLM_PORT}, PID: $PID)"
    elif [ "$LOCAL_LLM_ENABLED" -eq 1 ]; then
        echo -e "  ${RED}❌ Local LLM${NC} (端口: ${LOCAL_LLM_PORT}, 未运行)"
    else
        echo -e "  ${YELLOW}⚠️ Local LLM${NC} (未启用)"
    fi

    for PORT_INFO in "8866:Groq Whisper Proxy" "8877:Edge-TTS Proxy" "${API_PORT}:video-factory API"; do
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
            elif [ -z "$PYTHON" ]; then
                echo -e "  ${YELLOW}⚠️ Worker${NC} (PID: $WORKER_PID, 未找到 Python 3.11，无法读取 heartbeat)"
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
    if [ -n "$PYTHON_LABEL" ]; then
        echo -e "  ${BLUE}ℹ️ Python 解释器${NC}: $PYTHON_LABEL"
    fi
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
        local_llm|llm)
            tail -f "$LOG_DIR/local_llm.log"
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
        echo "用法: $0 {start|stop|restart|status|logs [local_llm|whisper|tts|api|worker|all]}"
        exit 1
        ;;
esac
