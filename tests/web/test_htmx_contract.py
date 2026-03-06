import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = PROJECT_ROOT / "web" / "templates"

HX_ATTR_PATTERN = re.compile(r'hx-(get|post|delete)\s*=\s*"([^"]+)"')
JINJA_EXPR_PATTERN = re.compile(r"\{\{[^}]+\}\}")


def _normalize_hx_path(path: str) -> str:
    path_without_query = path.split("?", 1)[0].strip()
    normalized = JINJA_EXPR_PATTERN.sub("{param}", path_without_query)
    return re.sub(r"/{2,}", "/", normalized)


def _is_dynamic_segment(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _path_matches(template_path: str, route_path: str) -> bool:
    template_parts = [p for p in template_path.strip("/").split("/") if p]
    route_parts = [p for p in route_path.strip("/").split("/") if p]

    if len(template_parts) != len(route_parts):
        return False

    for template_segment, route_segment in zip(template_parts, route_parts):
        if _is_dynamic_segment(template_segment) or _is_dynamic_segment(route_segment):
            continue
        if template_segment != route_segment:
            return False

    return True


def test_all_hx_routes_exist(app):
    routes_by_method = {"GET": [], "POST": [], "DELETE": []}
    for route in app.routes:
        methods = getattr(route, "methods", None)
        route_path = getattr(route, "path", None)
        if not methods or not route_path:
            continue
        for method in methods:
            if method in routes_by_method:
                routes_by_method[method].append(route_path)

    missing = []
    for template_path in TEMPLATE_ROOT.rglob("*.html"):
        content = template_path.read_text(encoding="utf-8")
        for raw_method, raw_path in HX_ATTR_PATTERN.findall(content):
            method = raw_method.upper()
            if raw_path.startswith(("http://", "https://")):
                continue

            normalized_path = _normalize_hx_path(raw_path)
            matched = any(
                _path_matches(normalized_path, route_path)
                for route_path in routes_by_method[method]
            )
            if not matched:
                missing.append(
                    f"{template_path.relative_to(PROJECT_ROOT)} -> {method} {raw_path}"
                )

    assert not missing, "发现未实现的 HTMX 路由:\n" + "\n".join(missing)
