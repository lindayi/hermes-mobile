from datetime import datetime, timezone

import pytest

from deploy.task_receipts import ReceiptError, receipt_instruction, validate_task_receipt


NOW = datetime.fromisoformat("2026-10-01T12:06:00+00:00")
NONCE = "sQb2j8J_Wac6hJ5sU1ZWVwO4GdI1xC7r8sVZgg"
TASK_ID = "task-123"
SESSION_ID = "session-456"
START_HEAD = "a" * 40
RESULT_HEAD = "c" * 40
BASE = "b" * 40


def receipt_body(result="ready"):
    return (
        "Hermes-Task-Receipt: v1\n"
        f"nonce={NONCE}\n"
        f"task={TASK_ID}\n"
        f"session={SESSION_ID}\n"
        "pr=16\n"
        f"start_head={START_HEAD}\n"
        f"head={RESULT_HEAD}\n"
        f"base={BASE}\n"
        f"result={result}"
    )


def binding():
    owner = {"id": 5164171}
    repository = {"id": 1399942965}
    action = {
        "issue": 16, "task_id": TASK_ID, "task_created_at": "2026-10-01T12:00:00Z",
        "dispatch_nonce": NONCE, "owner_id": owner["id"],
        "repository_id": repository["id"], "pull_id": 160000016,
        "pull_node_id": "PR_node_16", "head_ref": "topic", "head": START_HEAD,
    }
    task = {
        "id": TASK_ID, "state": "completed", "creator": owner, "owner": owner,
        "repository": repository, "created_at": action["task_created_at"],
        "updated_at": "2026-10-01T12:05:30Z",
        "artifacts": [
            {"provider": "github", "type": "branch",
             "data": {"head_ref": "topic", "base_ref": "main"}},
            {"provider": "github", "type": "pull",
             "data": {"id": action["pull_id"], "global_id": action["pull_node_id"]}},
        ],
        "sessions": [{
            "id": SESSION_ID, "task_id": TASK_ID, "state": "completed",
            "user": owner, "owner": owner, "repository": repository,
            "head_ref": "topic", "base_ref": "main",
            "prompt": receipt_instruction(NONCE, pull_number=16, start_head=START_HEAD, base_sha=BASE),
            "created_at": "2026-10-01T12:01:00Z",
            "completed_at": "2026-10-01T12:05:00Z",
        }],
    }
    pull = {
        "number": 16, "id": action["pull_id"], "node_id": action["pull_node_id"],
        "head": {"sha": RESULT_HEAD}, "base": {"sha": BASE},
    }
    comments = [{
        "id": 777, "user": {"id": 198982749}, "body": receipt_body(),
        "created_at": "2026-10-01T12:05:00Z",
        "updated_at": "2026-10-01T12:05:00Z",
    }]
    return task, action, pull, comments


def v2_binding(result="ready"):
    task, action, pull, comments = binding()
    action["main_sha"] = BASE
    comments[0]["body"] = receipt_body(result).replace(
        "Hermes-Task-Receipt: v1", "Hermes-Task-Receipt: v2",
    ).replace(f"task={TASK_ID}\n", "")
    return task, action, pull, comments


def transported_v2_body(nonce=NONCE, session_id=SESSION_ID,
                        start_head=START_HEAD, head=RESULT_HEAD, result="ready"):
    return (
        "\n> Cloud completion report preserved before parent normalization:\n"
        "> \n"
        "> This quoted report is not receipt evidence.\n\n"
        "Hermes-Task-Receipt: v2\n"
        f"nonce={nonce}\n"
        "pr=16\n"
        f"session={session_id}\n"
        f"start_head={start_head}\n"
        f"base={BASE}\n"
        f"head={head}\n"
        f"result={result}"
    )


