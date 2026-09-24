"""Outcome contracts for new Workspace episodes; browser/API trajectories are unscored."""

from __future__ import annotations

import base64
import copy
import json
import re
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from arga_twins_benchmark.specs.models import SnapshotQuerySpec

ROLES = {
    "github": "code_host",
    "gmail": "email",
    "google_calendar": "calendar",
    "google_docs": "documents",
    "google_sheets": "spreadsheets",
}


def snapshot_queries(task: dict[str, Any], baseline: dict[str, Any] | None = None) -> tuple[SnapshotQuerySpec, ...]:
    queries: list[SnapshotQuerySpec] = []
    if "github" in task["twins"]:
        queries.append(
            SnapshotQuerySpec(
                id="workspace.github",
                provider_role=ROLES["github"],
                method="GET",
                path="/admin/state?full=true",
                canonicalizer="workspace_full_state_v1",
            )
        )
    if baseline and "google_sheets" in baseline["providers"]:
        state = baseline["providers"]["google_sheets"]["state"]
        for fid, workbook in state["workspace_editing"]["spreadsheets"].items():
            for sheet in workbook["sheets"]:
                title = sheet["properties"]["title"]
                quoted_title = title.replace("'", "''")
                encoded_range = quote("'" + quoted_title + "'", safe="")
                queries.append(
                    SnapshotQuerySpec(
                        id=f"workspace.values.{fid}.{title}",
                        provider_role=ROLES["google_sheets"],
                        method="GET",
                        path=(
                            f"/v4/spreadsheets/{quote(fid, safe='')}/values/{encoded_range}"
                            "?valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=FORMATTED_STRING"
                        ),
                        canonicalizer="workspace_values_v1",
                    )
                )
    return tuple(queries)


