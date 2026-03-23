"""
发布模板管理 — 保存常用的发布配置。

模板包含平台列表、标题/描述模板、标签和平台选项，
可以一键应用到视频路径上生成多个发布任务规格。
"""
import json
import uuid
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


class PublishTemplateService:
    """发布模板管理 — 保存常用的发布配置。"""

    def __init__(self, db):
        self.db = db

    def create_template(
        self,
        user_id: str,
        name: str,
        platforms: list,
        title_template: str = "",
        description_template: str = "",
        tags: list = None,
        platform_options: dict = None,
    ) -> dict:
        """创建发布模板。"""
        template_id = str(uuid.uuid4())
        template = {
            "id": template_id,
            "user_id": user_id,
            "name": name,
            "platforms": json.dumps(platforms),
            "title_template": title_template,
            "description_template": description_template,
            "tags": json.dumps(tags or []),
            "platform_options": json.dumps(platform_options or {}),
        }
        self.db.insert_publish_template(template)
        logger.info("模板已创建: id=%s name=%s", template_id, name)
        return {"id": template_id, "name": name}

    def list_templates(self, user_id: str = "") -> list:
        """列出模板（可按 user_id 过滤）。"""
        return self.db.get_publish_templates(user_id=user_id)

    def get_template(self, template_id: str) -> Optional[dict]:
        """获取单个模板。"""
        return self.db.get_publish_template(template_id)

    def update_template(self, template_id: str, **kwargs) -> bool:
        """更新模板字段。"""
        return self.db.update_publish_template(template_id, **kwargs)

    def delete_template(self, template_id: str) -> bool:
        """删除模板。"""
        return self.db.delete_publish_template(template_id)

    def apply_template(
        self,
        template_id: str,
        video_path: str,
        title_vars: dict = None,
        desc_vars: dict = None,
    ) -> list:
        """Apply a template to generate publish task specs for each platform.

        Returns a list of dicts, one per platform, ready to be passed to
        PublishQueue.enqueue().  title_vars / desc_vars are used for simple
        string substitution in templates.
        """
        template = self.db.get_publish_template(template_id)
        if not template:
            logger.warning("模板不存在: %s", template_id)
            return []

        platforms = json.loads(template.get("platforms", "[]"))
        title = template.get("title_template", "")
        description = template.get("description_template", "")
        tags = json.loads(template.get("tags", "[]"))
        options = json.loads(template.get("platform_options", "{}"))

        # Simple variable substitution
        for k, v in (title_vars or {}).items():
            title = title.replace(f"{{{{{k}}}}}", str(v))
        for k, v in (desc_vars or {}).items():
            description = description.replace(f"{{{{{k}}}}}", str(v))

        tasks: List[dict] = []
        for platform in platforms:
            tasks.append({
                "platform": platform,
                "video_path": video_path,
                "title": title,
                "description": description,
                "tags": tags,
                "platform_options": options.get(platform, {}),
            })

        logger.info(
            "模板 %s 已应用: 生成 %d 个任务规格", template_id, len(tasks),
        )
        return tasks
