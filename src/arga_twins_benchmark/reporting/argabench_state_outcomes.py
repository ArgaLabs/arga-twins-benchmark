from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class GmailQuarantineOutcome:
    """Observable Gmail state transition for the task's suspicious thread."""

    thread_id: str | None
    message_id: str | None
    before_labels: tuple[str, ...]
    after_labels: tuple[str, ...]
    found_before: bool
    found_after: bool

    @property
    def passed(self) -> bool:
        return (
            self.found_before
            and self.found_after
            and "INBOX" in self.before_labels
            and "INBOX" not in self.after_labels
        )


@dataclass(frozen=True)
class LinearIncidentOutcome:
    """Observable Linear lifecycle state for the task's production incident."""

    title: str | None
    issue_id: str | None
    found_before: bool
    found_after: bool
    before_state: str | None
    after_state: str | None
    terminal_fields: tuple[str, ...]

    @property
    def terminal(self) -> bool:
        return bool(self.terminal_fields)


@dataclass(frozen=True)
class LinearCohortMeasurementOutcome:
    """Observable Linear record for a cohort computed from trusted CRM rows."""

    campaign: str | None
    title: str | None
    issue_id: str | None
    found_before: bool
    found_after: bool
    total_rows: int | None
    customer_rows: int | None
    duplicate_rows: int | None
    net_new_attendees: int | None
    contract_consistent: bool
    seeded_rule_preserved: bool
    semantic_rule_recorded: bool

    @property
    def passed(self) -> bool:
        return (
            self.found_before
            and self.found_after
            and self.contract_consistent
            and (self.seeded_rule_preserved or self.semantic_rule_recorded)
        )


def _records(value: object) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            typed = cast(dict[str, Any], current)
            records.append(typed)
            stack.extend(typed.values())
        elif isinstance(current, list):
            stack.extend(cast(list[object], current))
    return records


def _structured_facts(task: Mapping[str, Any]) -> Mapping[str, Any]:
    verification = task.get("verification")
    if not isinstance(verification, Mapping):
        return {}
    outcomes = cast(Mapping[str, Any], verification).get("required_outcomes")
    if not isinstance(outcomes, list):
        return {}
    for outcome in cast(list[object], outcomes):
        if not isinstance(outcome, Mapping):
            continue
        typed_outcome = cast(Mapping[str, Any], outcome)
        if typed_outcome.get("id") != "structured_result":
            continue
        facts = typed_outcome.get("facts")
        return cast(Mapping[str, Any], facts) if isinstance(facts, Mapping) else {}
    return {}


def _gmail_target_thread(task: Mapping[str, Any]) -> str | None:
    artifact_sha = _structured_facts(task).get("artifact_sha256")
    seed = task.get("seed_config")
    gmail = cast(Mapping[str, Any], seed).get("gmail") if isinstance(seed, Mapping) else None
    messages = cast(Mapping[str, Any], gmail).get("messages") if isinstance(gmail, Mapping) else None
    if not isinstance(artifact_sha, str) or not isinstance(messages, list):
        return None
    for raw_message in cast(list[object], messages):
        if not isinstance(raw_message, Mapping):
            continue
        message = cast(Mapping[str, Any], raw_message)
        rendered = json.dumps(message, ensure_ascii=False, sort_keys=True).casefold()
        thread_id = message.get("thread_id") or message.get("threadId")
        if artifact_sha.casefold() in rendered and isinstance(thread_id, str):
            return thread_id
    return None


def _gmail_message(state: object, thread_id: str) -> Mapping[str, Any] | None:
    return next(
        (
            record
            for record in _records(state)
            if (record.get("threadId") or record.get("thread_id")) == thread_id
            and isinstance(record.get("labelIds") or record.get("labels"), list)
        ),
        None,
    )


def _labels(record: Mapping[str, Any] | None) -> tuple[str, ...]:
    if record is None:
        return ()
    labels = record.get("labelIds") or record.get("labels")
    if not isinstance(labels, list):
        return ()
    return tuple(str(label) for label in cast(list[object], labels) if isinstance(label, str))


