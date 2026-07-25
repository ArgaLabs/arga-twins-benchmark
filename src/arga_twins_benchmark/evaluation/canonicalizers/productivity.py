from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from arga_twins_benchmark.evaluation.deterministic import CanonicalResource
from arga_twins_benchmark.evaluation.state_capture import CapturedQueryState, StateCaptureError

Canonicalizer = Callable[[CapturedQueryState], Sequence[CanonicalResource]]

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_INVOICE_RE = re.compile(r"\bINV-\d+\b", re.IGNORECASE)
_CONTENT_MARKER_RE = re.compile(
    r"(?:content[\s_-]*marker\s*[:=]|marker\s*[:=])\s*([A-Za-z0-9._:-]+)",
    re.IGNORECASE,
)
_CALENDAR_PATH_RE = re.compile(r"^/calendar/v3/calendars/([^/]+)/events(?:[/?]|$)")


def _error(capture: CapturedQueryState, message: str) -> StateCaptureError:
    return StateCaptureError(f"canonicalizer {capture.canonicalizer!r} for query {capture.query_id!r}: {message}")


def _object(value: object, capture: CapturedQueryState, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(capture, f"{label} must be a JSON object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise _error(capture, f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _objects(value: object, capture: CapturedQueryState, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _error(capture, f"{label} must be a JSON array")
    return [
        _object(item, capture, f"{label}[{index}]")
        for index, item in enumerate(cast(list[object], value))
    ]


def _optional_object(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _required_id(item: Mapping[str, Any], capture: CapturedQueryState, label: str) -> str:
    value = item.get("id")
    if not isinstance(value, str) or not value:
        raise _error(capture, f"{label}.id must be a non-empty string")
    return value


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _complete_google_page(body: Mapping[str, Any], capture: CapturedQueryState) -> None:
    next_page = body.get("nextPageToken")
    if next_page not in (None, ""):
        raise _error(capture, "paginated response is incomplete (nextPageToken is present)")
    if body.get("incompleteSearch") is True:
        raise _error(capture, "provider marked the response as an incomplete search")


def _complete_notion_page(body: Mapping[str, Any], capture: CapturedQueryState) -> None:
    if body.get("has_more") is True or body.get("next_cursor") not in (None, ""):
        raise _error(capture, "paginated response is incomplete")


def _complete_stripe_page(body: Mapping[str, Any], capture: CapturedQueryState) -> None:
    if body.get("has_more") is True or body.get("next_page") not in (None, ""):
        raise _error(capture, "paginated response is incomplete")


def _normalized_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in cast(list[object], value) if isinstance(item, str) and item})


def _iso(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if "T" not in value:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        return value
    rendered = parsed.astimezone(UTC).isoformat(timespec="seconds")
    return rendered.replace("+00:00", "Z")


def _rich_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            continue
        item_dict = cast(dict[str, Any], item)
        plain = item_dict.get("plain_text")
        if isinstance(plain, str):
            parts.append(plain)
            continue
        text = item_dict.get("text")
        text_dict = _optional_object(text)
        if text_dict is not None and isinstance(text_dict.get("content"), str):
            parts.append(cast(str, text_dict["content"]))
    return "".join(parts)


def _notion_property_value(raw: object) -> object:
    if not isinstance(raw, dict):
        return raw if raw is None or isinstance(raw, bool | int | float | str) else None
    value = cast(dict[str, Any], raw)
    prop_type = value.get("type")
    if prop_type in {"title", "rich_text"}:
        return _rich_text(value.get(str(prop_type)))
    if prop_type in {"select", "status"}:
        selected = value.get(str(prop_type))
        return cast(dict[str, Any], selected).get("name") if isinstance(selected, dict) else None
    if prop_type == "date":
        date = value.get("date")
        if not isinstance(date, dict):
            return None
        date_value = cast(dict[str, Any], date)
        start = date_value.get("start")
        end = date_value.get("end")
        return {"start": start, "end": end} if end is not None else start
    if prop_type == "relation":
        relations = value.get("relation")
        if not isinstance(relations, list):
            return []
        return sorted(
            cast(str, relation["id"])
            for item in cast(list[object], relations)
            if isinstance(item, dict)
            and isinstance((relation := cast(dict[str, Any], item)).get("id"), str)
        )
    if prop_type in {"checkbox", "number", "url", "email", "phone_number"}:
        return value.get(str(prop_type))
    if prop_type == "people":
        people = value.get("people")
        if not isinstance(people, list):
            return []
        return sorted(
            cast(str, person["id"])
            for item in cast(list[object], people)
            if isinstance(item, dict)
            and isinstance((person := cast(dict[str, Any], item)).get("id"), str)
        )
    # KMS/admin projections and older twin snapshots may omit ``type`` while
    # retaining one provider-native property arm.
    for key in ("title", "rich_text"):
        if key in value:
            return _rich_text(value.get(key))
    for key in ("select", "status"):
        selected = value.get(key)
        if isinstance(selected, dict):
            return cast(dict[str, Any], selected).get("name")
    for key in ("checkbox", "number", "url", "email", "phone_number"):
        if key in value:
            return value.get(key)
    return None


def _notion_page_properties(page: Mapping[str, Any]) -> dict[str, Any]:
    raw_properties: object = page.get("properties")
    metadata = _optional_object(page.get("metadata"))
    raw_properties_dict = _optional_object(raw_properties)
    if raw_properties_dict is None and metadata is not None:
        raw_properties_dict = _optional_object(metadata.get("properties"))
    if raw_properties_dict is None:
        return {}
    return {
        name: value
        for raw_name, raw_value in raw_properties_dict.items()
        if (value := _notion_property_value(raw_value)) is not None
        for name in [raw_name.replace(" ", "_")]
    }


def _notion_title(page: Mapping[str, Any], properties: Mapping[str, Any]) -> str:
    direct = page.get("title")
    if isinstance(direct, str) and direct:
        return direct
    for key in ("title", "Name", "Policy", "Ticket"):
        value = properties.get(key)
        if isinstance(value, str) and value:
            return value
    return "Untitled"


def _notion_markdown(page: Mapping[str, Any]) -> str | None:
    for key in ("markdown", "content"):
        value = page.get(key)
        if isinstance(value, str):
            return value
    page_markdown = page.get("page_markdown")
    page_markdown_dict = _optional_object(page_markdown)
    if page_markdown_dict is not None and isinstance(page_markdown_dict.get("markdown"), str):
        return cast(str, page_markdown_dict["markdown"])
    return None


def _notion_database_name(properties: Mapping[str, Any]) -> str | None:
    # The Notion API search and current admin summary expose the data-source ID
    # but not its display name. Only infer names from an unambiguous schema
    # signature used by the checked-in benchmark.
    keys = set(properties)
    if {"Policy", "Lifecycle", "Version", "Supersedes", "Artifact_Digest"} <= keys:
        return "Policy Registry"
    return None


def _notion_page_resources(
    capture: CapturedQueryState,
    *,
    require_admin: bool = False,
) -> list[CanonicalResource]:
    body = _object(capture.body, capture, "response")
    if "results" in body:
        if require_admin:
            raise _error(capture, "expected the Notion admin-state shape")
        _complete_notion_page(body, capture)
        pages = [
            item
            for item in _objects(body.get("results"), capture, "response.results")
            if item.get("object") in (None, "page")
        ]
    elif "pages" in body:
        pages = _objects(body.get("pages"), capture, "response.pages")
    elif body.get("object") in {"page", "page_markdown"}:
        pages = [body]
    else:
        raise _error(capture, "response must contain results or pages")

    resources: list[CanonicalResource] = []
    stable_pages: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        page_id = _required_id(page, capture, f"page[{index}]")
        properties = _notion_page_properties(page)
        title = _notion_title(page, properties)
        markdown = _notion_markdown(page)
        archived = bool(page.get("archived") or page.get("in_trash") or page.get("is_active") is False)
        parent = _optional_object(page.get("parent")) or {}
        parent_type = parent.get("type")
        is_database_page = page.get("type") == "database" or parent_type in {"data_source_id", "database_id"}

        page_fields: dict[str, Any] = {
            "title": title,
            "workspace": "default",
            "archived": archived,
            "properties": properties,
        }
        if markdown is not None:
            page_fields["markdown"] = markdown
        resources.append(CanonicalResource(capture.provider_role, "page", page_id, page_fields))
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "page_collection",
                page_id,
                {
                    "workspace": "default",
                    "title": title,
                    "archived": archived,
                    "stable_digest": _stable_digest(page_fields),
                },
            )
        )
        if markdown is not None:
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "page_markdown",
                    page_id,
                    {"title": title, "markdown": markdown},
                )
            )

        if is_database_page:
            database_fields = dict(properties)
            database_name = _notion_database_name(properties)
            if database_name is not None:
                database_fields["database"] = database_name
            raw_properties: object = page.get("properties")
            metadata = _optional_object(page.get("metadata"))
            raw_properties_dict = _optional_object(raw_properties)
            if raw_properties_dict is None and metadata is not None:
                raw_properties_dict = _optional_object(metadata.get("properties"))
            if raw_properties_dict is not None:
                for raw_name, raw_value in raw_properties_dict.items():
                    raw_value_dict = _optional_object(raw_value)
                    if raw_value_dict is None:
                        continue
                    prop_type = raw_value_dict.get("type")
                    if prop_type in {"select", "status"}:
                        database_fields[f"properties.{raw_name}.{prop_type}.name"] = _notion_property_value(
                            raw_value_dict
                        )
            resources.append(
                CanonicalResource(capture.provider_role, "database_page", page_id, database_fields)
            )

        stable_pages.append(
            {
                "id": page_id,
                "title": title,
                "archived": archived,
                "properties": properties,
                "markdown": markdown,
            }
        )

    resources.append(
        CanonicalResource(
            capture.provider_role,
            "notion_snapshot",
            "workspace",
            {"scope": "workspace", "stable_digest": _stable_digest(sorted(stable_pages, key=lambda item: item["id"]))},
        )
    )
    return resources


