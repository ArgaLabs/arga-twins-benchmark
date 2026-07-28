from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from arga_twins_benchmark.evaluation.canonicalizers.code_collab import (
    CODE_COLLAB_CANONICALIZERS,
)
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    CapturedQueryState,
    StateCaptureError,
    TrustedStateSnapshot,
    canonicalize_query_results,
)

EXPECTED_CANONICALIZERS = {
    "discord_guild_channels_messages_stable",
    "discord_guild_channels_messages_v1",
    "github_all_pull_review_artifacts",
    "github_contents_recursive_hashes",
    "github_file_and_ref_stable",
    "github_pull_and_repo_tree_v1",
    "github_pulls_stable",
    "github_refs_stable",
    "github_repository_and_pulls_stable",
    "github_repository_stable",
    "github_review_comments_stable",
    "github_reviews_stable",
    "gitlab_branches_stable",
    "gitlab_discussions_stable",
    "gitlab_file_and_branch_stable",
    "gitlab_file_hash",
    "gitlab_merge_request_and_tree_v1",
    "gitlab_merge_requests_stable",
    "gitlab_project_and_merge_requests_stable",
    "gitlab_project_stable",
    "jira_SEC_issues_stable",
    "jira_issues_and_comments_v1",
    "jira_issues_comments_stable",
    "linear_issues_comments_v1",
    "linear_issues_projects_comments_v1",
    "linear_team_ENG_issues_stable",
    "linear_team_OPS_issues_stable",
    "linear_team_REL_issues_comments_stable",
    "slack_channels_messages_stable",
    "slack_channels_messages_v1",
}


def capture(
    canonicalizer: str,
    path: str,
    body: Any,
    *,
    provider_name: str,
    provider_role: str,
    method: str = "GET",
) -> CapturedQueryState:
    return CapturedQueryState(
        query_id=canonicalizer,
        provider_name=provider_name,
        provider_role=provider_role,
        method=method,
        path=path,
        canonicalizer=canonicalizer,
        status_code=200,
        body=body,
    )


def resource(
    resources: list[Any],
    resource_type: str,
    resource_id: str,
) -> Any:
    return next(item for item in resources if item.resource_type == resource_type and item.resource_id == resource_id)


def test_registry_covers_every_code_and_collaboration_manifest_name() -> None:
    assert set(CODE_COLLAB_CANONICALIZERS) == EXPECTED_CANONICALIZERS


