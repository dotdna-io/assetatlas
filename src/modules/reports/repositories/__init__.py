# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Reports Repositories

Data access layer for reports and system-wide dashboard preferences.
"""

from src.modules.reports.repositories.reports_repository import ReportsRepository
from src.modules.reports.repositories.system_preferences_repository import SystemPreferencesRepository

__all__ = ['ReportsRepository', 'SystemPreferencesRepository']
