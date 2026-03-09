"""
存储管理模块 - 统一管理R2/本地/NAS的文件操作
"""
import subprocess
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging
from datetime import datetime, timezone, timedelta

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

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """格式化文件大小"""
        value = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024.0:
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} PB"

    @staticmethod
    def _parse_time(iso_time: str) -> Optional[datetime]:
        if not iso_time:
            return None
        try:
            text = iso_time.replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @classmethod
    def _format_time(cls, iso_time: str) -> str:
        """格式化时间为相对时间"""
        dt = cls._parse_time(iso_time)
        if not dt:
            return "unknown"
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        if delta.seconds >= 3600:
            return f"{delta.seconds // 3600}h ago"
        if delta.seconds >= 60:
            return f"{delta.seconds // 60}m ago"
        return "just now"

    def list_files_with_details(self, r2_path: str = "") -> List[Dict[str, Any]]:
        """
        列出R2文件详情

        使用 rclone lsjson 获取详细信息（递归）
        """
        try:
            full_r2_path = f"{self.r2_prefix}/{r2_path}" if r2_path else self.r2_prefix
            cmd = ["rclone", "lsjson", full_r2_path, "-R"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if result.returncode != 0:
                logger.error(f"列出文件详情失败: {result.stderr}")
                return []

            import json
            items = json.loads(result.stdout) if result.stdout else []
            files: List[Dict[str, Any]] = []
            for item in items:
                if item.get("IsDir"):
                    continue
                path_value = item.get("Path") or item.get("Name") or item.get("path") or ""
                name_value = item.get("Name") or Path(path_value).name
                size_value = int(item.get("Size") or 0)
                mod_time = item.get("ModTime") or item.get("mod_time") or ""
                relative_path = f"{r2_path}/{path_value}".strip("/") if r2_path else path_value
                files.append(
                    {
                        "name": name_value,
                        "size": size_value,
                        "size_human": self._format_size(size_value),
                        "modified": mod_time,
                        "modified_human": self._format_time(mod_time),
                        "path": relative_path,
                    }
                )
            return files
        except Exception as e:
            logger.error(f"列出文件详情异常: {e}")
            return []

    def delete_files(self, r2_paths: List[str]) -> int:
        """
        批量删除R2文件
        """
        deleted = 0
        for r2_path in r2_paths:
            if not r2_path:
                continue
            if self.delete_from_r2(r2_path):
                deleted += 1
        return deleted

    def cleanup_old_files(self, r2_path: str, days: int) -> Dict[str, Any]:
        """
        清理过期文件
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        files = self.list_files_with_details(r2_path)
        deleted = 0
        freed_bytes = 0
        for item in files:
            dt = self._parse_time(item.get("modified", ""))
            if not dt:
                continue
            if dt < cutoff:
                if self.delete_from_r2(item["path"]):
                    deleted += 1
                    freed_bytes += int(item.get("size", 0) or 0)
                    logger.info(f"🗑️ 清理过期文件: {item['path']}")
        return {
            "deleted": deleted,
            "freed_bytes": freed_bytes,
            "freed_human": self._format_size(freed_bytes),
        }

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

    def _safe_join(self, base_dir: Path, relative_path: str) -> Optional[Path]:
        if not relative_path:
            return None
        candidate = (base_dir / relative_path).resolve()
        if base_dir.resolve() not in candidate.parents and candidate != base_dir.resolve():
            return None
        return candidate

    def list_files_with_details(self, path: str = "working") -> List[Dict[str, Any]]:
        """
        列出本地文件详情
        """
        if path == "working":
            base_dir = self.working_dir
        elif path == "output":
            base_dir = self.output_dir
        else:
            return []

        files: List[Dict[str, Any]] = []
        if not base_dir.exists():
            return files

        for root, _, filenames in os.walk(base_dir):
            for filename in filenames:
                filepath = Path(root) / filename
                try:
                    stat = filepath.stat()
                except FileNotFoundError:
                    continue
                rel_path = str(filepath.relative_to(base_dir))
                mod_time = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
                files.append(
                    {
                        "name": filename,
                        "size": stat.st_size,
                        "size_human": StorageManager._format_size(stat.st_size),
                        "modified": mod_time,
                        "modified_human": StorageManager._format_time(mod_time),
                        "path": rel_path,
                    }
                )
        return files

    def delete_files(self, paths: List[str]) -> int:
        """
        批量删除本地文件
        """
        deleted = 0
        for rel_path in paths:
            if not rel_path:
                continue
            removed = False
            for base_dir in [self.working_dir, self.output_dir]:
                target = self._safe_join(base_dir, rel_path)
                if not target or not target.exists():
                    continue
                try:
                    target.unlink()
                    logger.info(f"🗑️ 删除文件: {target}")
                    deleted += 1
                    removed = True
                    break
                except Exception as e:
                    logger.error(f"删除文件失败: {target}, {e}")
            if removed:
                continue
        return deleted

    def cleanup_old_files(self, path: str, days: int) -> Dict[str, Any]:
        """清理本地过期文件"""
        if path == "working":
            base_dir = self.working_dir
        elif path == "output":
            base_dir = self.output_dir
        else:
            return {"deleted": 0, "freed_bytes": 0, "freed_human": StorageManager._format_size(0)}

        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0
        freed_bytes = 0
        if not base_dir.exists():
            return {"deleted": 0, "freed_bytes": 0, "freed_human": StorageManager._format_size(0)}

        for root, _, filenames in os.walk(base_dir):
            for filename in filenames:
                filepath = Path(root) / filename
                try:
                    stat = filepath.stat()
                except FileNotFoundError:
                    continue
                file_time = datetime.fromtimestamp(stat.st_mtime)
                if file_time < cutoff:
                    try:
                        filepath.unlink()
                        deleted += 1
                        freed_bytes += stat.st_size
                        logger.info(f"🗑️ 清理过期文件: {filepath}")
                    except Exception as e:
                        logger.error(f"清理文件失败: {filepath}, {e}")
        return {
            "deleted": deleted,
            "freed_bytes": freed_bytes,
            "freed_human": StorageManager._format_size(freed_bytes),
        }
