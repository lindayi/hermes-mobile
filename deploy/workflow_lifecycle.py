"""Pure construction and retention rules for workflow lifecycle events."""

from datetime import datetime, timezone
import hashlib

from deploy.workflow_events import MAX_EVENTS, SCHEMA_VERSION, validate_export


REPOSITORY = "lindayi/hermes-mobile"
REPOSITORY_ID = 1399942965
REASON_OUTCOMES = {
    "issue_failed": "failed",
    "task_failed": "failed",
    "execution_exhausted": "failed",
    "sensitive_approval": "approval_required",
    "execution_uncertain": "execution_uncertain",
    "merged": "merged",
    "controller_verified": "deployed",
    "closed_without_merge": "closed",
    "conflict_incompatible": "blocked",
    "policy_broken": "blocked",
}


def _now(now):
    if now is None:
        value = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        value = now
    else:
        value = datetime.fromtimestamp(now, timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Lifecycle time must include a timezone")
    return value.astimezone(timezone.utc)


def timestamp(value):
    return _now(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_event(event, *, now=None):
    generated_at = _now(now)
    envelope = {
        "version": SCHEMA_VERSION,
        "repository_id": REPOSITORY_ID,
        "repository": REPOSITORY,
        "owner_user_id": "schema-validation",
        "generated_at": timestamp(generated_at),
        "events": [event],
    }
    validate_export(envelope, now=generated_at)
    return event


def merge_events(existing, additions, *, now=None, limit=MAX_EVENTS):
    if not isinstance(existing, list) or not isinstance(additions, (list, tuple)):
        raise ValueError("Lifecycle events must be a list")
    retained = []
    known = {}
    for event in (*existing, *additions):
        validate_event(event, now=now)
        event_id = event["event_id"]
        prior = known.get(event_id)
        if prior is not None:
            stable_identity = (
                prior.get("reason") == event.get("reason")
                and prior.get("outcome") == event.get("outcome")
                and prior.get("issue_number") == event.get("issue_number")
                and prior.get("pr_number") == event.get("pr_number")
                and prior.get("merge_sha") == event.get("merge_sha")
                and prior.get("decision") == event.get("decision")
            )
            persistent_incident = (
                prior.get("reason") in {
                    "execution_exhausted", "execution_uncertain", "policy_broken",
                    "sensitive_approval", "conflict_incompatible",
                }
                and stable_identity
                and (
                    prior.get("reason") not in {
                        "sensitive_approval", "conflict_incompatible",
                    }
                    or prior.get("head_sha") == event.get("head_sha")
                )
            )
            if prior != event and not persistent_incident:
                raise ValueError("Lifecycle event identity conflicts with its immutable payload")
            continue
        known[event_id] = event
        retained.append(event)
        if len(retained) > limit:
            raise ValueError("Lifecycle event capacity reached without safe acknowledgement")
    return retained


def build_export(events, owner_user_id, *, now=None):
    generated = _now(now)
    payload = {
        "version": SCHEMA_VERSION,
        "repository_id": REPOSITORY_ID,
        "repository": REPOSITORY,
        "owner_user_id": owner_user_id,
        "generated_at": timestamp(generated),
        "events": events,
    }
    validate_export(payload, now=generated)
    return payload


def pull_event(snapshot, reason, *, occurred_at, merge_sha=None, decision=None,
               incident=""):
    if reason not in REASON_OUTCOMES:
        raise ValueError("Unsupported lifecycle reason")
    issue = snapshot.get("issue")
    head = snapshot.get("head")
    if type(issue) is not int:
        raise ValueError("Lifecycle pull identity is invalid")
    enrollment = snapshot.get("enrollment") or {}
    generation = enrollment.get("comment")
    identity_head = "" if reason in {"execution_exhausted", "policy_broken"} else head
    identity = f"{issue}:{generation}:{reason}:{identity_head}:{merge_sha or ''}:{incident}"
    event_id = f"pr:{issue}:{reason}:{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
    return validate_event({
        "event_id": event_id,
        "outcome": REASON_OUTCOMES[reason],
        "reason": reason,
        "issue_number": issue,
        "pr_number": issue,
        "head_sha": head,
        "merge_sha": merge_sha,
        "decision": decision,
        "occurred_at": occurred_at,
    })


def issue_event(issue_number, reason, *, occurred_at, incident):
    if (reason not in {"issue_failed", "execution_uncertain"}
            or type(issue_number) is not int or not 1 <= issue_number <= 2**31 - 1):
        raise ValueError("Issue lifecycle identity or reason is invalid")
    identity = f"{issue_number}:{reason}:{incident}"
    event_id = f"issue:{issue_number}:{reason}:{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
    return validate_event({
        "event_id": event_id,
        "outcome": REASON_OUTCOMES[reason],
        "reason": reason,
        "issue_number": issue_number,
        "pr_number": None,
        "head_sha": None,
        "merge_sha": None,
        "decision": None,
        "occurred_at": occurred_at,
    })
