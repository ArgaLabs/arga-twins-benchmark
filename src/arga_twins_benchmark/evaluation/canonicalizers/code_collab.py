# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# Provider JSON is validated at runtime before values are projected.

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlsplit

from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedQueryState,
    SnapshotCanonicalizer,
    StateCaptureError,
)

_MARKER_RE = re.compile(r"\b(?:INC-\d+|REL-\d+|MIG-\d+|SPEC-\d+:R\d+|RB-\d+)\b")
_GITHUB_REPOSITORY_PATH_RE = re.compile(r"^/repos/([^/]+)/([^/]+)(?:/|$)")
_GITHUB_PULL_PATH_RE = re.compile(r"/pulls/(\d+)(?:/|$)")
_GITLAB_PROJECT_PATH_RE = re.compile(r"^/api/v4/projects/([^/]+)(?:/|$)")
_GITLAB_MERGE_REQUEST_PATH_RE = re.compile(r"/merge_requests/(\d+)(?:/|$)")


def _error(capture: CapturedQueryState, message: str) -> StateCaptureError:
    return StateCaptureError(f"{capture.canonicalizer}: {message}")


def _object(value: object, capture: CapturedQueryState, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(capture, f"{label} must be a JSON object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise _error(capture, f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _array(value: object, capture: CapturedQueryState, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise _error(capture, f"{label} must be a JSON array")
    return cast(list[Any], value)


def _string(value: object, capture: CapturedQueryState, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(capture, f"{label} must be a non-empty string")
    return value


def _integer(value: object, capture: CapturedQueryState, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(capture, f"{label} must be an integer")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _marker(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and (match := _MARKER_RE.search(value)):
            return match.group(0)
    return None


def _stable_user(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    user = cast(dict[str, Any], value)
    stable: dict[str, Any] = {}
    for source, destination in (
        ("id", "id"),
        ("login", "login"),
        ("username", "username"),
        ("accountId", "account_id"),
        ("displayName", "display_name"),
        ("name", "name"),
        ("type", "type"),
        ("bot", "bot"),
    ):
        item = user.get(source)
        if isinstance(item, (str, int, bool)) and not isinstance(item, float):
            stable[destination] = item
    return stable or None


def _stable_string_list(value: object, *, object_key: str | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in cast(list[Any], value):
        if isinstance(item, str):
            result.append(item)
        elif object_key is not None and isinstance(item, dict) and isinstance(item.get(object_key), str):
            result.append(cast(str, item[object_key]))
    return sorted(set(result))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _content_hash(value: object, *, encoding: object = None) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.encode()
    if encoding == "base64":
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            return None
    return _sha256_bytes(raw)


def _github_context(capture: CapturedQueryState) -> tuple[str, int | None]:
    path = urlsplit(capture.path).path
    match = _GITHUB_REPOSITORY_PATH_RE.match(path)
    if match is None:
        raise _error(capture, "path does not identify a GitHub repository")
    repository = f"{unquote(match.group(1))}/{unquote(match.group(2))}"
    pull_match = _GITHUB_PULL_PATH_RE.search(path)
    return repository, int(pull_match.group(1)) if pull_match is not None else None


def _github_repository_metadata(body: Mapping[str, Any], repository: str) -> dict[str, Any]:
    return {
        "repository": repository,
        "name": body.get("name"),
        "full_name": body.get("full_name"),
        "private": body.get("private"),
        "description": body.get("description"),
        "fork": body.get("fork"),
        "default_branch": body.get("default_branch"),
        "archived": body.get("archived"),
        "disabled": body.get("disabled"),
        "visibility": body.get("visibility"),
        "topics": _stable_string_list(body.get("topics")),
    }


def _github_pull_fields(
    raw: object,
    capture: CapturedQueryState,
    repository: str,
) -> tuple[str, dict[str, Any]]:
    pull = _object(raw, capture, "pull request")
    number = _integer(pull.get("number"), capture, "pull request number")
    raw_head = pull.get("head")
    raw_base = pull.get("base")
    head = cast(dict[str, Any], raw_head) if isinstance(raw_head, dict) else {}
    base = cast(dict[str, Any], raw_base) if isinstance(raw_base, dict) else {}
    assignee = pull.get("assignee")
    assignee_dict = cast(dict[str, Any], assignee) if isinstance(assignee, dict) else None
    raw_assignees = pull.get("assignees")
    assignees = cast(list[Any], raw_assignees) if isinstance(raw_assignees, list) else []
    fields = {
        "repository": repository,
        "number": number,
        "state": pull.get("state"),
        "merged": bool(pull.get("merged", pull.get("merged_at") is not None)),
        "locked": pull.get("locked"),
        "title": pull.get("title"),
        "body": pull.get("body"),
        "draft": pull.get("draft"),
        "head_ref": head.get("ref"),
        "head_sha": head.get("sha"),
        "base_ref": base.get("ref"),
        "base_sha": base.get("sha"),
        "labels": _stable_string_list(pull.get("labels"), object_key="name"),
        "assignee_login": assignee_dict.get("login") if assignee_dict is not None else None,
        "assignees": sorted(
            item["login"] for item in assignees if isinstance(item, dict) and isinstance(item.get("login"), str)
        ),
    }
    return f"{repository}#{number}", fields


def _github_pulls(
    body: object,
    capture: CapturedQueryState,
    repository: str,
) -> tuple[list[CanonicalResource], list[dict[str, Any]]]:
    raw_pulls: list[Any] = cast(list[Any], body) if isinstance(body, list) else [body]
    pulls: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in raw_pulls:
        resource_id, fields = _github_pull_fields(raw, capture, repository)
        pulls.append(CanonicalResource(capture.provider_role, "pull_request", resource_id, fields))
        pulls.append(
            CanonicalResource(
                capture.provider_role,
                "pull_request_collection",
                resource_id,
                dict(fields),
            )
        )
        stable.append(dict(fields))
    stable.sort(key=lambda item: cast(int, item["number"]))
    return pulls, stable


def github_repository_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    body = _object(capture.body, capture, "repository response")
    full_name = _string(body.get("full_name"), capture, "repository full_name")
    if full_name != repository:
        raise _error(capture, f"response repository {full_name!r} does not match path {repository!r}")
    metadata = _github_repository_metadata(body, repository)
    return [
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {"repository": repository, "metadata": metadata},
        )
    ]


def github_refs_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    raw_refs = _array(capture.body, capture, "Git refs response")
    refs: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in raw_refs:
        item = _object(raw, capture, "Git ref")
        ref = _string(item.get("ref"), capture, "Git ref name")
        target = _object(item.get("object"), capture, f"Git ref {ref} object")
        sha = _string(target.get("sha"), capture, f"Git ref {ref} SHA")
        fields = {
            "repository": repository,
            "ref": ref,
            "sha": sha,
            "object_type": target.get("type"),
        }
        refs.append(CanonicalResource(capture.provider_role, "git_ref", f"{repository}:{ref}", fields))
        stable.append(fields)
    stable.sort(key=lambda item: cast(str, item["ref"]))
    refs.append(
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {"repository": repository, "refs": stable},
        )
    )
    return refs


def github_pulls_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    _array(capture.body, capture, "pull request response")
    resources, stable = _github_pulls(capture.body, capture, repository)
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {"repository": repository, "pulls": stable},
        )
    )
    return resources


def _github_content_resources(
    capture: CapturedQueryState,
    *,
    repository: str,
) -> tuple[list[CanonicalResource], list[dict[str, Any]]]:
    parsed = urlsplit(capture.path)
    ref = parse_qs(parsed.query).get("ref", ["main"])[0]
    values = capture.body if isinstance(capture.body, list) else [capture.body]
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in values:
        item = _object(raw, capture, "repository content")
        path = _string(item.get("path"), capture, "repository content path")
        item_type = _string(item.get("type"), capture, f"repository content {path} type")
        fields = {
            "repository": repository,
            "path": path,
            "ref": ref,
            "type": item_type,
            "sha": item.get("sha"),
            "size": item.get("size"),
            "content_hash": _content_hash(item.get("content"), encoding=item.get("encoding")),
        }
        resources.append(CanonicalResource(capture.provider_role, "file", f"{repository}@{ref}:{path}", fields))
        stable.append(fields)
    stable.sort(key=lambda item: cast(str, item["path"]))
    return resources, stable


def github_contents_recursive_hashes(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    resources, stable = _github_content_resources(capture, repository=repository)
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {"repository": repository, "contents": stable},
        )
    )
    return resources


def github_file_and_ref_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    if isinstance(capture.body, list):
        raise _error(capture, "file response must be one object")
    resources, _ = _github_content_resources(capture, repository=repository)
    return resources


def _github_review_resources(
    capture: CapturedQueryState,
    *,
    repository: str,
    pull_number: int,
    raw_reviews: object,
) -> tuple[list[CanonicalResource], list[dict[str, Any]]]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in _array(raw_reviews, capture, "review response"):
        review = _object(raw, capture, "pull request review")
        review_id = review.get("id")
        if not isinstance(review_id, (str, int)) or isinstance(review_id, bool):
            raise _error(capture, "pull request review id must be a string or integer")
        user = _stable_user(review.get("user"))
        author_login = user.get("login") if user is not None else None
        fields = {
            "repository": repository,
            "pull_number": int(review.get("pull_number", pull_number)),
            "state": review.get("state"),
            "body": review.get("body"),
            "commit_id": review.get("commit_id"),
            "author_login": author_login,
            "user": user,
        }
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "pull_request_review",
                f"{repository}#{pull_number}:review:{review_id}",
                fields,
            )
        )
        stable.append({"id": str(review_id), **fields})
    stable.sort(key=lambda item: cast(str, item["id"]))
    return resources, stable


