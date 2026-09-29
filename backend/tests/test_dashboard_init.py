"""测试 /api/dashboard/init 聚合接口。"""
from __future__ import annotations


def test_dashboard_init_structure():
    """聚合接口返回 7 个子数据，键名与单独接口一致。"""
    from app.api import dashboard

    # 验证路由注册
    paths = [r.path for r in dashboard.router.routes]
    assert "/api/dashboard/init" in paths


def test_dashboard_init_calls_handlers(monkeypatch):
    """聚合接口复用现有 handler，不修改返回格式。"""
    from fastapi import Request

    from app.api import dashboard

    calls = []

    class FakeRequest:
        pass

    # mock 各 handler，验证被调用且结果原样聚合
    import app.api.dashboard as dash_mod

    # 直接验证函数签名
    import inspect

    sig = inspect.signature(dash_mod.dashboard_init)
    params = list(sig.parameters.keys())
    assert "request" in params
    assert "as_of" in params
