#!/usr/bin/env python3
"""
测试video-factory基础设施是否就绪
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.storage import StorageManager, LocalStorage
from production.klicstudio_client import KlicStudioClient


async def test_r2_storage():
    """测试R2存储"""
    print("\n=== 测试 R2 存储 ===")

    storage = StorageManager(bucket="videoflow")

    # 测试上传
    test_file = "/tmp/r2_test.txt"
    with open(test_file, "w") as f:
        f.write(f"R2 test at {asyncio.get_event_loop().time()}")

    success = storage.upload_to_r2(test_file, "test/r2_test.txt")
    print(f"上传测试: {'✅' if success else '❌'}")

    # 测试列出文件
    files = storage.list_r2_files("test/")
    print(f"列出文件: {files}")

    # 测试下载
    download_path = "/tmp/r2_downloaded.txt"
    success = storage.download_from_r2("test/r2_test.txt", download_path)
    print(f"下载测试: {'✅' if success else '❌'}")

    if success:
        with open(download_path, "r") as f:
            print(f"下载内容: {f.read()}")

    # 清理
    storage.delete_from_r2("test/r2_test.txt")
    print("✅ R2存储测试完成")


async def test_local_storage():
    """测试本地存储"""
    print("\n=== 测试本地存储 ===")

    storage = LocalStorage()

    # 创建任务目录
    task_id = "test_task_001"
    working_dir = storage.get_task_working_dir(task_id)
    output_dir = storage.get_task_output_dir(task_id)

    print(f"工作目录: {working_dir}")
    print(f"输出目录: {output_dir}")

    # 磁盘使用
    usage = storage.get_disk_usage()
    print(f"磁盘使用: {usage['used_gb']}/{usage['total_gb']} GB ({usage['usage_percent']:.1f}%)")

    # 清理
    storage.cleanup_task(task_id)
    print("✅ 本地存储测试完成")


async def test_klicstudio():
    """测试KlicStudio连接"""
    print("\n=== 测试 KlicStudio ===")

    client = KlicStudioClient()

    # 测试连接
    config = await client.get_config()

    if config:
        print(f"✅ KlicStudio连接成功")
        print(f"   LLM Model: {config.get('llm', {}).get('model')}")
        print(f"   TTS Provider: {config.get('tts', {}).get('provider')}")
        print(f"   Transcribe Provider: {config.get('transcribe', {}).get('provider')}")
    else:
        print("❌ KlicStudio连接失败")

    await client.close()


async def main():
    print("=" * 50)
    print("video-factory 基础设施测试")
    print("=" * 50)

    # 测试存储
    await test_r2_storage()
    await test_local_storage()

    # 测试KlicStudio
    await test_klicstudio()

    print("\n" + "=" * 50)
    print("✅ 所有测试完成！")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