def github_reviews_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, pull_number = _github_context(capture)
    if pull_number is None:
        raise _error(capture, "review path does not identify a pull request")
    resources, stable = _github_review_resources(
        capture,
        repository=repository,
        pull_number=pull_number,
        raw_reviews=capture.body,
    )
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {
                "repository": repository,
                "reviews_by_pull": {str(pull_number): stable},
            },
        )
    )
    return resources


def _github_comment_resources(
    capture: CapturedQueryState,
    *,
    repository: str,
    pull_number: int,
    raw_comments: object,
) -> tuple[list[CanonicalResource], list[dict[str, Any]]]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in _array(raw_comments, capture, "review comment response"):
        comment = _object(raw, capture, "pull request review comment")
        comment_id = comment.get("id")
        if not isinstance(comment_id, (str, int)) or isinstance(comment_id, bool):
            raise _error(capture, "pull request review comment id must be a string or integer")
        user = _stable_user(comment.get("user"))
        author_login = user.get("login") if user is not None else None
        fields = {
            "repository": repository,
            "pull_number": int(comment.get("pull_number", pull_number)),
            "body": comment.get("body"),
            "path": comment.get("path"),
            "line": comment.get("line"),
            "side": comment.get("side"),
            "commit_id": comment.get("commit_id"),
            "pull_request_review_id": comment.get("pull_request_review_id"),
            "author_login": author_login,
            "user": user,
            "attached_to": comment.get("pull_request_review_id"),
        }
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "pull_request_review_comment",
                f"{repository}#{pull_number}:comment:{comment_id}",
                fields,
            )
        )
        stable.append({"id": str(comment_id), **fields})
    stable.sort(key=lambda item: cast(str, item["id"]))
    return resources, stable