@pytest.fixture(params=["first-acceptance", "persisted-proof"])
def check_v2_transport(request):
    def check(body, *, accepted):
        task, action, pull, comments = v2_binding()
        if request.param == "first-acceptance":
            comments[0]["body"] = body
            if accepted:
                proof = validate_task_receipt(task, action, pull, comments, now=NOW)
                assert proof["body"] == body
            else:
                with pytest.raises(ReceiptError):
                    validate_task_receipt(task, action, pull, comments, now=NOW)
        else:
            import json
            from deploy.cloud_coordinator import _valid_receipt_proof

            # Start with genuine accepted synthetic metadata, then model an old
            # persisted body and identical remote comment: equality is not enough.
            proof = validate_task_receipt(task, action, pull, comments, now=NOW)
            action.update(status="completed", **{
                f"receipt_{key}": value for key, value in proof.items()
            })
            assert _valid_receipt_proof(action, comments)
            action["receipt_body"] = comments[0]["body"] = body
            action, comments = json.loads(json.dumps([action, comments]))
            assert _valid_receipt_proof(action, comments) is accepted
    return check


@pytest.mark.parametrize("prefix,accepted", [
    ("> quoted example\n", False),
    ("\n> quoted example\n", False),
    ("> quoted example\n> \n", False),
    ("> quoted example\n\u00a0\n", False),
    ("> quoted example\n\v\n", False),
    ("> quoted example\n\f\n", False),
    ("> quoted example\n\x1c\n", False),
    ("> quoted example\n\u2028\n", False),
    ("> quoted example\n\u00a0\n\n", False),
    ("> quoted example\n\n", True),
    ("> quoted example\n \t \n", True),
    ("", True),
])
def test_v2_transport_ascii_quote_boundary(check_v2_transport, prefix, accepted):
    plain = transported_v2_body().split("\n\n", 1)[1]
    check_v2_transport(prefix + plain, accepted=accepted)


@pytest.mark.parametrize("size", [8191, 8192, 8193])
@pytest.mark.parametrize("character", ["x", "\u00e9", "\U0001f680"])
def test_v2_transport_utf8_byte_budget(check_v2_transport, size, character):
    plain = transported_v2_body().split("\n\n", 1)[1]
    framing = "> \n\n" + plain
    count, remainder = divmod(size - len(framing.encode("utf-8")),
                              len(character.encode("utf-8")))
    body = "> " + character * count + "x" * remainder + "\n\n" + plain
    assert len(body.encode("utf-8")) == size
    check_v2_transport(body, accepted=size <= 8192)


@pytest.mark.parametrize("line_count", [63, 64, 65])
def test_v2_transport_line_budget(check_v2_transport, line_count):
    plain = transported_v2_body().split("\n\n", 1)[1]
    body = "> quoted\n" * (line_count - 9) + "\n" + plain
    assert len(body.split("\n")) == line_count
    check_v2_transport(body, accepted=line_count <= 64)


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_v2_transport_rejects_malformed_unicode(check_v2_transport, surrogate):
    check_v2_transport("> " + surrogate + "\n" + transported_v2_body(), accepted=False)


def test_v2_transport_caps_before_encoding_counting_or_splitting():
    from deploy.task_receipts import _v2_fields

    class Unscanned(str):
        def count(self, *args, **kwargs):
            pytest.fail("oversized transport must be rejected before count")

        def split(self, *args, **kwargs):
            pytest.fail("oversized transport must be rejected before split")

    class Unencoded(Unscanned):
        def encode(self, *args, **kwargs):
            pytest.fail("character cap must precede UTF-8 encoding")

    for body in (Unencoded("x" * 8193), Unscanned("\U0001f680" * 2049)):
        with pytest.raises(ReceiptError):
            _v2_fields(body)


