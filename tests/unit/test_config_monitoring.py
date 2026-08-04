# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate monitoring-related config parsing and ProductionConfig hard-fail.

Rationale:
- Config base class reads env at import time (module load). monkeypatch in
  tests happens after, so class attributes don't re-read. Tests below use
  ProductionConfig.init_app which re-reads env inside the method body.
"""
import pytest

from flask import Flask

from src.api.config import BaseConfig, ProductionConfig


def test_config_has_monitoring_attributes():
    """The BaseConfig class exposes the three monitoring fields."""
    assert hasattr(BaseConfig, 'ALERTMANAGER_WEBHOOK_SECRET')
    assert hasattr(BaseConfig, 'MONITORING_ENABLED')
    assert hasattr(BaseConfig, 'MONITORING_RULES_DIR')


def test_monitoring_enabled_true_requires_secret_in_production(monkeypatch):
    """ProductionConfig.init_app must raise if MONITORING_ENABLED=true and no secret."""
    monkeypatch.setenv('MONITORING_ENABLED', 'true')
    monkeypatch.setenv('ALERTMANAGER_WEBHOOK_SECRET', '')
    monkeypatch.setenv('SECRET_KEY', 'x' * 32)
    monkeypatch.setenv('JWT_SECRET_KEY', 'y' * 32)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost/db')
    monkeypatch.setenv('CORS_ORIGINS', 'https://example.com')
    monkeypatch.setenv('ASSETATLAS_ENCRYPTION_KEY', 'z' * 44)

    app = Flask(__name__)
    with pytest.raises(ValueError, match="ALERTMANAGER_WEBHOOK_SECRET"):
        ProductionConfig.init_app(app)


def test_monitoring_enabled_true_with_secret_ok_in_production(monkeypatch):
    monkeypatch.setenv('MONITORING_ENABLED', 'true')
    monkeypatch.setenv('ALERTMANAGER_WEBHOOK_SECRET', 's' * 32)
    monkeypatch.setenv('SECRET_KEY', 'x' * 32)
    monkeypatch.setenv('JWT_SECRET_KEY', 'y' * 32)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost/db')
    monkeypatch.setenv('CORS_ORIGINS', 'https://example.com')
    monkeypatch.setenv('ASSETATLAS_ENCRYPTION_KEY', 'z' * 44)

    app = Flask(__name__)
    ProductionConfig.init_app(app)  # should not raise


def test_monitoring_disabled_does_not_require_secret_in_production(monkeypatch):
    monkeypatch.setenv('MONITORING_ENABLED', 'false')
    monkeypatch.setenv('ALERTMANAGER_WEBHOOK_SECRET', '')
    monkeypatch.setenv('SECRET_KEY', 'x' * 32)
    monkeypatch.setenv('JWT_SECRET_KEY', 'y' * 32)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost/db')
    monkeypatch.setenv('CORS_ORIGINS', 'https://example.com')
    monkeypatch.setenv('ASSETATLAS_ENCRYPTION_KEY', 'z' * 44)

    app = Flask(__name__)
    ProductionConfig.init_app(app)  # should not raise — monitoring is opt-in


def test_production_init_app_raises_without_encryption_key(monkeypatch):
    """ProductionConfig.init_app must raise if ASSETATLAS_ENCRYPTION_KEY is missing."""
    monkeypatch.setenv('SECRET_KEY', 'x' * 32)
    monkeypatch.setenv('JWT_SECRET_KEY', 'y' * 32)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost/db')
    monkeypatch.setenv('CORS_ORIGINS', 'https://example.com')
    monkeypatch.delenv('ASSETATLAS_ENCRYPTION_KEY', raising=False)

    app = Flask(__name__)
    with pytest.raises(ValueError, match="ASSETATLAS_ENCRYPTION_KEY"):
        ProductionConfig.init_app(app)


def test_production_init_app_succeeds_with_encryption_key(monkeypatch):
    """ProductionConfig.init_app stores the key into app.config when present."""
    encryption_key = 'k' * 44
    monkeypatch.setenv('SECRET_KEY', 'x' * 32)
    monkeypatch.setenv('JWT_SECRET_KEY', 'y' * 32)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost/db')
    monkeypatch.setenv('CORS_ORIGINS', 'https://example.com')
    monkeypatch.setenv('ASSETATLAS_ENCRYPTION_KEY', encryption_key)

    app = Flask(__name__)
    ProductionConfig.init_app(app)  # should not raise
    assert app.config['ASSETATLAS_ENCRYPTION_KEY'] == encryption_key
