#!/usr/bin/env python3
"""
Edge-TTS 代理服务
将 OpenAI TTS API 格式的请求转换为 Python edge-tts 调用

解决KlicStudio自带的edge-tts二进制是arm64无法在Intel Mac上运行的问题。
通过设置 KlicStudio 的 tts.provider = "openai" 并将 base_url 指向本代理，
实现 edge-tts 的功能。

用法: python3.11 scripts/edge_tts_proxy.py
服务启动在: http://127.0.0.1:8877
"""
import logging
import asyncio
import os
import tempfile
from pathlib import Path

import edge_tts
from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Edge-TTS Proxy (OpenAI TTS API Compatible)")

# voice映射：OpenAI voice name → Edge-TTS voice name
VOICE_MAP = {
    # 中文
    "alloy": "zh-CN-XiaoxiaoNeural",
    "echo": "zh-CN-YunxiNeural",
    "fable": "zh-CN-YunyangNeural",
    "onyx": "zh-CN-YunjianNeural",
    "nova": "zh-CN-XiaoyiNeural",
    "shimmer": "zh-CN-XiaochenNeural",
    # 直接传Edge-TTS voice名称也支持
}

# 模型不重要，都使用edge-tts
SUPPORTED_MODELS = ["tts-1", "tts-1-hd"]


@app.post("/v1/audio/speech")
async def create_speech(request: Request):
    """
    OpenAI TTS API 兼容接口
    接收标准格式请求，使用edge-tts生成语音
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    input_text = body.get("input", "")
    voice = body.get("voice", "") or "alloy"  # KlicStudio可能传空字符串
    model = body.get("model", "tts-1")
    response_format = body.get("response_format", "mp3")
    speed = body.get("speed", 1.0)

    if not input_text:
        return JSONResponse(status_code=400, content={"error": "input text is required"})

    # 映射voice（空字符串、None等都默认使用中文女声）
    if not voice or voice.strip() == "":
        voice = "alloy"
    edge_voice = VOICE_MAP.get(voice, voice)  # 如果不在映射表中，直接当做edge-tts voice name
    # 最终检查：如果voice仍然无效，使用默认中文语音
    if not edge_voice or edge_voice.strip() == "":
        edge_voice = "zh-CN-XiaoxiaoNeural"

    logger.info(f"🎤 TTS请求: voice={voice}→{edge_voice}, 文本长度={len(input_text)}, format={response_format}")

    try:
        # 创建临时文件
        suffix = f".{response_format}" if response_format in ["mp3", "wav", "opus", "aac", "flac"] else ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        # 使用edge-tts生成语音
        # edge-tts只支持mp3输出，但对大多数场景够用
        rate_str = ""
        if speed != 1.0:
            # edge-tts 速率格式: "+10%", "-20%", etc.
            pct = int((speed - 1.0) * 100)
            rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"

        communicate = edge_tts.Communicate(input_text, edge_voice, rate=rate_str if rate_str else "+0%")
        await communicate.save(tmp_path)

        # 读取并返回
        with open(tmp_path, "rb") as f:
            audio_data = f.read()

        # 清理临时文件
        os.unlink(tmp_path)

        content_type_map = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "opus": "audio/opus",
            "aac": "audio/aac",
            "flac": "audio/flac",
        }

        logger.info(f"✅ TTS完成: {len(audio_data)} bytes")

        return Response(
            content=audio_data,
            media_type=content_type_map.get(response_format, "audio/mpeg"),
            headers={
                "Content-Disposition": f'attachment; filename="speech.{response_format}"',
            },
        )

    except Exception as e:
        logger.error(f"❌ TTS失败: {e}")
        # 清理临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return JSONResponse(
            status_code=500,
            content={"error": {"message": str(e), "type": "server_error"}},
        )


@app.get("/v1/models")
async def list_models():
    """模型列表"""
    return {
        "data": [
            {"id": "tts-1", "object": "model", "owned_by": "edge-tts-proxy"},
            {"id": "tts-1-hd", "object": "model", "owned_by": "edge-tts-proxy"},
        ]
    }


@app.get("/v1/audio/voices")
async def list_voices():
    """列出可用的语音"""
    return {
        "voices": [
            {"voice_id": k, "name": k, "edge_tts_voice": v}
            for k, v in VOICE_MAP.items()
        ]
    }


@app.get("/")
async def root():
    return {"service": "edge-tts-proxy", "status": "running", "api": "OpenAI TTS Compatible"}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "edge-tts-proxy",
        "models": SUPPORTED_MODELS,
    }


if __name__ == "__main__":
    print("=" * 50)
    print("🎤 Edge-TTS Proxy (OpenAI TTS API Compatible)")
    print("   http://127.0.0.1:8877")
    print("   Maps OpenAI TTS API → Python edge-tts")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8877)
