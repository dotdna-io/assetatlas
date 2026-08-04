# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Search Blueprint

Provides endpoints for Explorer search functionality:
- GET /api/search/synonyms - Synonym lookup table for real-time regex enhancement
- GET /api/search/fields - Field metadata for autocomplete (future)
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, jsonify, current_app, send_file, request
from flask_jwt_extended import jwt_required

logger = logging.getLogger(__name__)

# Create blueprint
search_bp = Blueprint('search', __name__, url_prefix='/api/search')

# Default path to synonym lookup file (relative to project root)
# __file__ is src/api/blueprints/search.py, so go up 4 levels to reach project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DEFAULT_SYNONYM_PATH = PROJECT_ROOT / 'static' / 'synonym_lookup.json'


@search_bp.route('/synonyms', methods=['GET'])
@jwt_required()
def get_synonyms():
    """
    Get synonym lookup table for browser-side field resolution.
    ---
    tags:
      - Search
    summary: Get synonym lookup table
    description: |
      Returns the pre-computed synonym lookup table that maps common IT terms
      to actual field names. Used by the Explorer frontend for instant (<1ms)
      field name resolution during typing.

      The response is cached for 1 hour (Cache-Control header).
      Use ?refresh=true to bypass cache and get fresh data.

      **Response Structure:**
      - `_metadata`: Generation info (timestamp, model, thresholds)
      - `synonyms`: Map of term → field_name (e.g., "computer name" → "hostname")
      - `fields`: Field metadata (display_name, description, field_type)
    parameters:
      - name: refresh
        in: query
        type: boolean
        description: Force reload from disk (bypass any server-side cache)
    responses:
      200:
        description: Synonym lookup table
        schema:
          type: object
          properties:
            _metadata:
              type: object
              properties:
                generated_at:
                  type: string
                  format: date-time
                model:
                  type: string
                similarity_threshold:
                  type: number
                total_terms:
                  type: integer
                matched_terms:
                  type: integer
                total_fields:
                  type: integer
            synonyms:
              type: object
              additionalProperties:
                type: string
            fields:
              type: object
              additionalProperties:
                type: object
                properties:
                  display_name:
                    type: string
                  description:
                    type: string
                  field_type:
                    type: string
                  data_source:
                    type: string
      404:
        description: Synonym lookup table not found (not yet generated)
      500:
        description: Error reading synonym lookup table
    """
    logger.debug("GET /api/search/synonyms")

    # Get synonym file path from config or use default
    synonym_path = current_app.config.get('SYNONYM_LOOKUP_PATH', DEFAULT_SYNONYM_PATH)
    synonym_path = Path(synonym_path)

    if not synonym_path.exists():
        logger.warning(f"Synonym lookup file not found: {synonym_path}")
        return jsonify({
            'error': 'Synonym lookup table not found',
            'message': 'Run scripts/generate_synonyms.py to generate the lookup table'
        }), 404

    try:
        # Read and parse the JSON file
        with open(synonym_path, 'r') as f:
            data = json.load(f)

        # Add server timestamp
        data['_server'] = {
            'served_at': datetime.utcnow().isoformat() + 'Z',
            'file_size_bytes': synonym_path.stat().st_size
        }

        # Create response with caching headers
        response = jsonify(data)

        # Cache for 1 hour (browser and CDN)
        # Use ETag based on file modification time for cache validation
        mtime = synonym_path.stat().st_mtime
        etag = f'"{int(mtime)}"'

        response.headers['Cache-Control'] = 'public, max-age=3600'
        response.headers['ETag'] = etag

        # Check If-None-Match for 304 response
        if_none_match = request.headers.get('If-None-Match')
        if if_none_match == etag:
            return '', 304

        return response

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in synonym lookup file: {e}")
        return jsonify({
            'error': 'Invalid synonym lookup file',
            'message': str(e)
        }), 500
    except Exception as e:
        logger.error(f"Error reading synonym lookup file: {e}")
        return jsonify({
            'error': 'Failed to read synonym lookup table',
            'message': str(e)
        }), 500


@search_bp.route('/synonyms/stats', methods=['GET'])
@jwt_required()
def get_synonym_stats():
    """
    Get synonym lookup statistics without the full data.
    ---
    tags:
      - Search
    summary: Get synonym statistics
    description: |
      Returns statistics about the synonym lookup table without
      returning the full synonyms and fields data. Useful for
      monitoring and health checks.
    responses:
      200:
        description: Synonym statistics
        schema:
          type: object
          properties:
            generated_at:
              type: string
              format: date-time
            model:
              type: string
            similarity_threshold:
              type: number
            total_terms:
              type: integer
            matched_terms:
              type: integer
            total_fields:
              type: integer
            file_size_bytes:
              type: integer
            file_age_seconds:
              type: number
      404:
        description: Synonym lookup table not found
    """
    logger.debug("GET /api/search/synonyms/stats")

    synonym_path = current_app.config.get('SYNONYM_LOOKUP_PATH', DEFAULT_SYNONYM_PATH)
    synonym_path = Path(synonym_path)

    if not synonym_path.exists():
        return jsonify({
            'error': 'Synonym lookup table not found',
            'exists': False
        }), 404

    try:
        with open(synonym_path, 'r') as f:
            data = json.load(f)

        metadata = data.get('_metadata', {})
        file_stat = synonym_path.stat()

        # Calculate file age
        generated_at = metadata.get('generated_at')
        file_age_seconds = None
        if generated_at:
            try:
                gen_time = datetime.fromisoformat(generated_at.replace('Z', '+00:00'))
                file_age_seconds = (datetime.now(gen_time.tzinfo) - gen_time).total_seconds()
            except Exception:
                pass

        return jsonify({
            'exists': True,
            'generated_at': metadata.get('generated_at'),
            'model': metadata.get('model'),
            'similarity_threshold': metadata.get('similarity_threshold'),
            'total_terms': metadata.get('total_terms'),
            'matched_terms': metadata.get('matched_terms'),
            'total_fields': metadata.get('total_fields'),
            'synonym_count': len(data.get('synonyms', {})),
            'field_count': len(data.get('fields', {})),
            'file_size_bytes': file_stat.st_size,
            # tz=timezone.utc, not a bare + 'Z': fromtimestamp() with no tz returns
            # naive LOCAL wall-clock, so labelling it UTC put the instant out by the
            # host's offset (#1162).
            'file_modified_at': datetime.fromtimestamp(
                file_stat.st_mtime, tz=timezone.utc
            ).isoformat().replace('+00:00', 'Z'),
            'file_age_seconds': file_age_seconds
        })

    except Exception as e:
        logger.error(f"Error reading synonym stats: {e}")
        return jsonify({
            'error': 'Failed to read synonym statistics',
            'message': str(e)
        }), 500