def test_v2_instruction_fixed_bindings_survive_actual_adapter_json_request():
    import json
    from types import SimpleNamespace
    from deploy.cloud_coordinator import GhApi

    captured = []

    def capture_request(command, **kwargs):
        captured.append((command, kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout='{"id":"synthetic-task"}', stderr="")

    instruction = receipt_instruction(NONCE, pull_number=16, start_head=START_HEAD, base_sha=BASE)
    prompt = 'Synthetic repair "quoted" \\ path.\n\n' + instruction
    body = {"prompt": prompt, "base_ref": "main", "head_ref": "synthetic-topic"}
    # Exercise the production adapter up to subprocess stdin, without invoking gh
    # or a provider. This is request representation, not model-input evidence.
    response = GhApi(run=capture_request).write("agents/repos/lindayi/hermes-mobile/tasks", body)
    assert response == {"id": "synthetic-task"}
    assert len(captured) == 1
    command, payload = captured[0]
    assert command == [
        "gh", "api", "--hostname", "github.com", "--method", "POST",
        "agents/repos/lindayi/hermes-mobile/tasks", "--input", "-",
    ]
    decoded = json.loads(payload)
    assert decoded == body
    transported = decoded["prompt"].removeprefix('Synthetic repair "quoted" \\ path.\n\n')
    assert transported == instruction
    assert instruction.isascii()
    assert "<" not in instruction and ">" not in instruction
    assert instruction.index(f"base={BASE}") < instruction.index("SESSION_ID_REPLACE_ME")
    assert instruction.index(f"base={BASE}") < instruction.index("PUSHED_PULL_HEAD_SHA_REPLACE_ME")
    block = transported[transported.index("Hermes-Task-Receipt: v2\n"):]
    assert block.splitlines() == [
        "Hermes-Task-Receipt: v2",
        f"nonce={NONCE}", "pr=16", f"start_head={START_HEAD}", f"base={BASE}",
        "session=SESSION_ID_REPLACE_ME", "head=PUSHED_PULL_HEAD_SHA_REPLACE_ME",
        "result=ready|conflict_incompatible|policy_broken",
    ]


def test_v2_instruction_requires_complete_context_before_source_edits():
    instruction = receipt_instruction(NONCE, pull_number=16, start_head=START_HEAD, base_sha=BASE)
    preflight, _ = instruction.split("After pushing your result", 1)
    assert preflight.startswith("Before any source edits, ")
    assert "nonce, pr, start_head and base are all present and complete" in preflight
    assert "nonblank COPILOT_AGENT_SESSION_ID from your exposed environment" in preflight
    assert "If any fixed binding is missing or incomplete, or the session variable is missing or blank" in preflight
    assert "stop without source edits and report an explicit blocker" in preflight
    assert "do not guess, substitute values, or emit any receipt" in preflight
    assert "Replace the session label with that exact session ID" in instruction
    assert "the head label with the pushed PR head SHA" in instruction
    assert "replacement labels are not receipt values" in instruction
    assert "one result from the closed list" in instruction


@pytest.mark.parametrize("result", ["ready", "conflict_incompatible", "policy_broken"])
@pytest.mark.parametrize("unreplaced", [None, "session", "head", "result", "all"])
def test_v2_instruction_labels_require_actual_bound_values(result, unreplaced):
    from deploy.cloud_coordinator import _valid_receipt_proof

    task, action, pull, comments = v2_binding(result)
    proof = validate_task_receipt(task, action, pull, comments, now=NOW)
    action.update(status="completed", **{f"receipt_{key}": value for key, value in proof.items()})
    assert _valid_receipt_proof(action, comments)
    instruction = receipt_instruction(NONCE, pull_number=16, start_head=START_HEAD, base_sha=BASE)
    body = instruction[instruction.index("Hermes-Task-Receipt: v2\n"):]
    replacements = {
        "session": ("SESSION_ID_REPLACE_ME", SESSION_ID),
        "head": ("PUSHED_PULL_HEAD_SHA_REPLACE_ME", RESULT_HEAD),
        "result": ("ready|conflict_incompatible|policy_broken", result),
    }
    for field, (label, value) in replacements.items():
        assert body.count(f"{field}={label}") == 1
        if unreplaced not in (field, "all"):
            body = body.replace(f"{field}={label}", f"{field}={value}")
    # Both first acceptance and replay validation must reject even one untouched
    # label. Filling every value still works for each closed result.
    comments[0]["body"] = body
    if unreplaced is None:
        assert validate_task_receipt(task, action, pull, comments, now=NOW)["result"] == result
    else:
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, comments, now=NOW)
    action["receipt_body"] = body
    assert _valid_receipt_proof(action, comments) is (unreplaced is None)


