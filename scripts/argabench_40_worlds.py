"""Neutral, task-specific world facts for the ArgaBench suite.

These records describe the state of the simulated business before an agent
starts.  They intentionally contain no benchmark terminology, action plan,
answer key, or instructions to the candidate.
"""

from __future__ import annotations

from typing import Any

WORLD_FACTS: dict[str, dict[str, Any]] = {
    "IT-01": {
        "asset": "Keisha Bell / PRC-LT-214 / sourcing reply THR-447",
        "current": (
            "Mail trace THR-447 shows From sourcing@novasupplies.co, Return-Path "
            "billing@nova-supplies.co, SPF fail, and no DKIM signature. The attached "
            "rate-card-aug.zip has SHA-256 31d8c9f0. PRC-LT-214 telemetry records "
            "browser-helper.exe spawning reg.exe at 09:14 UTC with the same hash."
        ),
        "related": (
            "The July sourcing thread from sourcing@novasupplies.co passed SPF and DKIM; "
            "its rate-card-jul.pdf hash is 8a4e7712 and no endpoint execution is recorded."
        ),
    },
    "IT-02": {
        "asset": "Northstar support tenant / authentication rollout AUTH-214",
        "current": (
            "AUTH-214 changed the Northstar support tenant issuer from /saml2 to /saml. "
            "Login-loop telemetry began four minutes later. Rollout check 1842 is still "
            "green because it covers password authentication only. Approved rollback record "
            "IDP-RB-12 revision 9 covers the Northstar support tenant, restores /saml2, and "
            "requires two successful SSO probes while the incident remains open."
        ),
        "related": (
            "AUTH-241 updates session duration for the sandbox tenant. Its rollout has not "
            "started and its test results contain no Northstar traffic."
        ),
    },
    "IT-03": {
        "asset": "payments-api deployment credential fingerprint tok_7A91",
        "current": (
            "Release audit 771 records credential fingerprint tok_7A91 for acme/payments-api. "
            "The deleted Slack event was captured at 16:22 UTC and the credential last "
            "authenticated from the release runner at 16:28 UTC. No raw token is retained. "
            "Release runner rotation RR-771 invalidated the credential at 16:31 UTC, and an "
            "authentication probe rejected fingerprint tok_7A91 at 16:33 UTC. Security response "
            "policy permits the matching repository exposure issue to close only after that "
            "rejection evidence is reconciled with the incident record."
        ),
        "related": (
            "Fingerprint tok_7A19 belongs to the read-only documentation publisher for "
            "acme/developer-docs and last authenticated eight days ago."
        ),
    },
    "IT-04": {
        "asset": "Cloud Sketch client CSK-991 / design brief file 1XQ7",
        "current": (
            "Client CSK-991 requested repo:write and drive.file after file 1XQ7 was opened. "
            "The client publisher is cloud-sketch-tools.example and has no vendor record. "
            "Jon Bell denied consent; no access grant is present."
        ),
        "related": (
            "CloudSketch Enterprise client CSE-104 is registered to cloudsketch.example, "
            "requests repo:read only, and appears in the 2026 vendor review."
        ),
    },
    "IT-05": {
        "asset": "SecureLink desktop client 6.4.2 / VPN regression",
        "current": (
            "SecureLink 6.4.2 was released at 18:00 UTC. Four disconnect reports show the "
            "same keepalive timeout on 6.4.2; service probes stayed healthy. Support note "
            "KB-118 says affected users can disable adaptive keepalive until 6.4.3 ships."
        ),
        "related": (
            "A February gateway outage affected clients 6.3.x and produced failed service "
            "probes. Its workaround changed DNS and was retired after the gateway repair."
        ),
    },
    "IT-06": {
        "asset": "Lena Ortiz / lena.contractor@acme.example / offboarding OFF-308",
        "current": (
            "OFF-308 names lena.contractor@acme.example and lists acme/vendor-portal, "
            "acme/pricing-tools, and the Procurement 2026 Drive folder. The engagement ended "
            "2026-08-12; the form is signed by Emil Navarro and the engineering manager. "
            "Engineering offboarding policy requires each listed repository's matching access-handoff "
            "issue to be closed after the signed identity and Jira handoff are reconciled."
        ),
        "related": (
            "Lena Ortez (lena.ortez@acme.example) is an active finance employee and owns the "
            "Quarterly Planning folder. She is not named on OFF-308."
        ),
    },
    "IT-07": {
        "asset": "checkout-api latency burst / metrics change MON-771",
        "current": (
            "MON-771 renamed route labels at 07:55 UTC. Twenty-seven alerts from 08:00 to "
            "08:06 share an empty route label and normal request volume. Alert DB-912 at "
            "08:04 has a populated route and 96 percent database connection usage."
        ),
        "related": (
            "The March checkout latency incident followed a regional traffic spike and has "
            "no deployment or metrics-label change associated with it."
        ),
    },
    "IT-08": {
        "asset": "production runner prod-linux-07 / workflow run 8841",
        "current": (
            "Workflow run 8841 used prod-linux-07 and connected to 203.0.113.77 at 11:42 UTC. "
            "The runner registration is active in the production group. Investigation "
            "SEC-552 contains the job log checksum 7bfe11."
        ),
        "related": (
            "lab-linux-07 is an offline lab registration last used in 2025. Its last job log "
            "contains no connection to 203.0.113.77."
        ),
    },
    "CRM-01": {
        "asset": "Northstar Robotics / buyer@northstar-robotics.example",
        "current": (
            "buyer@northstar-robotics.example submitted the enterprise demo form. Salesforce "
            "account Northstar Robotics has opportunity NSR Expansion, stage Discovery, amount "
            "$120,000, owned by Priyanka Rao. HubSpot has two company records for the same domain."
        ),
        "related": (
            "Northstar Labs uses northstarlabs.example, is owned by the SMB team, and has no "
            "open enterprise opportunity."
        ),
    },
    "CRM-02": {
        "asset": "Alder Bank expansion / procurement review",
        "current": (
            "Champion Renee Cho at alderbank.example replied on 2026-08-12 that vendor security "
            "and a data-processing addendum are pending. Salesforce opportunity Alder Bank "
            "Expansion is in Negotiation and owned by Lucas Wong."
        ),
        "related": (
            "Alder Credit Union uses aldercu.example and has a separate renewal opportunity "
            "owned by the public-sector team."
        ),
    },
    "CRM-03": {
        "asset": "Driftline Logistics / Platform business unit demo",
        "current": (
            "Nia Ford at platform.driftline.example requested deployment for 240 operators in "
            "Q4 and confirmed a September evaluation. The parent is Driftline Logistics; the "
            "Platform business unit has no open opportunity."
        ),
        "related": (
            "Driftline Freight Brokerage used driftline.example in 2024 and was disqualified "
            "after requesting a consumer shipment tracker."
        ),
    },
    "CRM-04": {
        "asset": "Cedar Health US / FY27 renewal",
        "current": (
            "Cedar Health US opportunity FY27 Renewal closes 2026-10-31. Champion Asha Reed "
            "reported that security review SR-188 is blocking signature. HubSpot health is "
            "green from a score last calculated before the report."
        ),
        "related": ("Cedar Health Canada renewal closes 2027-02-28 and its security review is complete."),
    },
    "CRM-05": {
        "asset": "FinOps webinar held 2026-08-07",
        "current": (
            "Attendance: Mei Park (mei@finworks.example), Omar Shah (omar.personal@example.net), "
            "and Jules Diaz (jules@existingco.example). Mei asked for pricing; Omar attended and "
            "omar@vertexops.example is his existing corporate contact. Jules is a current customer."
        ),
        "related": ("No-shows were Kira Long and Trent Bell. Neither opened the follow-up survey."),
    },
    "CRM-06": {
        "asset": "BluePeak Energy / territory request TERR-62",
        "current": (
            "BluePeak Energy moved its headquarters to New York on 2026-07-01 and now meets the "
            "Strategic segment threshold. TERR-62 was approved by revenue leadership for owner "
            "Amina Yusuf. The active opportunity remains in Discovery."
        ),
        "related": (
            "BluePeak Solar is headquartered in Nevada, remains in West territory, and has no "
            "relationship to BluePeak Energy."
        ),
    },
    "CRM-07": {
        "asset": "Marco Ruiz / HelioWorks contact record",
        "current": (
            "marco.ruiz@helioworks.example hard-bounced on 2026-08-11. A signed reply from "
            "marco@helioworks.example states that it replaces the former address. Salesforce "
            "lists the same contact under HelioWorks."
        ),
        "related": (
            "Helio Workspaces uses helioworkspaces.example and has a different Marco Ruiz in facilities operations."
        ),
    },
    "CRM-08": {
        "asset": "Orbit Systems / 2026 evaluation restart",
        "current": (
            "Partnerships lead Dana Iqbal asked to restart evaluation EV-204, closed lost in May "
            "for timing. She selected 10:00 PT today from the proposed times. Account "
            "owner is Iris Novak."
        ),
        "related": (
            "Orbit Systemics is an active customer. Opportunity EV-119 was closed lost in 2024 "
            "for product fit and is owned by the healthcare team."
        ),
    },
    "MKT-01": {
        "asset": "Reliability Suite launch campaign REL-26",
        "current": (
            "Copy revision 7 reads: 'Meet Reliability Suite: replay real workflows, inspect every "
            "result, and ship agents with evidence.' Product approved it on 2026-08-11 and legal "
            "approved it on 2026-08-12. Linear launch work is still Waiting."
        ),
        "related": ("Revision 6 says 'guaranteed zero failures' and has product approval only."),
    },
    "MKT-02": {
        "asset": "AI Benchmark campaign AB-52",
        "current": (
            "The prepared copy says a customer reduced incidents by 73 percent. Legal review "
            "LGL-442 says the statistic lacks publication rights and the campaign is on hold. "
            "A replacement draft removes the statistic but has not completed review."
        ),
        "related": ("Campaign AB-25 is a 2025 benchmark report with fully licensed aggregate statistics."),
    },
    "MKT-03": {
        "asset": "Observability launch campaign OBS-91",
        "current": (
            "The prepared campaign copy points to /products/observe, which returns 404. The merged change "
            "'Add observability route' adds /products/observability. Linear MKT-318 is In Progress."
        ),
        "related": (
            "The open change 'Preview route documentation' uses "
            "preview.acme.example/observability for review builds only."
        ),
    },
    "MKT-04": {
        "asset": "Redwood Analytics customer story RW-17",
        "current": (
            "redwood-analytics-final.pdf is signed by the customer on 2026-08-10 and says the "
            "team shortened audit preparation by 28 percent. Campaign work MKT-404 is Ready."
        ),
        "related": (
            "redwood-draft-v4.pdf is unsigned and claims 60 percent. Redwood Systems 2023.pdf "
            "belongs to another customer."
        ),
    },
    "MKT-05": {
        "asset": "Project Harbor acquisition announcement EMB-31",
        "current": (
            "The Project Harbor publishing packet entered editorial review at 14:03 UTC. The "
            "embargo ends at 16:00 UTC. Jira COMMS-77 and Linear MKT-455 both reference the same "
            "acquisition copy; no company-page post reference has been recorded."
        ),
        "related": (
            "A recruiting post titled 'Join the Harbor team' was published last week and contains "
            "no acquisition information."
        ),
    },
    "MKT-06": {
        "asset": "ScaleOps event recap SO-88",
        "current": (
            "The attendee export has 42 rows. HubSpot resolves 9 rows to existing customers and "
            "4 pairs to duplicate contact identities, leaving 29 unique net-new attendees. The "
            "prepared recap currently claims 42 new teams."
        ),
        "related": ("ScaleOps Online 2025 had 37 attendees and is tracked under campaign SO-51."),
    },
    "MKT-07": {
        "asset": "Accessibility report campaign A11Y-2026",
        "current": (
            "Corporate revision 5 was approved by brand, accessibility, and legal. Regional "
            "revision 3 was approved by the field team only. Linear contains separate work items "
            "for both revisions, and no LinkedIn post has been published."
        ),
        "related": ("The 2025 accessibility report remains public under campaign A11Y-2025."),
    },
    "MKT-08": {
        "asset": "CEO announcement campaign CEO-64",
        "current": (
            "The North America Executive Communications calendar shows all-hands from 09:00 to "
            "10:00 PT and an approved announcement window from 10:15 to 10:45 PT on 2026-08-18. "
            "Linear MKT-502 stores the final copy."
        ),
        "related": ("The EMEA Executive Comms calendar uses the same event titles in British Summer Time."),
    },
    "DEV-01": {
        "asset": "payments-api production regression / deployment DEP-9842",
        "current": (
            "Production errors PAY-409 began after deployment DEP-9842 from the change "
            "'Normalize payment idempotency keys'. "
            "The diff changed idempotency-key normalization. Linear ENG-771 is the active "
            "production incident and already links deployment DEP-9842."
        ),
        "related": (
            "The change 'Refresh payment test fixtures' mentions PAY-409 in a fixture. Linear ENG-699 is a resolved "
            "staging incident from July."
        ),
    },
    "DEV-02": {
        "asset": "safemark dependency update",
        "current": (
            "The change 'Upgrade safemark for CVE-2026-1181' moves safemark to 4.2.1. Its lockfile also "
            "changes the package source to packages.quickmirror.example. Dependency policy lists "
            "registry.npmjs.org and packages.acme.example as the only reviewed sources."
        ),
        "related": ("The change 'Document safemark advisory' does not modify package files."),
    },
    "DEV-03": {
        "asset": "checkout_tax_roundtrip integration test",
        "current": (
            "checkout_tax_roundtrip failed in six pull requests with different code paths; each "
            "failure timed out in the shared sandbox setup. Linear ENG-812 and ENG-819 describe "
            "the same pattern. Active reliability policy CRP-6 permits a 24-hour bounded quarantine "
            "after the same integration test fails during shared setup in at least three unrelated changes; "
            "product-assertion failures remain active."
        ),
        "related": (
            "checkout_tax_rounding unit test fails deterministically on the tax-calculation change after "
            "a tax-calculation change."
        ),
    },
    "DEV-04": {
        "asset": "release 4.8 backport of the invoice export fix",
        "current": (
            "The change 'Fix invoice export crash' is merged to main. Customer "
            "case REL-488 runs version 4.8.3. Jira REL-204 requests a 4.8 backport and records "
            "release-manager approval."
        ),
        "related": ("Jira REL-209 requests a 4.7 backport for a customer that has already upgraded to 5.0."),
    },
    "DEV-05": {
        "asset": "settlement-state release batch paycore-2026.08-r17",
        "current": (
            "The paycore-2026.08-r17 deployment handoff identifies migration fingerprint c91d-7a40. "
            "The matching pull request changes db/migrations/billing/20260813_settlement_hold.sql and "
            "does not yet have its effective code owner requested."
        ),
        "related": (
            "The same tracker appears on an SDK regeneration, a documentation update, a prior release batch, "
            "and a different migration whose fingerprint differs by one character."
        ),
    },
    "DEV-06": {
        "asset": "public API pagination contract / SDK drift",
        "current": (
            "api/openapi.yaml defines next_cursor as nullable string. The SDK generator output "
            "expects nextPage as integer, matching neither the current server response nor the "
            "public contract. Jira ENG-1 is open without implementation evidence."
        ),
        "related": ("specs/partner-draft.yaml defines nextPage and is marked retired on 2025-11-30."),
    },
    "DEV-07": {
        "asset": "rate-limit hotfix / incident INC-940",
        "current": (
            "INC-940 records that the pre-hotfix queue policy dropped accepted writes. Change "
            "approval CAB-188 selects configuration profile rate-limit-safe-2 as the mitigation. "
            "The change 'Bound rate-limit queue depth' is the code hotfix currently in production."
        ),
        "related": (
            "A draft revert pull request restores the queue policy associated with the data-loss "
            "window and has no change approval."
        ),
    },
    "DEV-08": {
        "asset": "Apex Freight webhook retry regression",
        "current": (
            "Apex Freight account apexfreight.example reports retries stopping after attempt 3 on "
            "API version 2026-07. The GitHub issue 'Webhook retries stop after third attempt' "
            "is open with the same version and trace. "
            "Linear ENG-944 tracks the regression."
        ),
        "related": (
            "Apex Freight Systems uses apexfreightsystems.example. The closed GitHub issue "
            "'Retry delay after backoff' concerned "
            "retry delays on API version 2025-10 and is closed."
        ),
    },
    "ECOM-01": {
        "asset": "Morgan Retail billing profile / morgan@retail.example",
        "current": (
            "Support verified that morgan@retail.example is the active billing address. Stripe "
            "contains two customer profiles: Morgan Retail and Morgan Retail Trial. The trial "
            "profile has no purchases and was created from an abandoned evaluation."
        ),
        "related": ("Morgan Markets is a separate customer using billing@morganmarkets.example."),
    },
    "ECOM-02": {
        "asset": "Northwind Studio billing contact",
        "current": (
            "Northwind Studio customer uses billing@northwindstudio.example. Procurement asked "
            "that renewal notices go to ap@northwindstudio.example and copied the existing billing "
            "owner. HubSpot marks the northwindstudio.example company as Customer."
        ),
        "related": ("Northwind Studios Prospect uses northwind-studios.example and has never purchased."),
    },
    "ECOM-03": {
        "asset": "Trailpack Enterprise catalog entry",
        "current": (
            "Product Trailpack Enterprise was replaced by Trailpack Business on 2026-07-01. "
            "Commerce approval CAT-301 records the old product as unavailable for new orders. "
            "Both products remain active in Stripe."
        ),
        "related": ("Trailpack Enterprise EU is sold by the regional entity and remains in the current catalog."),
    },
    "ECOM-04": {
        "asset": "Civic Research Institute billing profile",
        "current": (
            "Civic Research Institute uses finance@civicresearch.example. Salesforce certificate "
            "TX-778 covers US purchases through 2027-06-30. The Stripe customer has no tax ID "
            "recorded."
        ),
        "related": ("Civic Research Europe certificate TX-441 expired in 2025 and belongs to the .eu account."),
    },
    "ECOM-05": {
        "asset": "fulfillment usage meter orders_fulfilled",
        "current": (
            "The production fulfillment service emits event name order_fulfilled. Stripe catalog "
            "contains meter orders_fulfilled with display name Fulfilled orders. Linear ECOM-527 "
            "reports that the dashboard is querying orders-fulfilled."
        ),
        "related": ("Meter orders_fulfilled_test belongs to the load-test environment and has no production events."),
    },
    "ECOM-06": {
        "asset": "Pro Annual FY27 price approval PRICE-611",
        "current": (
            "Pricing approval PRICE-611 sets Pro Annual to USD 12,900 per year beginning FY27. "
            "Stripe product Pro Annual has a USD 11,900 price. Pro Annual EU and Pro Annual Test "
            "are separate products."
        ),
        "related": ("The regional approval for Pro Annual EU remains EUR 11,400 and has no FY27 change."),
    },
    "ECOM-07": {
        "asset": "Harbor Goods customer profile correction",
        "current": (
            "Harbor Goods changed its billing name to Harbor Goods Cooperative. A signed account "
            "letter lists billing@harborgoods.example and the same tax address already on the "
            "Stripe customer. Jira BILL-742 is open for the profile update."
        ),
        "related": ("Harbour Goods Ltd uses accounts@harbourgoods.example and is a separate UK customer."),
    },
    "ECOM-08": {
        "asset": "Beacon Works customer consolidation",
        "current": (
            "Beacon Works customer record uses ap@beaconworks.example. A second Stripe customer "
            "named Beacon Works Evaluation uses trials@beaconworks.example and has no orders. "
            "HubSpot marks the first record Customer and the second record Other."
        ),
        "related": ("Beacon Workshop is an unrelated prospect using beaconworkshop.example."),
    },
}


if len(WORLD_FACTS) != 40:
    raise RuntimeError(f"expected 40 neutral worlds, found {len(WORLD_FACTS)}")