def notion_state_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _notion_page_resources(capture, require_admin=True)


def notion_pages_complete(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _notion_page_resources(capture)


def notion_pages_markdown_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _notion_page_resources(capture)


def notion_spec_pages_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _notion_page_resources(capture)


def _drive_items(capture: CapturedQueryState, *, require_admin: bool) -> list[dict[str, Any]]:
    body = _object(capture.body, capture, "response")
    if "files" not in body:
        raise _error(capture, "response must contain files")
    if require_admin and capture.path != "/admin/state":
        raise _error(capture, "expected the Google Drive admin-state endpoint")
    if not require_admin:
        _complete_google_page(body, capture)
    return _objects(body.get("files"), capture, "response.files")


def _drive_content_marker(file: Mapping[str, Any]) -> str | None:
    candidates: list[str] = []
    for key in ("content", "description", "content_marker"):
        value = file.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for key in ("properties", "appProperties"):
        value = file.get(key)
        value_dict = _optional_object(value)
        if value_dict is not None:
            candidates.extend(str(item) for item in value_dict.values() if isinstance(item, str))
    for candidate in candidates:
        match = _CONTENT_MARKER_RE.search(candidate)
        if match is not None:
            return match.group(1)
        if candidate == file.get("content_marker"):
            return candidate
    return None


def _drive_resources(capture: CapturedQueryState, *, require_admin: bool) -> list[CanonicalResource]:
    files = _drive_items(capture, require_admin=require_admin)
    resources: list[CanonicalResource] = []
    stable_files: list[dict[str, Any]] = []
    for index, file in enumerate(files):
        file_id = _required_id(file, capture, f"file[{index}]")
        name = file.get("name")
        mime_type = file.get("mimeType") or file.get("mime_type")
        if not isinstance(name, str) or not name:
            raise _error(capture, f"file[{index}].name must be a non-empty string")
        if not isinstance(mime_type, str) or not mime_type:
            raise _error(capture, f"file[{index}].mimeType must be a non-empty string")
        marker = _drive_content_marker(file)
        permissions = _objects(file.get("permissions"), capture, f"file[{index}].permissions")
        permission_fields: list[dict[str, Any]] = []
        for permission_index, permission in enumerate(permissions):
            permission_id = _required_id(permission, capture, f"file[{index}].permissions[{permission_index}]")
            fields: dict[str, Any] = {
                "file_id": file_id,
                "file_name": name,
                "type": permission.get("type"),
                "role": permission.get("role"),
                "emailAddress": permission.get("emailAddress"),
                "domain": permission.get("domain"),
                "present": not bool(permission.get("deleted")),
            }
            if marker is not None:
                fields["content_marker"] = marker
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "file_permission",
                    f"{file_id}:{permission_id}",
                    fields,
                )
            )
            permission_fields.append({"id": permission_id, **fields})

        file_fields: dict[str, Any] = {
            "name": name,
            "mime_type": mime_type,
            "parents": _normalized_string_list(file.get("parents")),
            "trashed": bool(file.get("trashed")),
            "content_hash": file.get("md5Checksum") or file.get("sha256Checksum"),
            "permissions": sorted(permission_fields, key=lambda item: item["id"]),
        }
        if marker is not None:
            file_fields["content_marker"] = marker
        resources.append(CanonicalResource(capture.provider_role, "file", file_id, file_fields))
        if mime_type == _FOLDER_MIME_TYPE:
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "folder",
                    file_id,
                    {"name": name, "parents": file_fields["parents"], "trashed": file_fields["trashed"]},
                )
            )
        stable_files.append({"id": file_id, **file_fields})

    snapshot_digest = _stable_digest(sorted(stable_files, key=lambda item: item["id"]))
    resources.extend(
        [
            CanonicalResource(
                capture.provider_role,
                "drive_snapshot",
                "all_files",
                {"scope": "all_files", "stable_digest": snapshot_digest},
            ),
            CanonicalResource(
                capture.provider_role,
                "file_collection",
                "all_files",
                {"scope": "all_files", "stable_digest": snapshot_digest},
            ),
            CanonicalResource(
                capture.provider_role,
                "provider_state",
                "all_files",
                {"scope": "all_files", "stable_digest": snapshot_digest},
            ),
        ]
    )
    return resources