def test_v2_transport_instruction_documents_boundary_and_budget():
    instruction = receipt_instruction(NONCE, pull_number=16, start_head=START_HEAD, base_sha=BASE)
    assert "ASCII blank line (empty or only spaces/tabs)" in instruction
    assert "8192 UTF-8 bytes and 64 LF-delimited lines" in instruction


def test_v2_accepts_observed_quoted_prefix_and_reordered_plain_receipt():
    task, action, pull, comments = v2_binding()
    comments[0]["body"] = transported_v2_body()

    proof = validate_task_receipt(task, action, pull, comments, now=NOW)

    assert proof["result"] == "ready"
    assert proof["head"] == RESULT_HEAD
    assert proof["base"] == BASE
    assert proof["body"] == comments[0]["body"]


@pytest.mark.parametrize("body", [
    lambda: "Unquoted completion prose\n\n" + transported_v2_body(),
    lambda: "\n".join("> " + line for line in transported_v2_body().splitlines()),
    lambda: "```text\n" + transported_v2_body() + "\n```",
    lambda: "\n> Hermes-Task-Receipt: v2\n> copied marker\n" + transported_v2_body(),
    lambda: transported_v2_body() + "\nextra text",
])
def test_v2_transport_rejects_prose_quoted_or_ambiguous_receipts(body):
    task, action, pull, comments = v2_binding()
    comments[0]["body"] = body()

    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)


@pytest.mark.parametrize("result", ["ready", "conflict_incompatible", "policy_broken"])
def test_v2_echoes_session_not_task_but_host_still_binds_saved_task(result):
    task, action, pull, comments = v2_binding(result)
    proof = validate_task_receipt(task, action, pull, comments, now=NOW)
    assert proof["result"] == result
    assert proof["version"] == "v2"
    assert proof["task_id"] == TASK_ID
    assert proof["session_id"] == SESSION_ID
    assert proof["base"] == action["main_sha"]
    assert "task=" not in proof["body"]
    for identity in (task, task["sessions"][0]):
        field = "id" if identity is task else "task_id"
        identity[field] = "unrelated-task"
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, comments, now=NOW)
        identity[field] = TASK_ID


@pytest.mark.parametrize("change", [
    "missing_dispatch_base", "wrong_dispatch_base", "wrong_base", "current_not_dispatch_base",
    "session", "nonce", "pr", "start_head", "head", "result", "mixed_shape",
    "duplicate_field", "duplicate_comment", "mixed_versions", "edited", "trailing_text",
    "before_session", "after_session", "wrong_session_owner", "wrong_session_repository",
])
def test_v2_rejects_unbound_ambiguous_edited_or_out_of_interval_receipts(change):
    task, action, pull, comments = v2_binding()
    pull["base"]["sha"] = "d" * 40
    if change == "missing_dispatch_base":
        del action["main_sha"]
    elif change == "wrong_dispatch_base":
        action["main_sha"] = "e" * 40
    elif change in {"wrong_base", "current_not_dispatch_base"}:
        value = "e" * 40 if change == "wrong_base" else pull["base"]["sha"]
        comments[0]["body"] = comments[0]["body"].replace(f"base={BASE}", f"base={value}")
    elif change in {"session", "nonce", "pr", "start_head", "head", "result"}:
        field_value = {"session": SESSION_ID, "nonce": NONCE, "pr": "16",
                       "start_head": START_HEAD, "head": RESULT_HEAD, "result": "ready"}
        comments[0]["body"] = comments[0]["body"].replace(
            f"{change}={field_value[change]}", f"{change}=wrong",
        )
    elif change == "mixed_shape":
        comments[0]["body"] = comments[0]["body"].replace("session=", f"task={TASK_ID}\nsession=")
    elif change == "duplicate_field":
        comments[0]["body"] += f"\nnonce={NONCE}"
    elif change in {"duplicate_comment", "mixed_versions"}:
        comments.append(dict(comments[0], id=778))
        if change == "mixed_versions":
            comments[-1]["body"] = receipt_body().replace(f"base={BASE}", f"base={pull['base']['sha']}")
    elif change == "edited":
        comments[0]["updated_at"] = "2026-10-01T12:05:01Z"
    elif change == "trailing_text":
        comments[0]["body"] += "\n"
    elif change in {"before_session", "after_session"}:
        timestamp = "2026-10-01T12:00:30Z" if change == "before_session" else "2026-10-01T12:05:01Z"
        comments[0].update(created_at=timestamp, updated_at=timestamp)
    else:
        field = "owner" if change == "wrong_session_owner" else "repository"
        task["sessions"][0][field] = {"id": 42}
    if change == "nonce":
        assert validate_task_receipt(task, action, pull, comments, now=NOW) is None
    else:
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, comments, now=NOW)


