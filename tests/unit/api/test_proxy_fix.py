# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the ProxyFix middleware initialisation in src/api/factory.py."""
from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from src.api.factory import _init_proxy_fix


def test_init_proxy_fix_does_nothing_when_hops_zero():
    app = Flask(__name__)
    app.config['TRUSTED_PROXY_HOPS'] = 0

    _init_proxy_fix(app, should_log=False)

    # No instance attribute was set; wsgi_app is still resolved via Flask's method
    assert 'wsgi_app' not in app.__dict__
    assert not isinstance(app.wsgi_app, ProxyFix)


def test_init_proxy_fix_wraps_when_hops_positive():
    app = Flask(__name__)
    app.config['TRUSTED_PROXY_HOPS'] = 1

    _init_proxy_fix(app, should_log=False)

    # Instance attribute was set to a ProxyFix that wraps Flask's wsgi_app method
    assert 'wsgi_app' in app.__dict__
    assert isinstance(app.wsgi_app, ProxyFix)
    inner = app.wsgi_app.app
    # ProxyFix wraps the original bound method of Flask.wsgi_app
    assert getattr(inner, '__self__', None) is app
    assert getattr(inner, '__func__', None) is Flask.wsgi_app


def test_proxy_fix_rewrites_remote_addr_from_x_forwarded_for():
    """End-to-end: with hops=1, X-Forwarded-For becomes request.remote_addr."""
    app = Flask(__name__)
    app.config['TRUSTED_PROXY_HOPS'] = 1

    @app.route('/peek')
    def peek():
        return request.remote_addr or ''

    _init_proxy_fix(app, should_log=False)

    with app.test_client() as client:
        resp = client.get(
            '/peek',
            environ_base={'REMOTE_ADDR': '172.17.0.2'},  # Simulated proxy IP
            headers={'X-Forwarded-For': '203.0.113.42'},  # Real client IP
        )
        assert resp.status_code == 200
        assert resp.data.decode() == '203.0.113.42'


def test_proxy_fix_disabled_keeps_proxy_remote_addr():
    """End-to-end: with hops=0, X-Forwarded-For is ignored."""
    app = Flask(__name__)
    app.config['TRUSTED_PROXY_HOPS'] = 0

    @app.route('/peek')
    def peek():
        return request.remote_addr or ''

    _init_proxy_fix(app, should_log=False)

    with app.test_client() as client:
        resp = client.get(
            '/peek',
            environ_base={'REMOTE_ADDR': '172.17.0.2'},
            headers={'X-Forwarded-For': '203.0.113.42'},
        )
        assert resp.status_code == 200
        # ProxyFix is OFF — original REMOTE_ADDR wins.
        assert resp.data.decode() == '172.17.0.2'