def test_github_projections_merge_without_volatile_fields() -> None:
    repository = "acme/web-parser"
    head_sha = "a" * 40
    queries = {
        "repository": capture(
            "github_repository_stable",
            "/repos/acme/web-parser",
            {
                "id": 1,
                "node_id": "volatile-node",
                "full_name": repository,
                "name": "web-parser",
                "private": True,
                "description": "Parser",
                "fork": False,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
                "visibility": "private",
                "topics": ["security", "typescript"],
                "updated_at": "2026-07-25T00:00:00Z",
                "url": "https://api.github.com/repos/acme/web-parser",
            },
            provider_name="github",
            provider_role="code_host",
        ),
        "refs": capture(
            "github_refs_stable",
            "/repos/acme/web-parser/git/matching-refs/heads/",
            [
                {
                    "ref": "refs/heads/main",
                    "node_id": "different-on-every-read",
                    "url": "https://api.github.com/ref",
                    "object": {"sha": head_sha, "type": "commit", "url": "https://api.github.com/commit"},
                }
            ],
            provider_name="github",
            provider_role="code_host",
        ),
        "pulls": capture(
            "github_pulls_stable",
            "/repos/acme/web-parser/pulls?state=all",
            [
                {
                    "id": 22,
                    "number": 2,
                    "state": "open",
                    "merged": False,
                    "locked": False,
                    "title": "Unsafe parser",
                    "body": "Uses eval",
                    "draft": False,
                    "head": {"ref": "unsafe", "sha": head_sha},
                    "base": {"ref": "main", "sha": "b" * 40},
                    "labels": [{"name": "security", "color": "red"}],
                    "assignee": None,
                    "assignees": [],
                    "updated_at": "2026-07-25T00:00:00Z",
                    "html_url": "https://github.com/acme/web-parser/pull/2",
                }
            ],
            provider_name="github",
            provider_role="code_host",
        ),
        "contents": capture(
            "github_contents_recursive_hashes",
            "/repos/acme/web-parser/contents/?ref=main",
            [
                {
                    "path": "SECURITY_REVIEW.md",
                    "name": "SECURITY_REVIEW.md",
                    "type": "file",
                    "sha": "c" * 40,
                    "size": 6,
                    "content": base64.b64encode(b"policy").decode(),
                    "encoding": "base64",
                    "download_url": "https://raw.githubusercontent.com/volatile",
                }
            ],
            provider_name="github",
            provider_role="code_host",
        ),
        "reviews": capture(
            "github_reviews_stable",
            "/repos/acme/web-parser/pulls/2/reviews",
            [
                {
                    "id": 71,
                    "node_id": "volatile",
                    "pull_number": 2,
                    "state": "REQUEST_CHANGES",
                    "body": "Blocking: untrusted input reaches eval.",
                    "commit_id": head_sha,
                    "user": {
                        "id": 7,
                        "login": "benchmark-reviewer",
                        "avatar_url": "https://volatile/avatar",
                    },
                    "submitted_at": "2026-07-25T00:00:00Z",
                }
            ],
            provider_name="github",
            provider_role="code_host",
        ),
        "comments": capture(
            "github_review_comments_stable",
            "/repos/acme/web-parser/pulls/2/comments",
            [
                {
                    "id": 72,
                    "pull_number": 2,
                    "pull_request_review_id": 71,
                    "body": "Replace eval with a non-executing parser.",
                    "path": "src/parser.ts",
                    "line": 2,
                    "side": "RIGHT",
                    "commit_id": head_sha,
                    "user": {"id": 7, "login": "benchmark-reviewer"},
                    "created_at": "2026-07-25T00:00:00Z",
                }
            ],
            provider_name="github",
            provider_role="code_host",
        ),
    }
    snapshot = TrustedStateSnapshot(
        providers={
            "github": CapturedProviderState(
                provider_name="github",
                provider_role="code_host",
                state={},
            )
        },
        queries=queries,
    )

    resources = canonicalize_query_results(
        snapshot,
        canonicalizers=CODE_COLLAB_CANONICALIZERS,
    )

    repository_snapshot = resource(resources, "repository_snapshot", repository)
    assert set(repository_snapshot.fields) == {
        "repository",
        "metadata",
        "refs",
        "pulls",
        "contents",
        "reviews_by_pull",
        "review_comments_by_pull",
    }
    review = resource(resources, "pull_request_review", f"{repository}#2:review:71")
    assert review.fields["author_login"] == "benchmark-reviewer"
    assert review.fields["commit_id"] == head_sha
    comment = resource(resources, "pull_request_review_comment", f"{repository}#2:comment:72")
    assert comment.fields["pull_request_review_id"] == 71
    assert comment.fields["attached_to"] == 71
    file_resource = resource(resources, "file", f"{repository}@main:SECURITY_REVIEW.md")
    assert file_resource.fields["content_hash"] == ("823412d1eacb67956220e532959f0104603057c88704863ca38e7cd188fda812")
    rendered = json.dumps([item.fields for item in resources], sort_keys=True)
    for volatile in ("updated_at", "submitted_at", "created_at", "node_id", "avatar_url", "html_url"):
        assert volatile not in rendered


def test_github_all_pull_artifacts_rejects_plain_pull_list() -> None:
    query = capture(
        "github_all_pull_review_artifacts",
        "/repos/acme/web-parser/pulls",
        [{"number": 1, "state": "open", "head": {}, "base": {}}],
        provider_name="github",
        provider_role="code_host",
    )

    with pytest.raises(StateCaptureError, match="omits embedded reviews"):
        CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query)


