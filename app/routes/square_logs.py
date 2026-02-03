"""
Square Logs Dashboard - view recent Square integration logs.

This provides an API endpoint to view recent log entries from the
Square integration. Useful for debugging and monitoring.

Endpoints:
- GET /api/integrations/square/logs - Get recent log entries
"""

from flask import Blueprint, jsonify, request
from app.services.auth_service import require_auth
from app.services.square_logger import get_recent_logs, LOG_FILE
import os

square_logs_bp = Blueprint('square_logs', __name__)


@square_logs_bp.route('/logs', methods=['GET'])
@require_auth
def get_logs():
    """
    Get recent Square integration log entries.

    Query parameters:
        lines: Number of log lines to return (default: 100, max: 500)
        level: Filter by log level (INFO, WARNING, ERROR)

    Headers required:
        Authorization: Bearer <access_token>

    Returns:
        {
            "success": true,
            "log_file": "logs/square_integration.log",
            "entries": [
                {
                    "timestamp": "2024-01-15 10:30:00",
                    "module": "square.oauth",
                    "level": "INFO",
                    "message": "[CONNECT_COMPLETED] business_id=abc123"
                },
                ...
            ],
            "total": 100
        }
    """
    try:
        # Get query parameters
        lines = min(int(request.args.get('lines', 100)), 500)
        level = request.args.get('level')

        if level:
            level = level.upper()
            if level not in ('INFO', 'WARNING', 'ERROR', 'DEBUG'):
                return jsonify({
                    "success": False,
                    "error": "Invalid level. Must be INFO, WARNING, ERROR, or DEBUG"
                }), 400

        # Get log entries
        entries = get_recent_logs(lines=lines, level=level)

        return jsonify({
            "success": True,
            "log_file": os.path.basename(LOG_FILE),
            "entries": entries,
            "total": len(entries)
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@square_logs_bp.route('/logs/summary', methods=['GET'])
@require_auth
def get_logs_summary():
    """
    Get a summary of recent Square integration activity.

    Headers required:
        Authorization: Bearer <access_token>

    Returns:
        {
            "success": true,
            "summary": {
                "total_entries": 250,
                "info_count": 200,
                "warning_count": 30,
                "error_count": 20,
                "recent_errors": [...],
                "log_file_exists": true,
                "log_file_size_kb": 45.2
            }
        }
    """
    try:
        # Get all entries for counting
        all_entries = get_recent_logs(lines=500)

        # Count by level
        info_count = sum(1 for e in all_entries if e.get('level') == 'INFO')
        warning_count = sum(1 for e in all_entries if e.get('level') == 'WARNING')
        error_count = sum(1 for e in all_entries if e.get('level') == 'ERROR')

        # Get recent errors
        recent_errors = [e for e in all_entries if e.get('level') == 'ERROR'][-10:]

        # File stats
        log_exists = os.path.exists(LOG_FILE)
        log_size_kb = 0
        if log_exists:
            log_size_kb = round(os.path.getsize(LOG_FILE) / 1024, 2)

        return jsonify({
            "success": True,
            "summary": {
                "total_entries": len(all_entries),
                "info_count": info_count,
                "warning_count": warning_count,
                "error_count": error_count,
                "recent_errors": recent_errors,
                "log_file_exists": log_exists,
                "log_file_size_kb": log_size_kb
            }
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