def google_drive_state_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _drive_resources(capture, require_admin=True)


def drive_files_content_hash_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _drive_resources(capture, require_admin=False)


def _gmail_mailboxes(body: Mapping[str, Any], capture: CapturedQueryState) -> list[tuple[str, dict[str, Any]]]:
    raw_mailboxes = body.get("mailboxes")
    if not isinstance(raw_mailboxes, dict):
        return []
    mailboxes: list[tuple[str, dict[str, Any]]] = []
    for email_address, raw_mailbox in cast(dict[object, object], raw_mailboxes).items():
        if not isinstance(email_address, str):
            raise _error(capture, "response.mailboxes keys must be strings")
        mailboxes.append((email_address, _object(raw_mailbox, capture, f"mailbox[{email_address!r}]")))
    return sorted(mailboxes)


def _gmail_items(
    capture: CapturedQueryState,
    key: str,
) -> list[tuple[str, dict[str, Any]]]:
    body = _object(capture.body, capture, "response")
    mailboxes = _gmail_mailboxes(body, capture)
    if mailboxes:
        items: list[tuple[str, dict[str, Any]]] = []
        for mailbox, payload in mailboxes:
            items.extend((mailbox, item) for item in _objects(payload.get(key), capture, f"mailbox.{key}"))
        return items
    _complete_google_page(body, capture)
    return [("me", item) for item in _objects(body.get(key), capture, f"response.{key}")]


