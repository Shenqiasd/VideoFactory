from __future__ import annotations

import logging
from typing import Any, Dict, List

from factory.long_video import LongVideoProcessor

logger = logging.getLogger(__name__)

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    cv2 = None

try:
    from ultralytics import YOLO  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    YOLO = None


class SubjectDetector:
    """主体检测器，缺少依赖时自动回退中心焦点。"""

    def __init__(self, model_name: str = "yolov8n.pt", sample_every_seconds: float = 1.0):
        self.model_name = model_name
        self.sample_every_seconds = max(0.2, float(sample_every_seconds))
        self.long_video = LongVideoProcessor()
        self._model = None

    def _load_model(self):
        if self._model is not None or YOLO is None:
            return self._model
        try:  # pragma: no cover - optional dependency
            self._model = YOLO(self.model_name)
        except Exception as exc:
            logger.warning("YOLO 模型加载失败，降级中心裁剪: %s", exc)
            self._model = None
        return self._model

    @staticmethod
    def _center_track(width: int, height: int) -> Dict[str, Any]:
        focus_width = max(1, int(width * 0.4))
        focus_height = max(1, int(height * 0.7))
        x = max(0, int((width - focus_width) / 2))
        y = max(0, int((height - focus_height) / 2))
        return {
            "strategy": "center",
            "focus_class": "center",
            "video_width": width,
            "video_height": height,
            "samples": [
                {
                    "time": 0.0,
                    "bbox": [x, y, focus_width, focus_height],
                }
            ],
        }

    @staticmethod
    def _pick_focus_bbox(results) -> Dict[str, Any] | None:
        try:  # pragma: no cover - optional dependency
            boxes = results.boxes
        except Exception:
            return None

        best = None
        best_score = -1.0
        focus_label = "center"
        for box in boxes:
            try:
                cls_id = int(box.cls[0])
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = [float(value) for value in box.xyxy[0]]
            except Exception:
                continue
            if cls_id not in {0, 62, 63}:
                continue
            width = max(1.0, x2 - x1)
            height = max(1.0, y2 - y1)
            area_score = width * height
            priority = 2.0 if cls_id == 0 else 1.0
            score = area_score * priority * max(0.01, confidence)
            if score <= best_score:
                continue
            best_score = score
            focus_label = "person" if cls_id == 0 else "screen"
            best = {
                "bbox": [int(x1), int(y1), int(width), int(height)],
                "label": focus_label,
            }
        return best

    async def detect(self, video_path: str) -> Dict[str, Any]:
        info = await self.long_video.get_video_info(video_path)
        width = int((info or {}).get("width", 1920) or 1920)
        height = int((info or {}).get("height", 1080) or 1080)
        duration = float((info or {}).get("duration", 0.0) or 0.0)

        if cv2 is None:
            return self._center_track(width, height)

        model = self._load_model()
        if model is None:
            return self._center_track(width, height)

        capture = cv2.VideoCapture(video_path)  # pragma: no cover - media dependent
        if not capture.isOpened():  # pragma: no cover - media dependent
            return self._center_track(width, height)

        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        frame_step = max(1, int(fps * self.sample_every_seconds))
        samples: List[Dict[str, Any]] = []
        focus_class = "center"
        index = 0

        try:  # pragma: no cover - media dependent
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if index % frame_step != 0:
                    index += 1
                    continue

                timestamp = index / fps
                if duration and timestamp > duration:
                    break

                try:
                    results = model(frame, verbose=False)
                except Exception as exc:
                    logger.warning("YOLO 帧检测失败，降级中心裁剪: %s", exc)
                    samples = []
                    break
                picked = self._pick_focus_bbox(results[0] if results else None)
                if picked:
                    focus_class = picked["label"]
                    samples.append({"time": round(timestamp, 3), "bbox": picked["bbox"]})
                index += 1
        finally:  # pragma: no cover - media dependent
            capture.release()

        if not samples:
            return self._center_track(width, height)

        return {
            "strategy": "yolo",
            "focus_class": focus_class,
            "video_width": width,
            "video_height": height,
            "samples": samples,
        }
