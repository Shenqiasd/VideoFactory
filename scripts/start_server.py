#!/usr/bin/env python3
"""
启动video-factory服务
同时启动: FastAPI HTTP服务 + Worker后台任务
"""
import sys
import os
import asyncio
import logging
import signal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """启动FastAPI服务"""
    api_host = os.environ.get("VF_API_HOST", "localhost")
    api_port = int(os.environ.get("VF_API_PORT", "8087"))

    print("=" * 50)
    print("🚀 video-factory 启动中...")
    print("=" * 50)
    print()
    print(f"API服务: http://{api_host}:{api_port}")
    print(f"API文档: http://{api_host}:{api_port}/docs")
    print()

    uvicorn.run(
        "api.server:app",
        host=api_host,
        port=api_port,
        reload=False,
        log_level="info",
        workers=1,
    )


if __name__ == "__main__":
    main()
