#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SentinelOne Singularity SIEM Connector

Collects asset and security data from SentinelOne Singularity SIEM using PowerQuery.
Uses API Token authentication with Service User credentials.

This connector is distinct from the SentinelOne EDR connector:
- EDR connector: Fetches agent/endpoint data directly from the agents API
- SIEM connector: Runs PowerQuery searches against the Singularity Data Lake

PowerQuery Language:
- SQL-like syntax for querying security event data
- Commands: filter, columns, group by, join, limit, sort, parse, union
- Functions: sum, count, avg, median, min, max, percentile
- Use cases: Threat hunting, incident investigation, asset discovery
"""

from typing import Dict, List, Iterator, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import re
import time

from src.modules.integrations import BaseConnector, ConnectorRegistry
from src.modules.integrations.base import APIKeyAuth, ConfigSchema


@ConnectorRegistry.register
class SentinelOneSiemConnector(BaseConnector):
    """
    SentinelOne Singularity SIEM Connector

    Fetches asset and security data using PowerQuery searches.
    Requires Service User with Data Lake access.
    """

    # Connector Metadata
    integration_type = "sentinelone_siem"
    display_name = "SentinelOne Singularity SIEM"
    category = "SIEM"
    description = "Query asset and security data from SentinelOne Singularity SIEM using PowerQuery"
    logo_url = "/logos/sentinel one.webp"
    connector_role = "transport"

    # API version
    API_VERSION = "v2.1"

    # Maximum hours per query batch — the PowerQuery API only returns complete
    # results for time spans of this length or shorter.
    MAX_BATCH_HOURS = 8

    # Default PowerQuery for asset discovery using Scalyr syntax
    DEFAULT_QUERY = """source='scalyr' | limit 1000""".strip()

    def __init__(self, config: Dict[str, Any]):
        self.base_url = config.get('url', '').rstrip('/')
        self.api_token = config.get('api_token')
        self.powerquery = config.get('powerquery', self.DEFAULT_QUERY).strip()
        self.timespan_hours = config.get('timespan', 24)
        self.limit = config.get('limit', 10000)

        if not self.base_url:
            raise ValueError("SentinelOne URL is required")
        if not self.api_token:
            raise ValueError("SentinelOne API token is required")
        if not self.powerquery:
            raise ValueError("PowerQuery is required")

        super().__init__(config)

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return (
            ConfigSchema()
            .add_string(
                'url', 'SentinelOne XDR URL',
                required=True,
                placeholder='https://xdr.eu1.sentinelone.net',
                description=(
                    'SentinelOne Singularity XDR instance URL (must be XDR hostname format). '
                    'Examples: xdr.eu1.sentinelone.net, xdr.us1.sentinelone.net'
                ),
            )
            .add_password(
                'api_token', 'API Token (Bearer)',
                required=True,
                description=(
                    'Service User API token with Log Read Access / PowerQuery permissions '
                    '(will be encrypted). Token will be used with Bearer authentication.'
                ),
            )
            .add_string(
                'powerquery', 'PowerQuery (Scalyr Syntax)',
                required=False,
                placeholder=cls.DEFAULT_QUERY,
                description=(
                    'Scalyr PowerQuery search to execute for asset data retrieval. '
                    'Leave empty to use default asset discovery query.'
                ),
            )
            .add_number(
                'timespan', 'Time Range (hours)',
                required=False,
                default=24,
                min_val=1,
                max_val=720,
                description='How many hours of data to query (default: 24, max: 720)',
            )
            .add_number(
                'limit', 'Result Limit',
                required=False,
                default=10000,
                min_val=1,
                max_val=100000,
                description='Maximum number of results to return (default: 10000)',
            )
            .build()
        )

    def _build_auth(self) -> APIKeyAuth:
        # SentinelOne XDR uses Bearer token authentication
        return APIKeyAuth(
            {
                'api_key': self.api_token,
                'api_key_header': 'Authorization',
                'api_key_prefix': 'Bearer ',
                'base_url': self.base_url,
            },
            self.logger,
        )

    def test_connection(self) -> Dict[str, Any]:
        """
        Test connection to SentinelOne SIEM.

        XDR instances only have the PowerQuery API, not the management API endpoints.
        We test directly with a simple PowerQuery.
        """
        try:
            self.logger.info("Testing connection to SentinelOne Singularity SIEM...")

            test_query = "source='scalyr' | limit 1"

            try:
                query_result = self._execute_powerquery(test_query, limit=1)

                return {
                    'success': True,
                    'message': 'Successfully connected to SentinelOne Singularity XDR. PowerQuery access verified.',
                    'metadata': {
                        'base_url': self.base_url,
                        'api_version': self.API_VERSION,
                        'powerquery_access': True,
                        'test_query_results': len(query_result) if query_result else 0
                    }
                }
            except Exception as powerquery_error:
                error_msg = str(powerquery_error)
                self.logger.warning(f"PowerQuery test failed: {error_msg}")

                return {
                    'success': False,
                    'message': f'PowerQuery access failed: {error_msg}',
                    'error': error_msg,
                    'metadata': {
                        'base_url': self.base_url,
                        'api_version': self.API_VERSION,
                        'powerquery_access': False
                    }
                }

        except Exception as e:
            import requests

            if isinstance(e, requests.exceptions.HTTPError):
                status_code = e.response.status_code if hasattr(e, 'response') else 'unknown'

                if status_code == 401:
                    error_msg = 'Authentication failed (401 Unauthorized). Verify your API token is correct and has not expired.'
                elif status_code == 403:
                    error_msg = 'Access forbidden (403 Forbidden). Verify your API token has the required permissions (Log Read Access / PowerQuery).'
                elif status_code == 404:
                    error_msg = 'Endpoint not found (404 Not Found). Verify the URL is correct and includes the XDR hostname format (xdr.{region}.sentinelone.net).'
                else:
                    error_msg = f'HTTP {status_code} error: {str(e)}'
            else:
                error_msg = str(e)

            self.logger.error(f"Connection test failed: {error_msg}")
            return {
                'success': False,
                'message': f'Connection failed: {error_msg}',
                'error': error_msg
            }

    def _execute_powerquery(
        self,
        query: str,
        limit: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a single PowerQuery (Scalyr query language) and return results.

        Args:
            query: PowerQuery/Scalyr query string to execute
            limit: Optional limit override
            start_time: Explicit start time (ISO 8601 or relative like "24h").
                        If None, calculated from self.timespan_hours.
            end_time: Explicit end time (ISO 8601). Used for batched time-window queries.

        Returns:
            List of result records (normalized from API response)
        """
        if start_time is None:
            if self.timespan_hours < 24:
                start_time = f"{self.timespan_hours}h"
            elif self.timespan_hours % 24 == 0:
                days = self.timespan_hours // 24
                start_time = f"{days}d"
            else:
                start_time = f"{self.timespan_hours}h"

        payload: Dict[str, Any] = {
            'queryType': 'complex',
            'query': query,
            'startTime': start_time,
            'dataset': 'SentinelOne'
        }

        if end_time:
            payload['endTime'] = end_time

        if limit or self.limit:
            payload['maxCount'] = limit or self.limit

        time_desc = f"{start_time}" + (f" to {end_time}" if end_time else "")
        self.logger.debug(f"Executing PowerQuery ({time_desc}): {query[:100]}...")

        endpoint = '/api/powerQuery'

        try:
            response = self.auth.request(
                endpoint,
                method='POST',
                json=payload
            )

            response_text = response.text
            if '<!doctype html>' in response_text.lower() or '<html' in response_text.lower():
                raise Exception(
                    'Received HTML response instead of JSON. '
                    'API token may not have PowerQuery/Log Read Access permissions, '
                    'or the hostname may not be an XDR instance (should be xdr.{region}.sentinelone.net).'
                )

            data = response.json()

            if data.get('status') != 'success':
                error_msg = data.get('message', 'Unknown error')
                raise Exception(f"PowerQuery execution failed: {error_msg}")

            results = self._normalize_powerquery_results(data)

            self.logger.info(f"PowerQuery executed successfully. Retrieved {len(results)} records.")
            return results

        except Exception as e:
            self.logger.error(f"PowerQuery execution failed: {e}")
            raise

    def _normalize_powerquery_results(self, response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Normalize PowerQuery API response to list of dictionaries.

        Handles two response formats:
        1. Table format: columns[] + values[][] (converts to dicts)
        2. Log format: matches[] (already in dict format)
        """
        # Format 1: Table format (columns + values)
        if 'columns' in response_data and 'values' in response_data:
            columns = [col.get('name') if isinstance(col, dict) else col
                       for col in response_data['columns']]
            values = response_data['values']

            results = []
            for row in values:
                record = {}
                for i, col_name in enumerate(columns):
                    if i < len(row):
                        record[col_name] = row[i]
                results.append(record)

            return results

        # Format 2: Log format (matches)
        if 'matches' in response_data:
            return response_data['matches']

        # Empty or unknown format
        self.logger.warning("Unknown PowerQuery response format, returning empty list")
        return []

    # ------------------------------------------------------------------
    # Time-window batching (max MAX_BATCH_HOURS per API call)
    # ------------------------------------------------------------------

    def _parse_query_aggregation(self, query: str) -> Dict[str, Any]:
        """
        Parse a PowerQuery to detect aggregation patterns.

        Detects group-by fields and aggregation functions (count, sum, avg, min, max)
        so that results from multiple time-window batches can be correctly
        re-aggregated into a single result set.

        Returns:
            Dict with keys:
                has_aggregation  – bool
                group_by_fields  – list of field names
                aggregations     – list of {alias, function, field}
                needs_count_injection – bool (True when avg is used without count)
        """
        info: Dict[str, Any] = {
            'has_aggregation': False,
            'group_by_fields': [],
            'aggregations': [],
            'needs_count_injection': False,
        }

        # Pattern 1: "| group by field1, field2"
        group_by_match = re.search(
            r'\|\s*group\s+by\s+([\w.,\s]+?)(?:\s*\||$)', query, re.IGNORECASE
        )
        if group_by_match:
            fields = [f.strip() for f in group_by_match.group(1).split(',')]
            info['group_by_fields'] = [f for f in fields if f]

        # Pattern 2: "| group field1, field2 compute ..."
        group_compute_match = re.search(
            r'\|\s*group\s+([\w.,\s]+?)\s+compute\s+', query, re.IGNORECASE
        )
        if group_compute_match:
            fields = [f.strip() for f in group_compute_match.group(1).split(',')]
            info['group_by_fields'] = [f for f in fields if f]

        # Pattern 3: Scalyr syntax "| group <aggs> by field1, field2"
        if not info['group_by_fields']:
            scalyr_by_match = re.search(
                r'\|\s*group\s+.+?\sby\s+([\w.,\s]+?)(?:\s*\||$)',
                query, re.IGNORECASE | re.DOTALL
            )
            if scalyr_by_match:
                fields = [f.strip() for f in scalyr_by_match.group(1).split(',')]
                info['group_by_fields'] = [f for f in fields if f]

        # Detect "alias = func(field)" aggregation patterns
        agg_pattern = re.compile(
            r'(\w+)\s*=\s*(count|sum|avg|min|max)\s*\(\s*([\w.]*)\s*\)',
            re.IGNORECASE,
        )
        for match in agg_pattern.finditer(query):
            info['aggregations'].append({
                'alias': match.group(1),
                'function': match.group(2).lower(),
                'field': match.group(3),
            })

        # Detect bare count()/count without alias
        count_agg = {'alias': 'count', 'function': 'count', 'field': ''}
        if not any(a['alias'] == 'count' for a in info['aggregations']):
            found_bare_count = False
            bare_count = re.search(r'\bcount\s*\(\s*\)', query, re.IGNORECASE)
            if bare_count:
                prefix = query[:bare_count.start()].rstrip()
                if not prefix.endswith('='):
                    found_bare_count = True
            if not found_bare_count:
                found_bare_count = bool(
                    re.search(r'\|\s*count\b(?!\s*\()', query, re.IGNORECASE)
                )
            if found_bare_count:
                info['aggregations'].append(dict(count_agg))

        # If group-by exists but no explicit aggregation, assume implicit count
        if info['group_by_fields'] and not info['aggregations']:
            info['aggregations'].append(dict(count_agg))

        info['has_aggregation'] = bool(info['aggregations'])

        # avg requires per-batch record counts for correct weighted averaging
        has_avg = any(a['function'] == 'avg' for a in info['aggregations'])
        has_count = any(a['function'] == 'count' for a in info['aggregations'])
        info['needs_count_injection'] = has_avg and not has_count

        return info

    def _calculate_time_windows(self) -> List[Tuple[str, str]]:
        """
        Split the configured timespan into consecutive non-overlapping windows
        of at most MAX_BATCH_HOURS each.

        Returns:
            List of (start_iso, end_iso) tuples covering the full timespan.
        """
        now = datetime.now(timezone.utc)
        overall_start = now - timedelta(hours=self.timespan_hours)

        windows: List[Tuple[str, str]] = []
        window_start = overall_start

        while window_start < now:
            window_end = min(
                window_start + timedelta(hours=self.MAX_BATCH_HOURS), now
            )
            windows.append((
                window_start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                window_end.strftime('%Y-%m-%dT%H:%M:%SZ'),
            ))
            window_start = window_end

        return windows

    def _inject_batch_count(self, query: str) -> str:
        """
        Inject a hidden ``_batch_count = count()`` into queries that use
        ``avg()`` without an explicit ``count()``.

        This enables correct weighted averaging when re-aggregating across
        time windows.  The ``_batch_count`` column is stripped from the final
        result set after re-aggregation.
        """
        compute_match = re.search(r'(compute\s+)', query, re.IGNORECASE)
        if compute_match:
            pos = compute_match.end()
            return query[:pos] + '_batch_count = count(), ' + query[pos:]

        group_match = re.search(
            r'(\|\s*group\s+by\s+[\w.,\s]+?)(\s*\||\s*$)',
            query, re.IGNORECASE,
        )
        if group_match:
            insert_pos = group_match.end(1)
            return (
                query[:insert_pos]
                + ' compute _batch_count = count()'
                + query[insert_pos:]
            )

        return query

    def _execute_batched_query(
        self, query: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a PowerQuery with automatic time-windowing.

        For timespans > MAX_BATCH_HOURS:
        1. Splits into consecutive 8-hour (or shorter) windows.
        2. Executes the query independently for each window.
        3. Merges partial results (concatenated or re-aggregated).
        """
        if self.timespan_hours <= self.MAX_BATCH_HOURS:
            return self._execute_powerquery(query, limit=limit)

        agg_info = self._parse_query_aggregation(query)

        # When re-aggregating across batches, the per-batch response must
        # carry every group-by field and aggregation alias. A `| columns`
        # clause projects those away, so strip it before sending and
        # re-apply the projection locally after re-aggregation.
        exec_query = query
        columns_spec: Optional[List[Tuple[str, str]]] = None
        if agg_info['has_aggregation']:
            exec_query, columns_spec = self._strip_columns_clause(exec_query)
            if columns_spec:
                self.logger.debug(
                    f"Stripped `| columns` from batched query; "
                    f"will project {len(columns_spec)} columns locally after re-aggregation"
                )

        if agg_info['needs_count_injection']:
            exec_query = self._inject_batch_count(exec_query)
            self.logger.debug("Injected _batch_count for weighted avg re-aggregation")

        windows = self._calculate_time_windows()
        self.logger.info(
            f"Timespan {self.timespan_hours}h exceeds {self.MAX_BATCH_HOURS}h limit. "
            f"Splitting into {len(windows)} batches."
        )

        all_batch_results: List[List[Dict[str, Any]]] = []
        for i, (win_start, win_end) in enumerate(windows, 1):
            self.logger.info(f"Executing batch {i}/{len(windows)}: {win_start} -> {win_end}")
            batch_results = self._execute_powerquery(
                exec_query, limit=limit, start_time=win_start, end_time=win_end,
            )
            all_batch_results.append(batch_results)
            self.logger.info(f"  Batch {i} returned {len(batch_results)} records")

        if agg_info['has_aggregation']:
            results = self._reaggregate_results(all_batch_results, agg_info)
            total_batch = sum(len(b) for b in all_batch_results)
            self.logger.info(
                f"Re-aggregated {total_batch} partial records into {len(results)} final records"
            )
        else:
            results = [record for batch in all_batch_results for record in batch]
            self.logger.info(
                f"Concatenated {len(results)} total records from {len(windows)} batches"
            )

        if columns_spec:
            results = self._apply_columns_projection(results, columns_spec)

        return results

    @staticmethod
    def _strip_columns_clause(
        query: str,
    ) -> Tuple[str, Optional[List[Tuple[str, str]]]]:
        """
        Remove the final ``| columns ...`` clause from a Scalyr PowerQuery.

        SentinelOne applies `| columns` as a projection at the end of the
        pipeline. When batching aggregated queries we need every group-by
        field and aggregation alias in the per-batch response, so we strip
        the clause and apply the projection locally after re-aggregation.

        Behavior:
        - Only the **last** ``| columns`` clause is stripped. Any earlier
          occurrences (e.g. inside a chained projection) remain in the
          query and are evaluated by the API as usual.
        - The regex is not string-literal aware: a ``| columns`` substring
          inside a quoted ``parse`` pattern (e.g.
          ``parse "x | columns y" from message``) would be falsely matched.
          PowerQuery doesn't typically nest pipeline-looking text inside
          string literals, so this is accepted as a known limitation.

        Returns:
            (stripped_query, columns) — ``columns`` is a list of
            ``(output_name, source_field)`` tuples, or None if no clause
            was found. For bare names (no rename), output and source are
            the same.
        """
        columns_matches = list(re.finditer(
            r'\|\s*columns\s+(.+?)(?=\s*\||\s*\Z)',
            query, re.IGNORECASE | re.DOTALL,
        ))
        if not columns_matches:
            return query, None

        m = columns_matches[-1]
        columns: List[Tuple[str, str]] = []
        for spec in m.group(1).split(','):
            spec = spec.strip()
            if not spec:
                continue
            if '=' in spec:
                alias, source = spec.split('=', 1)
                columns.append((alias.strip(), source.strip()))
            else:
                columns.append((spec, spec))

        stripped = (query[:m.start()].rstrip() + query[m.end():]).rstrip()
        return stripped, columns

    @staticmethod
    def _apply_columns_projection(
        records: List[Dict[str, Any]],
        columns: List[Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        """
        Apply a Scalyr ``| columns`` projection: keep only the requested
        fields, optionally renaming them. Source fields missing from a
        record become None.
        """
        projected: List[Dict[str, Any]] = []
        for record in records:
            new_record: Dict[str, Any] = {}
            for alias, source in columns:
                new_record[alias] = record.get(source)
            projected.append(new_record)
        return projected

    def _reaggregate_results(
        self,
        batch_results: List[List[Dict[str, Any]]],
        agg_info: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Re-aggregate partial results from multiple time-window batches.

        Aggregation semantics per function:
            count / sum  -> sum across batches
            min          -> min across batches
            max          -> max across batches
            avg          -> weighted average using _batch_count
        """
        group_by_fields = agg_info['group_by_fields']
        aggregations = agg_info['aggregations']

        grouped: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
        for batch in batch_results:
            for record in batch:
                if group_by_fields:
                    key = tuple(record.get(f) for f in group_by_fields)
                else:
                    key = ('__all__',)
                grouped[key].append(record)

        results: List[Dict[str, Any]] = []
        for key, records in grouped.items():
            merged: Dict[str, Any] = {}

            for i, field in enumerate(group_by_fields):
                merged[field] = key[i]

            for agg in aggregations:
                alias = agg['alias']
                func = agg['function']
                values = [r[alias] for r in records if r.get(alias) is not None]

                if not values:
                    merged[alias] = None
                    continue

                if func in ('count', 'sum'):
                    merged[alias] = sum(values)
                elif func == 'min':
                    merged[alias] = min(values)
                elif func == 'max':
                    merged[alias] = max(values)
                elif func == 'avg':
                    counts = [
                        r['_batch_count'] for r in records
                        if r.get('_batch_count') is not None
                    ]
                    if counts and len(counts) == len(values):
                        total_count = sum(counts)
                        if total_count > 0:
                            merged[alias] = (
                                sum(v * c for v, c in zip(values, counts))
                                / total_count
                            )
                        else:
                            merged[alias] = 0
                    else:
                        self.logger.warning(
                            f"No _batch_count available for weighted avg of '{alias}', "
                            f"using simple average across {len(values)} batches"
                        )
                        merged[alias] = sum(values) / len(values)
                else:
                    merged[alias] = values[-1]

            agg_aliases = {a['alias'] for a in aggregations}
            group_field_set = set(group_by_fields)
            for field, value in records[0].items():
                if (field not in merged
                        and field not in agg_aliases
                        and field not in group_field_set
                        and field != '_batch_count'):
                    merged[field] = value

            results.append(merged)

        for r in results:
            r.pop('_batch_count', None)

        return results

    # ------------------------------------------------------------------

    def _collect_records(self, batch_id: str) -> Iterator[Dict[str, Any]]:
        """
        Execute PowerQuery and yield result records.

        Yields:
            Query result records with _record_type set.
        """
        self.logger.info(f"Starting SentinelOne SIEM PowerQuery execution (batch: {batch_id})")
        self.logger.info(f"Query timespan: last {self.timespan_hours} hours")
        self.logger.info(f"PowerQuery: {self.powerquery[:100]}...")

        results = self._execute_batched_query(self.powerquery)

        for record in results:
            record['_record_type'] = 'powerquery_result'
            yield record

    def get_schema(self) -> Dict[str, Any]:
        """Return field schema for mapping configuration."""
        fields = [
            {
                'name': 'endpoint.name',
                'path': 'endpoint.name',
                'type': 'string',
                'description': 'Endpoint/computer name',
                'required': False
            },
            {
                'name': 'endpoint.ip',
                'path': 'endpoint.ip',
                'type': 'string',
                'description': 'IP address',
                'required': False
            },
            {
                'name': 'endpoint.mac',
                'path': 'endpoint.mac',
                'type': 'string',
                'description': 'MAC address',
                'required': False
            },
            {
                'name': 'endpoint.os',
                'path': 'endpoint.os',
                'type': 'string',
                'description': 'Operating system',
                'required': False
            },
            {
                'name': 'endpoint.domain',
                'path': 'endpoint.domain',
                'type': 'string',
                'description': 'Domain name',
                'required': False
            },
            {
                'name': 'endpoint.type',
                'path': 'endpoint.type',
                'type': 'string',
                'description': 'Endpoint type (server, workstation, etc.)',
                'required': False
            },
            {
                'name': 'event.type',
                'path': 'event.type',
                'type': 'string',
                'description': 'Event type',
                'required': False
            },
            {
                'name': 'event.time',
                'path': 'event.time',
                'type': 'datetime',
                'description': 'Event timestamp',
                'required': False
            },
            {
                'name': 'user.name',
                'path': 'user.name',
                'type': 'string',
                'description': 'Username',
                'required': False
            },
            {
                'name': 'threat.name',
                'path': 'threat.name',
                'type': 'string',
                'description': 'Threat name',
                'required': False
            },
            {
                'name': 'network.src_ip',
                'path': 'network.src_ip',
                'type': 'string',
                'description': 'Source IP address',
                'required': False
            },
            {
                'name': 'network.dst_ip',
                'path': 'network.dst_ip',
                'type': 'string',
                'description': 'Destination IP address',
                'required': False
            }
        ]

        return {
            'source': 'sentinelone_siem',
            'version': '1.0',
            'fields': fields,
            'sample_record_count': 3,
            'notes': 'Actual fields depend on the PowerQuery used. Common fields are listed here.'
        }