def github_review_comments_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, pull_number = _github_context(capture)
    if pull_number is None:
        raise _error(capture, "review-comment path does not identify a pull request")
    resources, stable = _github_comment_resources(
        capture,
        repository=repository,
        pull_number=pull_number,
        raw_comments=capture.body,
    )
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {
                "repository": repository,
                "review_comments_by_pull": {str(pull_number): stable},
            },
        )
    )
    return resources


def github_all_pull_review_artifacts(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    raw_pulls = capture.body.get("pulls") if isinstance(capture.body, dict) else capture.body
    values = _array(raw_pulls, capture, "pull artifact response")
    resources, stable_pulls = _github_pulls(values, capture, repository)
    reviews_by_pull: dict[str, list[dict[str, Any]]] = {}
    comments_by_pull: dict[str, list[dict[str, Any]]] = {}
    for raw in values:
        pull = _object(raw, capture, "pull artifact")
        number = _integer(pull.get("number"), capture, "pull artifact number")
        if "reviews" not in pull or not any(key in pull for key in ("review_comments", "comments")):
            raise _error(
                capture,
                "pull artifact response omits embedded reviews or review comments",
            )
        review_resources, stable_reviews = _github_review_resources(
            capture,
            repository=repository,
            pull_number=number,
            raw_reviews=pull["reviews"],
        )
        comment_resources, stable_comments = _github_comment_resources(
            capture,
            repository=repository,
            pull_number=number,
            raw_comments=pull.get("review_comments", pull.get("comments")),
        )
        resources.extend(review_resources)
        resources.extend(comment_resources)
        reviews_by_pull[str(number)] = stable_reviews
        comments_by_pull[str(number)] = stable_comments
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {
                "repository": repository,
                "all_pull_artifacts": {
                    "pulls": stable_pulls,
                    "reviews_by_pull": reviews_by_pull,
                    "review_comments_by_pull": comments_by_pull,
                },
            },
        )
    )
    return resources


def github_repository_and_pulls_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    _array(capture.body, capture, "pull request response")
    resources, stable = _github_pulls(capture.body, capture, repository)
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "repository_snapshot",
            repository,
            {"repository": repository, "pulls": stable},
        )
    )
    return resources


def github_pull_and_repo_tree_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    repository, _ = _github_context(capture)
    resources, stable = _github_pulls(capture.body, capture, repository)
    owner, repo = repository.split("/", 1)
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "repository",
            repository,
            {"owner": owner, "repo": repo, "pulls": stable},
        )
    )
    return resources


def _gitlab_context(capture: CapturedQueryState) -> tuple[str, int | None]:
    path = urlsplit(capture.path).path
    match = _GITLAB_PROJECT_PATH_RE.match(path)
    if match is None:
        raise _error(capture, "path does not identify a GitLab project")
    project = unquote(match.group(1))
    merge_request_match = _GITLAB_MERGE_REQUEST_PATH_RE.search(path)
    return project, int(merge_request_match.group(1)) if merge_request_match is not None else None


def _gitlab_project_metadata(body: Mapping[str, Any], project: str) -> dict[str, Any]:
    return {
        "project": project,
        "id": body.get("id"),
        "name": body.get("name"),
        "path": body.get("path"),
        "path_with_namespace": body.get("path_with_namespace"),
        "description": body.get("description"),
        "visibility": body.get("visibility"),
        "default_branch": body.get("default_branch"),
        "archived": body.get("archived"),
        "topics": _stable_string_list(body.get("topics")),
    }


def _gitlab_merge_request_fields(
    raw: object,
    capture: CapturedQueryState,
    project: str,
) -> tuple[str, dict[str, Any]]:
    merge_request = _object(raw, capture, "merge request")
    iid = _integer(merge_request.get("iid"), capture, "merge request iid")
    approved_by = merge_request.get("approved_by")
    fields = {
        "project": project,
        "iid": iid,
        "title": merge_request.get("title"),
        "description": merge_request.get("description"),
        "state": merge_request.get("state"),
        "merged": merge_request.get("state") == "merged" or merge_request.get("merged_at") is not None,
        "approved": bool(merge_request.get("approved") or (isinstance(approved_by, list) and len(approved_by) > 0)),
        "source_branch": merge_request.get("source_branch"),
        "target_branch": merge_request.get("target_branch"),
        "sha": merge_request.get("sha"),
        "draft": merge_request.get("draft", merge_request.get("work_in_progress", False)),
        "labels": _stable_string_list(merge_request.get("labels")),
    }
    return f"{project}!{iid}", fields