def _decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _gmail_headers(message: Mapping[str, Any]) -> dict[str, str]:
    payload = _optional_object(message.get("payload"))
    raw_headers = payload.get("headers") if payload is not None else None
    headers: dict[str, str] = {}
    if isinstance(raw_headers, list):
        for raw_header in cast(list[object], raw_headers):
            raw_header_dict = _optional_object(raw_header)
            if raw_header_dict is None:
                continue
            name = raw_header_dict.get("name")
            value = raw_header_dict.get("value")
            if isinstance(name, str) and isinstance(value, str):
                headers[name.casefold()] = value
    return headers


def _gmail_body(message: Mapping[str, Any]) -> str:
    payload = _optional_object(message.get("payload"))
    if payload is not None:
        body = _optional_object(payload.get("body"))
        encoded = body.get("data") if body is not None else None
        if isinstance(encoded, str):
            try:
                return _decode_b64url(encoded).decode("utf-8", errors="replace").rstrip("\n")
            except ValueError:
                return ""
    raw = message.get("raw")
    if isinstance(raw, str):
        try:
            parsed = BytesParser(policy=policy.default).parsebytes(_decode_b64url(raw))
        except (ValueError, TypeError):
            return ""
        body_part = parsed.get_body(preferencelist=("plain", "html"))
        if body_part is not None:
            return str(body_part.get_content()).rstrip("\n")
    snippet = message.get("snippet")
    return snippet if isinstance(snippet, str) else ""


