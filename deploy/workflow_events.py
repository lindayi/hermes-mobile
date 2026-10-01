"""Pure schema validation for the sanitized workflow lifecycle export."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re


SCHEMA_VERSION = 1
REPOSITORY_ID = 1399942965
REPOSITORY = 'lindayi/hermes-mobile'
MAX_EVENTS = 256
MAX_EVENT_AGE = 24 * 60 * 60
MAX_CLOCK_SKEW = 5 * 60

_EVENT_KEYS = {
    'event_id', 'outcome', 'reason', 'issue_number', 'pr_number',
    'head_sha', 'merge_sha', 'decision', 'occurred_at',
}
_REASONS = {
    'issue_failed': 'failed',
    'task_failed': 'failed',
    'execution_exhausted': 'failed',
    'sensitive_approval': 'approval_required',
    'execution_uncertain': 'execution_uncertain',
    'merged': 'merged',
    'controller_verified': 'deployed',
}
_DECISIONS = {'approve_production', 'resolve_review', 'authorize_sensitive_action'}
_EVENT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_OWNER_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_SHA = re.compile(r'[0-9a-f]{40}\Z')


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid lifecycle JSON value') from error


def event_digest(event):
    return hashlib.sha256(canonical_json(event).encode('utf-8')).hexdigest()


def _timestamp(value):
    if not isinstance(value, str) or len(value) != 20 or not value.endswith('Z'):
        raise ValueError('Invalid lifecycle timestamp')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as error:
        raise ValueError('Invalid lifecycle timestamp') from error
    if parsed.tzinfo != timezone.utc or parsed.isoformat(timespec='seconds').replace('+00:00', 'Z') != value:
        raise ValueError('Invalid lifecycle timestamp')
    return parsed


def _event(value, generated_at):
    if not isinstance(value, dict) or set(value) != _EVENT_KEYS:
        raise ValueError('Invalid lifecycle event fields')
    if (not isinstance(value['event_id'], str) or not _EVENT_ID.fullmatch(value['event_id'])
            or value['reason'] not in _REASONS
            or value['outcome'] != _REASONS[value['reason']]):
        raise ValueError('Invalid lifecycle event identity or outcome')
    for key in ('issue_number', 'pr_number'):
        item = value[key]
        if item is not None and (type(item) is not int or not 1 <= item <= 2**31 - 1):
            raise ValueError('Invalid lifecycle issue or pull request number')
    if value['issue_number'] is None and value['pr_number'] is None:
        raise ValueError('Lifecycle event requires an issue or pull request')
    for key in ('head_sha', 'merge_sha'):
        item = value[key]
        if item is not None and (not isinstance(item, str) or not _SHA.fullmatch(item)):
            raise ValueError('Invalid lifecycle SHA')
    if value['pr_number'] is not None and value['head_sha'] is None:
        raise ValueError('Pull request event requires its exact head SHA')
    decision = value['decision']
    if value['outcome'] == 'approval_required':
        if (value['pr_number'] is None or decision not in _DECISIONS
                or value['merge_sha'] is not None):
            raise ValueError('Approval event requires a supported decision and exact PR head')
    elif decision is not None:
        raise ValueError('Decision is only valid for approval events')
    if value['outcome'] in ('merged', 'deployed'):
        if value['pr_number'] is None or value['merge_sha'] is None:
            raise ValueError('Merged lifecycle event requires exact PR and merge SHAs')
    elif value['merge_sha'] is not None:
        raise ValueError('Merge SHA is only valid for merged or deployed events')
    occurred_at = _timestamp(value['occurred_at'])
    if occurred_at > generated_at:
        raise ValueError('Lifecycle event is newer than its export')
    return occurred_at


def validate_export(value, *, now=None):
    """Validate only the closed, bounded contract; never infer missing evidence."""
    if not isinstance(value, dict) or set(value) != {
            'version', 'repository_id', 'repository', 'owner_user_id', 'generated_at', 'events'}:
        raise ValueError('Invalid lifecycle export fields')
    if (type(value['version']) is not int or value['version'] != SCHEMA_VERSION
            or type(value['repository_id']) is not int or value['repository_id'] != REPOSITORY_ID
            or value['repository'] != REPOSITORY
            or not isinstance(value['owner_user_id'], str)
            or not _OWNER_ID.fullmatch(value['owner_user_id'])):
        raise ValueError('Lifecycle export repository, version, or owner binding mismatch')
    generated_at = _timestamp(value['generated_at'])
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Current time must be timezone-aware')
    now = now.astimezone(timezone.utc)
    age = (now - generated_at).total_seconds()
    if age > MAX_EVENT_AGE or age < -MAX_CLOCK_SKEW:
        raise ValueError('Lifecycle export is stale or future-dated')
    events = value['events']
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError('Lifecycle export event count is invalid')
    seen = set()
    for item in events:
        occurred_at = _event(item, generated_at)
        if item['event_id'] in seen:
            raise ValueError('Duplicate lifecycle event identity')
        seen.add(item['event_id'])
        event_age = (now - occurred_at).total_seconds()
        if event_age > MAX_EVENT_AGE or event_age < -MAX_CLOCK_SKEW:
            raise ValueError('Lifecycle event is stale or future-dated')
    return value