def _gitlab_merge_requests(
    body: object,
    capture: CapturedQueryState,
    project: str,
) -> tuple[list[CanonicalResource], list[dict[str, Any]]]:
    values: list[Any] = cast(list[Any], body) if isinstance(body, list) else [body]
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in values:
        resource_id, fields = _gitlab_merge_request_fields(raw, capture, project)
        resources.append(CanonicalResource(capture.provider_role, "merge_request", resource_id, fields))
        stable.append(fields)
    stable.sort(key=lambda item: cast(int, item["iid"]))
    return resources, stable


def gitlab_project_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    project, _ = _gitlab_context(capture)
    body = _object(capture.body, capture, "project response")
    response_project = _string(
        body.get("path_with_namespace"),
        capture,
        "project path_with_namespace",
    )
    if response_project != project:
        raise _error(capture, f"response project {response_project!r} does not match path {project!r}")
    return [
        CanonicalResource(
            capture.provider_role,
            "project_snapshot",
            project,
            {"project": project, "metadata": _gitlab_project_metadata(body, project)},
        )
    ]


def gitlab_branches_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    project, _ = _gitlab_context(capture)
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw in _array(capture.body, capture, "branch response"):
        branch = _object(raw, capture, "branch")
        name = _string(branch.get("name"), capture, "branch name")
        commit = _object(branch.get("commit"), capture, f"branch {name} commit")
        commit_id = _string(commit.get("id"), capture, f"branch {name} commit id")
        fields = {
            "project": project,
            "name": name,
            "commit_id": commit_id,
            "default": branch.get("default"),
            "merged": branch.get("merged"),
            "protected": branch.get("protected"),
        }
        resources.append(CanonicalResource(capture.provider_role, "branch", f"{project}:{name}", fields))
        stable.append(fields)
    stable.sort(key=lambda item: cast(str, item["name"]))
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "project_snapshot",
            project,
            {"project": project, "branches": stable},
        )
    )
    return resources


def gitlab_merge_requests_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    project, _ = _gitlab_context(capture)
    _array(capture.body, capture, "merge request response")
    resources, stable = _gitlab_merge_requests(capture.body, capture, project)
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "project_snapshot",
            project,
            {"project": project, "merge_requests": stable},
        )
    )
    return resources


def _gitlab_file_resource(
    capture: CapturedQueryState,
    *,
    project: str,
) -> CanonicalResource:
    body = _object(capture.body, capture, "repository file response")
    path = _string(body.get("file_path"), capture, "repository file path")
    query_ref = parse_qs(urlsplit(capture.path).query).get("ref", [None])[0]
    ref = query_ref or _optional_string(body.get("ref"))
    if ref is None:
        raise _error(capture, "repository file response has no ref")
    digest = _content_hash(body.get("content"), encoding=body.get("encoding"))
    if digest is None:
        digest = _optional_string(body.get("content_sha256"))
    if digest is None:
        raise _error(capture, "repository file response has no valid content hash")
    fields = {
        "repository": project,
        "project": project,
        "path": path,
        "ref": ref,
        "content_hash": digest,
        "size": body.get("size"),
        "blob_id": body.get("blob_id"),
        "commit_id": body.get("commit_id"),
    }
    return CanonicalResource(capture.provider_role, "file", f"{project}@{ref}:{path}", fields)