def test_gitlab_projections_preserve_semantics_and_strip_volatility() -> None:
    project = "acme/web-parser"
    file_content = base64.b64encode(b"eval(request.body)").decode()
    queries = {
        "project": capture(
            "gitlab_project_stable",
            "/api/v4/projects/acme%2Fweb-parser",
            {
                "id": 10,
                "name": "web-parser",
                "path": "web-parser",
                "path_with_namespace": project,
                "description": "Parser",
                "visibility": "private",
                "default_branch": "main",
                "archived": False,
                "topics": ["security"],
                "updated_at": "volatile",
                "web_url": "https://volatile",
            },
            provider_name="gitlab",
            provider_role="code_host",
        ),
        "branches": capture(
            "gitlab_branches_stable",
            "/api/v4/projects/acme%2Fweb-parser/repository/branches",
            [
                {
                    "name": "main",
                    "default": True,
                    "merged": False,
                    "protected": False,
                    "commit": {"id": "a" * 40, "created_at": "volatile"},
                    "web_url": "https://volatile",
                }
            ],
            provider_name="gitlab",
            provider_role="code_host",
        ),
        "merge_requests": capture(
            "gitlab_merge_requests_stable",
            "/api/v4/projects/acme%2Fweb-parser/merge_requests?state=all",
            [
                {
                    "id": 90,
                    "iid": 1,
                    "title": "Unsafe parser",
                    "description": "Uses eval",
                    "state": "opened",
                    "source_branch": "unsafe",
                    "target_branch": "main",
                    "sha": "b" * 40,
                    "draft": False,
                    "labels": [],
                    "updated_at": "volatile",
                }
            ],
            provider_name="gitlab",
            provider_role="code_host",
        ),
        "file": capture(
            "gitlab_file_hash",
            "/api/v4/projects/acme%2Fweb-parser/repository/files/src%2Fparser.ts?ref=unsafe",
            {
                "file_path": "src/parser.ts",
                "ref": "main",
                "encoding": "base64",
                "content": file_content,
                "content_sha256": "untrusted-server-hash",
                "size": 18,
                "blob_id": "blob",
                "commit_id": "b" * 40,
                "last_commit_id": "b" * 40,
            },
            provider_name="gitlab",
            provider_role="code_host",
        ),
        "discussions": capture(
            "gitlab_discussions_stable",
            "/api/v4/projects/acme%2Fweb-parser/merge_requests/1/discussions",
            [
                {
                    "id": "discussion-1",
                    "notes": [
                        {
                            "id": 101,
                            "body": "BLOCKING: replace eval.",
                            "resolved": False,
                            "author": {
                                "id": 1,
                                "username": "benchmark-reviewer",
                                "avatar_url": "https://volatile",
                            },
                            "position": {
                                "position_type": "text",
                                "base_sha": "a" * 40,
                                "start_sha": "a" * 40,
                                "head_sha": "b" * 40,
                                "new_path": "src/parser.ts",
                                "new_line": 1,
                            },
                            "created_at": "volatile",
                        }
                    ],
                }
            ],
            provider_name="gitlab",
            provider_role="code_host",
        ),
    }
    snapshot = TrustedStateSnapshot(
        providers={
            "gitlab": CapturedProviderState(
                provider_name="gitlab",
                provider_role="code_host",
                state={},
            )
        },
        queries=queries,
    )

    resources = canonicalize_query_results(
        snapshot,
        canonicalizers=CODE_COLLAB_CANONICALIZERS,
    )

    project_snapshot = resource(resources, "project_snapshot", project)
    assert set(project_snapshot.fields) == {
        "project",
        "metadata",
        "branches",
        "merge_requests",
        "files",
        "discussions_by_merge_request",
    }
    discussion = resource(
        resources,
        "merge_request_diff_discussion",
        f"{project}!1:discussion:discussion-1:note:101",
    )
    assert discussion.fields["path"] == "src/parser.ts"
    assert discussion.fields["new_line"] == 1
    assert discussion.fields["author_username"] == "benchmark-reviewer"
    assert discussion.fields["commit_sha"] == "b" * 40
    file_resource = resource(resources, "file", f"{project}@unsafe:src/parser.ts")
    assert file_resource.fields["content_hash"] != "untrusted-server-hash"
    rendered = json.dumps([item.fields for item in resources], sort_keys=True)
    for volatile in ("created_at", "updated_at", "web_url", "avatar_url", "last_commit_id"):
        assert volatile not in rendered


