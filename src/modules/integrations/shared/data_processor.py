# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Data Processing Utilities

Provides common data processing functions for connectors:
- Export to JSON, CSV, Excel
- Data flattening
- Summary generation

Created: 2025-11-04
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class DataProcessor:
    """
    Data processing utilities for connector outputs.

    Handles exporting data to various formats and generating summaries.
    """

    def save_json(
        self,
        data: List[Dict[str, Any]],
        filepath: Path,
        indent: int = 2
    ) -> Path:
        """
        Save data to JSON file.

        Args:
            data: List of records to save
            filepath: Output file path
            indent: JSON indentation (default: 2)

        Returns:
            Path to saved file

        Example:
            processor = DataProcessor()
            processor.save_json(devices, Path("devices.json"))
        """
        logger.info(f"Saving {len(data)} records to JSON: {filepath}")

        # Ensure parent directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=indent, default=str)

        logger.info(f"✅ Saved {len(data)} records to {filepath}")
        return filepath

    def save_csv(
        self,
        data: List[Dict[str, Any]],
        filepath: Path,
        columns: Optional[List[str]] = None,
        flatten: bool = True
    ) -> Path:
        """
        Save data to CSV file.

        Args:
            data: List of records to save
            filepath: Output file path
            columns: Specific columns to include (None = all)
            flatten: Whether to flatten nested dictionaries (default: True)

        Returns:
            Path to saved file

        Example:
            processor = DataProcessor()
            processor.save_csv(devices, Path("devices.csv"))
        """
        logger.info(f"Saving {len(data)} records to CSV: {filepath}")

        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas is required for CSV export. Install: pip install pandas")
            raise

        # Ensure parent directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Flatten data if requested
        if flatten:
            data = [self._flatten_dict(record) for record in data]

        # Create DataFrame
        df = pd.DataFrame(data)

        # Select specific columns if provided
        if columns:
            # Only keep columns that exist
            available_columns = [col for col in columns if col in df.columns]
            df = df[available_columns]

        # Save to CSV
        df.to_csv(filepath, index=False)

        logger.info(f"✅ Saved {len(data)} records to {filepath}")
        return filepath

    def save_excel(
        self,
        data: List[Dict[str, Any]],
        filepath: Path,
        sheet_name: str = "Data",
        columns: Optional[List[str]] = None,
        additional_sheets: Optional[Dict[str, Any]] = None
    ) -> Path:
        """
        Save data to Excel file.

        Args:
            filepath: Output file path
            data: List of records to save
            sheet_name: Main sheet name (default: "Data")
            columns: Specific columns to include (None = all)
            additional_sheets: Additional sheets {name: DataFrame or dict}

        Returns:
            Path to saved file

        Example:
            processor = DataProcessor()
            processor.save_excel(
                devices,
                Path("devices.xlsx"),
                sheet_name="Devices",
                additional_sheets={
                    "Summary": summary_df
                }
            )
        """
        logger.info(f"Saving {len(data)} records to Excel: {filepath}")

        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas is required for Excel export. Install: pip install pandas openpyxl")
            raise

        # Ensure parent directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Create Excel writer
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            # Main data sheet
            df = pd.DataFrame(data)

            # Select specific columns if provided
            if columns:
                available_columns = [col for col in columns if col in df.columns]
                df = df[available_columns]

            df.to_excel(writer, sheet_name=sheet_name, index=False)

            # Additional sheets
            if additional_sheets:
                for name, sheet_data in additional_sheets.items():
                    if isinstance(sheet_data, pd.DataFrame):
                        sheet_data.to_excel(writer, sheet_name=name, index=False)
                    else:
                        pd.DataFrame(sheet_data).to_excel(writer, sheet_name=name, index=False)

        logger.info(f"✅ Saved {len(data)} records to {filepath}")
        return filepath

    def _flatten_dict(
        self,
        d: Dict[str, Any],
        parent_key: str = '',
        sep: str = '_'
    ) -> Dict[str, Any]:
        """
        Flatten nested dictionary.

        Args:
            d: Dictionary to flatten
            parent_key: Parent key prefix (used in recursion)
            sep: Separator for nested keys (default: '_')

        Returns:
            Flattened dictionary

        Example:
            nested = {'user': {'name': 'John', 'age': 30}}
            flat = _flatten_dict(nested)
            # {'user_name': 'John', 'user_age': 30}
        """
        items = []

        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k

            if isinstance(v, dict):
                # Recursively flatten nested dicts
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                # Convert lists to strings
                items.append((new_key, str(v)))
            else:
                items.append((new_key, v))

        return dict(items)

    def generate_summary(
        self,
        data: List[Dict[str, Any]],
        group_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate summary statistics for data.

        Args:
            data: List of records
            group_by: Field to group by for breakdown (optional)

        Returns:
            Summary dictionary with statistics

        Example:
            summary = processor.generate_summary(
                devices,
                group_by='operatingSystem'
            )
            # {
            #     'total_records': 1000,
            #     'timestamp': '2025-11-04T10:30:00Z',
            #     'breakdown': {'Windows': 500, 'macOS': 300, ...}
            # }
        """
        summary = {
            'total_records': len(data),
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }

        if group_by and data:
            breakdown = {}
            for record in data:
                value = record.get(group_by, 'unknown')

                # Handle nested values
                if isinstance(value, dict):
                    value = value.get('value', 'unknown')
                elif isinstance(value, list):
                    value = value[0] if value else 'unknown'

                value = str(value)
                breakdown[value] = breakdown.get(value, 0) + 1

            summary['breakdown'] = breakdown
            summary['breakdown_field'] = group_by
            summary['unique_values'] = len(breakdown)

            # Add top value
            if breakdown:
                top = max(breakdown.items(), key=lambda x: x[1])
                summary['top_value'] = {
                    'name': top[0],
                    'count': top[1],
                    'percentage': round(top[1] / len(data) * 100, 2)
                }

        return summary

    def save_summary(
        self,
        summary: Dict[str, Any],
        filepath: Path
    ) -> Path:
        """
        Save summary to JSON file.

        Args:
            summary: Summary dictionary
            filepath: Output file path

        Returns:
            Path to saved file
        """
        logger.info(f"Saving summary to: {filepath}")

        # Ensure parent directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)

        logger.info(f"✅ Saved summary to {filepath}")
        return filepath

    @staticmethod
    def add_timestamp(record: Dict[str, Any], field_name: str = '_processed_at') -> Dict[str, Any]:
        """
        Add processing timestamp to record.

        Args:
            record: Record to modify
            field_name: Field name for timestamp

        Returns:
            Modified record
        """
        record[field_name] = datetime.utcnow().isoformat() + 'Z'
        return record

    @staticmethod
    def filter_fields(
        records: List[Dict[str, Any]],
        include_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Filter fields from records.

        Args:
            records: List of records
            include_fields: Fields to include (None = all)
            exclude_fields: Fields to exclude

        Returns:
            List of filtered records
        """
        filtered = []

        for record in records:
            filtered_record = {}

            for key, value in record.items():
                # Check include list
                if include_fields and key not in include_fields:
                    continue

                # Check exclude list
                if exclude_fields and key in exclude_fields:
                    continue

                filtered_record[key] = value

            filtered.append(filtered_record)

        return filtered