def _gmail_addresses(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({address for _, address in getaddresses([value]) if address})


def _gmail_label_map(body: Mapping[str, Any], capture: CapturedQueryState) -> dict[str, str]:
    mapping: dict[str, str] = {}
    mailboxes = _gmail_mailboxes(body, capture)
    label_groups: list[list[dict[str, Any]]] = []
    if mailboxes:
        label_groups.extend(_objects(payload.get("labels"), capture, "mailbox.labels") for _, payload in mailboxes)
    elif "labels" in body:
        label_groups.append(_objects(body.get("labels"), capture, "response.labels"))
    for labels in label_groups:
        for label in labels:
            label_id = label.get("id")
            name = label.get("name")
            if isinstance(label_id, str) and isinstance(name, str):
                mapping[label_id] = name
    return mapping


def _gmail_message_fields(
    message: Mapping[str, Any],
    *,
    label_names: Mapping[str, str],
) -> dict[str, Any]:
    headers = _gmail_headers(message)
    body = _gmail_body(message)
    subject = headers.get("subject") or (message.get("subject") if isinstance(message.get("subject"), str) else "")
    sender_addresses = _gmail_addresses(headers.get("from"))
    label_ids = _normalized_string_list(message.get("labelIds"))
    labels = sorted(label_names.get(label_id, label_id) for label_id in label_ids)
    invoice_match = _INVOICE_RE.search(f"{subject}\n{body}")
    fields: dict[str, Any] = {
        "threadId": message.get("threadId"),
        "thread_id": message.get("threadId"),
        "labelIds": label_ids,
        "labels": labels,
        "labels_contain": labels,
        "subject": subject,
        "sender": sender_addresses[0] if len(sender_addresses) == 1 else None,
        "to": _gmail_addresses(headers.get("to")),
        "body": body,
        "sent": "SENT" in label_ids,
        "Needs-Finance": "Needs-Finance" in labels,
        "Needs-Finance_count": labels.count("Needs-Finance"),
    }
    if invoice_match is not None:
        fields["invoice_id"] = invoice_match.group(0).upper()
    return fields


def _gmail_snapshot_resources(
    capture: CapturedQueryState,
    *,
    kind: str,
    stable_items: Iterable[Mapping[str, Any]],
) -> list[CanonicalResource]:
    digest = _stable_digest(sorted((dict(item) for item in stable_items), key=lambda item: str(item.get("id", ""))))
    digest_field = f"{kind}_digest"
    return [
        CanonicalResource(
            capture.provider_role,
            "gmail_snapshot",
            "mailbox",
            {"scope": "mailbox", digest_field: digest},
        ),
        CanonicalResource(
            capture.provider_role,
            "provider_state",
            "all",
            {"scope": "all", digest_field: digest},
        ),
    ]


def _gmail_messages(capture: CapturedQueryState) -> list[CanonicalResource]:
    body = _object(capture.body, capture, "response")
    label_names = _gmail_label_map(body, capture)
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for index, (mailbox, message) in enumerate(_gmail_items(capture, "messages")):
        message_id = _required_id(message, capture, f"message[{index}]")
        fields = {"mailbox": mailbox, **_gmail_message_fields(message, label_names=label_names)}
        resources.append(CanonicalResource(capture.provider_role, "message", message_id, fields))
        invoice_id = fields.get("invoice_id")
        if isinstance(invoice_id, str):
            resources.append(
                CanonicalResource(
                    capture.provider_role,
                    "message_or_draft",
                    invoice_id,
                    {
                        "invoice_id": invoice_id,
                        "Needs-Finance": fields["Needs-Finance"],
                        "draft_count": 0,
                    },
                )
            )
        stable.append({"id": message_id, **fields})
    resources.extend(_gmail_snapshot_resources(capture, kind="messages", stable_items=stable))
    return resources


def _gmail_drafts(capture: CapturedQueryState) -> list[CanonicalResource]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for index, (mailbox, draft) in enumerate(_gmail_items(capture, "drafts")):
        draft_id = _required_id(draft, capture, f"draft[{index}]")
        raw_message = draft.get("message")
        message = _object(raw_message, capture, f"draft[{index}].message")
        message_fields = _gmail_message_fields(message, label_names={})
        fields = {
            "mailbox": mailbox,
            "threadId": message_fields.get("threadId"),
            "thread_id": message_fields.get("thread_id"),
            "to": message_fields.get("to"),
            "subject": message_fields.get("subject"),
            "body": message_fields.get("body"),
            "sent": False,
        }
        resources.append(CanonicalResource(capture.provider_role, "draft", draft_id, fields))
        stable.append({"id": draft_id, **fields})
    resources.extend(_gmail_snapshot_resources(capture, kind="drafts", stable_items=stable))
    return resources


def _gmail_labels(capture: CapturedQueryState) -> list[CanonicalResource]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for index, (mailbox, label) in enumerate(_gmail_items(capture, "labels")):
        label_id = _required_id(label, capture, f"label[{index}]")
        fields = {
            "mailbox": mailbox,
            "name": label.get("name"),
            "type": label.get("type"),
        }
        resources.append(CanonicalResource(capture.provider_role, "label", label_id, fields))
        stable.append({"id": label_id, **fields})
    resources.extend(_gmail_snapshot_resources(capture, kind="labels", stable_items=stable))
    return resources


def _gmail_threads(capture: CapturedQueryState) -> list[CanonicalResource]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for index, (mailbox, thread) in enumerate(_gmail_items(capture, "threads")):
        thread_id = _required_id(thread, capture, f"thread[{index}]")
        fields = {
            "mailbox": mailbox,
            "snippet": thread.get("snippet") if isinstance(thread.get("snippet"), str) else "",
        }
        resources.append(CanonicalResource(capture.provider_role, "thread", thread_id, fields))
        stable.append({"id": thread_id, **fields})
    resources.extend(_gmail_snapshot_resources(capture, kind="threads", stable_items=stable))
    return resources


def gmail_messages_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_messages(capture)


def gmail_messages_threads_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_messages(capture)


def gmail_messages_labels_threads_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_messages(capture)


def gmail_drafts_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_drafts(capture)


def gmail_drafts_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_drafts(capture)


def gmail_labels_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_labels(capture)


def gmail_labels_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_labels(capture)


def gmail_threads_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _gmail_threads(capture)


def _calendar_id_from_path(capture: CapturedQueryState) -> str | None:
    match = _CALENDAR_PATH_RE.match(urlsplit(capture.path).path)
    return unquote(match.group(1)) if match is not None else None


def _calendar_time(event: Mapping[str, Any], key: str) -> str | None:
    value = event.get(key)
    value_dict = _optional_object(value)
    if value_dict is not None:
        return _iso(value_dict.get("dateTime") or value_dict.get("date"))
    return _iso(value)


def _calendar_event_resources(
    capture: CapturedQueryState,
    *,
    admin: bool,
) -> list[CanonicalResource]:
    body = _object(capture.body, capture, "response")
    if admin:
        raw_events = _objects(body.get("events"), capture, "response.events")
        raw_calendars = _objects(body.get("calendars"), capture, "response.calendars")
        calendar_names = {
            calendar["id"]: calendar.get("summary") or calendar.get("name")
            for calendar in raw_calendars
            if isinstance(calendar.get("id"), str)
        }
    else:
        _complete_google_page(body, capture)
        raw_events = _objects(body.get("items"), capture, "response.items")
        calendar_id = _calendar_id_from_path(capture)
        summary = body.get("summary")
        calendar_names = {calendar_id: summary} if calendar_id is not None else {}

    resources: list[CanonicalResource] = []
    stable_events: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, event in enumerate(raw_events):
        event_id = _required_id(event, capture, f"event[{index}]")
        calendar_id_value = event.get("_calendar_id") or event.get("calendar_id") or _calendar_id_from_path(capture)
        calendar_id = str(calendar_id_value) if calendar_id_value is not None else "primary"
        calendar_name_value = calendar_names.get(calendar_id)
        calendar_name = str(calendar_name_value) if calendar_name_value else calendar_id
        start = _calendar_time(event, "start")
        end = _calendar_time(event, "end")
        if start is None or end is None:
            raise _error(capture, f"event[{index}] must have stable start and end values")
        raw_attendees = _objects(event.get("attendees"), capture, f"event[{index}].attendees")
        attendees = sorted(
            (
                {
                    "email": attendee.get("email"),
                    "responseStatus": attendee.get("responseStatus"),
                    "optional": bool(attendee.get("optional")),
                }
                for attendee in raw_attendees
                if isinstance(attendee.get("email"), str)
            ),
            key=lambda attendee: str(attendee["email"]),
        )
        attendee_emails = [str(attendee["email"]) for attendee in attendees]
        fields: dict[str, Any] = {
            "calendar_id": calendar_id,
            "calendar": calendar_name,
            "calendar_name": calendar_name,
            "summary": event.get("summary") if isinstance(event.get("summary"), str) else "",
            "start": start,
            "end": end,
            "date": start[:10],
            "status": event.get("status") or "confirmed",
            "attendees": attendees,
            "attendee_emails": attendee_emails,
        }
        resources.append(CanonicalResource(capture.provider_role, "event", event_id, fields))
        stable = {"id": event_id, **fields}
        stable_events.append(stable)
        grouped.setdefault(calendar_name, []).append(stable)

    for calendar_name, events in sorted(grouped.items()):
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "event_collection",
                calendar_name,
                {
                    "calendar": calendar_name,
                    "event_count": len(events),
                    "deleted_count": sum(event["status"] == "cancelled" for event in events),
                    "stable_digest": _stable_digest(sorted(events, key=lambda event: event["id"])),
                },
            )
        )

    digest = _stable_digest(sorted(stable_events, key=lambda event: event["id"]))
    resources.extend(
        [
            CanonicalResource(
                capture.provider_role,
                "calendar_snapshot",
                "all",
                {"scope": "all", "stable_digest": digest},
            ),
            CanonicalResource(
                capture.provider_role,
                "provider_state",
                "all",
                {"scope": "all", "stable_digest": digest},
            ),
        ]
    )
    return resources


