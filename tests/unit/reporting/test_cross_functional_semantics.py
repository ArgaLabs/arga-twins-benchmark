from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from arga_twins_benchmark.reporting.cross_functional_semantics import (
    SEMANTIC_EQUIVALENTS,
    SEMANTIC_FACT_KEYS,
    maximum_record_fact_matches,
    normalize_match_text,
    semantic_value_present,
    structured_fact_present,
)

SUITE_PATH = Path("benchmark/cross_functional_40/suite.json")


_DISPOSITION_PARAPHRASES = {
    "alert_noise_consolidated_real_incident_open": "MON-771 was duplicate noise; DB-912 remains active",
    "backport_open_unmerged": "The release/4.8 pull request is awaiting merge",
    "blocked_pending_owner_review": "The batch is held for billing-storage approval",
    "bounded_quarantine": "The flaky test was isolated for one day",
    "changes_requested_for_registry_policy": "Changes requested because QuickMirror is an unapproved source",
    "credential_exposure_contained": "The leaked token was invalidated",
    "drift_open_pending_fix": "The schema mismatch remains unresolved",
    "embargo_hold": "Publishing is blocked until 16:00",
    "empty_evaluation_profile_archived": "The unused evaluation customer was deactivated",
    "empty_trial_profile_archived": "The empty trial customer was removed",
    "mapping_documented_no_meter_mutation": "The root cause was recorded and no Stripe change was made",
    "publication_blocked": "Nothing is authorized to publish while legal review is on hold",
    "regression_open_and_escalated": "The client bug is unresolved and with engineering",
    "renewal_at_risk": "The renewal is jeopardized by the open review",
    "rollback_required": "The previous configuration was restored",
    "unapproved_client_blocked": "OAuth client CSK-991 was disabled",
    "unavailable_for_new_orders": "The retired product cannot be purchased",
    "unsafe_revert_rejected": "The revert was declined",
    "verified_address_canonical_bounce_retained": (
        "The good address is primary and the bounced-address history was kept"
    ),
}


@pytest.mark.parametrize(("canonical", "evidence"), sorted(_DISPOSITION_PARAPHRASES.items()))
def test_every_suite_disposition_accepts_a_human_paraphrase(canonical: str, evidence: str) -> None:
    assert normalize_match_text(canonical) not in normalize_match_text(evidence)
    assert structured_fact_present(evidence, "disposition", canonical)


def test_suite_dispositions_have_auditable_task_bounded_equivalents() -> None:
    suite = cast(dict[str, Any], json.loads(SUITE_PATH.read_text(encoding="utf-8")))
    dispositions = {
        str(value)
        for task in cast(list[dict[str, Any]], suite["tasks"])
        for outcome in cast(list[dict[str, Any]], task["verification"]["required_outcomes"])
        if outcome.get("id") == "structured_result"
        for key, value in cast(dict[str, object], outcome["facts"]).items()
        if key == "disposition"
    }

    assert dispositions == set(_DISPOSITION_PARAPHRASES)
    assert {normalize_match_text(value) for value in dispositions} <= set(SEMANTIC_EQUIVALENTS)


@pytest.mark.parametrize(
    ("key", "expected", "evidence"),
    [
        ("blocker", "vendor security and a data-processing addendum", "Security review and the DPA are open"),
        ("workaround", "disable adaptive keepalive", "Adaptive keepalive was turned off"),
        ("evidence_change", "Add observability route", "The observability page was added"),
        ("change", "Normalize payment idempotency keys", "Canonicalize the idempotency keys"),
        ("failure_phase", "shared sandbox setup", "The failure occurred during sandbox initialization"),
        ("source_change", "Fix invoice export crash", "The invoice export crash was corrected"),
        ("contract_field", "next_cursor as nullable string", "next_cursor accepts a string or null"),
        ("sdk_field", "nextPage as integer", "nextPage is numeric"),
        ("issue", "Webhook retries stop after third attempt", "The webhook retry limit is 3"),
        ("approval_set", "Brand, Accessibility, and Legal", "Legal, Brand and Accessibility approved"),
        ("supported_claim", "28 percent", "Measured uplift was 28%"),
        ("quarantine_duration", "24 hours", "A 24-hour quarantine was applied"),
    ],
)
def test_descriptive_structured_facts_accept_bounded_paraphrases(key: str, expected: str, evidence: str) -> None:
    assert key in SEMANTIC_FACT_KEYS
    assert structured_fact_present(evidence, key, expected)


@pytest.mark.parametrize(
    ("key", "expected", "near_miss"),
    [
        ("owner", "Priyanka Rao", "Priyanka owns the opportunity"),
        ("incident", "ENG-771", "ENG-772 remains open"),
        ("contact", "buyer@northstar-robotics.example", "buyer@northstar.example"),
        ("production_cta", "/products/observability", "/products/observe"),
        ("approved_revision", 7, "revision 6"),
    ],
)
def test_names_identifiers_addresses_paths_and_counts_remain_exact(
    key: str,
    expected: object,
    near_miss: str,
) -> None:
    assert key not in SEMANTIC_FACT_KEYS
    assert not structured_fact_present(near_miss, key, expected)


def test_negated_machine_label_words_do_not_accidentally_pass() -> None:
    assert not structured_fact_present(
        "The incident was not mitigated and was closed as unresolved.",
        "disposition",
        "mitigated_not_closed",
    )


def test_iso_datetime_accepts_the_same_instant_with_an_explicit_offset() -> None:
    evidence = {
        "start": {"dateTime": "2026-08-18T10:15:00-07:00"},
        "end": {"dateTime": "2026-08-18T10:45:00-07:00"},
    }

    assert semantic_value_present(evidence, "2026-08-18T17:15")
    assert semantic_value_present(evidence, "2026-08-18T17:45")
    assert not semantic_value_present(evidence, "2026-08-18T18:15")


def test_record_fact_matching_does_not_compose_facts_across_sibling_records() -> None:
    facts = (("case", "OBS-91"), ("production_cta", "/products/observability"))
    split_records = {
        "issues": [
            {"id": "one", "title": "OBS-91"},
            {"id": "two", "description": "Use /products/observability"},
        ]
    }
    joined_record = {
        "issues": [
            {
                "id": "one",
                "title": "OBS-91",
                "description": "Use /products/observability",
            }
        ]
    }

    assert maximum_record_fact_matches(split_records, facts) == 1
    assert maximum_record_fact_matches(joined_record, facts) == 2