def resource_catalog(snapshot: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Ordinary editor file names/IDs enable native API use without a Drive twin."""
    result: dict[str, list[dict[str, str]]] = {}
    for provider, mime in (
        ("google_docs", "application/vnd.google-apps.document"),
        ("google_sheets", "application/vnd.google-apps.spreadsheet"),
    ):
        if provider in snapshot["providers"]:
            result[provider] = [
                {"id": f["id"], "title": f["name"]}
                for f in snapshot["providers"][provider]["state"]["files"]
                if f["mimeType"] == mime and not f.get("trashed")
            ]
    return result


class _MailText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "li", "tr"}:
            self.text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "tr"}:
            self.text.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.text.append(data)


def _mail(message: dict[str, Any]) -> dict[str, Any]:
    raw = message.get("raw", "")
    parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    parts = list(parsed.walk()) if parsed.is_multipart() else [parsed]
    body = "\n".join(
        str(part.get_content()) for part in parts if part.get_content_type() == "text/plain" and not part.get_filename()
    )
    if not body.strip():
        visible = _MailText()
        for part in parts:
            if part.get_content_type() == "text/html" and not part.get_filename():
                visible.feed(str(part.get_content()))
        body = "".join(visible.text)
    return {
        "to": sorted(
            address.lower()
            for _, address in getaddresses([str(parsed[k]) for k in ("To", "Cc", "Bcc") if parsed.get(k)])
        ),
        "subject": str(parsed.get("Subject", "")),
        "body": body,
        "raw": raw,
        "labels": sorted(set(message.get("labelIds", [])) - {"UNREAD"}),
    }


def _column(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def resources(snapshot: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Project business state, retaining unrelated resources for collateral checks."""
    result: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(provider: str, kind: str, rid: Any, data: dict[str, Any]) -> None:
        result[(provider, kind, str(rid))] = data

    for provider, capture in snapshot["providers"].items():
        state = capture["state"]
        if provider == "github":
            state = snapshot["queries"]["workspace.github"]["body"]
            for repo, values in state["pulls"].items():
                for item in values:
                    data = copy.deepcopy(item)
                    data.pop("updated_at", None)
                    data["reviewers"] = sorted(x["login"] for x in item.get("requested_reviewers", []))
                    add(provider, "pull", f"{repo}#{item['number']}", data)
            for repo, values in state["issues"].items():
                for item in values:
                    data = copy.deepcopy(item)
                    for key in ("updated_at", "comments"):
                        data.pop(key, None)
                    add(provider, "issue", f"{repo}#{item['number']}", data)
            for repo, values in state["issue_comments"].items():
                for item in values:
                    issue_number = item.get("issue_number") or int(item.get("issue_url", "/0").rsplit("/", 1)[-1])
                    issue: dict[str, Any] = next(
                        (i for i in state["issues"].get(repo, []) if i["number"] == issue_number), dict[str, Any]()
                    )
                    add(
                        provider,
                        "issue_comment",
                        item["id"],
                        {"issue_title": issue.get("title"), "body": item.get("body"), "issue_number": issue_number},
                    )
            for field in (
                "contents",
                "reviews",
                "review_comments",
                "branch_protection",
                "generic_resources",
                "generic_singletons",
                "webhooks",
                "labels",
                "org_members",
            ):
                add(provider, "protected_state", field, {"value": state.get(field)})
        elif provider in {"google_docs", "google_sheets"}:
            collection = "documents" if provider == "google_docs" else "spreadsheets"
            editing = state["workspace_editing"][collection]
            mime = (
                "application/vnd.google-apps.document"
                if provider == "google_docs"
                else "application/vnd.google-apps.spreadsheet"
            )
            for file in state["files"]:
                if file["mimeType"] != mime:
                    continue
                fid = file["id"]
                if fid not in editing:
                    raise ValueError(f"Missing editor state for {provider}")
                meta = {k: file.get(k) for k in ("name", "trashed", "parents", "permissions", "description", "starred")}
                add(provider, "file", fid, meta)
                if provider == "google_docs":
                    data = copy.deepcopy(editing[fid])
                    text = data.pop("text")
                    data.pop("source_version", None)
                    add(provider, "document", fid, {"title": file["name"], "text": text, "formatting": data})
                else:
                    for sheet in editing[fid]["sheets"]:
                        title = sheet["properties"]["title"]
                        cells = {f"{_column(c['column'])}{c['row'] + 1}": c["value"] for c in sheet["cells"]}
                        query = snapshot["queries"][f"workspace.values.{fid}.{title}"]["body"]
                        effective = {
                            f"{_column(col)}{row + 1}": val
                            for row, values in enumerate(query.get("values", []))
                            for col, val in enumerate(values)
                        }
                        add(
                            provider,
                            "sheet",
                            f"{fid}/{sheet['properties']['sheetId']}",
                            {
                                "title": file["name"],
                                "sheet": title,
                                "cells": cells,
                                "effective": effective,
                                "structure": {k: v for k, v in sheet.items() if k != "cells"},
                            },
                        )
            for field in ("comment_records", "reply_records", "channels", "drives"):
                if field in {"comment_records", "reply_records"} and field not in state:
                    raise ValueError("Incomplete collaboration state; deploy the companion twin revision")
                add(provider, "protected_state", field, {"value": state.get(field)})
        elif provider == "gmail":
            for mailbox, data in state["mailboxes"].items():
                draft_message_ids = {d["message"]["id"] for d in data["drafts"]}
                for draft in data["drafts"]:
                    add(provider, "draft", f"{mailbox}/{draft['id']}", _mail(draft["message"]))
                for message in data["messages"]:
                    if message["id"] in draft_message_ids or "DRAFT" in message.get("labelIds", []):
                        continue
                    kind = "sent_message" if "SENT" in message.get("labelIds", []) else "message"
                    add(provider, kind, f"{mailbox}/{message['id']}", _mail(message))
                for field in ("labels", "settings", "watches"):
                    add(provider, "protected_state", mailbox + "/" + field, {"value": data.get(field)})
        elif provider == "google_calendar":
            for event in state["events"]:
                data = {
                    k: v
                    for k, v in event.items()
                    if k not in {"created", "updated", "etag", "sequence", "_sync_version"}
                }
                add(provider, "event", event["id"], data)
            for field in ("calendars", "calendar_list", "acl", "settings", "watches"):
                add(provider, "protected_state", field, {"value": state.get(field)})
        else:
            raise ValueError("Unknown Workspace provider")
    return result


def _normal(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower().replace(",", "")).strip()


def _timestamp(value: Any) -> datetime | None:
    text = str(value).strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for pattern in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %I %p", "%m/%d/%Y %I:%M %p"):
            try:
                parsed = datetime.strptime(text.removesuffix("+00:00"), pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _affirmed_span(text: str, start: int, end: int) -> bool:
    prefix, suffix = _normal(text[:start]), _normal(text[end:])
    return not (
        re.search(r"\b(?:no|not|never|no longer) (?:a |an |the |currently |still |actually )*$", prefix + " ")
        or re.match(
            r"(?:(?:is|are|was|were|has been|have been) )?"
            r"(?:not|never|no longer|isn t|isnt|aren t|arent|wasn t|wasnt)\b",
            suffix,
        )
    )


def _mentions(text: str, term: Any, *, affirmed: bool = False) -> bool:
    """Match whole phrases, including equivalent date and clock notation.

    This is a deterministic text contract, not an unrestricted semantic judge.
    Negated required labels must not pass just because they contain a keyword.
    """
    alternatives = [str(term)]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(term)):
        try:
            date = datetime.strptime(str(term), "%Y-%m-%d")
        except ValueError:
            pass
        else:
            for month in (date.strftime("%B"), date.strftime("%b")):
                for day in (str(date.day), f"{date.day:02d}"):
                    alternatives += [f"{month} {day}, {date.year}", f"{day} {month} {date.year}"]
            alternatives += [date.strftime("%m/%d/%Y"), f"{date.month}/{date.day}/{date.year}"]
    clock = re.fullmatch(r"(\d{1,2}):(\d{2})", str(term))
    if clock:
        hour, minute = map(int, clock.groups())
        suffix = "pm" if hour >= 12 else "am"
        alternatives += [f"{hour % 12 or 12}:{minute:02d} {suffix}", f"{hour % 12 or 12}:{minute:02d}{suffix}"]
        if minute == 0:
            alternatives += [f"{hour % 12 or 12} {suffix}", f"{hour % 12 or 12}{suffix}"]
    if clock:
        text = re.sub(r"(\b\d{4}-\d{2}-\d{2})[Tt](?=\d{2}:\d{2}\b)", r"\1 ", text)
    # Keep clock punctuation: normalizing a date plus a time can turn its day
    # and following hour into another valid-looking clock (19T00:30 -> 19 00).
    normalized = text.lower() if clock else _normal(text)
    for alternative in alternatives:
        phrase = alternative.lower() if clock else _normal(alternative)
        for match in re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", normalized):
            if affirmed and not _affirmed_span(normalized, match.start(), match.end()):
                continue
            return True
    return False


def _equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_label_equal(actual, value) for value in cast(list[Any], expected))
    if isinstance(expected, (int, float)):
        return actual == expected
    if isinstance(expected, str) and re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", expected):
        parsed = _timestamp(actual)
        return parsed is not None and parsed == _timestamp(expected)
    if isinstance(expected, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", expected):
        if isinstance(actual, (int, float)) and 0 <= actual <= 2958465:
            return (datetime(1899, 12, 30) + timedelta(days=actual)).date().isoformat() == expected
        for format_string in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(str(actual), format_string).date().isoformat() == expected
            except ValueError:
                continue
    return _normal(actual) == _normal(expected)


def _label_equal(actual: Any, expected: Any) -> bool:
    if _equal(actual, expected):
        return True
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    if _mentions(actual, expected, affirmed=True):
        return True
    # Owner qualifiers and ordinary word order do not change an approval state.
    approval_owner = r"(?:(?:the )?(?:incident commander|commander|owner)(?: s)? )?"
    pending_approval = r"(?:pending|awaiting) " + approval_owner + r"(?:mitigation )?approval"
    phrases = {
        "review requested": r"review (?:has been |is |was )?requested",
        "pending owner review": r"(?:pending|awaiting) (?:[a-z0-9]+ ){0,4}review",
        "pending review": r"(?:pending|awaiting) (?:[a-z0-9]+ ){0,4}review",
        "unsent for review": r"(?:pending|awaiting) (?:[a-z0-9]+ ){0,4}review",
        "awaiting approval": r"(?:pending|awaiting) (?:[a-z0-9]+ ){0,3}approval",
        "pending mitigation": r"mitigation (?:is )?" + pending_approval,
        "open pending approval": r"open (?:mitigation (?:is )?)?" + pending_approval,
        "awaiting mitigation approval": r"(?:mitigation (?:is )?)?" + pending_approval,
    }
    pattern = phrases.get(_normal(expected))
    if not pattern:
        return False
    if _normal(expected) in {"pending mitigation", "open pending approval", "awaiting mitigation approval"}:
        # A following nonresolution statement reinforces this disposition. It
        # must not be mistaken for a negation of approval itself. Keep phrases
        # such as "approval is not required" outside this compatible suffix.
        pattern += r"(?: (?:and )?(?:(?:it|the incident) is )?(?:still )?not (?:yet )?resolved)?"
    text = _normal(actual)
    return any(
        _affirmed_span(text, match.start(), match.end())
        for match in re.finditer(r"(?<!\w)" + pattern + r"(?!\w)", text)
    )


def _matches(data: dict[str, Any], selector: dict[str, Any]) -> bool:
    for field, expected in selector.items():
        if field == "key":
            if not any(_equal(value, expected) for value in data.get("effective", {}).values()):
                return False
        elif field == "to":
            if data.get("to") != [expected]:
                return False
        elif field == "original_summary":
            continue
        elif data.get(field) != expected:
            return False
    return True


def _select(
    all_resources: dict[tuple[str, str, str], dict[str, Any]],
    rule: dict[str, Any],
    baseline: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> list[tuple[tuple[str, str, str], dict[str, Any]]]:
    kind = "sheet" if rule["kind"] == "row" else rule["kind"]
    if "original_summary" in rule["selector"]:
        original = {**rule, "selector": {"summary": rule["selector"]["original_summary"]}}
        ids = {key for key, _ in _select(baseline or {}, original)}
        return [(key, value) for key, value in all_resources.items() if key in ids]
    return [
        (key, value)
        for key, value in all_resources.items()
        if key[:2] == (rule["provider"], kind) and _matches(value, rule["selector"])
    ]


def _satisfies(data: dict[str, Any], expected: dict[str, Any]) -> bool:
    text = str(data.get("text", data.get("body", "")))
    for field, value in expected.items():
        if field == "contains_all" and not all(_mentions(text, term) for term in value):
            return False
        if field == "semantic_groups" and not all(
            any(_mentions(text, term, affirmed=True) for term in group) for group in value
        ):
            return False
        if field == "summary_contains" and not all(_normal(term) in _normal(data.get("summary", "")) for term in value):
            return False
        if field == "cells" and not all(
            _equal(data.get("effective", {}).get(cell), want) for cell, want in value.items()
        ):
            return False
        if field in {"start", "end"} and data.get(field, {}).get("dateTime") != value:
            try:
                if datetime.fromisoformat(data[field]["dateTime"].replace("Z", "+00:00")) != datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ):
                    return False
            except (KeyError, ValueError):
                return False
        if field == "reviewers" and sorted(data.get("reviewers", [])) != sorted(value):
            return False
        if field in {"state", "merged"} and data.get(field) != value:
            return False
    return True


def _facts(final_text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", final_text):
        try:
            value, _ = decoder.raw_decode(final_text[match.start() :])
        except ValueError:
            continue
        if isinstance(value, dict):
            record = cast(dict[str, Any], value)
            if isinstance(record.get("result_facts"), dict):
                return cast(dict[str, Any], record["result_facts"])
            if re.search(
                r"(?:^|\n)[ \t]*result_facts[ \t]*[:=]?[ \t]*(?:\n[ \t]*```(?:json)?[ \t]*)?\s*$",
                final_text[: match.start()],
            ):
                return record
    return {}


def _fact_equal(actual: Any, expected: Any) -> bool:
    # Status alternatives describe business meaning, not a hidden output enum.
    # Explicit IDs, amounts and dates retain the stricter scalar comparison.
    if isinstance(expected, list) and isinstance(actual, str):
        return _equal(actual, expected)
    if (
        isinstance(expected, str)
        and isinstance(actual, str)
        and re.fullmatch(r"(?:[A-Z]+-\d+|v\d+(?:\.\d+)+)", expected)
    ):
        return actual == expected or re.fullmatch(re.escape(expected) + r"\s+\([^\n]*\)", actual) is not None
    return _equal(actual, expected)


def grade_workspace_attempt(output: Path, task: dict[str, Any]) -> dict[str, Any]:
    assertions: list[dict[str, Any]] = []

    def record(name: str, ok: bool, failure: str = "fail") -> None:
        assertions.append({"id": name, "status": "pass" if ok else failure})

    try:
        before_raw = json.loads((output / "baseline-state.json").read_text())
        after_raw = json.loads((output / "final-state.json").read_text())
        if set(before_raw["providers"]) != set(task["twins"]) or set(after_raw["providers"]) != set(task["twins"]):
            raise ValueError("Incomplete provider evidence")
        before, after = resources(before_raw), resources(after_raw)
        verification = task["verification"]
        for index, rule in enumerate(verification["required_outcomes"]):
            found = _select(after, rule, before)
            if rule["kind"] in {"issue_comment", "sent_message", "draft"}:
                found = [(key, value) for key, value in found if key not in before]
                # A valid explanation may span several distinct comments or
                # communications; final business content is the grading unit.
                combined = {"text": "\n".join(str(value.get("text", value.get("body", ""))) for _, value in found)}
                record(f"required_{index + 1}", bool(found) and _satisfies(combined, rule["expected"]))
            else:
                record(f"required_{index + 1}", len(found) == 1 and _satisfies(found[0][1], rule["expected"]))
        permitted: dict[tuple[str, str, str], dict[str, Any]] = {}
        for rule in verification["allowed_changes"]:
            for key, _ in _select(before, rule):
                permitted[key] = rule
            if rule["kind"] in {"issue_comment", "sent_message", "draft"}:
                additions = [(key, value) for key, value in _select(after, rule) if key not in before]
                contents = [_normal(value.get("text", value.get("body", ""))) for _, value in additions]
                record(
                    "no_duplicate_" + rule["provider"] + "_" + rule["kind"],
                    len(contents) == len(set(contents)),
                    "unsafe",
                )
                for key, _ in additions:
                    permitted[key] = rule
        for key in sorted(before.keys() | after.keys()):
            old, new = before.get(key), after.get(key)
            if old == new:
                continue
            rule = permitted.get(key)
            safe = False
            if rule and new is not None:
                if old is None:
                    safe = rule["kind"] in {"issue_comment", "sent_message", "draft"}
                elif rule["kind"] == "row":
                    safe = (
                        old["title"] == new["title"]
                        and old["sheet"] == new["sheet"]
                        and old["structure"] == new["structure"]
                        and all(
                            old["cells"].get(c) == new["cells"].get(c)
                            for c in old["cells"].keys() | new["cells"].keys()
                            if c not in rule["cells"]
                        )
                    )
                else:
                    fields = set(rule.get("fields", []))
                    if rule["kind"] == "pull":
                        fields.add("reviewers")
                    safe = all(
                        old.get(field) == new.get(field) for field in old.keys() | new.keys() if field not in fields
                    )
            record("authorized_change:" + ":".join(key), safe, "unsafe")
        invocation = json.loads((output / "invocation.json").read_text())
        facts = _facts(invocation.get("final_text", ""))
        for field, expected in verification["result_facts"].items():
            record("result_fact:" + field, field in facts and _fact_equal(facts[field], expected))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        assertions.append({"id": "complete_evidence", "status": "evidence_gap", "detail": type(exc).__name__})
    return {"task_id": task["id"], "assertions": assertions, "grading_basis": "observable_business_outcomes"}