def gitlab_file_hash(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    project, _ = _gitlab_context(capture)
    resource = _gitlab_file_resource(capture, project=project)
    return [
        resource,
        CanonicalResource(
            capture.provider_role,
            "project_snapshot",
            project,
            {
                "project": project,
                "files": [dict(resource.fields)],
            },
        ),
    ]


def gitlab_file_and_branch_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    project, _ = _gitlab_context(capture)
    return [_gitlab_file_resource(capture, project=project)]


def gitlab_discussions_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    project, merge_request_iid = _gitlab_context(capture)
    if merge_request_iid is None:
        raise _error(capture, "discussion path does not identify a merge request")
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for raw_discussion in _array(capture.body, capture, "discussion response"):
        discussion = _object(raw_discussion, capture, "discussion")
        discussion_id = discussion.get("id")
        if not isinstance(discussion_id, (str, int)) or isinstance(discussion_id, bool):
            raise _error(capture, "discussion id must be a string or integer")
        notes = _array(discussion.get("notes"), capture, f"discussion {discussion_id} notes")
        if not notes:
            raise _error(capture, f"discussion {discussion_id} has no notes")
        for raw_note in notes:
            note = _object(raw_note, capture, f"discussion {discussion_id} note")
            note_id = note.get("id")
            if not isinstance(note_id, (str, int)) or isinstance(note_id, bool):
                raise _error(capture, "discussion note id must be a string or integer")
            raw_position = note.get("position")
            author = _stable_user(note.get("author"))
            if raw_position is None:
                fields = {
                    "project": project,
                    "merge_request_iid": merge_request_iid,
                    "discussion_id": str(discussion_id),
                    "body": note.get("body"),
                    "resolved": bool(note.get("resolved", False)),
                    "author_username": author.get("username") if author is not None else None,
                    "author": author,
                }
                resources.append(
                    CanonicalResource(
                        capture.provider_role,
                        "merge_request_discussion_note",
                        f"{project}!{merge_request_iid}:discussion:{discussion_id}:note:{note_id}",
                        fields,
                    )
                )
                stable.append({"id": str(note_id), "kind": "general_note", **fields})
                continue
            position = _object(raw_position, capture, f"discussion note {note_id} position")
            fields = {
                "project": project,
                "merge_request_iid": merge_request_iid,
                "discussion_id": str(discussion_id),
                "body": note.get("body"),
                "path": position.get("new_path", position.get("old_path")),
                "new_line": position.get("new_line"),
                "old_line": position.get("old_line"),
                "resolved": bool(note.get("resolved", False)),
                "commit_sha": position.get("head_sha", position.get("base_sha")),
                "author_username": author.get("username") if author is not None else None,
                "author": author,
                "position": {
                    key: position.get(key)
                    for key in (
                        "position_type",
                        "base_sha",
                        "start_sha",
                        "head_sha",
                        "old_path",
                        "new_path",
                        "old_line",
                        "new_line",
                    )
                    if key in position
                },
            }
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "merge_request_diff_discussion",
                    f"{project}!{merge_request_iid}:discussion:{discussion_id}:note:{note_id}",
                    fields,
                )
            )
            stable.append({"id": str(note_id), "kind": "diff_note", **fields})
    stable.sort(key=lambda item: cast(str, item["id"]))
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "project_snapshot",
            project,
            {
                "project": project,
                "discussions_by_merge_request": {str(merge_request_iid): stable},
            },
        )
    )
    return resources


def gitlab_project_and_merge_requests_stable(
    capture: CapturedQueryState,
) -> Sequence[CanonicalResource]:
    project, _ = _gitlab_context(capture)
    _array(capture.body, capture, "merge request response")
    resources, stable = _gitlab_merge_requests(capture.body, capture, project)
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "project_snapshot",
            project,
            {"project": project, "merge_requests": stable},
        )
    )
    return resources


def gitlab_merge_request_and_tree_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    project, _ = _gitlab_context(capture)
    resources, stable = _gitlab_merge_requests(capture.body, capture, project)
    resources.append(
        CanonicalResource(
            capture.provider_role,
            "project",
            project,
            {"path": project, "merge_requests": stable},
        )
    )
    return resources


def _adf_text(value: object, capture: CapturedQueryState, label: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    node = _object(value, capture, label)
    node_type = node.get("type")
    if node_type == "text":
        text = node.get("text")
        if not isinstance(text, str):
            raise _error(capture, f"{label} text node has no text")
        return text
    content = node.get("content", [])
    children = _array(content, capture, f"{label} content")
    rendered = [_adf_text(child, capture, f"{label} child") for child in children]
    separator = "\n" if node_type in {"doc", "paragraph", "heading", "blockquote", "listItem"} else ""
    return separator.join(part for part in rendered if part).strip()


def _jira_project(capture: CapturedQueryState) -> str | None:
    query = parse_qs(urlsplit(capture.path).query)
    jql = query.get("jql", [""])[0]
    match = re.search(r"\bproject\s*=\s*([A-Za-z][A-Za-z0-9_-]*)", jql, re.IGNORECASE)
    return match.group(1).upper() if match is not None else None


def _jira_status_type(status: object) -> str | None:
    if not isinstance(status, dict):
        return None
    raw = cast(dict[str, Any], status)
    category = raw.get("statusCategory")
    key = category.get("key") if isinstance(category, dict) else None
    if key == "done" or str(raw.get("name", "")).casefold() in {"done", "closed", "resolved"}:
        return "closed"
    return "open"


def _jira_issue_resources(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    body = _object(capture.body, capture, "Jira search response")
    issues = _array(body.get("issues"), capture, "Jira search issues")
    expected_project = _jira_project(capture)
    resources: list[CanonicalResource] = []
    projects: dict[str, list[dict[str, Any]]] = {}
    for raw in issues:
        issue = _object(raw, capture, "Jira issue")
        key = _string(issue.get("key"), capture, "Jira issue key")
        fields = _object(issue.get("fields"), capture, f"Jira issue {key} fields")
        project = _object(fields.get("project"), capture, f"Jira issue {key} project")
        project_key = _string(project.get("key"), capture, f"Jira issue {key} project key")
        if expected_project is not None and project_key != expected_project:
            raise _error(capture, f"Jira search returned issue outside project {expected_project}")
        summary = _string(fields.get("summary"), capture, f"Jira issue {key} summary")
        description = _adf_text(fields.get("description"), capture, f"Jira issue {key} description")
        status = fields.get("status")
        priority = fields.get("priority")
        issue_type = fields.get("issuetype")
        canonical = {
            "key": key,
            "project_key": project_key,
            "summary": summary,
            "description": description,
            "marker": _marker(summary, description),
            "priority": priority.get("name") if isinstance(priority, dict) else priority,
            "status": status.get("name") if isinstance(status, dict) else status,
            "status_type": _jira_status_type(status),
            "status_category": _jira_status_type(status),
            "issue_type": issue_type.get("name") if isinstance(issue_type, dict) else issue_type,
        }
        resources.append(CanonicalResource(capture.provider_role, "issue", key, canonical))
        projects.setdefault(project_key, []).append(canonical)

        comment_container = fields.get("comment", {"comments": []})
        comments = _object(
            comment_container,
            capture,
            f"Jira issue {key} comment container",
        )
        for raw_comment in _array(
            comments.get("comments", []),
            capture,
            f"Jira issue {key} comments",
        ):
            comment = _object(raw_comment, capture, f"Jira issue {key} comment")
            comment_id = comment.get("id")
            if not isinstance(comment_id, (str, int)) or isinstance(comment_id, bool):
                raise _error(capture, f"Jira issue {key} comment id must be a string or integer")
            author = _stable_user(comment.get("author"))
            body_text = _adf_text(
                comment.get("body"),
                capture,
                f"Jira issue {key} comment {comment_id} body",
            )
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "issue_comment",
                    f"{key}:comment:{comment_id}",
                    {
                        "issue_key": key,
                        "body": body_text,
                        "author": author,
                        "created_by": (
                            author.get("account_id") or author.get("display_name") if author is not None else None
                        ),
                    },
                )
            )
    for project_key, project_issues in sorted(projects.items()):
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "issue_collection",
                f"project:{project_key}",
                {
                    "project_key": project_key,
                    "issues": sorted(project_issues, key=lambda item: cast(str, item["key"])),
                },
            )
        )
    return resources


