# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models for Alertmanager v4 webhook payloads.

Reference: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
"""
from typing import Dict, List, Literal

from pydantic import BaseModel, ConfigDict


class AlertmanagerAlert(BaseModel):
    """A single alert inside an Alertmanager webhook payload."""
    model_config = ConfigDict(extra='ignore')

    status: Literal['firing', 'resolved']
    labels: Dict[str, str]
    annotations: Dict[str, str]
    startsAt: str
    endsAt: str
    fingerprint: str


class AlertmanagerPayload(BaseModel):
    """Alertmanager webhook v4 payload."""
    model_config = ConfigDict(extra='ignore')

    version: str
    status: Literal['firing', 'resolved']
    alerts: List[AlertmanagerAlert]
