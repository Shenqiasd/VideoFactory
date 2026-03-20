"""
全局测试 fixtures — 在每个测试前重置速率限制器，避免跨测试限流干扰。
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置 slowapi limiter 内部存储。"""
    from api.rate_limit import limiter
    limiter.reset()
    yield
    limiter.reset()