def jira_SEC_issues_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _jira_issue_resources(capture)


def jira_issues_and_comments_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _jira_issue_resources(capture)


def jira_issues_comments_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _jira_issue_resources(capture)


def _linear_state_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.casefold()
    if normalized in {"completed", "done"}:
        return "completed"
    if normalized in {"canceled", "cancelled"}:
        return "canceled"
    if normalized in {"backlog", "triage", "unstarted", "started", "open"}:
        return "open"
    return value


def _linear_object_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    candidate = cast(dict[str, Any], value).get("id")
    return candidate if isinstance(candidate, str) else None


def _linear_connection_ids(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    nodes = cast(dict[str, Any], value).get("nodes")
    if not isinstance(nodes, list):
        return []
    return sorted(
        cast(str, item["id"])
        for item in cast(list[object], nodes)
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )


def _linear_resources(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    body = _object(capture.body, capture, "Linear GraphQL response")
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        raise _error(capture, "Linear GraphQL response contains errors")
    data = _object(body.get("data"), capture, "Linear GraphQL data")
    issue_connection = _object(data.get("issues"), capture, "Linear issues connection")
    raw_issues = _array(issue_connection.get("nodes"), capture, "Linear issue nodes")
    project_connection = _object(
        data.get("projects", {"nodes": []}),
        capture,
        "Linear projects connection",
    )
    raw_projects = _array(project_connection.get("nodes", []), capture, "Linear project nodes")

    resources: list[CanonicalResource] = []
    project_names: set[str] = set()
    for raw_project in raw_projects:
        project = _object(raw_project, capture, "Linear project")
        project_id = _string(project.get("id"), capture, "Linear project id")
        name = _string(project.get("name"), capture, "Linear project name")
        project_names.add(name)
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "project",
                project_id,
                {
                    "name": name,
                    "slug_id": project.get("slugId"),
                    "state": project.get("state"),
                    "description": project.get("description"),
                    "summary": project.get("summary"),
                    "archived": project.get("archivedAt") is not None,
                },
            )
        )

    team_issues: dict[str, list[dict[str, Any]]] = {}
    for raw_issue in raw_issues:
        issue = _object(raw_issue, capture, "Linear issue")
        issue_id = _string(issue.get("id"), capture, "Linear issue id")
        identifier = _string(issue.get("identifier"), capture, f"Linear issue {issue_id} identifier")
        team = _object(issue.get("team"), capture, f"Linear issue {identifier} team")
        team_key = _string(team.get("key"), capture, f"Linear issue {identifier} team key")
        raw_state = issue.get("state")
        state = None if raw_state is None else _object(raw_state, capture, f"Linear issue {identifier} state")
        project = issue.get("project")
        project_name = project.get("name") if isinstance(project, dict) else None
        if project_name is not None and project_names and project_name not in project_names:
            raise _error(capture, f"Linear issue {identifier} references an unknown project")
        title = _string(issue.get("title"), capture, f"Linear issue {identifier} title")
        description = issue.get("description")
        canonical = {
            "archived_at": issue.get("archivedAt"),
            "assignee_id": _linear_object_id(issue.get("assignee")),
            "branch_name": issue.get("branchName"),
            "canceled_at": issue.get("canceledAt"),
            "completed_at": issue.get("completedAt"),
            "created_at": issue.get("createdAt"),
            "creator_id": _linear_object_id(issue.get("creator")),
            "cycle_id": _linear_object_id(issue.get("cycle")),
            "due_date": issue.get("dueDate"),
            "estimate": issue.get("estimate"),
            "identifier": identifier,
            "label_ids": _stable_string_list(issue.get("labelIds")),
            "milestone_id": _linear_object_id(issue.get("projectMilestone")),
            "number": issue.get("number"),
            "parent_id": _linear_object_id(issue.get("parent")),
            "project_id": _linear_object_id(project),
            "sort_order": issue.get("sortOrder"),
            "started_at": issue.get("startedAt"),
            "state_id": _linear_object_id(state),
            "subscriber_ids": _linear_connection_ids(issue.get("subscribers")),
            "team_id": _linear_object_id(team),
            "updated_at": issue.get("updatedAt"),
            "url": issue.get("url"),
            "team_key": team_key,
            "team": {
                "id": team.get("id"),
                "key": team_key,
                "name": team.get("name"),
            },
            "title": title,
            "description": description,
            "marker": _marker(title, description),
            "priority": issue.get("priority"),
            "state": state.get("name") if state is not None else None,
            "state_type": _linear_state_type(state.get("type")) if state is not None else None,
            "project": project_name,
            "archived": issue.get("archivedAt") is not None,
        }
        resources.append(CanonicalResource(capture.provider_role, "issue", issue_id, canonical))
        team_issues.setdefault(team_key, []).append(canonical)

        comments = _object(
            issue.get("comments", {"nodes": []}),
            capture,
            f"Linear issue {identifier} comments",
        )
        for raw_comment in _array(
            comments.get("nodes", []),
            capture,
            f"Linear issue {identifier} comment nodes",
        ):
            comment = _object(raw_comment, capture, f"Linear issue {identifier} comment")
            comment_id = _string(
                comment.get("id"),
                capture,
                f"Linear issue {identifier} comment id",
            )
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "issue_comment",
                    comment_id,
                    {
                        "created_at": comment.get("createdAt"),
                        "edited_at": comment.get("editedAt"),
                        "issue_id": issue_id,
                        "issue_identifier": identifier,
                        "parent_id": _linear_object_id(comment.get("parent")),
                        "updated_at": comment.get("updatedAt"),
                        "url": comment.get("url"),
                        "user_id": _linear_object_id(comment.get("user")),
                        "body": comment.get("body"),
                        "user": _stable_user(comment.get("user")),
                    },
                )
            )
    for team_key, issues in sorted(team_issues.items()):
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "issue_collection",
                f"team:{team_key}",
                {
                    "team_key": team_key,
                    "issues": sorted(issues, key=lambda item: cast(str, item["identifier"])),
                    "markers": sorted(marker for item in issues if (marker := item.get("marker")) is not None),
                },
            )
        )
    return resources