def test_v2_first_observation_uses_dispatch_base_while_v1_keeps_initial_semantics():
    task, action, pull, comments = v2_binding()
    pull["base"]["sha"] = "d" * 40
    assert validate_task_receipt(task, action, pull, comments, now=NOW)["base"] == BASE
    comments[0]["body"] = receipt_body()
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)
    # Legacy v1 used main at initial validation, even if different at dispatch.
    comments[0]["body"] = receipt_body().replace(f"base={BASE}", f"base={pull['base']['sha']}")
    assert validate_task_receipt(task, action, pull, comments, now=NOW)["base"] == pull["base"]["sha"]


@pytest.mark.parametrize("author_id", [198982749.0, "198982749", True])
def test_v2_fresh_receipt_requires_strict_numeric_author(author_id):
    task, action, pull, comments = v2_binding()
    comments[0]["user"]["id"] = author_id
    assert validate_task_receipt(task, action, pull, comments, now=NOW) is None


def test_receipt_binds_task_session_nonce_pr_and_result_head():
    task, action, pull, comments = binding()

    proof = validate_task_receipt(task, action, pull, comments, now=NOW)

    assert proof == {
        "result": "ready", "comment_id": 777,
        "created_at": "2026-10-01T12:05:00Z", "body": receipt_body(),
        "task_id": TASK_ID, "session_id": SESSION_ID, "nonce": NONCE,
        "completed_at": "2026-10-01T12:05:00Z",
        "start_head": START_HEAD, "head": RESULT_HEAD, "base": BASE,
    }


@pytest.mark.parametrize(("change", "expected"), [
    (lambda task, action, pull, comments: comments[0].update(
        updated_at="2026-10-01T12:05:01Z",
    ), "error"),
    (lambda task, action, pull, comments: comments[0].update(user={"id": 42}), None),
    (lambda task, action, pull, comments: comments[0].update(id=True), "error"),
    (lambda task, action, pull, comments: task["sessions"][0].update(id=""), "error"),
    (lambda task, action, pull, comments: task["sessions"][0].update(
        prompt=receipt_instruction("different-nonce", pull_number=16, start_head=START_HEAD, base_sha=BASE),
    ), "error"),
    (lambda task, action, pull, comments: pull["head"].update(sha="d" * 40), "error"),
    (lambda task, action, pull, comments: comments[0].update(
        body=receipt_body("policy_broken"),
    ), "policy_broken"),
])
def test_receipt_handles_edited_wrong_identity_and_typed_result(change, expected):
    task, action, pull, comments = binding()
    change(task, action, pull, comments)

    if expected == "error":
        with pytest.raises(ReceiptError):
            validate_task_receipt(task, action, pull, comments, now=NOW)
    else:
        proof = validate_task_receipt(task, action, pull, comments, now=NOW)
        if expected is None:
            assert proof is None
        else:
            assert proof["result"] == expected


def test_missing_or_conflicting_receipts_do_not_prove_task_completion():
    task, action, pull, comments = binding()
    assert validate_task_receipt(task, action, pull, [], now=NOW) is None

    comments.append(comments[0] | {
        "id": 778, "body": receipt_body("policy_broken"),
    })
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)


def test_receipt_rejects_incomplete_task_session_and_artifact_evidence():
    task, action, pull, comments = binding()
    task["sessions"] = []
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)

    task, action, pull, comments = binding()
    task["artifacts"].pop()
    with pytest.raises(ReceiptError):
        validate_task_receipt(task, action, pull, comments, now=NOW)
