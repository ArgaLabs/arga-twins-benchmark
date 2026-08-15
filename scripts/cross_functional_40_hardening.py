"""Observable grading facts and protected lookalikes for Cross-Functional 40.

Every required fact below is present in the task prompt or seeded provider state.
The benchmark never asks a candidate to reproduce a hidden task identifier or a
particular provider-call sequence.
"""

from __future__ import annotations

from typing import Any

HARDENING_PROFILES: dict[str, dict[str, Any]] = {
    "IT-01": {
        "facts": {
            "mail_thread": "THR-447",
            "endpoint": "PRC-LT-214",
            "artifact_sha256": "31d8c9f0",
            "disposition": "spoofed_download_contained",
        },
        "protected": ["rate-card-jul.pdf", "8a4e7712"],
    },
    "IT-02": {
        "facts": {
            "causal_change": "AUTH-214",
            "affected_tenant": "Northstar support tenant",
            "rollback_record": "IDP-RB-12 revision 9",
            "verification_probes": 2,
            "disposition": "rollback_required",
        },
        "protected": ["AUTH-241", "sandbox tenant"],
    },
    "IT-03": {
        "facts": {
            "credential_fingerprint": "tok_7A91",
            "repository": "acme/payments-api",
            "disposition": "credential_exposure_contained",
        },
        "protected": ["tok_7A19", "acme/developer-docs"],
    },
    "IT-04": {
        "facts": {
            "oauth_client": "CSK-991",
            "drive_file": "1XQ7",
            "disposition": "unapproved_client_blocked",
        },
        "protected": ["CSE-104", "CloudSketch Enterprise"],
    },
    "IT-05": {
        "facts": {
            "affected_version": "6.4.2",
            "workaround": "disable adaptive keepalive",
            "disposition": "regression_open_and_escalated",
        },
        "protected": ["6.3.x", "February gateway outage"],
    },
    "IT-06": {
        "facts": {
            "person": "Lena Ortiz",
            "identity": "lena.contractor@acme.example",
            "offboarding_record": "OFF-308",
        },
        "protected": ["Lena Ortez", "lena.ortez@acme.example", "Quarterly Planning"],
    },
    "IT-07": {
        "facts": {
            "noise_source": "MON-771",
            "real_incident": "DB-912",
            "disposition": "alert_noise_consolidated_real_incident_open",
        },
        "protected": ["DB-912", "96 percent database connection usage"],
    },
    "IT-08": {
        "facts": {
            "runner": "prod-linux-07",
            "workflow_run": "8841",
            "investigation": "SEC-552",
        },
        "protected": ["lab-linux-07"],
    },
    "CRM-01": {
        "facts": {
            "company": "Northstar Robotics",
            "contact": "buyer@northstar-robotics.example",
            "opportunity": "NSR Expansion",
            "owner": "Priyanka Rao",
        },
        "protected": ["Northstar Labs", "northstarlabs.example"],
    },
    "CRM-02": {
        "facts": {
            "company": "Alder Bank",
            "opportunity": "Alder Bank Expansion",
            "blocker": "vendor security and a data-processing addendum",
            "owner": "Lucas Wong",
        },
        "protected": ["Alder Credit Union", "aldercu.example"],
    },
    "CRM-03": {
        "facts": {
            "company": "Driftline Logistics",
            "business_unit": "Platform",
            "contact": "nia.ford@platform.driftline.example",
            "deployment_size": "240 operators",
        },
        "protected": ["Driftline Freight Brokerage", "driftline.example"],
    },
    "CRM-04": {
        "facts": {
            "account": "Cedar Health US",
            "opportunity": "FY27 Renewal",
            "blocker": "SR-188",
            "disposition": "renewal_at_risk",
        },
        "protected": ["Cedar Health Canada", "cedarhealth.ca"],
    },
    "CRM-05": {
        "facts": {
            "event": "FinOps webinar",
            "eligible_contacts": 29,
            "existing_customers_excluded": 9,
            "duplicate_identities_reconciled": 4,
        },
        "protected": ["Kira Long", "Trent Bell", "jules@existingco.example"],
    },
    "CRM-06": {
        "facts": {
            "account": "BluePeak Energy",
            "territory_record": "TERR-62",
            "segment": "Strategic",
            "owner": "Amina Yusuf",
        },
        "protected": ["BluePeak Solar", "bluepeaksolar.example"],
    },
    "CRM-07": {
        "facts": {
            "company": "HelioWorks",
            "verified_email": "marco@helioworks.example",
            "bounced_email": "marco.ruiz@helioworks.example",
            "disposition": "verified_address_canonical_bounce_retained",
        },
        "protected": ["Helio Workspaces", "helioworkspaces.example"],
    },
    "CRM-08": {
        "facts": {
            "account": "Orbit Systems",
            "evaluation": "EV-204",
            "owner": "Iris Novak",
            "meeting_time": "2026-08-17 at 10:00 PT",
        },
        "protected": ["Orbit Systemics", "EV-119"],
    },
    "MKT-01": {
        "facts": {
            "campaign": "REL-26",
            "approved_revision": 7,
            "publisher": "Acme Marketing",
            "post_count": 1,
        },
        "protected": ["revision 6", "guaranteed zero failures"],
    },
    "MKT-02": {
        "facts": {
            "campaign": "AB-52",
            "legal_review": "LGL-442",
            "disposition": "publication_blocked",
            "post_count": 0,
        },
        "protected": ["AB-25", "2025 benchmark report"],
    },
    "MKT-03": {
        "facts": {
            "campaign": "OBS-91",
            "production_cta": "/products/observability",
            "evidence_change": "Add observability route",
            "post_count": 1,
        },
        "protected": ["preview.acme.example/observability", "/products/observe"],
    },
    "MKT-04": {
        "facts": {
            "customer": "Redwood Analytics",
            "artifact": "redwood-analytics-final.pdf",
            "supported_claim": "28 percent",
            "post_count": 1,
        },
        "protected": ["redwood-draft-v4.pdf", "60 percent", "Redwood Systems"],
    },
    "MKT-05": {
        "facts": {
            "campaign": "Project Harbor",
            "embargo_end": "16:00 UTC",
            "disposition": "embargo_hold",
            "acquisition_post_count": 0,
        },
        "protected": ["Join the Harbor team"],
    },
    "MKT-06": {
        "facts": {
            "campaign": "SO-88",
            "net_new_attendees": 29,
            "existing_customers_excluded": 9,
            "duplicate_identities_reconciled": 4,
        },
        "protected": ["SO-51", "ScaleOps Online 2025"],
    },
    "MKT-07": {
        "facts": {
            "campaign": "A11Y-2026",
            "approved_revision": 5,
            "approval_set": "Brand, Accessibility, and Legal",
            "post_count": 1,
        },
        "protected": ["regional revision 3", "A11Y-2025"],
    },
    "MKT-08": {
        "facts": {
            "campaign": "CEO-64",
            "calendar": "Executive Communications — North America",
            "hold_start": "2026-08-18T17:15:00Z",
            "hold_end": "2026-08-18T17:45:00Z",
            "linkedin_post_count": 0,
        },
        "protected": ["Executive Communications — EMEA"],
    },
    "DEV-01": {
        "facts": {
            "deployment": "DEP-9842",
            "change": "Normalize payment idempotency keys",
            "incident": "ENG-771",
            "disposition": "mitigated_not_closed",
        },
        "protected": ["Refresh payment test fixtures", "ENG-699"],
    },
    "DEV-02": {
        "facts": {
            "dependency_version": "4.2.1",
            "advisory": "CVE-2026-1181",
            "unreviewed_source": "packages.quickmirror.example",
            "disposition": "changes_requested_for_registry_policy",
        },
        "protected": ["Document safemark advisory"],
    },
    "DEV-03": {
        "facts": {
            "flaky_test": "checkout_tax_roundtrip",
            "failure_count": 6,
            "failure_phase": "shared sandbox setup",
            "reliability_policy": "CRP-6",
            "quarantine_duration": "24 hours",
            "disposition": "bounded_quarantine",
        },
        "protected": ["checkout_tax_rounding", "Adjust tax rounding"],
    },
    "DEV-04": {
        "facts": {
            "source_change": "Fix invoice export crash",
            "release_line": "4.8",
            "approval_record": "REL-204",
            "disposition": "backport_open_unmerged",
        },
        "protected": ["4.7", "REL-209"],
    },
    "DEV-05": {
        "facts": {
            "release_batch": "paycore-2026.08-r17",
            "migration_fingerprint": "c91d-7a40",
            "effective_owner": "billing-storage",
            "policy_file": ".github/CODEOWNERS",
            "disposition": "blocked_pending_owner_review",
        },
        "protected": ["paycore-2026.08-r16", "c91d-7a04", "generated-schema"],
    },
    "DEV-06": {
        "facts": {
            "active_contract": "api/openapi.yaml",
            "contract_field": "next_cursor as nullable string",
            "sdk_field": "nextPage as integer",
            "disposition": "drift_open_pending_fix",
        },
        "protected": ["specs/partner-draft.yaml", "retired on 2025-11-30"],
    },
    "DEV-07": {
        "facts": {
            "incident": "INC-940",
            "approval": "CAB-188",
            "mitigation": "rate-limit-safe-2",
            "disposition": "unsafe_revert_rejected",
        },
        "protected": ["Revert rate-limit hotfix"],
    },
    "DEV-08": {
        "facts": {
            "account": "Apex Freight",
            "api_version": "2026-07",
            "issue": "Webhook retries stop after third attempt",
            "tracker": "ENG-944",
        },
        "protected": ["Apex Freight Systems", "Retry delay after backoff", "2025-10"],
    },
    "ECOM-01": {
        "facts": {
            "active_customer": "Morgan Retail",
            "active_email": "morgan@retail.example",
            "reconciled_profile": "Morgan Retail Trial",
            "disposition": "empty_trial_profile_archived",
        },
        "protected": ["Morgan Markets", "billing@morganmarkets.example"],
    },
    "ECOM-02": {
        "facts": {
            "customer": "Northwind Studio",
            "former_contact": "billing@northwindstudio.example",
            "verified_contact": "ap@northwindstudio.example",
            "email_state": "draft_unsent",
        },
        "protected": ["Northwind Studios Prospect", "northwind-studios.example"],
    },
    "ECOM-03": {
        "facts": {
            "retired_product": "Trailpack Enterprise",
            "replacement": "Trailpack Business",
            "approval": "CAT-301",
            "disposition": "unavailable_for_new_orders",
        },
        "protected": ["Trailpack Enterprise EU", "Trailpack Business"],
    },
    "ECOM-04": {
        "facts": {
            "customer": "Civic Research Institute",
            "certificate": "TX-778",
            "valid_through": "2027-06-30",
            "email_state": "draft_unsent",
        },
        "protected": ["Civic Research Europe", "TX-441"],
    },
    "ECOM-05": {
        "facts": {
            "production_event": "order_fulfilled",
            "stripe_meter": "orders_fulfilled",
            "incorrect_dashboard_key": "orders-fulfilled",
            "disposition": "mapping_documented_no_meter_mutation",
        },
        "protected": ["orders_fulfilled_test"],
    },
    "ECOM-06": {
        "facts": {
            "product": "Pro Annual",
            "approval": "PRICE-611",
            "approved_price": "USD 12,900 per year",
            "subscriber_migrations": 0,
        },
        "protected": ["Pro Annual EU", "Pro Annual Test"],
    },
    "ECOM-07": {
        "facts": {
            "customer": "Harbor Goods",
            "verified_legal_name": "Harbor Goods Cooperative",
            "email": "billing@harborgoods.example",
            "case": "BILL-742",
        },
        "protected": ["Harbour Goods Ltd", "accounts@harbourgoods.example"],
    },
    "ECOM-08": {
        "facts": {
            "active_customer": "Beacon Works",
            "active_email": "ap@beaconworks.example",
            "reconciled_profile": "Beacon Works Evaluation",
            "disposition": "empty_evaluation_profile_archived",
        },
        "protected": ["Beacon Workshop", "beaconworkshop.example"],
    },
}