def google_calendar_list_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    body = _object(capture.body, capture, "response")
    _complete_google_page(body, capture)
    resources: list[CanonicalResource] = []
    for index, calendar in enumerate(_objects(body.get("items"), capture, "response.items")):
        calendar_id = _required_id(calendar, capture, f"calendar[{index}]")
        resources.append(
            CanonicalResource(
                capture.provider_role,
                "calendar",
                calendar_id,
                {
                    "name": calendar.get("summaryOverride") or calendar.get("summary"),
                    "summary": calendar.get("summary"),
                    "primary": bool(calendar.get("primary")),
                    "deleted": bool(calendar.get("deleted")),
                },
            )
        )
    return resources


def google_calendar_events_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _calendar_event_resources(capture, admin=False)


def google_calendar_all_events_v1(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _calendar_event_resources(capture, admin=False)


def google_calendar_state_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    return _calendar_event_resources(capture, admin=True)


def _stripe_data(capture: CapturedQueryState) -> list[dict[str, Any]]:
    body = _object(capture.body, capture, "response")
    _complete_stripe_page(body, capture)
    return _objects(body.get("data"), capture, "response.data")


def _stripe_snapshot_resources(
    capture: CapturedQueryState,
    *,
    kind: str,
    stable_items: Iterable[Mapping[str, Any]],
) -> list[CanonicalResource]:
    digest = _stable_digest(sorted((dict(item) for item in stable_items), key=lambda item: str(item.get("id", ""))))
    return [
        CanonicalResource(
            capture.provider_role,
            "stripe_snapshot",
            scope,
            {"scope": scope, f"{kind}_digest": digest},
        )
        for scope in ("all", "catalog_and_customers")
    ]


def stripe_customers_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for index, customer in enumerate(_stripe_data(capture)):
        customer_id = _required_id(customer, capture, f"customer[{index}]")
        metadata = _optional_object(customer.get("metadata")) or {}
        fields: dict[str, Any] = {
            "name": customer.get("name"),
            "email": customer.get("email"),
            "description": customer.get("description"),
            "metadata": metadata,
            "deleted": bool(customer.get("deleted")),
        }
        resources.append(CanonicalResource(capture.provider_role, "customer", customer_id, fields))
        stable.append({"id": customer_id, **fields})
    resources.extend(_stripe_snapshot_resources(capture, kind="customers", stable_items=stable))
    return resources


def stripe_products_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for index, product in enumerate(_stripe_data(capture)):
        product_id = _required_id(product, capture, f"product[{index}]")
        default_price = product.get("default_price")
        default_price_dict = _optional_object(default_price)
        if default_price_dict is not None:
            default_price = default_price_dict.get("id")
        metadata = _optional_object(product.get("metadata")) or {}
        fields: dict[str, Any] = {
            "name": product.get("name"),
            "active": bool(product.get("active", True)),
            "description": product.get("description"),
            "default_price": default_price,
            "metadata": metadata,
        }
        resources.append(CanonicalResource(capture.provider_role, "product", product_id, fields))
        stable.append({"id": product_id, **fields})
    resources.extend(_stripe_snapshot_resources(capture, kind="products", stable_items=stable))
    return resources


def stripe_prices_stable(capture: CapturedQueryState) -> Sequence[CanonicalResource]:
    resources: list[CanonicalResource] = []
    stable: list[dict[str, Any]] = []
    for index, price in enumerate(_stripe_data(capture)):
        price_id = _required_id(price, capture, f"price[{index}]")
        raw_product = price.get("product")
        raw_product_dict = _optional_object(raw_product)
        if raw_product_dict is not None:
            product_id = raw_product_dict.get("id")
            product_name = raw_product_dict.get("name")
        else:
            product_id = raw_product
            product_name = price.get("product_name")
        metadata = _optional_object(price.get("metadata")) or {}
        fields: dict[str, Any] = {
            "product_id": product_id,
            "unit_amount": price.get("unit_amount"),
            "currency": price.get("currency"),
            "nickname": price.get("nickname"),
            "lookup_key": price.get("lookup_key"),
            "active": bool(price.get("active", True)),
            "recurring": price.get("recurring"),
            "metadata": metadata,
        }
        if isinstance(product_name, str) and product_name:
            fields["product_name"] = product_name
        resources.append(CanonicalResource(capture.provider_role, "price", price_id, fields))
        stable.append({"id": price_id, **fields})
    resources.extend(_stripe_snapshot_resources(capture, kind="prices", stable_items=stable))
    return resources


PRODUCTIVITY_CANONICALIZERS: dict[str, Canonicalizer] = {
    "drive_files_content_hash_v1": drive_files_content_hash_v1,
    "gmail_drafts_stable": gmail_drafts_stable,
    "gmail_drafts_v1": gmail_drafts_v1,
    "gmail_labels_stable": gmail_labels_stable,
    "gmail_labels_v1": gmail_labels_v1,
    "gmail_messages_labels_threads_v1": gmail_messages_labels_threads_v1,
    "gmail_messages_stable": gmail_messages_stable,
    "gmail_messages_threads_v1": gmail_messages_threads_v1,
    "gmail_threads_stable": gmail_threads_stable,
    "google_calendar_all_events_v1": google_calendar_all_events_v1,
    "google_calendar_events_v1": google_calendar_events_v1,
    "google_calendar_list_v1": google_calendar_list_v1,
    "google_calendar_state_stable": google_calendar_state_stable,
    "google_drive_state_stable": google_drive_state_stable,
    "notion_pages_complete": notion_pages_complete,
    "notion_pages_markdown_v1": notion_pages_markdown_v1,
    "notion_spec_pages_stable": notion_spec_pages_stable,
    "notion_state_stable": notion_state_stable,
    "stripe_customers_stable": stripe_customers_stable,
    "stripe_prices_stable": stripe_prices_stable,
    "stripe_products_stable": stripe_products_stable,
}


def canonicalizers() -> dict[str, Canonicalizer]:
    """Return a copy suitable for merging into the runner's canonicalizer registry."""

    return dict(PRODUCTIVITY_CANONICALIZERS)