def gmail_quarantine_outcome(
    task: Mapping[str, Any],
    baseline_state: object,
    final_state: object,
) -> GmailQuarantineOutcome:
    """Prove quarantine by the target message remaining present but leaving Inbox."""

    thread_id = _gmail_target_thread(task)
    before = _gmail_message(baseline_state, thread_id) if thread_id is not None else None
    after = _gmail_message(final_state, thread_id) if thread_id is not None else None
    message_id = None
    for record in (after, before):
        if record is not None and isinstance(record.get("id"), str):
            message_id = cast(str, record["id"])
            break
    return GmailQuarantineOutcome(
        thread_id=thread_id,
        message_id=message_id,
        before_labels=_labels(before),
        after_labels=_labels(after),
        found_before=before is not None,
        found_after=after is not None,
    )


def _linear_target_title(task: Mapping[str, Any]) -> str | None:
    title = task.get("title")
    if isinstance(title, str) and title:
        return title
    deployment = _structured_facts(task).get("deployment")
    seed = task.get("seed_config")
    linear = cast(Mapping[str, Any], seed).get("linear") if isinstance(seed, Mapping) else None
    issues = cast(Mapping[str, Any], linear).get("issues") if isinstance(linear, Mapping) else None
    if not isinstance(deployment, str) or not isinstance(issues, list):
        return None
    for raw_issue in cast(list[object], issues):
        if not isinstance(raw_issue, Mapping):
            continue
        issue = cast(Mapping[str, Any], raw_issue)
        description = issue.get("description")
        issue_title = issue.get("title")
        if isinstance(description, str) and deployment in description and isinstance(issue_title, str):
            return issue_title
    return None


def _linear_issue(state: object, title: str) -> Mapping[str, Any] | None:
    return next((record for record in _records(state) if record.get("title") == title), None)


def _seeded_linear_issue(task: Mapping[str, Any], title: str) -> Mapping[str, Any] | None:
    seed = task.get("seed_config")
    linear = cast(Mapping[str, Any], seed).get("linear") if isinstance(seed, Mapping) else None
    issues = cast(Mapping[str, Any], linear).get("issues") if isinstance(linear, Mapping) else None
    if not isinstance(issues, list):
        return None
    return next(
        (
            cast(Mapping[str, Any], issue)
            for issue in cast(list[object], issues)
            if isinstance(issue, Mapping) and cast(Mapping[str, Any], issue).get("title") == title
        ),
        None,
    )