def test_gitlab_discussions_reject_non_expanded_api_shape() -> None:
    query = capture(
        "gitlab_discussions_stable",
        "/api/v4/projects/acme%2Fweb-parser/merge_requests/1/discussions",
        [{"id": "discussion-1"}],
        provider_name="gitlab",
        provider_role="code_host",
    )

    with pytest.raises(StateCaptureError, match="notes must be a JSON array"):
        CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query)


def test_gitlab_discussions_represent_general_notes_without_positions() -> None:
    query = capture(
        "gitlab_discussions_stable",
        "/api/v4/projects/acme%2Fweb-parser/merge_requests/1/discussions",
        [
            {
                "id": "discussion-general-missing",
                "notes": [{"id": 100, "body": "General merge-request note."}],
            },
            {
                "id": "discussion-general-null",
                "notes": [{"id": 101, "body": "Another general note.", "position": None}],
            },
            {
                "id": "discussion-diff",
                "notes": [
                    {
                        "id": 102,
                        "body": "BLOCKING: replace eval.",
                        "position": {
                            "position_type": "text",
                            "head_sha": "b" * 40,
                            "new_path": "src/parser.ts",
                            "new_line": 1,
                        },
                    }
                ],
            },
        ],
        provider_name="gitlab",
        provider_role="code_host",
    )

    resources = list(CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query))

    discussions = [item for item in resources if item.resource_type == "merge_request_diff_discussion"]
    assert [item.resource_id for item in discussions] == ["acme/web-parser!1:discussion:discussion-diff:note:102"]
    assert discussions[0].fields["path"] == "src/parser.ts"
    general_notes = [item for item in resources if item.resource_type == "merge_request_discussion_note"]
    assert [item.resource_id for item in general_notes] == [
        "acme/web-parser!1:discussion:discussion-general-missing:note:100",
        "acme/web-parser!1:discussion:discussion-general-null:note:101",
    ]
    assert [item.fields["body"] for item in general_notes] == [
        "General merge-request note.",
        "Another general note.",
    ]
    project = resource(resources, "project_snapshot", "acme/web-parser")
    assert [item["id"] for item in project.fields["discussions_by_merge_request"]["1"]] == ["100", "101", "102"]
    assert [item["kind"] for item in project.fields["discussions_by_merge_request"]["1"]] == [
        "general_note",
        "general_note",
        "diff_note",
    ]


@pytest.mark.parametrize("position", ["not-an-object", [], 42, True])
def test_gitlab_discussions_reject_malformed_non_null_positions(position: Any) -> None:
    query = capture(
        "gitlab_discussions_stable",
        "/api/v4/projects/acme%2Fweb-parser/merge_requests/1/discussions",
        [
            {
                "id": "discussion-1",
                "notes": [{"id": 101, "body": "Malformed diff note.", "position": position}],
            }
        ],
        provider_name="gitlab",
        provider_role="code_host",
    )

    with pytest.raises(StateCaptureError, match="note 101 position must be a JSON object"):
        CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query)