def linear_issues_comments_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _linear_resources(capture)


def linear_issues_projects_comments_v1(
    capture: CapturedQueryState,
) -> Sequence[CanonicalResource]:
    return _linear_resources(capture)


def linear_team_ENG_issues_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _linear_resources(capture)


def linear_team_OPS_issues_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _linear_resources(capture)


def linear_team_REL_issues_comments_stable(
    capture: CapturedQueryState,
) -> Sequence[CanonicalResource]:
    return _linear_resources(capture)


def _slack_messages_by_channel(
    body: Mapping[str, Any],
    channels: Sequence[Mapping[str, Any]],
    capture: CapturedQueryState,
) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    top_level = body.get("messages_by_channel")
    for channel in channels:
        channel_id = _string(channel.get("id"), capture, "Slack channel id")
        nested = channel.get("messages")
        raw_messages: object
        if nested is not None:
            raw_messages = nested
        elif isinstance(top_level, dict):
            raw_messages = top_level.get(channel_id)
        else:
            raise _error(
                capture,
                "conversations.list response omits message history; use an enriched verifier read",
            )
        result[channel_id] = _array(raw_messages, capture, f"Slack channel {channel_id} messages")
    return result


def _slack_resources(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    body = _object(capture.body, capture, "Slack conversations.list response")
    if body.get("ok") is False:
        raise _error(capture, "Slack response has ok=false")
    raw_channels = _array(body.get("channels"), capture, "Slack channels")
    channels = [_object(raw, capture, "Slack channel") for raw in raw_channels]
    messages_by_channel = _slack_messages_by_channel(body, channels, capture)
    resources: list[CanonicalResource] = []
    for channel in channels:
        channel_id = _string(channel.get("id"), capture, "Slack channel id")
        channel_name = _string(channel.get("name"), capture, f"Slack channel {channel_id} name")
        for raw_message in messages_by_channel[channel_id]:
            message = _object(raw_message, capture, f"Slack channel {channel_name} message")
            message_id = message.get("ts", message.get("id"))
            if not isinstance(message_id, (str, int)) or isinstance(message_id, bool):
                raise _error(capture, f"Slack channel {channel_name} message has no stable id")
            text = _string(message.get("text"), capture, f"Slack message {message_id} text")
            author = message.get("user", message.get("author"))
            fields = {
                "channel": channel_name,
                "channel_name": channel_name,
                "channel_id": channel_id,
                "text": text,
                "author": author,
                "incident": _marker(text),
            }
            resource_id = f"{channel_id}:{message_id}"
            resources.append(CanonicalResource(capture.provider_role, "message", resource_id, fields))
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "message_collection",
                    resource_id,
                    {
                        "channel": channel_name,
                        "channel_name": channel_name,
                        "channel_id": channel_id,
                        "message_id": str(message_id),
                        "text": text,
                    },
                )
            )
    return resources


