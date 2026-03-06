"""
存储管理模块 - 统一管理R2/本地/NAS的文件操作
"""
import subprocess
import os
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class StorageManager:
    """统一存储管理器"""

    def __init__(self, bucket: str = "videoflow", rclone_remote: str = "r2"):
        self.bucket = bucket
        self.rclone_remote = rclone_remote
        self.r2_prefix = f"{rclone_remote}:{bucket}"

    def upload_to_r2(self, local_path: str, r2_path: str) -> bool:
        """
        上传文件到R2

        rclone copy 的语义：将local_path文件复制到r2目标目录
        所以我们需要将r2_path拆分为目录部分和文件名部分

        Args:
            local_path: 本地文件路径
            r2_path: R2相对路径（如 raw/video.mp4）

        Returns:
            bool: 是否成功
        """
        try:
            # rclone copy <src_file> <dest_dir> — 需要目标是目录
            r2_dir = os.path.dirname(r2_path)
            r2_filename = os.path.basename(r2_path)
            local_filename = os.path.basename(local_path)

            if r2_dir:
                full_r2_dir = f"{self.r2_prefix}/{r2_dir}"
            else:
                full_r2_dir = self.r2_prefix

            # 如果本地文件名和目标文件名不同，需要用copyto
            if local_filename != r2_filename:
                full_r2_path = f"{self.r2_prefix}/{r2_path}"
                cmd = ["rclone", "copyto", local_path, full_r2_path, "-v"]
            else:
                cmd = ["rclone", "copy", local_path, full_r2_dir, "-v"]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info(f"✅ 上传成功: {local_path} → {self.r2_prefix}/{r2_path}")
                return True
            else:
                logger.error(f"❌ 上传失败: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"上传异常: {e}")
            return False

    def download_from_r2(self, r2_path: str, local_path: str) -> bool:
        """
        从R2下载文件到指定本地路径

        使用 rclone copyto 实现精确的文件到文件复制

        Args:
            r2_path: R2相对路径（如 raw/video.mp4）
            local_path: 本地保存的完整文件路径

        Returns:
            bool: 是否成功
        """
        try:
            full_r2_path = f"{self.r2_prefix}/{r2_path}"

            # 确保本地目录存在
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)

            # 使用copyto精确复制到目标文件路径（不是目录）
            cmd = ["rclone", "copyto", full_r2_path, local_path, "-v"]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0 and os.path.exists(local_path):
                logger.info(f"✅ 下载成功: {full_r2_path} → {local_path}")
                return True
            elif result.returncode == 0:
                logger.warning(f"⚠️ rclone返回成功但文件不存在: {local_path}")
                return False
            else:
                logger.error(f"❌ 下载失败: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"下载异常: {e}")
            return False

    def list_r2_files(self, r2_path: str = "") -> list[str]:
        """
        列出R2目录下的文件

        Args:
            r2_path: R2相对路径（如 raw/）

        Returns:
            list[str]: 文件列表
        """
        try:
            full_r2_path = f"{self.r2_prefix}/{r2_path}" if r2_path else self.r2_prefix
            cmd = ["rclone", "lsf", full_r2_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                files = [f.strip() for f in result.stdout.split('\n') if f.strip()]
                return files
            else:
                logger.error(f"列出文件失败: {result.stderr}")
                return []
        except Exception as e:
            logger.error(f"列出文件异常: {e}")
            return []

    def delete_from_r2(self, r2_path: str) -> bool:
        """
        删除R2上的文件

        Args:
            r2_path: R2相对路径

        Returns:
            bool: 是否成功
        """
        try:
            full_r2_path = f"{self.r2_prefix}/{r2_path}"
            cmd = ["rclone", "delete", full_r2_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info(f"✅ 删除成功: {full_r2_path}")
                return True
            else:
                logger.error(f"❌ 删除失败: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"删除异常: {e}")
            return False

    def sync_to_r2(self, local_dir: str, r2_path: str) -> bool:
        """
        同步本地目录到R2

        Args:
            local_dir: 本地目录
            r2_path: R2路径

        Returns:
            bool: 是否成功
        """
        try:
            full_r2_path = f"{self.r2_prefix}/{r2_path}"
            cmd = ["rclone", "sync", local_dir, full_r2_path, "-v"]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info(f"✅ 同步成功: {local_dir} → {full_r2_path}")
                return True
            else:
                logger.error(f"❌ 同步失败: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"同步异常: {e}")
            return False

    def get_file_size(self, r2_path: str) -> Optional[int]:
        """
        获取R2文件大小（字节）

        Args:
            r2_path: R2相对路径

        Returns:
            Optional[int]: 文件大小（字节），失败返回None
        """
        try:
            full_r2_path = f"{self.r2_prefix}/{r2_path}"
            cmd = ["rclone", "size", full_r2_path, "--json"]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                return data.get("bytes", 0)
            else:
                return None
        except Exception as e:
            logger.error(f"获取文件大小异常: {e}")
            return None


class LocalStorage:
    """本地存储管理"""

    def __init__(self, working_dir: str = "/tmp/video-factory/working",
                 output_dir: str = "/tmp/video-factory/output"):
        self.working_dir = Path(working_dir)
        self.output_dir = Path(output_dir)

        # 创建目录
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_task_working_dir(self, task_id: str) -> Path:
        """获取任务的工作目录"""
        task_dir = self.working_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def get_task_output_dir(self, task_id: str) -> Path:
        """获取任务的输出目录"""
        output_dir = self.output_dir / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def cleanup_task(self, task_id: str):
        """清理任务的本地文件"""
        import shutil

        working_dir = self.working_dir / task_id
        output_dir = self.output_dir / task_id

        if working_dir.exists():
            shutil.rmtree(working_dir)
            logger.info(f"🗑️ 清理工作目录: {working_dir}")

        if output_dir.exists():
            shutil.rmtree(output_dir)
            logger.info(f"🗑️ 清理输出目录: {output_dir}")

    def get_disk_usage(self) -> dict:
        """获取磁盘使用情况"""
        import shutil

        total, used, free = shutil.disk_usage(self.working_dir)

        return {
            "total_gb": total // (2**30),
            "used_gb": used // (2**30),
            "free_gb": free // (2**30),
            "usage_percent": (used / total) * 100
        }
