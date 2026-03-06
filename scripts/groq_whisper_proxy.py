#!/usr/bin/env python3
"""
Groq Whisper API 代理
解决KlicStudio硬编码model名"whisper-1"的问题
将 whisper-1 → whisper-large-v3-turbo 转发给Groq

用法: python3 scripts/groq_whisper_proxy.py
服务启动在: http://127.0.0.1:8866
"""
import logging
import httpx
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import Response, JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE = "https://api.groq.com/openai/v1"
TARGET_MODEL = "whisper-large-v3-turbo"

app = FastAPI(title="Groq Whisper Proxy")


@app.post("/v1/audio/transcriptions")
async def transcribe(request: Request):
    """
    转发音频转录请求到Groq，自动替换model名
    """
    # 读取原始multipart表单
    form = await request.form()

    # 构建转发的表单数据
    files = {}
    data = {}

    for key, value in form.items():
        if key == "model":
            # 替换model名
            original_model = value
            data["model"] = TARGET_MODEL
            logger.info(f"🔄 模型映射: {original_model} → {TARGET_MODEL}")
        elif key == "file":
            # 文件字段
            content = await value.read()
            files["file"] = (value.filename, content, value.content_type)
        else:
            data[key] = value

    if "model" not in data:
        data["model"] = TARGET_MODEL

    logger.info(f"🎤 转发转录请求: model={data['model']}, 文件大小={len(files.get('file', (None,b''))[1])/1024:.0f}KB")

    async with httpx.AsyncClient(timeout=300) as client:
        try:
            resp = await client.post(
                f"{GROQ_BASE}/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files=files,
                data=data,
            )

            logger.info(f"✅ Groq响应: HTTP {resp.status_code}, 大小: {len(resp.content)}B")

            # 过滤掉压缩/传输相关的header，避免gzip解码问题
            skip_headers = {"content-encoding", "transfer-encoding", "content-length"}
            clean_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in skip_headers
            }
            clean_headers["content-type"] = "application/json"

            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=clean_headers,
            )

        except Exception as e:
            logger.error(f"❌ 转发失败: {e}")
            return JSONResponse(
                status_code=500,
                content={"error": str(e)},
            )


@app.get("/v1/models")
async def list_models():
    """返回模型列表"""
    return {
        "data": [
            {"id": "whisper-1", "object": "model"},
            {"id": "whisper-large-v3", "object": "model"},
            {"id": "whisper-large-v3-turbo", "object": "model"},
        ]
    }


@app.get("/")
async def root():
    return {"service": "groq-whisper-proxy", "status": "running", "target": TARGET_MODEL}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "groq-whisper-proxy",
        "target_model": TARGET_MODEL,
    }


if __name__ == "__main__":
    print("=" * 50)
    print("🎤 Groq Whisper Proxy")
    print(f"   whisper-1 → {TARGET_MODEL}")
    print("   http://127.0.0.1:8866")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8866)