def test_jira_search_projects_issues_comments_and_adf() -> None:
    query = capture(
        "jira_issues_comments_stable",
        "/rest/api/3/search?jql=project%3DREL",
        {
            "issues": [
                {
                    "id": "10001",
                    "key": "REL-1",
                    "fields": {
                        "summary": "[REL-205] release readiness",
                        "description": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Gate: REL-205"}],
                                },
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "All checks pass"}],
                                },
                            ],
                        },
                        "project": {"key": "REL", "name": "Release"},
                        "priority": {"id": "1", "name": "Highest"},
                        "status": {
                            "name": "In Progress",
                            "statusCategory": {"key": "indeterminate"},
                        },
                        "issuetype": {"name": "Task"},
                        "updated": "volatile",
                        "comment": {
                            "comments": [
                                {
                                    "id": "20001",
                                    "body": {
                                        "type": "doc",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [
                                                    {
                                                        "type": "text",
                                                        "text": "REL-205 READY: all gates passed.",
                                                    }
                                                ],
                                            }
                                        ],
                                    },
                                    "author": {
                                        "accountId": "benchmark-reviewer",
                                        "displayName": "Benchmark Reviewer",
                                        "avatarUrls": {"48x48": "https://volatile"},
                                    },
                                    "created": "volatile",
                                }
                            ]
                        },
                    },
                }
            ]
        },
        provider_name="jira",
        provider_role="issue_tracker",
    )

    resources = list(CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query))

    issue = resource(resources, "issue", "REL-1")
    assert issue.fields == {
        "key": "REL-1",
        "project_key": "REL",
        "summary": "[REL-205] release readiness",
        "description": "Gate: REL-205\nAll checks pass",
        "marker": "REL-205",
        "priority": "Highest",
        "status": "In Progress",
        "status_type": "open",
        "status_category": "open",
        "issue_type": "Task",
    }
    comment = resource(resources, "issue_comment", "REL-1:comment:20001")
    assert comment.fields["body"] == "REL-205 READY: all gates passed."
    assert comment.fields["created_by"] == "benchmark-reviewer"
    collection = resource(resources, "issue_collection", "project:REL")
    assert collection.fields["issues"][0]["key"] == "REL-1"
    rendered = json.dumps([item.fields for item in resources], sort_keys=True)
    for volatile in ('"updated":', '"created":', '"avatarUrls":'):
        assert volatile not in rendered


def test_jira_adf_preserves_hard_breaks_empty_paragraphs_and_inline_adjacency() -> None:
    query = capture(
        "jira_issues_and_comments_v1",
        "/rest/api/3/search?jql=project%3DMIG",
        {
            "issues": [
                {
                    "key": "MIG-1",
                    "fields": {
                        "summary": "Remove legacy deployment lock",
                        "description": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "Migration marker: "},
                                        {"type": "text", "text": "MIG-34"},
                                        {"type": "hardBreak"},
                                        {"type": "text", "text": "Code reference: acme/platform!1"},
                                        {"type": "hardBreak"},
                                        {"type": "text", "text": "Remove legacy deployment lock."},
                                    ],
                                },
                                {"type": "paragraph", "content": []},
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "Migration source: ENG-1"},
                                    ],
                                },
                            ],
                        },
                        "project": {"key": "MIG"},
                        "status": {
                            "name": "To Do",
                            "statusCategory": {"key": "new"},
                        },
                        "comment": {"comments": []},
                    },
                }
            ]
        },
        provider_name="jira",
        provider_role="target_tracker",
    )

    resources = list(CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query))
    description = resource(resources, "issue", "MIG-1").fields["description"]

    assert description == (
        "Migration marker: MIG-34\n"
        "Code reference: acme/platform!1\n"
        "Remove legacy deployment lock.\n\n"
        "Migration source: ENG-1"
    )
    assert description != (
        "Migration marker: MIG-34\n"
        "Code reference: acme/platform!1\n"
        "Remove legacy deployment lock.\n"
        "Migration source: ENG-1"
    )


