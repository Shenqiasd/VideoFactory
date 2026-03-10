#!/usr/bin/env python3
"""
本地Whisper API代理服务
提供OpenAI兼容的 /v1/audio/transcriptions 接口
供 VideoFactory 自管 ASR 链路或兼容 OpenAI Whisper 的客户端调用

用法:
  1. pip install openai-whisper fastapi uvicorn python-multipart
  2. python scripts/whisper_proxy.py

服务会在 http://127.0.0.1:8866 启动
客户端配置示例:
  [transcribe.openai]
    base_url = "http://127.0.0.1:8866/v1"
    api_key = "local"
    model = "base"  # 可选: tiny/base/small/medium/large
"""
import sys
import os
import logging
import tempfile
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, UploadFile, File, Form
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError:
    print("请先安装依赖: pip install fastapi uvicorn python-multipart")
    sys.exit(1)

try:
    import whisper
except ImportError:
    print("请先安装openai-whisper: pip install openai-whisper")
    print("注意: 首次安装需要下载PyTorch (~2GB)")
    sys.exit(1)


app = FastAPI(title="Local Whisper Proxy", version="1.0")

# 全局模型缓存
_model = None
_model_name = None


def get_model(model_name: str = "base"):
    """延迟加载Whisper模型"""
    global _model, _model_name
    if _model is None or _model_name != model_name:
        logger.info(f"📦 加载Whisper模型: {model_name}...")
        _model = whisper.load_model(model_name)
        _model_name = model_name
        logger.info(f"✅ Whisper模型 {model_name} 加载完成")
    return _model


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("base"),
    language: str = Form(None),
    response_format: str = Form("json"),
):
    """
    OpenAI-compatible Whisper transcription endpoint
    """
    start_time = time.time()
    logger.info(f"🎤 收到转录请求: {file.filename}, 模型: {model}, 语言: {language}")

    # 保存上传的音频文件到临时目录
    suffix = os.path.splitext(file.filename)[1] if file.filename else ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 加载模型并转录
        whisper_model = get_model(model)

        # 转录参数
        options = {}
        if language:
            options["language"] = language

        result = whisper_model.transcribe(tmp_path, **options)

        elapsed = time.time() - start_time
        text = result.get("text", "")
        detected_lang = result.get("language", "")
        logger.info(f"✅ 转录完成: {len(text)} 字符, 语言: {detected_lang}, 耗时: {elapsed:.1f}秒")

        if response_format == "verbose_json":
            return JSONResponse({
                "text": text,
                "language": detected_lang,
                "duration": result.get("duration", 0),
                "segments": [
                    {
                        "id": seg["id"],
                        "start": seg["start"],
                        "end": seg["end"],
                        "text": seg["text"],
                    }
                    for seg in result.get("segments", [])
                ],
            })
        elif response_format == "srt":
            # 生成SRT格式
            srt_lines = []
            for seg in result.get("segments", []):
                idx = seg["id"] + 1
                start = _format_srt_time(seg["start"])
                end = _format_srt_time(seg["end"])
                srt_lines.append(f"{idx}\n{start} --> {end}\n{seg['text'].strip()}\n")
            return "\n".join(srt_lines)
        else:
            # 默认JSON格式
            return JSONResponse({"text": text})

    finally:
        # 清理临时文件
        os.unlink(tmp_path)


def _format_srt_time(seconds: float) -> str:
    """将秒数转为SRT时间格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


@app.get("/v1/models")
async def list_models():
    """列出可用模型"""
    return {
        "data": [
            {"id": "whisper-1", "object": "model"},
            {"id": "tiny", "object": "model"},
            {"id": "base", "object": "model"},
            {"id": "small", "object": "model"},
            {"id": "medium", "object": "model"},
            {"id": "large", "object": "model"},
        ]
    }


@app.get("/")
async def root():
    return {"service": "local-whisper-proxy", "status": "running"}


if __name__ == "__main__":
    print("=" * 50)
    print("🎤 Local Whisper Proxy Server")
    print("=" * 50)
    print()
    print("API: http://127.0.0.1:8866/v1/audio/transcriptions")
    print()
    print("客户端配置示例:")
    print('  [transcribe.openai]')
    print('    base_url = "http://127.0.0.1:8866/v1"')
    print('    api_key = "local"')
    print('    model = "base"')
    print()

    uvicorn.run(app, host="127.0.0.1", port=8866)