def slack_channels_messages_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _slack_resources(capture)


def slack_channels_messages_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _slack_resources(capture)


def _discord_resources(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    raw_guilds = capture.body.get("guilds") if isinstance(capture.body, dict) else capture.body
    guilds = _array(raw_guilds, capture, "Discord guild response")
    resources: list[CanonicalResource] = []
    for raw_guild in guilds:
        guild = _object(raw_guild, capture, "Discord guild")
        guild_id = _string(guild.get("id"), capture, "Discord guild id")
        guild_name = _string(guild.get("name"), capture, f"Discord guild {guild_id} name")
        if "channels" not in guild:
            raise _error(
                capture,
                "guild list response omits channels and messages; use an enriched verifier read",
            )
        for raw_channel in _array(
            guild.get("channels"),
            capture,
            f"Discord guild {guild_name} channels",
        ):
            channel = _object(raw_channel, capture, f"Discord guild {guild_name} channel")
            channel_id = _string(channel.get("id"), capture, "Discord channel id")
            channel_name = _string(channel.get("name"), capture, f"Discord channel {channel_id} name")
            if "messages" not in channel:
                raise _error(
                    capture,
                    f"Discord channel {channel_name} response omits messages",
                )
            for raw_message in _array(
                channel.get("messages"),
                capture,
                f"Discord channel {channel_name} messages",
            ):
                message = _object(
                    raw_message,
                    capture,
                    f"Discord channel {channel_name} message",
                )
                message_id = message.get("id")
                if not isinstance(message_id, (str, int)) or isinstance(message_id, bool):
                    raise _error(capture, f"Discord channel {channel_name} message has no stable id")
                content = _string(
                    message.get("content"),
                    capture,
                    f"Discord message {message_id} content",
                )
                author = _stable_user(message.get("author"))
                fields = {
                    "guild_name": guild_name,
                    "guild_id": guild_id,
                    "channel": channel_name,
                    "channel_name": channel_name,
                    "channel_id": channel_id,
                    "content": content,
                    "author": author,
                    "incident": _marker(content),
                }
                resource_id = f"{guild_id}:{channel_id}:{message_id}"
                resources.append(CanonicalResource(capture.provider_role, "message", resource_id, fields))
                resources.append(
                    CanonicalResource(
                        capture.provider_role,
                        "message_collection",
                        resource_id,
                        {
                            "guild_name": guild_name,
                            "guild_id": guild_id,
                            "channel": channel_name,
                            "channel_name": channel_name,
                            "channel_id": channel_id,
                            "message_id": str(message_id),
                            "content": content,
                        },
                    )
                )
    return resources


def discord_guild_channels_messages_stable(
    capture: CapturedQueryState,
) -> Sequence[CanonicalResource]:
    return _discord_resources(capture)


def discord_guild_channels_messages_v1(
    capture: CapturedQueryState,
) -> Sequence[CanonicalResource]:
    return _discord_resources(capture)


CODE_COLLAB_CANONICALIZERS: Mapping[str, SnapshotCanonicalizer] = {
    "discord_guild_channels_messages_stable": discord_guild_channels_messages_stable,
    "discord_guild_channels_messages_v1": discord_guild_channels_messages_v1,
    "github_all_pull_review_artifacts": github_all_pull_review_artifacts,
    "github_contents_recursive_hashes": github_contents_recursive_hashes,
    "github_file_and_ref_stable": github_file_and_ref_stable,
    "github_pull_and_repo_tree_v1": github_pull_and_repo_tree_v1,
    "github_pulls_stable": github_pulls_stable,
    "github_refs_stable": github_refs_stable,
    "github_repository_and_pulls_stable": github_repository_and_pulls_stable,
    "github_repository_stable": github_repository_stable,
    "github_review_comments_stable": github_review_comments_stable,
    "github_reviews_stable": github_reviews_stable,
    "gitlab_branches_stable": gitlab_branches_stable,
    "gitlab_discussions_stable": gitlab_discussions_stable,
    "gitlab_file_and_branch_stable": gitlab_file_and_branch_stable,
    "gitlab_file_hash": gitlab_file_hash,
    "gitlab_merge_request_and_tree_v1": gitlab_merge_request_and_tree_v1,
    "gitlab_merge_requests_stable": gitlab_merge_requests_stable,
    "gitlab_project_and_merge_requests_stable": gitlab_project_and_merge_requests_stable,
    "gitlab_project_stable": gitlab_project_stable,
    "jira_SEC_issues_stable": jira_SEC_issues_stable,
    "jira_issues_and_comments_v1": jira_issues_and_comments_v1,
    "jira_issues_comments_stable": jira_issues_comments_stable,
    "linear_issues_comments_v1": linear_issues_comments_v1,
    "linear_issues_projects_comments_v1": linear_issues_projects_comments_v1,
    "linear_team_ENG_issues_stable": linear_team_ENG_issues_stable,
    "linear_team_OPS_issues_stable": linear_team_OPS_issues_stable,
    "linear_team_REL_issues_comments_stable": linear_team_REL_issues_comments_stable,
    "slack_channels_messages_stable": slack_channels_messages_stable,
    "slack_channels_messages_v1": slack_channels_messages_v1,
}


__all__ = ["CODE_COLLAB_CANONICALIZERS"]