def test_jira_search_fails_closed_on_cross_project_results() -> None:
    query = capture(
        "jira_SEC_issues_stable",
        "/rest/api/3/search?jql=project%3DSEC",
        {
            "issues": [
                {
                    "key": "OPS-1",
                    "fields": {
                        "summary": "Wrong project",
                        "description": "",
                        "project": {"key": "OPS"},
                        "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                    },
                }
            ]
        },
        provider_name="jira",
        provider_role="issue_tracker",
    )

    with pytest.raises(StateCaptureError, match="outside project SEC"):
        CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query)


def test_linear_graphql_projects_issues_comments_and_collections() -> None:
    query = capture(
        "linear_issues_projects_comments_v1",
        "/graphql",
        {
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "id": "issue-1",
                            "identifier": "OPS-1",
                            "number": 1,
                            "title": "Retry failed deliveries",
                            "description": "Migration marker: MIG-33",
                            "priority": 2,
                            "createdAt": "volatile",
                            "updatedAt": "volatile",
                            "archivedAt": None,
                            "team": {"id": "team-1", "key": "OPS", "name": "Operations"},
                            "state": {"id": "state-1", "name": "In Progress", "type": "started"},
                            "project": {"id": "project-1", "name": "Reliability", "state": "started"},
                            "comments": {
                                "nodes": [
                                    {
                                        "id": "comment-1",
                                        "body": "Migrated to MIG-1.",
                                        "createdAt": "volatile",
                                        "user": {
                                            "id": "user-1",
                                            "name": "Benchmark Reviewer",
                                            "email": "volatile@example.com",
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                },
                "projects": {
                    "nodes": [
                        {
                            "id": "project-1",
                            "name": "Reliability",
                            "slugId": "reliability",
                            "state": "started",
                            "description": "Reliability work",
                            "summary": "Ops",
                            "createdAt": "volatile",
                            "updatedAt": "volatile",
                            "archivedAt": None,
                        }
                    ]
                },
            }
        },
        provider_name="linear",
        provider_role="target_tracker",
        method="POST",
    )

    resources = list(CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query))

    issue = resource(resources, "issue", "issue-1")
    assert issue.fields["identifier"] == "OPS-1"
    assert issue.fields["team_key"] == "OPS"
    assert issue.fields["project"] == "Reliability"
    assert issue.fields["marker"] == "MIG-33"
    assert issue.fields["state_type"] == "open"
    comment = resource(resources, "issue_comment", "comment-1")
    assert comment.fields["issue_identifier"] == "OPS-1"
    collection = resource(resources, "issue_collection", "team:OPS")
    assert collection.fields["markers"] == ["MIG-33"]
    rendered = json.dumps([item.fields for item in resources], sort_keys=True)
    for volatile in ("createdAt", "updatedAt", "email"):
        assert volatile not in rendered


def test_linear_graphql_errors_fail_closed() -> None:
    query = capture(
        "linear_team_OPS_issues_stable",
        "/graphql",
        {"data": {}, "errors": [{"message": "query failed"}]},
        provider_name="linear",
        provider_role="issue_tracker",
        method="POST",
    )

    with pytest.raises(StateCaptureError, match="contains errors"):
        CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query)


@pytest.mark.parametrize("state_fields", [{}, {"state": None}])
def test_linear_graphql_allows_missing_or_null_issue_state(
    state_fields: dict[str, Any],
) -> None:
    query = capture(
        "linear_issues_projects_comments_v1",
        "/graphql",
        {
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "id": "issue-1",
                            "identifier": "OPS-1",
                            "number": 1,
                            "title": "Retry failed deliveries",
                            "description": "Migration marker: MIG-33",
                            "team": {"id": "team-1", "key": "OPS", "name": "Operations"},
                            "comments": {"nodes": []},
                            **state_fields,
                        }
                    ]
                },
                "projects": {"nodes": []},
            }
        },
        provider_name="linear",
        provider_role="target_tracker",
        method="POST",
    )

    resources = list(CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query))

    issue = resource(resources, "issue", "issue-1")
    assert issue.fields["state"] is None
    assert issue.fields["state_type"] is None


