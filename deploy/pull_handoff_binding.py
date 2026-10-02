"""Bounded canonical issue/PR binding shared by handoff producer and consumer.

Dependency leaf: authenticated adapters are supplied by the caller; incomplete
reads never confer authority. Repository anchors and bounds are fixed policy.
"""

import hashlib
import re

REPOSITORY = "lindayi/hermes-mobile"
REPOSITORY_ID = 1399942965
MAX_PAGES = 20
MAX_ITEMS_PER_PAGE = 100
MAX_TEXT_CHARS = 60_000


def _is_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _pull_body_digest(pull):
    body = pull.get("body") if isinstance(pull, dict) else None
    if not isinstance(body, str) or len(body) > MAX_TEXT_CHARS:
        return None
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _pull_snapshot(pull):
    if not isinstance(pull, dict):
        return None
    head = pull.get("head")
    base = pull.get("base")
    head_repo = head.get("repo") if isinstance(head, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    body = pull.get("body")
    if (type(pull.get("id")) is not int or pull["id"] <= 0
            or type(pull.get("number")) is not int or pull["number"] <= 0
            or not isinstance(pull.get("node_id"), str) or not pull["node_id"]
            or pull.get("state") != "open" or pull.get("merged") is not False
            or type(pull.get("draft")) is not bool
            or not isinstance(head, dict) or not isinstance(head.get("ref"), str)
            or not head["ref"] or not _is_sha(head.get("sha"))
            or not isinstance(head_repo, dict)
            or type(head_repo.get("id")) is not int
            or head_repo["id"] != REPOSITORY_ID
            or not isinstance(base, dict) or not isinstance(base.get("ref"), str)
            or not isinstance(base_repo, dict)
            or type(base_repo.get("id")) is not int
            or base_repo["id"] != REPOSITORY_ID
            or not isinstance(base_repo.get("node_id"), str)
            or not base_repo["node_id"]
            or not isinstance(body, str) or len(body) > MAX_TEXT_CHARS):
        return None
    return (
        pull["id"], pull["number"], pull["node_id"], pull["state"],
        pull["merged"], pull["draft"], head["ref"], head["sha"],
        head_repo["id"], base["ref"], base_repo["id"], base_repo["node_id"], body,
        base.get("sha"),
    )


def _closing_issue_linked(api, pull, issue_number):
    snapshot = _pull_snapshot(pull)
    if snapshot is None or type(issue_number) is not int or issue_number <= 0:
        return False
    query = """
      query PullRequestClosingIssues($number: Int!, $after: String) {
        repository(owner: "lindayi", name: "hermes-mobile") {
          id
          nameWithOwner
          pullRequest(number: $number) {
            id
            number
            headRefName
            headRefOid
            baseRefName
            body
            closingIssuesReferences(first: 100, after: $after) {
              nodes { number repository { id nameWithOwner } }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    """
    cursor = None
    seen_cursors = set()
    seen_issues = set()
    linked = 0
    graph_snapshot = None
    repository_id = None
    for _ in range(MAX_PAGES):
        variables = {"number": pull["number"]}
        # Omit an absent cursor: both production adapters preserve omission,
        # while the coordinator CLI adapter stringifies Python None.
        if cursor is not None:
            variables["after"] = cursor
        response = api.graphql(query, variables)
        if (not isinstance(response, dict)
                or ("errors" in response and response["errors"] != [])):
            return False
        data = response.get("data")
        repository = data.get("repository") if isinstance(data, dict) else None
        node = repository.get("pullRequest") if isinstance(repository, dict) else None
        if (not isinstance(repository, dict)
                or not isinstance(repository.get("id"), str)
                or not repository["id"]
                or repository["id"] != pull["base"]["repo"]["node_id"]
                or not isinstance(repository.get("nameWithOwner"), str)
                or repository["nameWithOwner"].casefold() != REPOSITORY.casefold()
                or not isinstance(node, dict)
                or not isinstance(node.get("id"), str) or not node["id"]
                or type(node.get("number")) is not int
                or not isinstance(node.get("headRefName"), str)
                or not isinstance(node.get("headRefOid"), str)
                or not isinstance(node.get("baseRefName"), str)
                or not isinstance(node.get("body"), str)
                or len(node["body"]) > MAX_TEXT_CHARS):
            return False
        current_graph_snapshot = (
            repository["id"], repository["nameWithOwner"].casefold(),
            node["id"], node["number"], node["headRefName"],
            node["headRefOid"], node["baseRefName"], node["body"],
        )
        if graph_snapshot is None:
            graph_snapshot = current_graph_snapshot
            repository_id = repository["id"]
        elif current_graph_snapshot != graph_snapshot:
            return False
        head = pull["head"]
        base = pull["base"]
        if (node["id"] != pull["node_id"]
                or node["number"] != pull["number"]
                or node["headRefName"] != head["ref"]
                or node["headRefOid"] != head["sha"]
                or node["baseRefName"] != base["ref"]
                or node["body"] != pull["body"]):
            return False
        connection = node.get("closingIssuesReferences")
        nodes = connection.get("nodes") if isinstance(connection, dict) else None
        page_info = connection.get("pageInfo") if isinstance(connection, dict) else None
        if (not isinstance(nodes, list) or len(nodes) > MAX_ITEMS_PER_PAGE
                or not isinstance(page_info, dict)
                or type(page_info.get("hasNextPage")) is not bool):
            return False
        for reference in nodes:
            issue_repository = (
                reference.get("repository") if isinstance(reference, dict) else None
            )
            number = reference.get("number") if isinstance(reference, dict) else None
            if (type(number) is not int or number <= 0
                    or not isinstance(issue_repository, dict)
                    or not isinstance(issue_repository.get("id"), str)
                    or not issue_repository["id"]
                    or not isinstance(issue_repository.get("nameWithOwner"), str)
                    or not issue_repository["nameWithOwner"]):
                return False
            issue_key = (issue_repository["id"], number)
            if issue_key in seen_issues:
                return False
            seen_issues.add(issue_key)
            if number == issue_number:
                if (issue_repository["id"] != repository_id
                        or issue_repository["nameWithOwner"].casefold()
                        != REPOSITORY.casefold()):
                    return False
                linked += 1
        has_next = page_info["hasNextPage"]
        end_cursor = page_info.get("endCursor")
        if (end_cursor is not None
                and (not isinstance(end_cursor, str) or len(end_cursor) > 2048)):
            return False
        if not has_next:
            break
        if (not isinstance(end_cursor, str) or not end_cursor
                or end_cursor in seen_cursors):
            return False
        seen_cursors.add(end_cursor)
        cursor = end_cursor
    else:
        return False
    if linked != 1:
        return False
    fresh = api.get(f"repos/{REPOSITORY}/pulls/{pull['number']}")
    return _pull_snapshot(fresh) == snapshot
