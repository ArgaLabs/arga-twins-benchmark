"""Provider seed builders for neutral Cross-Functional 40 environments."""

from __future__ import annotations

import re
from typing import Any

from cross_functional_40_worlds import WORLD_FACTS

WORKFLOW_POLICIES: dict[str, tuple[str, str]] = {
    "CRM-02": (
        "gmail",
        "Customer-facing opportunity follow-up requires a proposed confirmation reviewed by the account owner "
        "before anything is sent.",
    ),
    "CRM-03": (
        "gmail",
        "Qualified inbound demo requests require a proposed customer confirmation reviewed by the opportunity "
        "owner before anything is sent.",
    ),
    "CRM-05": (
        "gmail",
        "Lifecycle follow-up requires the campaign owner to review both the recipient cohort and customer-facing "
        "confirmation before release; reconciliation never sends messages.",
    ),
    "CRM-08": (
        "jira",
        "Once a prospect selects a proposed time, partnerships reserves the slot internally without external "
        "attendees until the account owner approves invitations.",
    ),
    "MKT-08": (
        "linear",
        "Executive announcement work is not ready until the approved regional window is reserved internally; "
        "publication remains blocked until final copy and identity approvals are complete.",
    ),
    "ECOM-02": (
        "gmail",
        "Billing-contact and tax-status changes require a customer confirmation reviewed by the account owner "
        "before sending.",
    ),
    "ECOM-04": (
        "gmail",
        "Billing-contact and tax-status changes require a customer confirmation reviewed by the account owner "
        "before sending.",
    ),
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _sentences(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", value.strip()) if item.strip()]


def _provider_fact(task_spec: dict[str, Any], provider: str, key: str) -> str:
    """Distribute evidence across providers so correlation remains necessary."""
    providers = sorted(name for name in task_spec["twins"] if name not in {"slack", "linkedin"})
    parts = _sentences(WORLD_FACTS[task_spec["id"]][key])
    if not parts:
        return ""
    index = providers.index(provider) if provider in providers else 0
    selected = parts[index :: max(1, len(providers))]
    return " ".join(selected) or parts[index % len(parts)]


def _add_workflow_policy(task_spec: dict[str, Any], provider: str, seed: dict[str, Any]) -> None:
    configured = WORKFLOW_POLICIES.get(task_spec["id"])
    if configured is None or configured[0] != provider:
        return
    policy = configured[1]
    if provider == "gmail":
        seed["messages"].append(
            {
                "subject": "Customer communication review policy",
                "body": policy,
                "from": "operations-policy@acme.example",
                "to": ["owner@gmail-twin.local"],
                "labels": ["INBOX"],
                "thread_id": f"thread-policy-{_slug(task_spec['title'])}",
            }
        )
    elif provider == "jira":
        seed["projects"][0]["issues"][0].setdefault("comments", []).append({"body": f"Operating policy: {policy}"})
    elif provider == "linear":
        issue = seed["issues"][0]
        issue["description"] = f"{issue['description']}\n\nOperating policy: {policy}"
    else:
        raise ValueError(f"Unsupported workflow-policy provider {provider!r}")


def slack_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    reporter_name = task_spec["reporter"].split(" from ", 1)[0].split(" on the ", 1)[0]
    reporter = _slug(reporter_name)[:24]
    first_paragraph = task_spec["prompt"].split("\n\n", 1)[0]
    report_text = first_paragraph.split(" Slack channel: ", 1)[-1]
    return {
        "users": [
            {"name": reporter, "real_name": reporter_name},
            {"name": "operations-coordinator", "real_name": "Operations Coordinator"},
        ],
        "channels": [
            {
                "name": task_spec["channel"],
                "messages": [
                    {"user": reporter, "text": report_text},
                    {
                        "user": "operations-coordinator",
                        "text": "The records system completed its nightly archival at 02:00 UTC.",
                    },
                ],
            },
            {
                "name": "company-updates",
                "messages": [
                    {
                        "user": "operations-coordinator",
                        "text": "Facilities testing will briefly interrupt the fourth-floor guest network at 18:30.",
                    }
                ],
            },
        ],
    }


def jira_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    project_key = {
        "it_support": "IT",
        "crm_outreach": "GTM",
        "marketing": "MKT",
        "developer": "ENG",
        "ecommerce": "COM",
    }[task_spec["domain"]]
    world = WORLD_FACTS[task_spec["id"]]
    return {
        "projects": [
            {
                "key": project_key,
                "name": f"{task_spec['domain'].replace('_', ' ').title()} Operations",
                "issues": [
                    {
                        "summary": task_spec["title"],
                        "description": f"Record: {world['asset']}\n{_provider_fact(task_spec, 'jira', 'current')}",
                        "issuetype": "Bug" if task_spec["domain"] in {"it_support", "developer"} else "Task",
                        "priority": "High",
                        "status": "In Progress",
                        "labels": [_slug(task_spec["domain"])],
                        "comments": [
                            {"body": "Initial report received; investigation notes have not yet been reconciled."}
                        ],
                    },
                    {
                        "summary": f"Earlier review: {task_spec['title'].lower()}",
                        "description": _provider_fact(task_spec, "jira", "related"),
                        "issuetype": "Task",
                        "priority": "Medium",
                        "status": "Done",
                        "labels": ["operations-history"],
                    },
                ],
            }
        ]
    }


GITHUB_CHANGE_TITLES: dict[str, tuple[str, str]] = {
    "MKT-03": ("Add observability route", "Preview route documentation"),
    "DEV-01": ("Normalize payment idempotency keys", "Refresh payment test fixtures"),
    "DEV-02": ("Upgrade safemark for CVE-2026-1181", "Document safemark advisory"),
    "DEV-04": ("Fix invoice export crash", "Prepare legacy release notes"),
    "DEV-05": ("Add billing settlement state", "Update billing migration guide"),
    "DEV-07": ("Bound rate-limit queue depth", "Revert rate-limit hotfix"),
}


GITHUB_ISSUE_TITLES: dict[str, tuple[str, str]] = {
    "DEV-08": ("Webhook retries stop after third attempt", "Retry delay after backoff"),
}


def github_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    repository = {
        "it_support": "internal-operations",
        "crm_outreach": "revenue-systems",
        "marketing": "web-campaigns",
        "developer": "platform-services",
        "ecommerce": "commerce-platform",
    }[task_spec["domain"]]
    current = _provider_fact(task_spec, "github", "current")
    related = _provider_fact(task_spec, "github", "related")
    change_titles = GITHUB_CHANGE_TITLES.get(
        task_spec["id"],
        (f"Change associated with {task_spec['title'].lower()}", "Documentation maintenance"),
    )
    issue_titles = GITHUB_ISSUE_TITLES.get(
        task_spec["id"],
        (task_spec["title"], "Quarterly maintenance follow-up"),
    )
    return {
        "users": [
            {"login": "ops-maintainer", "name": "Operations Maintainer", "email": "ops-maintainer@acme.example"},
            {"login": "release-reviewer", "name": "Release Reviewer", "email": "release-reviewer@acme.example"},
        ],
        "orgs": [{"login": "acme", "name": "Acme"}],
        "repos": [
            {
                "owner": "acme",
                "name": repository,
                "description": "Operational service configuration and change history",
                "private": True,
                "default_branch": "main",
                "files": [
                    {
                        "path": f"records/{_slug(task_spec['title'])}.md",
                        "content": f"# {world['asset']}\n\n{current}\n",
                    },
                    {"path": "records/previous-quarter.md", "content": f"# Previous-quarter notes\n\n{related}\n"},
                    {"path": "README.md", "content": "# Operational service records\n"},
                ],
                "issues": [
                    {"title": issue_titles[0], "body": f"Asset: {world['asset']}\n\n{current}"},
                    {"title": issue_titles[1], "body": related, "state": "closed"},
                ],
                "prs": [
                    {
                        "title": change_titles[0],
                        "body": current,
                        "head": f"change/{_slug(task_spec['title'])}",
                        "base": "main",
                        "state": "merged"
                        if task_spec["id"] in {"IT-02", "IT-05", "IT-07", "DEV-01", "DEV-04", "DEV-07", "MKT-03"}
                        else "open",
                        "merged": task_spec["id"]
                        in {"IT-02", "IT-05", "IT-07", "DEV-01", "DEV-04", "DEV-07", "MKT-03"},
                        "files": [{"path": "config/change.txt", "content": current}],
                    },
                    {
                        "title": change_titles[1],
                        "body": related,
                        "head": "docs/maintenance",
                        "base": "main",
                        "state": "open",
                        "files": [{"path": "docs/maintenance.md", "content": related}],
                    },
                ],
            }
        ],
    }


def linear_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    team_key = {"it_support": "OPS", "crm_outreach": "GTM", "marketing": "MKT", "developer": "ENG", "ecommerce": "COM"}[
        task_spec["domain"]
    ]
    return {
        "teams": [{"key": team_key, "name": f"{task_spec['domain'].replace('_', ' ').title()} Team"}],
        "projects": [{"team_key": team_key, "name": "Current operations", "state": "started"}],
        "issues": [
            {
                "team_key": team_key,
                "project": "Current operations",
                "title": task_spec["title"],
                "description": f"{world['asset']}\n\n{_provider_fact(task_spec, 'linear', 'current')}",
                "priority": 2,
            },
            {
                "team_key": team_key,
                "project": "Current operations",
                "title": "Previous-quarter follow-up",
                "description": _provider_fact(task_spec, "linear", "related"),
                "priority": 4,
            },
        ],
        "comments": [],
    }


def notion_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    return {
        "pages": [
            {
                "title": f"Operations record — {world['asset']}",
                "content": _provider_fact(task_spec, "notion", "current"),
            },
            {"title": "Previous-quarter operations record", "content": _provider_fact(task_spec, "notion", "related")},
        ],
        "databases": [],
    }


def gmail_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    return {
        "labels": [{"name": "Operations"}],
        "messages": [
            {
                "subject": task_spec["title"],
                "body": f"Regarding {world['asset']}:\n\n{_provider_fact(task_spec, 'gmail', 'current')}",
                "from": "records@acme.example",
                "to": ["owner@gmail-twin.local"],
                "labels": ["INBOX", "Operations"],
                "thread_id": f"thread-{_slug(task_spec['title'])}",
            },
            {
                "subject": "Previous account correspondence",
                "body": _provider_fact(task_spec, "gmail", "related"),
                "from": "archive@acme.example",
                "to": ["owner@gmail-twin.local"],
                "labels": ["INBOX"],
                "thread_id": "thread-previous-account-correspondence",
            },
        ],
        "drafts": [],
    }


def drive_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    return {
        "folders": [
            {
                "name": "Operations records",
                "files": [
                    {
                        "name": f"{_slug(task_spec['title'])}.txt",
                        "content": f"{world['asset']}\n\n{_provider_fact(task_spec, 'google_drive', 'current')}",
                        "mime_type": "text/plain",
                    },
                    {
                        "name": "previous-quarter-record.txt",
                        "content": _provider_fact(task_spec, "google_drive", "related"),
                        "mime_type": "text/plain",
                    },
                ],
            }
        ]
    }


def calendar_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    return {
        "calendars": [
            {
                "name": "Operations — North America",
                "events": [
                    {
                        "summary": task_spec["title"],
                        "start": "2026-08-14T17:15:00Z",
                        "end": "2026-08-14T17:45:00Z",
                        "description": f"{world['asset']}\n{_provider_fact(task_spec, 'google_calendar', 'current')}",
                    }
                ],
            },
            {
                "name": "Operations — Europe",
                "events": [
                    {
                        "summary": "Regional communications window",
                        "start": "2026-08-14T09:00:00Z",
                        "end": "2026-08-14T09:30:00Z",
                        "description": _provider_fact(task_spec, "google_calendar", "related"),
                    }
                ],
            },
        ]
    }


CRM_STRUCTURED: dict[str, dict[str, str]] = {
    "CRM-01": {
        "company": "Northstar Robotics",
        "domain": "northstar-robotics.example",
        "email": "buyer@northstar-robotics.example",
        "deal": "NSR Expansion",
        "stage": "appointmentscheduled",
        "related_company": "Northstar Labs",
        "related_domain": "northstarlabs.example",
    },
    "CRM-02": {
        "company": "Alder Bank",
        "domain": "alderbank.example",
        "email": "renee.cho@alderbank.example",
        "deal": "Alder Bank Expansion",
        "stage": "contractsent",
        "related_company": "Alder Credit Union",
        "related_domain": "aldercu.example",
    },
    "CRM-03": {
        "company": "Driftline Logistics — Platform",
        "domain": "platform.driftline.example",
        "email": "nia.ford@platform.driftline.example",
        "deal": "Platform Evaluation",
        "stage": "qualifiedtobuy",
        "related_company": "Driftline Freight Brokerage",
        "related_domain": "driftline.example",
    },
    "CRM-04": {
        "company": "Cedar Health US",
        "domain": "cedarhealth.example",
        "email": "asha.reed@cedarhealth.example",
        "deal": "FY27 Renewal",
        "stage": "contractsent",
        "related_company": "Cedar Health Canada",
        "related_domain": "cedarhealth.ca",
    },
    "CRM-05": {
        "company": "FinWorks",
        "domain": "finworks.example",
        "email": "mei@finworks.example",
        "deal": "FinOps webinar follow-up",
        "stage": "qualifiedtobuy",
        "related_company": "ExistingCo",
        "related_domain": "existingco.example",
    },
    "CRM-06": {
        "company": "BluePeak Energy",
        "domain": "bluepeakenergy.example",
        "email": "procurement@bluepeakenergy.example",
        "deal": "BluePeak Expansion",
        "stage": "appointmentscheduled",
        "related_company": "BluePeak Solar",
        "related_domain": "bluepeaksolar.example",
    },
    "CRM-07": {
        "company": "HelioWorks",
        "domain": "helioworks.example",
        "email": "marco.ruiz@helioworks.example",
        "deal": "HelioWorks Renewal",
        "stage": "contractsent",
        "related_company": "Helio Workspaces",
        "related_domain": "helioworkspaces.example",
    },
    "CRM-08": {
        "company": "Orbit Systems",
        "domain": "orbitsystems.example",
        "email": "dana.iqbal@orbitsystems.example",
        "deal": "Evaluation EV-204",
        "stage": "closedlost",
        "related_company": "Orbit Systemics",
        "related_domain": "orbitsystemics.example",
    },
    "DEV-08": {
        "company": "Apex Freight",
        "domain": "apexfreight.example",
        "email": "engineering@apexfreight.example",
        "deal": "Apex Freight Renewal",
        "stage": "contractsent",
        "related_company": "Apex Freight Systems",
        "related_domain": "apexfreightsystems.example",
    },
    "ECOM-02": {
        "company": "Northwind Studio",
        "domain": "northwindstudio.example",
        "email": "billing@northwindstudio.example",
        "deal": "Northwind annual plan",
        "stage": "closedwon",
        "related_company": "Northwind Studios Prospect",
        "related_domain": "northwind-studios.example",
    },
    "ECOM-08": {
        "company": "Beacon Works",
        "domain": "beaconworks.example",
        "email": "ap@beaconworks.example",
        "deal": "Beacon Works customer",
        "stage": "closedwon",
        "related_company": "Beacon Workshop",
        "related_domain": "beaconworkshop.example",
    },
}


def hubspot_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    item = CRM_STRUCTURED.get(
        task_spec["id"],
        {
            "company": world["asset"].split(" / ", 1)[0],
            "domain": f"{_slug(task_spec['title'])}.example",
            "email": f"contact@{_slug(task_spec['title'])}.example",
            "deal": task_spec["title"],
            "stage": "appointmentscheduled",
        },
    )
    related_domain = item.get("related_domain", f"{_slug(item['company'])}-services.example")
    related_company = item.get("related_company", f"{item['company']} Services")
    return {
        "contacts": [
            {
                "properties": {
                    "email": item["email"],
                    "firstname": item["company"].split()[0],
                    "lastname": "Contact",
                    "lifecyclestage": "customer" if task_spec["id"].startswith("ECOM") else "lead",
                    "notes": _provider_fact(task_spec, "hubspot", "current"),
                }
            },
            {
                "properties": {
                    "email": f"contact@{related_domain}",
                    "firstname": "Regional",
                    "lastname": "Contact",
                    "lifecyclestage": "lead",
                    "notes": _provider_fact(task_spec, "hubspot", "related"),
                }
            },
        ],
        "companies": [
            {
                "properties": {
                    "name": item["company"],
                    "domain": item["domain"],
                    "description": _provider_fact(task_spec, "hubspot", "current"),
                }
            },
            {
                "properties": {
                    "name": related_company,
                    "domain": related_domain,
                    "description": _provider_fact(task_spec, "hubspot", "related"),
                }
            },
        ],
        "deals": [
            {
                "properties": {
                    "dealname": item["deal"],
                    "amount": "120000",
                    "dealstage": item["stage"],
                    "pipeline": "default",
                    "description": world["asset"],
                }
            }
        ],
        "tickets": [],
        "associations": [{"contact_email": item["email"], "company_domain": item["domain"]}],
        "lists": [],
    }


def salesforce_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    world = WORLD_FACTS[task_spec["id"]]
    item = CRM_STRUCTURED.get(
        task_spec["id"],
        {
            "company": world["asset"].split(" / ", 1)[0],
            "domain": f"{_slug(task_spec['title'])}.example",
            "email": f"contact@{_slug(task_spec['title'])}.example",
            "deal": task_spec["title"],
            "stage": "Qualification",
        },
    )
    stage = {"closedlost": "Closed Lost", "closedwon": "Closed Won", "contractsent": "Negotiation/Review"}.get(
        item["stage"], "Qualification"
    )
    related_domain = item.get("related_domain", f"regional-{item['domain']}")
    related_company = item.get("related_company", f"{item['company']} Regional")
    return {
        "accounts": [
            {
                "Name": item["company"],
                "Website": f"https://{item['domain']}",
                "Description": _provider_fact(task_spec, "salesforce", "current"),
            },
            {
                "Name": related_company,
                "Website": f"https://{related_domain}",
                "Description": _provider_fact(task_spec, "salesforce", "related"),
            },
        ],
        "contacts": [
            {"FirstName": "Primary", "LastName": "Contact", "Email": item["email"], "Description": world["asset"]}
        ],
        "records": [
            {
                "object": "Opportunity",
                "values": {
                    "Name": item["deal"],
                    "StageName": stage,
                    "Amount": 120000,
                    "CloseDate": "2026-10-31",
                    "Description": _provider_fact(task_spec, "salesforce", "current"),
                },
            },
            {
                "object": "Case",
                "values": {
                    "Subject": task_spec["title"],
                    "Status": "New",
                    "Priority": "High",
                    "Description": world["asset"],
                },
            },
        ],
    }


def linkedin_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "members": [
            {
                "name": "Acme Marketing",
                "headline": "Company communications",
                "email": "marketing@acme.example",
                "vanity_name": "acme-marketing",
            },
            {
                "name": "Acme Recruiting",
                "headline": "Careers at Acme",
                "email": "recruiting@acme.example",
                "vanity_name": "acme-recruiting",
            },
        ],
        "posts": [
            {
                "author_email": "recruiting@acme.example",
                "text": (
                    "We are hiring product engineers in Seattle and Toronto. See the Acme careers page for details."
                ),
                "visibility": "PUBLIC",
            }
        ],
    }


STRIPE_STRUCTURED: dict[str, dict[str, Any]] = {
    "ECOM-01": {
        "customers": [
            ("Morgan Retail", "morgan@retail.example"),
            ("Morgan Retail Trial", "morgan+trial@retail.example"),
            ("Morgan Markets", "billing@morganmarkets.example"),
        ],
        "products": [("Retail Standard", 9900)],
    },
    "ECOM-02": {
        "customers": [
            ("Northwind Studio", "billing@northwindstudio.example"),
            ("Northwind Studios Prospect", "hello@northwind-studios.example"),
        ],
        "products": [("Studio Annual", 149000)],
    },
    "ECOM-03": {
        "customers": [("Catalog Operations", "catalog@acme.example")],
        "products": [
            ("Trailpack Enterprise", 18900),
            ("Trailpack Business", 19900),
            ("Trailpack Enterprise EU", 17400),
        ],
    },
    "ECOM-04": {
        "customers": [
            ("Civic Research Institute", "finance@civicresearch.example"),
            ("Civic Research Europe", "finance@civicresearch.eu"),
        ],
        "products": [("Research Annual", 88000)],
    },
    "ECOM-05": {
        "customers": [("Fulfillment Operations", "fulfillment@acme.example")],
        "products": [("Order processing", 100)],
        "meters": [("Fulfilled orders", "orders_fulfilled"), ("Fulfilled orders test", "orders_fulfilled_test")],
    },
    "ECOM-06": {
        "customers": [("Pricing Operations", "pricing@acme.example")],
        "products": [
            ("Pro Annual", 1190000, "usd"),
            ("Pro Annual EU", 1140000, "eur"),
            ("Pro Annual Test", 100, "usd"),
        ],
    },
    "ECOM-07": {
        "customers": [
            ("Harbor Goods", "billing@harborgoods.example"),
            ("Harbour Goods Ltd", "accounts@harbourgoods.example"),
        ],
        "products": [("Business Annual", 79000)],
    },
    "ECOM-08": {
        "customers": [
            ("Beacon Works", "ap@beaconworks.example"),
            ("Beacon Works Evaluation", "trials@beaconworks.example"),
            ("Beacon Workshop", "hello@beaconworkshop.example"),
        ],
        "products": [("Team Annual", 99000)],
    },
}


def stripe_seed(task_spec: dict[str, Any]) -> dict[str, Any]:
    item = STRIPE_STRUCTURED[task_spec["id"]]

    def product_seed(product: tuple[Any, ...]) -> dict[str, Any]:
        name, amount, *currency = product
        return {
            "name": name,
            "prices": [{"unit_amount": amount, "currency": currency[0] if currency else "usd"}],
        }

    return {
        "customers": [{"name": name, "email": email} for name, email in item["customers"]],
        "products": [product_seed(product) for product in item["products"]],
        "meters": [
            {"display_name": display_name, "event_name": event_name, "aggregation": "sum", "events": []}
            for display_name, event_name in item.get("meters", [])
        ],
    }


def _add_issue(seed: dict[str, Any], issue: dict[str, Any]) -> None:
    seed["projects"][0]["issues"].append(issue)


def _it01_gmail() -> dict[str, Any]:
    return {
        "labels": [{"name": "Security Review"}],
        "messages": [
            {
                "subject": "Re: August supplier rate card",
                "body": (
                    "Authentication-Results: spf=fail; dkim=none\n"
                    "Return-Path: billing@nova-supplies.co\n\n"
                    "Attached archive: rate-card-aug.zip\nSHA-256: 31d8c9f0"
                ),
                "from": "sourcing@novasupplies.co",
                "to": ["owner@gmail-twin.local"],
                "labels": ["INBOX", "Security Review"],
                "thread_id": "thread-sourcing-447",
            },
            {
                "subject": "Re: July supplier rate card",
                "body": (
                    "Authentication-Results: spf=pass; dkim=pass\n"
                    "Return-Path: sourcing@novasupplies.co\n\n"
                    "Attached document: rate-card-jul.pdf\nSHA-256: 8a4e7712"
                ),
                "from": "sourcing@novasupplies.co",
                "to": ["owner@gmail-twin.local"],
                "labels": ["INBOX"],
                "thread_id": "thread-sourcing-july",
            },
        ],
        "drafts": [],
    }


def _it05_jira(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = jira_seed(task_spec)
    for reporter, detail in (
        ("Morgan Price", "SecureLink 6.4.2 disconnects after approximately four minutes."),
        ("Dae Kim", "SecureLink 6.4.2 keepalive timeout while gateway health remains green."),
        ("Luca Moretti", "Disconnect began after updating from 6.4.1 to 6.4.2."),
    ):
        _add_issue(
            seed,
            {
                "summary": f"VPN disconnect report from {reporter}",
                "description": detail,
                "issuetype": "Bug",
                "priority": "Medium",
                "status": "To Do",
                "labels": ["vpn", "support-intake"],
            },
        )
    return seed


def _it07_jira(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = jira_seed(task_spec)
    for minute in ("08:01", "08:02", "08:05"):
        _add_issue(
            seed,
            {
                "summary": f"checkout-api latency alert at {minute} UTC",
                "description": "Route label is empty; request volume and error rate are normal.",
                "issuetype": "Bug",
                "priority": "Medium",
                "status": "To Do",
                "labels": ["checkout", "latency"],
            },
        )
    _add_issue(
        seed,
        {
            "summary": "checkout database saturation DB-912",
            "description": "08:04 UTC: database connection usage reached 96 percent; route label is populated.",
            "issuetype": "Bug",
            "priority": "Highest",
            "status": "In Progress",
            "labels": ["checkout", "database"],
        },
    )
    return seed


def _it07_linear(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = linear_seed(task_spec)
    seed["issues"].extend(
        [
            {
                "team_key": "OPS",
                "project": "Current operations",
                "title": "Empty-route latency notifications",
                "description": "Alert samples at 08:01, 08:02, and 08:05 UTC; traffic remained healthy.",
                "priority": 3,
            },
            {
                "team_key": "OPS",
                "project": "Current operations",
                "title": "Database saturation DB-912",
                "description": "Connection usage reached 96 percent at 08:04 UTC.",
                "priority": 1,
            },
        ]
    )
    return seed


def _crm01_hubspot(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = hubspot_seed(task_spec)
    seed["companies"].insert(
        1,
        {
            "properties": {
                "name": "Northstar Robotics — Demo Request",
                "domain": "demo.northstar-robotics.example",
                "description": "Created from the August enterprise demo form; no sales activity is attached.",
            }
        },
    )
    return seed


def _crm05_contacts() -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    for index in range(1, 30):
        if index == 1:
            first_name, last_name, email = "Mei", "Park", "mei@finworks.example"
        elif index <= 5:
            first_name, last_name = f"Duplicate{index - 1}", "Attendee"
            email = f"person{index - 1}@company{index - 1}.example"
        else:
            first_name, last_name = f"Attendee{index:02d}", "Lead"
            email = f"attendee{index:02d}@growth{index:02d}.example"
        contacts.append(
            {
                "properties": {
                    "email": email,
                    "firstname": first_name,
                    "lastname": last_name,
                    "lifecyclestage": "lead",
                    "event_status": "attended",
                    "event_intent": "high",
                }
            }
        )
    contacts.extend(
        {
            "properties": {
                "email": f"customer{index:02d}@customer{index:02d}.example",
                "firstname": f"Customer{index:02d}",
                "lastname": "Attendee",
                "lifecyclestage": "customer",
                "event_status": "attended",
            }
        }
        for index in range(1, 10)
    )
    contacts.extend(
        [
            {
                "properties": {
                    "email": f"person{index}@mail.example",
                    "firstname": f"Duplicate{index}",
                    "lastname": "Attendee",
                    "lifecyclestage": "lead",
                    "event_status": "attended",
                    "corporate_email": f"person{index}@company{index}.example",
                }
            }
            for index in range(1, 5)
        ]
    )
    return contacts


def _crm05_hubspot(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = hubspot_seed(task_spec)
    seed["contacts"] = _crm05_contacts()
    seed["companies"] = [
        {
            "properties": {
                "name": "FinOps webinar",
                "domain": "finops-webinar.example",
                "description": "Attendance imported from the 2026-08-07 event registration export.",
            }
        }
    ]
    seed["associations"] = []
    return seed


def _crm05_gmail(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = gmail_seed(task_spec)
    seed["messages"][0]["body"] = (
        "FinOps webinar attendance export: 42 rows.\n"
        "29 unique non-customer attendees recorded high intent.\n"
        "9 attendees are current customers.\n"
        "4 personal-email rows match existing corporate contacts.\n"
        "No-shows: Kira Long and Trent Bell."
    )
    return seed


def _crm05_salesforce(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = salesforce_seed(task_spec)
    seed["contacts"] = [
        {
            "FirstName": contact["properties"]["firstname"],
            "LastName": contact["properties"]["lastname"],
            "Email": contact["properties"]["email"],
            "Description": (
                "Current customer"
                if contact["properties"].get("lifecyclestage") == "customer"
                else "FinOps webinar contact"
            ),
        }
        for contact in _crm05_contacts()
        if not contact["properties"]["email"].endswith("@mail.example")
    ]
    return seed


def _crm_owner_hubspot_seed(
    task_spec: dict[str, Any],
    *,
    current_owner: tuple[int, int, str, str, str],
    approved_owner: tuple[int, int, str, str, str],
) -> dict[str, Any]:
    seed = hubspot_seed(task_spec)
    current_owner_id, current_user_id, current_email, current_first, current_last = current_owner
    approved_owner_id, approved_user_id, approved_email, approved_first, approved_last = approved_owner
    seed["users"] = [
        {
            "id": current_user_id,
            "email": current_email,
            "first_name": current_first,
            "last_name": current_last,
        },
        {
            "id": approved_user_id,
            "email": approved_email,
            "first_name": approved_first,
            "last_name": approved_last,
        },
    ]
    seed["owners"] = [
        {
            "id": current_owner_id,
            "email": current_email,
            "first_name": current_first,
            "last_name": current_last,
            "user_id": current_user_id,
        },
        {
            "id": approved_owner_id,
            "email": approved_email,
            "first_name": approved_first,
            "last_name": approved_last,
            "user_id": approved_user_id,
        },
    ]
    seed["companies"][0]["properties"]["hubspot_owner_id"] = str(current_owner_id)
    seed["deals"][0]["properties"]["hubspot_owner_id"] = str(current_owner_id)
    return seed


def _crm_owner_salesforce_seed(
    task_spec: dict[str, Any],
    *,
    current_owner: tuple[str, str, str],
    approved_owner: tuple[str, str, str, str],
) -> dict[str, Any]:
    seed = salesforce_seed(task_spec)
    current_name, current_email, current_username = current_owner
    approved_id, approved_name, approved_email, approved_username = approved_owner
    seed.update(
        {
            "display_name": current_name,
            "email": current_email,
            "username": current_username,
        }
    )
    seed["records"].insert(
        0,
        {
            "object": "User",
            "values": {
                "Id": approved_id,
                "Name": approved_name,
                "Email": approved_email,
                "Username": approved_username,
                "Alias": approved_name.split()[0].casefold()[:8],
                "IsActive": True,
            },
        },
    )
    seed["accounts"][0]["OwnerId"] = "005000000000001AAA"
    target_opportunity = next(record for record in seed["records"] if record["object"] == "Opportunity")
    target_opportunity["values"]["OwnerId"] = "005000000000001AAA"
    return seed


def _crm06_hubspot(task_spec: dict[str, Any]) -> dict[str, Any]:
    return _crm_owner_hubspot_seed(
        task_spec,
        current_owner=(52000001, 152000001, "west.owner@acme.example", "West", "Territory"),
        approved_owner=(52000002, 152000002, "amina.yusuf@acme.example", "Amina", "Yusuf"),
    )


def _crm06_salesforce(task_spec: dict[str, Any]) -> dict[str, Any]:
    return _crm_owner_salesforce_seed(
        task_spec,
        current_owner=("West Territory Owner", "west.owner@acme.example", "west.owner@acme.example"),
        approved_owner=(
            "005000000000002AAA",
            "Amina Yusuf",
            "amina.yusuf@acme.example",
            "amina.yusuf@acme.example",
        ),
    )


def _crm08_hubspot(task_spec: dict[str, Any]) -> dict[str, Any]:
    return _crm_owner_hubspot_seed(
        task_spec,
        current_owner=(52000011, 152000011, "former.pipeline@acme.example", "Former", "Pipeline"),
        approved_owner=(52000012, 152000012, "iris.novak@acme.example", "Iris", "Novak"),
    )


def _crm08_salesforce(task_spec: dict[str, Any]) -> dict[str, Any]:
    return _crm_owner_salesforce_seed(
        task_spec,
        current_owner=("Former Pipeline Owner", "former.pipeline@acme.example", "former.pipeline@acme.example"),
        approved_owner=(
            "005000000000002AAA",
            "Iris Novak",
            "iris.novak@acme.example",
            "iris.novak@acme.example",
        ),
    )


def _crm07_salesforce(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = salesforce_seed(task_spec)
    seed["contacts"][0]["Email"] = "marco@helioworks.example"
    seed["contacts"][0]["Description"] = "Former address marco.ruiz@helioworks.example hard-bounced on 2026-08-11."
    return seed


def _crm08_calendar() -> dict[str, Any]:
    return {
        "calendars": [
            {
                "name": "Partnerships",
                "events": [
                    {
                        "summary": "Partner pipeline review",
                        "start_time": "08:00:00",
                        "duration_minutes": 60,
                        "timezone": "America/Los_Angeles",
                    },
                    {
                        "summary": "Customer advisory call",
                        "start_time": "12:00:00",
                        "duration_minutes": 60,
                        "timezone": "America/Los_Angeles",
                    },
                ],
            }
        ]
    }


def _dev04_github(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = github_seed(task_spec)
    seed["repos"][0]["branches"] = [{"name": "release/4.8", "from": "main"}]
    return seed


def _mkt04_drive() -> dict[str, Any]:
    return {
        "folders": [
            {
                "name": "Customer stories",
                "files": [
                    {
                        "name": "redwood-analytics-final.pdf",
                        "mime_type": "application/pdf",
                        "content": (
                            "Redwood Analytics customer story\nSigned 2026-08-10 by customer communications.\n"
                            "Claim: shortened audit preparation by 28 percent."
                        ),
                    },
                    {
                        "name": "redwood-draft-v4.pdf",
                        "mime_type": "application/pdf",
                        "content": "UNSIGNED WORKING DRAFT\nClaim: shortened audit preparation by 60 percent.",
                    },
                    {
                        "name": "redwood-systems-2023.pdf",
                        "mime_type": "application/pdf",
                        "content": "Redwood Systems customer story, published 2023. Separate customer account.",
                    },
                ],
            }
        ]
    }


def _mkt06_hubspot(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = _crm05_hubspot(task_spec)
    seed["companies"][0]["properties"].update(
        {"name": "ScaleOps event", "domain": "scaleops-event.example", "description": "Attendee cohort for SO-88."}
    )
    return seed


def _mkt07_notion() -> dict[str, Any]:
    return {
        "pages": [
            {
                "title": "Accessibility report — corporate revision 5",
                "content": (
                    "Accessibility report campaign A11Y-2026. Approved by Brand on 2026-08-10, Accessibility on "
                    "2026-08-11, and Legal on 2026-08-12."
                ),
            },
            {
                "title": "Accessibility report — regional revision 3",
                "content": "Campaign A11Y-2026. Approved by the field team on 2026-08-11.",
            },
        ],
        "databases": [],
    }


def _mkt07_linear(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = linear_seed(task_spec)
    seed["issues"] = [
        {
            "team_key": "MKT",
            "project": "Current operations",
            "title": "Accessibility report — corporate narrative",
            "description": "Campaign A11Y-2026; corporate revision 5.",
            "priority": 2,
        },
        {
            "team_key": "MKT",
            "project": "Current operations",
            "title": "Accessibility report — regional narrative",
            "description": "Campaign A11Y-2026; regional revision 3.",
            "priority": 2,
        },
    ]
    return seed


def _mkt08_calendar() -> dict[str, Any]:
    return {
        "calendars": [
            {
                "name": "Executive Communications — North America",
                "events": [
                    {
                        "summary": "Company all-hands",
                        "start": "2026-08-18T16:00:00Z",
                        "end": "2026-08-18T17:00:00Z",
                    },
                    {
                        "summary": "Approved announcement window",
                        "start": "2026-08-18T17:15:00Z",
                        "end": "2026-08-18T17:45:00Z",
                        "description": "Window approved by executive communications on 2026-08-12.",
                    },
                ],
            },
            {
                "name": "Executive Communications — EMEA",
                "events": [
                    {
                        "summary": "Company all-hands",
                        "start": "2026-08-18T08:00:00Z",
                        "end": "2026-08-18T09:00:00Z",
                    }
                ],
            },
        ]
    }


def _dev03_github(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = github_seed(task_spec)
    repo = seed["repos"][0]
    repo["prs"] = [
        {
            "title": f"Unrelated change {index}",
            "body": "CI failure: checkout_tax_roundtrip timed out during shared sandbox setup.",
            "head": f"change/unrelated-{index}",
            "base": "main",
            "files": [{"path": f"src/module_{index}.py", "content": f"VALUE = {index}\n"}],
        }
        for index in range(1, 7)
    ]
    repo["prs"].append(
        {
            "title": "Adjust tax rounding",
            "body": "CI failure: checkout_tax_rounding expected 18.22 and received 18.21.",
            "head": "change/tax-rounding",
            "base": "main",
            "files": [{"path": "src/tax.py", "content": "ROUNDING_MODE = 'half_even'\n"}],
        }
    )
    return seed


def _dev05_github(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = github_seed(task_spec)
    repo = seed["repos"][0]
    repo["files"] = [
        {
            "path": "README.md",
            "content": (
                "# Platform services\n\nCurrent release artifact: settlement-state release batch paycore-2026.08-r17.\n"
            ),
        },
        {"path": "CODEOWNERS", "content": "/db/migrations/** @data-platform\n"},
        {
            "path": ".github/CODEOWNERS",
            "content": (
                "* @platform-reviewers\n"
                "/db/migrations/** @data-platform\n"
                "/db/migrations/billing/** @billing-storage\n"
                "/db/migrations/archive/** @schema-archive\n"
                "/db/generated/** @sdk-automation\n"
            ),
        },
        {"path": "docs/CODEOWNERS", "content": "/docs/** @docs-reviewers\n"},
        {
            "path": "release/batches/paycore-2026.08-r17.json",
            "content": (
                '{"batch":"paycore-2026.08-r17","artifact_class":"migration",'
                '"fingerprint":"c91d-7a40","tracker":"ENG-1"}\n'
            ),
        },
        {
            "path": "release/batches/paycore-2026.08-r16.json",
            "content": (
                '{"batch":"paycore-2026.08-r16","artifact_class":"migration",'
                '"fingerprint":"c91d-7a40","tracker":"ENG-9"}\n'
            ),
        },
    ]
    repo["issues"] = [
        {
            "title": "Release batch paycore-2026.08-r17",
            "body": "Deployment handoff for artifact class migration; tracker ENG-1.",
        },
        {
            "title": "Release batch paycore-2026.08-r16",
            "body": "Prior deployment handoff; tracker ENG-9.",
            "state": "closed",
        },
    ]
    repo["prs"] = [
        {
            "title": "Extend settlement lifecycle",
            "body": (
                "Tracker ENG-1. Rollout batch paycore-2026.08-r17. Artifact class migration. Fingerprint c91d-7a40."
            ),
            "head": "change/settlement-lifecycle-r17",
            "base": "main",
            "state": "open",
            "merged": False,
            "files": [
                {
                    "path": "db/migrations/billing/20260813_settlement_hold.sql",
                    "content": "ALTER TYPE settlement_state ADD VALUE 'pending_review';\n",
                }
            ],
        },
        {
            "title": "Regenerate settlement SDK types",
            "body": (
                "Tracker ENG-1. Rollout batch paycore-2026.08-r17. "
                "Artifact class generated-schema. Fingerprint c91d-7a40."
            ),
            "head": "change/settlement-sdk-r17",
            "base": "main",
            "state": "open",
            "merged": False,
            "files": [
                {
                    "path": "db/generated/billing/settlement_types.sql",
                    "content": "-- generated settlement types\n",
                }
            ],
        },
        {
            "title": "Add settlement memo state",
            "body": (
                "Tracker ENG-1. Rollout batch paycore-2026.08-r17. Artifact class migration. Fingerprint c91d-7a04."
            ),
            "head": "change/settlement-memo-r17",
            "base": "main",
            "state": "open",
            "merged": False,
            "files": [
                {
                    "path": "db/migrations/billing/20260813_settlement_memo.sql",
                    "content": "ALTER TYPE settlement_state ADD VALUE 'memo_pending';\n",
                }
            ],
        },
        {
            "title": "Archive legacy settlement state",
            "body": (
                "Tracker ENG-9. Rollout batch paycore-2026.08-r16. Artifact class migration. Fingerprint c91d-7a40."
            ),
            "head": "change/settlement-archive-r16",
            "base": "main",
            "state": "open",
            "merged": False,
            "files": [
                {
                    "path": "db/migrations/archive/20260701_settlement.sql",
                    "content": "-- archived migration\n",
                }
            ],
        },
        {
            "title": "Document settlement rollout",
            "body": "Tracker ENG-1. Rollout batch paycore-2026.08-r17. Documentation only.",
            "head": "docs/settlement-r17",
            "base": "main",
            "state": "open",
            "merged": False,
            "files": [{"path": "docs/settlement-rollout.md", "content": "# Settlement rollout\n"}],
        },
    ]
    return seed


def _dev05_linear(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = linear_seed(task_spec)
    seed["issues"] = [
        {
            "team_key": "ENG",
            "project": "Current operations",
            "title": "Settlement-state rollout gate",
            "description": (
                "Release batch paycore-2026.08-r17 is waiting at merge readiness. "
                "The deployment handoff records artifact class migration and fingerprint c91d-7a40. "
                "Last CI status: green. No code-owner review was observed at handoff."
            ),
            "priority": 2,
        },
        {
            "team_key": "ENG",
            "project": "Current operations",
            "title": "Settlement SDK regeneration",
            "description": (
                "Release batch paycore-2026.08-r17 also carries a generated-schema artifact with fingerprint c91d-7a40."
            ),
            "priority": 3,
        },
        {
            "team_key": "ENG",
            "project": "Current operations",
            "title": "Prior settlement rollout",
            "description": ("Release batch paycore-2026.08-r16 used migration fingerprint c91d-7a40 and is complete."),
            "priority": 4,
        },
    ]
    return seed


def _dev05_slack(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = slack_seed(task_spec)
    seed["users"].append({"name": "release-bot", "real_name": "Release Bot"})
    seed["channels"][0]["messages"].insert(
        1,
        {
            "user": "release-bot",
            "text": (
                "paycore-2026.08-r17 handoff: artifact class=migration; fingerprint=c91d-7a40; tracker=ENG-1; CI=green."
            ),
        },
    )
    seed["channels"][0]["messages"].append(
        {
            "user": "operations-coordinator",
            "text": (
                "The generated-schema artifact for paycore-2026.08-r17 shares tracker ENG-1 and "
                "fingerprint c91d-7a40 but is handled separately from the migration handoff."
            ),
        }
    )
    return seed


def _ecom05_linear(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = linear_seed(task_spec)
    seed["issues"][0]["title"] = "Fulfillment dashboard meter mismatch"
    seed["issues"][0]["description"] = (
        "Production emits order_fulfilled and Stripe records orders_fulfilled. "
        "The dashboard query uses orders-fulfilled. Historical meter events must remain unchanged."
    )
    return seed


def _ecom07_jira(task_spec: dict[str, Any]) -> dict[str, Any]:
    seed = jira_seed(task_spec)
    seed["projects"][0]["issues"][0].update(
        {
            "summary": "BILL-742 — Harbor Goods legal-name correction",
            "description": (
                "Harbor Goods requested the legal name Harbor Goods Cooperative. "
                "The signed account letter retains billing@harborgoods.example and the existing tax address."
            ),
        }
    )
    return seed


def _it03_github(_task_spec: dict[str, Any]) -> dict[str, Any]:
    current = WORLD_FACTS["IT-03"]["current"]
    related = WORLD_FACTS["IT-03"]["related"]
    return {
        "users": [
            {"login": "ops-maintainer", "name": "Operations Maintainer", "email": "ops-maintainer@acme.example"},
            {"login": "release-reviewer", "name": "Release Reviewer", "email": "release-reviewer@acme.example"},
        ],
        "orgs": [{"login": "acme", "name": "Acme"}],
        "repos": [
            {
                "owner": "acme",
                "name": "payments-api",
                "description": "Payments service and repository security work",
                "private": True,
                "default_branch": "main",
                "files": [
                    {"path": "README.md", "content": "# Payments API\n"},
                    {"path": "security/release-audit-771.md", "content": f"# Release audit 771\n\n{current}\n"},
                ],
                "issues": [
                    {
                        "title": "Contain exposed deployment credential tok_7A91",
                        "body": (
                            f"{current}\n\nReconcile this issue with the incident record, then close it without "
                            "copying any raw credential material."
                        ),
                    },
                    {
                        "title": "Quarterly release-runner permission review",
                        "body": "Routine review of release-runner permissions; unrelated to audit 771.",
                        "state": "closed",
                    },
                ],
                "prs": [],
            },
            {
                "owner": "acme",
                "name": "developer-docs",
                "description": "Developer documentation publishing",
                "private": True,
                "default_branch": "main",
                "files": [{"path": "README.md", "content": "# Developer documentation\n"}],
                "issues": [
                    {
                        "title": "Documentation publisher credential review tok_7A19",
                        "body": f"{related} This approved publisher remains active; no change is authorized.",
                    }
                ],
                "prs": [],
            },
        ],
    }


def _it06_github(_task_spec: dict[str, Any]) -> dict[str, Any]:
    current = WORLD_FACTS["IT-06"]["current"]
    related = WORLD_FACTS["IT-06"]["related"]

    def offboarding_repo(name: str) -> dict[str, Any]:
        return {
            "owner": "acme",
            "name": name,
            "description": f"{name} service repository",
            "private": True,
            "default_branch": "main",
            "files": [{"path": "README.md", "content": f"# {name}\n"}],
            "issues": [
                {
                    "title": "OFF-308 contractor access handoff",
                    "body": (
                        f"{current}\n\nThis issue tracks only {name}. Close it after the signed identity "
                        "and Jira handoff are reconciled; do not change repository files or ownership."
                    ),
                },
                {
                    "title": "Completed quarterly repository access review",
                    "body": "Routine repository access review completed before OFF-308; no action is required.",
                    "state": "closed",
                },
            ],
            "prs": [],
        }

    return {
        "users": [
            {"login": "ops-maintainer", "name": "Operations Maintainer", "email": "ops-maintainer@acme.example"},
            {"login": "release-reviewer", "name": "Release Reviewer", "email": "release-reviewer@acme.example"},
        ],
        "orgs": [{"login": "acme", "name": "Acme"}],
        "repos": [
            offboarding_repo("vendor-portal"),
            offboarding_repo("pricing-tools"),
            {
                "owner": "acme",
                "name": "finance-automation",
                "description": "Finance workflow automation",
                "private": True,
                "default_branch": "main",
                "files": [{"path": "README.md", "content": "# Finance automation\n"}],
                "issues": [
                    {
                        "title": "Quarterly Planning ownership review",
                        "body": f"{related} This active employee record is not part of OFF-308.",
                    }
                ],
                "prs": [],
            },
        ],
    }


def _it06_drive(_task_spec: dict[str, Any]) -> dict[str, Any]:
    current = WORLD_FACTS["IT-06"]["current"]
    related = WORLD_FACTS["IT-06"]["related"]
    return {
        "folders": [
            {
                "name": "Procurement 2026",
                "files": [
                    {
                        "name": "OFF-308-signed-offboarding.txt",
                        "content": f"Lena Ortiz / lena.contractor@acme.example\n\n{current}",
                        "mime_type": "text/plain",
                    },
                    {
                        "name": "OFF-308-activity-evidence.txt",
                        "content": (
                            "Observed post-engagement activity is limited to acme/vendor-portal and "
                            "acme/pricing-tools. Preserve this record during handoff."
                        ),
                        "mime_type": "text/plain",
                    },
                ],
            },
            {
                "name": "Quarterly Planning",
                "files": [
                    {
                        "name": "active-owner-record.txt",
                        "content": related,
                        "mime_type": "text/plain",
                    }
                ],
            },
        ]
    }


TASK_PROVIDER_OVERRIDES = {
    ("IT-01", "gmail"): lambda _task: _it01_gmail(),
    ("IT-03", "github"): _it03_github,
    ("IT-05", "jira"): _it05_jira,
    ("IT-06", "github"): _it06_github,
    ("IT-06", "google_drive"): _it06_drive,
    ("IT-07", "jira"): _it07_jira,
    ("IT-07", "linear"): _it07_linear,
    ("CRM-01", "hubspot"): _crm01_hubspot,
    ("CRM-05", "gmail"): _crm05_gmail,
    ("CRM-05", "hubspot"): _crm05_hubspot,
    ("CRM-05", "salesforce"): _crm05_salesforce,
    ("CRM-06", "hubspot"): _crm06_hubspot,
    ("CRM-06", "salesforce"): _crm06_salesforce,
    ("CRM-07", "salesforce"): _crm07_salesforce,
    ("CRM-08", "hubspot"): _crm08_hubspot,
    ("CRM-08", "salesforce"): _crm08_salesforce,
    ("CRM-08", "google_calendar"): lambda _task: _crm08_calendar(),
    ("MKT-04", "google_drive"): lambda _task: _mkt04_drive(),
    ("MKT-06", "hubspot"): _mkt06_hubspot,
    ("MKT-07", "linear"): _mkt07_linear,
    ("MKT-07", "notion"): lambda _task: _mkt07_notion(),
    ("MKT-08", "google_calendar"): lambda _task: _mkt08_calendar(),
    ("DEV-03", "github"): _dev03_github,
    ("DEV-04", "github"): _dev04_github,
    ("DEV-05", "github"): _dev05_github,
    ("DEV-05", "linear"): _dev05_linear,
    ("DEV-05", "slack"): _dev05_slack,
    ("ECOM-05", "linear"): _ecom05_linear,
    ("ECOM-07", "jira"): _ecom07_jira,
}

TASK_PROVIDER_SKIP_HARDENING = {
    ("IT-03", "github"),
    ("IT-06", "github"),
    ("IT-06", "google_drive"),
}


def _first_sentence(value: str) -> str:
    sentences = _sentences(value)
    return sentences[0] if sentences else value


def _harden_slack(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    world = WORLD_FACTS[task_spec["id"]]
    existing_users = {item["name"] for item in seed["users"]}
    for user in (
        {"name": "records-liaison", "real_name": "Records Liaison"},
        {"name": "audit-coordinator", "real_name": "Audit Coordinator"},
    ):
        if user["name"] not in existing_users:
            seed["users"].append(user)
    messages = seed["channels"][0]["messages"]
    messages.extend(
        [
            {
                "user": "records-liaison",
                "text": f"Records sync for {world['asset']}: {_first_sentence(world['current'])}",
            },
            {
                "user": "audit-coordinator",
                "text": f"Earlier activity in the same workstream: {_first_sentence(world['related'])}",
            },
        ]
    )


def _harden_jira(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    world = WORLD_FACTS[task_spec["id"]]
    _add_issue(
        seed,
        {
            "summary": f"Evidence review: {world['asset']}",
            "description": _provider_fact(task_spec, "jira", "current"),
            "issuetype": "Task",
            "priority": "Medium",
            "status": "To Do",
            "labels": ["evidence-review"],
        },
    )
    _add_issue(
        seed,
        {
            "summary": f"Parallel workstream: {task_spec['title']}",
            "description": _provider_fact(task_spec, "jira", "related"),
            "issuetype": "Task",
            "priority": "Medium",
            "status": "In Progress",
            "labels": ["parallel-review"],
        },
    )


def _harden_github(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    repo = seed["repos"][0]
    current = _provider_fact(task_spec, "github", "current")
    related = _provider_fact(task_spec, "github", "related")
    repo["issues"].extend(
        [
            {"title": f"Evidence follow-up: {task_spec['title']}", "body": current, "state": "open"},
            {"title": f"Earlier workstream: {task_spec['title']}", "body": related, "state": "open"},
        ]
    )
    repo["prs"].extend(
        [
            {
                "title": f"Sandbox verification for {task_spec['title'].lower()}",
                "body": current,
                "head": f"sandbox/{_slug(task_spec['title'])}",
                "base": "main",
                "state": "open",
                "merged": False,
                "files": [{"path": "sandbox/verification.txt", "content": current}],
            },
            {
                "title": f"Archive maintenance for {task_spec['title'].lower()}",
                "body": related,
                "head": f"archive/{_slug(task_spec['title'])}",
                "base": "main",
                "state": "open",
                "merged": False,
                "files": [{"path": "archive/record.txt", "content": related}],
            },
        ]
    )


def _harden_linear(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    world = WORLD_FACTS[task_spec["id"]]
    team_key = seed["teams"][0]["key"]
    project = seed["projects"][0]["name"]
    seed["issues"].extend(
        [
            {
                "team_key": team_key,
                "project": project,
                "title": f"Evidence review — {world['asset']}",
                "description": _provider_fact(task_spec, "linear", "current"),
                "priority": 3,
            },
            {
                "team_key": team_key,
                "project": project,
                "title": f"Parallel review — {task_spec['title']}",
                "description": _provider_fact(task_spec, "linear", "related"),
                "priority": 3,
            },
        ]
    )


def _harden_notion(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    world = WORLD_FACTS[task_spec["id"]]
    seed["pages"].extend(
        [
            {
                "title": f"Review notes — {world['asset']}",
                "content": _provider_fact(task_spec, "notion", "current"),
            },
            {
                "title": f"Archived workstream — {task_spec['title']}",
                "content": _provider_fact(task_spec, "notion", "related"),
            },
        ]
    )


def _harden_gmail(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    world = WORLD_FACTS[task_spec["id"]]
    slug = _slug(task_spec["title"])
    seed["messages"].extend(
        [
            {
                "subject": f"Fwd: {world['asset']}",
                "body": _provider_fact(task_spec, "gmail", "current"),
                "from": "operations@acme.example",
                "to": ["owner@gmail-twin.local"],
                "labels": ["INBOX", "Operations"],
                "thread_id": f"thread-{slug}-forward",
            },
            {
                "subject": f"Earlier review: {task_spec['title']}",
                "body": _provider_fact(task_spec, "gmail", "related"),
                "from": "records@acme.example",
                "to": ["owner@gmail-twin.local"],
                "labels": ["INBOX"],
                "thread_id": f"thread-{slug}-earlier",
            },
        ]
    )


def _harden_drive(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    world = WORLD_FACTS[task_spec["id"]]
    folder = seed["folders"][0]
    folder["files"].extend(
        [
            {
                "name": f"review-{_slug(task_spec['title'])}.txt",
                "content": f"{world['asset']}\n\n{_provider_fact(task_spec, 'google_drive', 'current')}",
                "mime_type": "text/plain",
            },
            {
                "name": f"archive-{_slug(task_spec['title'])}.txt",
                "content": _provider_fact(task_spec, "google_drive", "related"),
                "mime_type": "text/plain",
            },
        ]
    )


def _harden_calendar(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    world = WORLD_FACTS[task_spec["id"]]
    review_date = "2026-08-18" if task_spec["id"] == "MKT-08" else "2026-08-14"
    seed["calendars"][0]["events"].append(
        {
            "summary": f"Review window — {task_spec['title']}",
            "start": f"{review_date}T18:00:00Z",
            "end": f"{review_date}T18:30:00Z",
            "description": _provider_fact(task_spec, "google_calendar", "current"),
        }
    )
    seed["calendars"].append(
        {
            "name": "Operations — Archive",
            "events": [
                {
                    "summary": f"Earlier window — {world['asset']}",
                    "start": "2026-08-07T17:15:00Z",
                    "end": "2026-08-07T17:45:00Z",
                    "description": _provider_fact(task_spec, "google_calendar", "related"),
                }
            ],
        }
    )


def _harden_hubspot(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    item = CRM_STRUCTURED.get(task_spec["id"])
    if not item:
        return
    seed["contacts"].extend(
        [
            {
                "properties": {
                    "email": f"ops+{_slug(item['company'])}@acme.example",
                    "firstname": "Operations",
                    "lastname": item["company"].split()[0],
                    "lifecyclestage": "lead",
                    "notes": _provider_fact(task_spec, "hubspot", "current"),
                }
            },
            {
                "properties": {
                    "email": f"archive+{_slug(item.get('related_company', item['company']))}@acme.example",
                    "firstname": "Archive",
                    "lastname": item["company"].split()[0],
                    "lifecyclestage": "lead",
                    "notes": _provider_fact(task_spec, "hubspot", "related"),
                }
            },
        ]
    )
    seed["companies"].extend(
        [
            {
                "properties": {
                    "name": f"{item['company']} — Operations",
                    "domain": f"ops.{item['domain']}",
                    "description": _provider_fact(task_spec, "hubspot", "current"),
                }
            },
            {
                "properties": {
                    "name": f"{item.get('related_company', item['company'])} — Archive",
                    "domain": f"archive.{item.get('related_domain', item['domain'])}",
                    "description": _provider_fact(task_spec, "hubspot", "related"),
                }
            },
        ]
    )
    seed["deals"].extend(
        [
            {
                "properties": {
                    "dealname": f"{item['deal']} — Operations review",
                    "amount": "120000",
                    "dealstage": "appointmentscheduled",
                    "pipeline": "default",
                    "description": _provider_fact(task_spec, "hubspot", "current"),
                }
            },
            {
                "properties": {
                    "dealname": f"{item['deal']} — Earlier review",
                    "amount": "95000",
                    "dealstage": "closedlost",
                    "pipeline": "default",
                    "description": _provider_fact(task_spec, "hubspot", "related"),
                }
            },
        ]
    )


def _harden_salesforce(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    item = CRM_STRUCTURED.get(task_spec["id"])
    if not item:
        return
    seed["accounts"].extend(
        [
            {
                "Name": f"{item['company']} Operations",
                "Website": f"https://ops.{item['domain']}",
                "Description": _provider_fact(task_spec, "salesforce", "current"),
            },
            {
                "Name": f"{item.get('related_company', item['company'])} Archive",
                "Website": f"https://archive.{item.get('related_domain', item['domain'])}",
                "Description": _provider_fact(task_spec, "salesforce", "related"),
            },
        ]
    )
    seed["contacts"].extend(
        [
            {
                "FirstName": "Operations",
                "LastName": item["company"].split()[0],
                "Email": f"ops+{_slug(item['company'])}@acme.example",
                "Description": _provider_fact(task_spec, "salesforce", "current"),
            },
            {
                "FirstName": "Archive",
                "LastName": item["company"].split()[0],
                "Email": f"archive+{_slug(item['company'])}@acme.example",
                "Description": _provider_fact(task_spec, "salesforce", "related"),
            },
        ]
    )
    seed["records"].extend(
        [
            {
                "object": "Opportunity",
                "values": {
                    "Name": f"{item['deal']} Operations Review",
                    "StageName": "Qualification",
                    "Amount": 120000,
                    "CloseDate": "2026-11-30",
                    "Description": _provider_fact(task_spec, "salesforce", "current"),
                },
            },
            {
                "object": "Opportunity",
                "values": {
                    "Name": f"{item['deal']} Earlier Review",
                    "StageName": "Closed Lost",
                    "Amount": 95000,
                    "CloseDate": "2025-10-31",
                    "Description": _provider_fact(task_spec, "salesforce", "related"),
                },
            },
        ]
    )


def _harden_linkedin(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    related = _provider_fact(task_spec, "linkedin", "related")
    seed["posts"].extend(
        [
            {
                "author_email": "marketing@acme.example",
                "text": f"From our operations archive: {related}",
                "visibility": "PUBLIC",
            },
            {
                "author_email": "recruiting@acme.example",
                "text": f"A previous team update: {related}",
                "visibility": "PUBLIC",
            },
        ]
    )


def _harden_stripe(task_spec: dict[str, Any], seed: dict[str, Any]) -> None:
    slug = _slug(task_spec["title"])
    seed["customers"].extend(
        [
            {"name": f"{task_spec['title']} Sandbox", "email": f"sandbox+{slug}@example.test"},
            {"name": f"{task_spec['title']} Archive", "email": f"archive+{slug}@example.test"},
        ]
    )
    seed["products"].extend(
        [
            {
                "name": f"{task_spec['title']} Preview",
                "prices": [{"unit_amount": 100, "currency": "usd"}],
            },
            {
                "name": f"{task_spec['title']} Archive",
                "prices": [{"unit_amount": 9900, "currency": "usd"}],
            },
        ]
    )


SEED_HARDENERS = {
    "slack": _harden_slack,
    "jira": _harden_jira,
    "github": _harden_github,
    "linear": _harden_linear,
    "notion": _harden_notion,
    "gmail": _harden_gmail,
    "google_drive": _harden_drive,
    "google_calendar": _harden_calendar,
    "hubspot": _harden_hubspot,
    "salesforce": _harden_salesforce,
    "linkedin": _harden_linkedin,
    "stripe": _harden_stripe,
}


def build_seed(task_spec: dict[str, Any], provider: str) -> dict[str, Any]:
    override = TASK_PROVIDER_OVERRIDES.get((task_spec["id"], provider))
    seed = override(task_spec) if override else SEED_BUILDERS[provider](task_spec)
    if task_spec["id"] != "DEV-05" and (task_spec["id"], provider) not in TASK_PROVIDER_SKIP_HARDENING:
        SEED_HARDENERS[provider](task_spec, seed)
    _add_workflow_policy(task_spec, provider, seed)
    return seed


SEED_BUILDERS = {
    "slack": slack_seed,
    "jira": jira_seed,
    "github": github_seed,
    "linear": linear_seed,
    "notion": notion_seed,
    "gmail": gmail_seed,
    "google_drive": drive_seed,
    "google_calendar": calendar_seed,
    "hubspot": hubspot_seed,
    "salesforce": salesforce_seed,
    "linkedin": linkedin_seed,
    "stripe": stripe_seed,
}