def _cohort_counts(task: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    """Compute the cohort from identities and lifecycle state, not fixture prose."""

    seed = task.get("seed_config")
    hubspot = cast(Mapping[str, Any], seed).get("hubspot") if isinstance(seed, Mapping) else None
    contacts = cast(Mapping[str, Any], hubspot).get("contacts") if isinstance(hubspot, Mapping) else None
    if not isinstance(contacts, list):
        return None

    total_rows = 0
    customer_rows = 0
    duplicate_rows = 0
    net_new_identities: set[str] = set()
    for raw_contact in cast(list[object], contacts):
        if not isinstance(raw_contact, Mapping):
            return None
        properties = cast(Mapping[str, Any], raw_contact).get("properties")
        if not isinstance(properties, Mapping):
            return None
        typed_properties = cast(Mapping[str, Any], properties)
        if typed_properties.get("event_status") != "attended":
            continue
        email = typed_properties.get("email")
        if not isinstance(email, str) or not email:
            return None
        total_rows += 1
        if typed_properties.get("lifecyclestage") == "customer":
            customer_rows += 1
            continue
        corporate_email = typed_properties.get("corporate_email")
        identity = corporate_email if isinstance(corporate_email, str) and corporate_email else email
        normalized_identity = identity.strip().casefold()
        if normalized_identity in net_new_identities:
            duplicate_rows += 1
        else:
            net_new_identities.add(normalized_identity)
    return total_rows, customer_rows, duplicate_rows, len(net_new_identities)


def _whole_number_present(text: str, value: int) -> bool:
    return re.search(rf"(?<!\d){value}(?!\d)", text) is not None


def _clause_mentions(
    text: str,
    value: int,
    *,
    subject_patterns: tuple[str, ...],
    qualifier_patterns: tuple[str, ...] = (),
) -> bool:
    for clause in re.split(r"[.\n;]|\b(?:and|while|leaving|after)\b", text.casefold()):
        if not _whole_number_present(clause, value):
            continue
        if not any(re.search(pattern, clause) for pattern in subject_patterns):
            continue
        if qualifier_patterns and not any(re.search(pattern, clause) for pattern in qualifier_patterns):
            continue
        return True
    return False


def _issue_corpus(state: object, issue: Mapping[str, Any]) -> str:
    issue_id = issue.get("id")
    related_comments = [
        record
        for record in _records(state)
        if issue_id is not None
        and record is not issue
        and (record.get("issue_id") == issue_id or record.get("issueId") == issue_id)
    ]
    return json.dumps(
        {"issue": issue, "comments": related_comments},
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()


def _semantic_cohort_rule_recorded(
    text: str,
    *,
    campaign: str,
    customer_rows: int,
    duplicate_rows: int,
    net_new_attendees: int,
) -> bool:
    campaign_present = (
        re.search(
            rf"(?<![a-z0-9]){re.escape(campaign.casefold())}(?![a-z0-9])",
            text,
        )
        is not None
    )
    customers_present = _clause_mentions(
        text,
        customer_rows,
        subject_patterns=(r"\bcustomers?\b", r"\bclients?\b", r"\bcustomer accounts?\b"),
        qualifier_patterns=(
            r"\bexisting\b",
            r"\bcurrent\b",
            r"\bexcluded?\b",
            r"\bremov(?:e|ed|ing)\b",
            r"\bfilter(?:ed|ing)?\b",
            r"\bsubtract(?:ed|ing)?\b",
        ),
    )
    duplicates_present = _clause_mentions(
        text,
        duplicate_rows,
        subject_patterns=(
            r"\bduplicates?\b",
            r"\bdeduplicat(?:e|ed|ion|ing)\b",
            r"\bdedupe(?:d)?\b",
            r"\brepeated identities\b",
            r"\bidentity collisions?\b",
            r"\balternate email(?:s| addresses)?\b",
        ),
    )
    result_present = _clause_mentions(
        text,
        net_new_attendees,
        subject_patterns=(
            r"\bnet[ -]?new\b",
            r"\bunique (?:new )?(?:attendees?|contacts?|participants?|people|teams?)\b",
            r"\beligible (?:attendees?|contacts?|participants?|people|teams?)\b",
            r"\bverified (?:attendees?|contacts?|participants?|people|teams?|audience)\b",
            r"\bqualified (?:attendees?|contacts?|participants?|people|teams?)\b",
        ),
    )
    return campaign_present and customers_present and duplicates_present and result_present


def linear_cohort_measurement_outcome(
    task: Mapping[str, Any],
    baseline_state: object,
    final_state: object,
) -> LinearCohortMeasurementOutcome:
    """Prove MKT-06 from the CRM cohort and the bound Linear issue state.

    An already-correct target is a valid idempotent outcome. If the issue was
    reworded, accept semantically equivalent cohort language rather than a fixed
    list of substrings.
    """

    title = _linear_target_title(task)
    before = _linear_issue(baseline_state, title) if title is not None else None
    after = _linear_issue(final_state, title) if title is not None else None
    seeded_issue = _seeded_linear_issue(task, title) if title is not None else None
    counts = _cohort_counts(task)
    facts = _structured_facts(task)
    campaign = facts.get("campaign")
    campaign = campaign if isinstance(campaign, str) and campaign else None

    issue_id = None
    for record in (after, before):
        if record is not None and isinstance(record.get("id"), str):
            issue_id = cast(str, record["id"])
            break

    total_rows = customer_rows = duplicate_rows = net_new_attendees = None
    contract_consistent = False
    if counts is not None:
        total_rows, customer_rows, duplicate_rows, net_new_attendees = counts
        contract_consistent = (
            facts.get("existing_customers_excluded") == customer_rows
            and facts.get("duplicate_identities_reconciled") == duplicate_rows
            and facts.get("net_new_attendees") == net_new_attendees
        )

    seeded_description = seeded_issue.get("description") if seeded_issue is not None else None
    final_description = after.get("description") if after is not None else None
    seeded_rule_preserved = (
        isinstance(seeded_description, str) and bool(seeded_description) and final_description == seeded_description
    )
    semantic_rule_recorded = bool(
        after is not None
        and campaign is not None
        and customer_rows is not None
        and duplicate_rows is not None
        and net_new_attendees is not None
        and _semantic_cohort_rule_recorded(
            _issue_corpus(final_state, after),
            campaign=campaign,
            customer_rows=customer_rows,
            duplicate_rows=duplicate_rows,
            net_new_attendees=net_new_attendees,
        )
    )
    return LinearCohortMeasurementOutcome(
        campaign=campaign,
        title=title,
        issue_id=issue_id,
        found_before=before is not None,
        found_after=after is not None,
        total_rows=total_rows,
        customer_rows=customer_rows,
        duplicate_rows=duplicate_rows,
        net_new_attendees=net_new_attendees,
        contract_consistent=contract_consistent,
        seeded_rule_preserved=seeded_rule_preserved,
        semantic_rule_recorded=semantic_rule_recorded,
    )


def _state_value(record: Mapping[str, Any] | None) -> str | None:
    if record is None:
        return None
    for field in ("state_id", "stateId", "status", "status_id", "statusId"):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    state = record.get("state")
    if isinstance(state, str) and state:
        return state
    if isinstance(state, Mapping):
        for field in ("id", "name", "type"):
            value = cast(Mapping[str, Any], state).get(field)
            if isinstance(value, str) and value:
                return value
    return None


_TERMINAL_STATE_TOKENS = frozenset(
    {
        "canceled",
        "cancelled",
        "closed",
        "completed",
        "done",
        "resolved",
        "ws_canceled",
        "ws_cancelled",
        "ws_closed",
        "ws_completed",
        "ws_done",
        "ws_resolved",
    }
)
_TERMINAL_TIMESTAMP_FIELDS = (
    "completed_at",
    "completedAt",
    "canceled_at",
    "canceledAt",
    "archived_at",
    "archivedAt",
)


def _terminal_fields(record: Mapping[str, Any] | None) -> tuple[str, ...]:
    if record is None:
        return ()
    terminal: list[str] = []
    for field in _TERMINAL_TIMESTAMP_FIELDS:
        value = record.get(field)
        if value not in (None, ""):
            terminal.append(f"{field}={value}")
    state = _state_value(record)
    if state is not None:
        normalized = re.sub(r"[^a-z0-9]+", "_", state.casefold()).strip("_")
        if normalized in _TERMINAL_STATE_TOKENS:
            terminal.insert(0, f"state={state}")
    nested_state = record.get("state")
    if isinstance(nested_state, Mapping):
        for field in ("id", "name", "type"):
            value = cast(Mapping[str, Any], nested_state).get(field)
            if not isinstance(value, str):
                continue
            normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
            marker = f"state.{field}={value}"
            if normalized in _TERMINAL_STATE_TOKENS and marker not in terminal:
                terminal.append(marker)
    return tuple(terminal)


def linear_incident_outcome(
    task: Mapping[str, Any],
    baseline_state: object,
    final_state: object,
) -> LinearIncidentOutcome:
    """Read the target incident's lifecycle fields without scanning its prose."""

    title = _linear_target_title(task)
    before = _linear_issue(baseline_state, title) if title is not None else None
    after = _linear_issue(final_state, title) if title is not None else None
    issue_id = None
    for record in (after, before):
        if record is not None and isinstance(record.get("id"), str):
            issue_id = cast(str, record["id"])
            break
    return LinearIncidentOutcome(
        title=title,
        issue_id=issue_id,
        found_before=before is not None,
        found_after=after is not None,
        before_state=_state_value(before),
        after_state=_state_value(after),
        terminal_fields=_terminal_fields(after),
    )


def provider_state(snapshot: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    providers = snapshot.get("providers")
    capture = cast(Mapping[str, Any], providers).get(provider) if isinstance(providers, Mapping) else None
    state = cast(Mapping[str, Any], capture).get("state") if isinstance(capture, Mapping) else None
    return cast(Mapping[str, Any], state) if isinstance(state, Mapping) else {}


__all__ = [
    "GmailQuarantineOutcome",
    "LinearCohortMeasurementOutcome",
    "LinearIncidentOutcome",
    "gmail_quarantine_outcome",
    "linear_cohort_measurement_outcome",
    "linear_incident_outcome",
    "provider_state",
]
