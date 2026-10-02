"""Read durable issue-starter and controller evidence for workflow lifecycle events."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re

from deploy.workflow_lifecycle import (
    issue_event,
    merge_events,
    timestamp,
    validate_event,
)
from deploy.workflow_notifications import (
    Blocked,
    Paths as NotificationPaths,
    _deployed_evidence,
    _json_file,
    _owner,
    _owned_private_path,
)


GITHUB_OWNER_ID = 5164171
MAX_STARTER_BYTES = 4 * 1024 * 1024
MAX_STARTER_COMMANDS = 1000
MAX_CONFIG_BYTES = 65536
MAX_STATUS_BYTES = 65536
STARTER_PHASES = {
    "reserved", "dispatch_started", "unknown", "task_created", "failed",
    "stale_authorization", "handoff_reserved", "handoff_ready",
    "handoff_comment_started", "handoff_uncertain", "handoff_failed", "handed_off",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE = re.compile(r"[0-9a-f]{32}\Z")


class LifecycleSourceError(RuntimeError):
    """Durable lifecycle evidence is unavailable or malformed."""


def _default_starter_state():
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return root / "hermes-mobile-issue-starter" / "state.json"


@dataclass(frozen=True)
class LifecycleSourcePaths:
    notifications: NotificationPaths = field(default_factory=NotificationPaths)
    starter_state: Path = field(default_factory=_default_starter_state)


def _parse_time(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def resolve_application_binding(paths=None):
    """Resolve the sole ready default-profile app owner and its configured state dir."""
    paths = paths or NotificationPaths()
    try:
        config, _ = _json_file(paths.config, max_bytes=MAX_CONFIG_BYTES)
        if not isinstance(config, dict) or not isinstance(config.get("state_dir"), str):
            raise LifecycleSourceError("Application state directory is not configured")
        state_dir = Path(config["state_dir"])
        if not state_dir.is_absolute():
            raise LifecycleSourceError("Application state directory is not absolute")
        _owned_private_path(state_dir, directory=True)
        from backend.configuration import load_settings

        settings = load_settings(paths.config)
        if settings.state_dir != state_dir:
            raise LifecycleSourceError("Application state directory binding changed")
        owner = _owner(state_dir / "auth.sqlite")
    except (Blocked, OSError, TypeError, ValueError) as error:
        if isinstance(error, LifecycleSourceError):
            raise
        raise LifecycleSourceError("Trusted application owner binding is unavailable") from error
    return owner, state_dir


def _issue_starter_events(state_path, api, now):
    state_path = Path(state_path).absolute()
    if not state_path.exists() and not state_path.is_symlink():
        return []
    try:
        state, _ = _json_file(state_path, max_bytes=MAX_STARTER_BYTES)
    except Blocked as error:
        raise LifecycleSourceError("Issue-starter state is unavailable") from error
    commands = state.get("commands") if isinstance(state, dict) else None
    if (not isinstance(state, dict) or type(state.get("version")) is not int
            or state["version"] != 1 or not isinstance(commands, dict)
            or len(commands) > MAX_STARTER_COMMANDS):
        raise LifecycleSourceError("Issue-starter state has an unsupported format")

    selected = []
    by_issue = {}
    for key, record in commands.items():
        if (not isinstance(key, str) or not isinstance(record, dict)
                or type(record.get("issue")) is not int
                or not 1 <= record["issue"] <= 2**31 - 1
                or type(record.get("command_id")) is not int
                or record["command_id"] <= 0
                or key != f"{record['issue']}:{record['command_id']}"
                or record.get("phase") not in STARTER_PHASES
                or not isinstance(record.get("accepted_title_body_sha256"), str)
                or not _SHA256.fullmatch(record["accepted_title_body_sha256"])
                or _parse_time(record.get("accepted_at")) is None):
            raise LifecycleSourceError("Issue-starter command identity is malformed")
        phase = record.get("phase")
        reason = (
            "issue_failed" if phase in {"failed", "handoff_failed", "stale_authorization"}
            else "execution_uncertain" if phase in {"unknown", "handoff_uncertain"}
            else None
        )
        receipt = record.get("receipt")
        if (reason is None or not isinstance(receipt, dict)
                or receipt.get("kind") != "blocked"
                or receipt.get("state") != "sent"):
            continue
        marker = (
            f"<!-- hermes-issue-starter:blocked:"
            f"{record['issue']}:{record['command_id']} -->"
        )
        if receipt.get("marker") != marker:
            raise LifecycleSourceError("Issue-starter receipt marker is invalid")
        by_issue.setdefault(record["issue"], []).append((record, reason, marker))
        selected.append(record["issue"])

    results = []
    for issue in sorted(set(selected)):
        try:
            comments = api.get_all(
                f"repos/lindayi/hermes-mobile/issues/{issue}/comments?per_page=100",
                collection=None,
            )
        except Exception as error:
            raise LifecycleSourceError("Issue-starter receipt comments are unavailable") from error
        if not isinstance(comments, list) or len(comments) > 2000:
            raise LifecycleSourceError("Issue-starter receipt comments are incomplete")
        for record, reason, marker in by_issue[issue]:
            expected = (
                f"{marker}\nHermes issue starter is blocked for issue #{issue}. "
                "Owner action is required; no uncertain task was automatically retried."
            )
            matches = [
                item for item in comments
                if isinstance(item, dict) and item.get("body") == expected
                and isinstance(item.get("user"), dict)
                and type(item["user"].get("id")) is int
                and item["user"]["id"] == GITHUB_OWNER_ID
            ]
            if not matches:
                continue
            if len(matches) != 1:
                raise LifecycleSourceError("Issue-starter receipt comment is ambiguous")
            comment = matches[0]
            accepted = _parse_time(record["accepted_at"])
            occurred = _parse_time(comment.get("created_at"))
            if (type(comment.get("id")) is not int or comment["id"] <= 0
                    or comment.get("updated_at") != comment.get("created_at")
                    or occurred is None or occurred < accepted or occurred > now):
                raise LifecycleSourceError("Issue-starter receipt timestamp is invalid")
            try:
                results.append(issue_event(
                    issue, reason, occurred_at=timestamp(occurred),
                    incident=str(record["command_id"]),
                ))
            except ValueError as error:
                raise LifecycleSourceError("Issue-starter outcome is outside the shared schema") from error
    return results


def collect_controller_verified_events(merged_events, paths, now):
    try:
        status, status_info = _json_file(
            paths.controller_state / "status.json", max_bytes=MAX_STATUS_BYTES,
        )
    except (Blocked, OSError, TypeError):
        return []
    if (not isinstance(status, dict) or not isinstance(status.get("release"), str)
            or not _RELEASE.fullmatch(status["release"])):
        return []
    occurred = datetime.fromtimestamp(status_info.st_mtime, timezone.utc)
    results = []
    for merged in merged_events:
        if (not isinstance(merged, dict) or merged.get("reason") != "merged"
                or not isinstance(merged.get("merge_sha"), str)):
            continue
        identity = ":".join((
            str(merged.get("pr_number")), merged["head_sha"],
            merged["merge_sha"], status["release"],
        ))
        event_id = (
            f"pr:{merged['pr_number']}:controller_verified:"
            f"{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
        )
        event = {
            "event_id": event_id,
            "outcome": "deployed",
            "reason": "controller_verified",
            "issue_number": merged["issue_number"],
            "pr_number": merged["pr_number"],
            "head_sha": merged["head_sha"],
            "merge_sha": merged["merge_sha"],
            "decision": None,
            "occurred_at": timestamp(occurred),
        }
        try:
            validate_event(event, now=now)
            _deployed_evidence(event, paths, now)
        except (Blocked, TypeError, ValueError):
            continue
        results.append(event)
    return results


def collect_source_events(merged_events, *, api, paths, now):
    """Collect only receipt-backed starter failures and controller-proven deployments."""
    now = now.astimezone(timezone.utc)
    starter = _issue_starter_events(paths.starter_state, api, now)
    deployed = collect_controller_verified_events(
        merged_events, paths.notifications, now,
    ) if merged_events else []
    try:
        return merge_events([], [*starter, *deployed], now=now)
    except (TypeError, ValueError) as error:
        raise LifecycleSourceError("Lifecycle source events conflict with the shared schema") from error