@pytest.mark.parametrize("state", ["started", [], 1, True])
def test_linear_graphql_rejects_malformed_non_null_issue_state(state: Any) -> None:
    query = capture(
        "linear_issues_projects_comments_v1",
        "/graphql",
        {
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "id": "issue-1",
                            "identifier": "OPS-1",
                            "title": "Retry failed deliveries",
                            "team": {"id": "team-1", "key": "OPS"},
                            "state": state,
                            "comments": {"nodes": []},
                        }
                    ]
                },
                "projects": {"nodes": []},
            }
        },
        provider_name="linear",
        provider_role="target_tracker",
        method="POST",
    )

    with pytest.raises(StateCaptureError, match="issue OPS-1 state must be a JSON object"):
        CODE_COLLAB_CANONICALIZERS[query.canonicalizer](query)


@pytest.mark.parametrize(
    ("canonicalizer", "body", "message_field"),
    [
        (
            "slack_channels_messages_stable",
            {
                "ok": True,
                "channels": [
                    {
                        "id": "C1",
                        "name": "incidents",
                        "messages": [
                            {
                                "ts": "1.000001",
                                "user": "U1",
                                "text": "TRACKED INC-417 AS OPS-1: fixed.",
                                "edited": {"ts": "volatile"},
                            }
                        ],
                    }
                ],
            },
            "text",
        ),
        (
            "discord_guild_channels_messages_stable",
            [
                {
                    "id": "G1",
                    "name": "Acme Ops",
                    "channels": [
                        {
                            "id": "C1",
                            "name": "incidents",
                            "messages": [
                                {
                                    "id": "M1",
                                    "content": "TRACKED INC-420 AS OPS-1: fixed.",
                                    "author": {
                                        "id": "U1",
                                        "username": "benchmark-reviewer",
                                        "avatar": "volatile",
                                    },
                                    "timestamp": "volatile",
                                }
                            ],
                        }
                    ],
                }
            ],
            "content",
        ),
    ],
)
def test_chat_projections_require_enriched_history(
    canonicalizer: str,
    body: Any,
    message_field: str,
) -> None:
    provider_name = "slack" if canonicalizer.startswith("slack") else "discord"
    path = "/api/conversations.list" if provider_name == "slack" else "/api/v10/users/@me/guilds"
    query = capture(
        canonicalizer,
        path,
        body,
        provider_name=provider_name,
        provider_role="team_chat",
        method="POST" if provider_name == "slack" else "GET",
    )

    resources = list(CODE_COLLAB_CANONICALIZERS[canonicalizer](query))

    message = next(item for item in resources if item.resource_type == "message")
    assert message.fields["channel_name"] == "incidents"
    assert message.fields["incident"] in {"INC-417", "INC-420"}
    assert message_field in message.fields
    assert "timestamp" not in message.fields
    collection = next(item for item in resources if item.resource_type == "message_collection")
    assert collection.fields["channel_name"] == "incidents"


@pytest.mark.parametrize(
    ("canonicalizer", "provider_name", "path", "body", "error"),
    [
        (
            "slack_channels_messages_v1",
            "slack",
            "/api/conversations.list",
            {"ok": True, "channels": [{"id": "C1", "name": "ops"}]},
            "omits message history",
        ),
        (
            "discord_guild_channels_messages_v1",
            "discord",
            "/api/v10/users/@me/guilds",
            [{"id": "G1", "name": "Acme Ops"}],
            "omits channels and messages",
        ),
    ],
)
def test_native_chat_list_responses_fail_closed_when_history_is_absent(
    canonicalizer: str,
    provider_name: str,
    path: str,
    body: Any,
    error: str,
) -> None:
    query = capture(
        canonicalizer,
        path,
        body,
        provider_name=provider_name,
        provider_role="team_chat",
    )

    with pytest.raises(StateCaptureError, match=error):
        CODE_COLLAB_CANONICALIZERS[canonicalizer](query)
