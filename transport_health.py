"""Read-only, PID/start-time-bound transport diagnostics. Never expose log bodies."""
from pathlib import Path
import sqlite3
import time


def summarize(db_path, servers, now=None, since=None, window=300):
    now = time.time() if now is None else now
    result = {"available": False, "scope": "current_app_server", "window_seconds": window,
              "http_sse_failures": 0, "model_websocket_failures": 0,
              "remote_disconnects": 0, "compact_failures": 0,
              "remote_state": "unknown", "remote_observed_at": None,
              "last_failure_at": None, "events": [], "truncated": False}
    if not servers or not db_path or not Path(db_path).is_file():
        return result
    lower = max(now - window, since or 0)
    connection = None
    try:
        connection = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        connection.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 2
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        remote_states = []
        for server in servers:
            earliest = max(lower, server["started"])
            rows = connection.execute(
                "SELECT ts, target, feedback_log_body, process_uuid FROM logs "
                "WHERE ts>=? AND ts<=? AND process_uuid LIKE ? AND "
                "(target='codex_api::sse::responses' OR "
                "target='codex_api::endpoint::responses_websocket' OR "
                "target='codex_core::responses_retry' OR "
                "target='codex_app_server_transport::transport::remote_control::websocket') "
                "ORDER BY ts DESC, ts_nanos DESC, id DESC LIMIT 1001",
                (earliest, now, 'pid:%d:%%' % server['pid'])).fetchall()
            if len(rows) > 1000:
                result["truncated"] = True
                rows = rows[:1000]
            # Process UUIDs protect against recycled PIDs. Mixed runs are not trusted.
            identities = {row[3] for row in rows}
            if len(identities) > 1:
                result["error"] = "ambiguous_process_identity"
                return result
            latest_remote = None
            for stamp, target, body, _ in rows:
                body = body or ""
                category = None
                if target.endswith("remote_control::websocket"):
                    if "reader disconnected" in body or "timed out connecting" in body:
                        category = "remote_disconnects"
                        if latest_remote is None:
                            latest_remote = (stamp, "failed")
                    elif "next_status=Connecting" in body and latest_remote is None:
                        latest_remote = (stamp, "unknown")
                    elif ("connected to app-server remote control websocket" in body
                          or "next_status=Connected" in body) and latest_remote is None:
                        latest_remote = (stamp, "connected")
                elif target == "codex_api::sse::responses" and "SSE Error:" in body:
                    category = "http_sse_failures"
                elif target == "codex_api::endpoint::responses_websocket" and any(
                        key in body.lower() for key in ("error", "failed", "timed out", "disconnected")):
                    category = "model_websocket_failures"
                elif target == "codex_core::responses_retry" and "compaction" in body and any(
                        key in body for key in ("compact_error=", "stream failed")):
                    category = "compact_failures"
                if category:
                    result[category] += 1
                    result["last_failure_at"] = max(result["last_failure_at"] or 0, stamp)
                    result["events"].append({"at": stamp, "kind": category})
            if latest_remote:
                remote_states.append(latest_remote)
        result["available"] = True
        result["events"] = sorted(result["events"], key=lambda e: e["at"], reverse=True)[:12]
        # A recent handshake is host-side evidence, never a phone end-to-end guarantee.
        if len(servers) == 1 and remote_states:
            stamp, state = remote_states[0]
            if now - stamp <= 120:
                result["remote_state"], result["remote_observed_at"] = state, stamp
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        result["available"] = False
        result["error"] = "log_unavailable_or_schema_changed"
    finally:
        if connection is not None:
            connection.close()
    return result


def cross_channel_failure(report):
    """Require distinct failing transport types; absence/403 alone is not failure."""
    return (report.get("available") is True and not report.get("truncated")
            and not report.get("error") and report.get("remote_disconnects", 0) >= 2
            and report.get("http_sse_failures", 0) + report.get("model_websocket_failures", 0) >= 2)


def stream_failure(report):
    """Diagnosis is broader than permission to change a network route."""
    return (report.get("available") is True and not report.get("truncated")
            and not report.get("error") and (
                report.get("http_sse_failures", 0) + report.get("model_websocket_failures", 0) >= 2
                or report.get("remote_disconnects", 0) >= 2
                or report.get("compact_failures", 0) >= 2))
