# Cross-Functional 40 v1

Exactly 40 deterministic, multi-system benchmark tasks: eight per domain.

## It Support

### IT-01 — Suspicious supplier download response

**Prompt**

Keisha Bell from strategic sourcing just posted in the #security-intake Slack channel: a compressed supplier rate-card download opened a hidden browser process and registered a new startup entry on her procurement laptop. The attachment arrived inside a reply to an existing sourcing thread, so she cannot tell whether the sender was spoofed.

You're the endpoint incident analyst. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

### IT-02 — SSO outage after configuration change

**Prompt**

Eli Morgan from customer operations just posted in the #it-helpdesk Slack channel: the support team started getting SSO login loops right after the morning authentication configuration rollout. Password logins still work for a few users, and there are two similarly named changes in flight.

You're the identity incident responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

### IT-03 — Deployment credential exposure triage

**Prompt**

Maya Chen from release engineering just posted in the #security-help Slack channel: a deployment token may have been pasted into a public Slack thread during a failed release. She removed her message but does not know whether the token was real or which repository it belonged to.

You're the security operations responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

### IT-04 — Suspicious OAuth application review

**Prompt**

Jon Bell from design just posted in the #it-helpdesk Slack channel: a new OAuth application called Cloud Sketch asked for repository and file access after he opened a shared design brief. He denied the prompt, but a similarly named approved app is already used by the company.

You're the access review responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

### IT-05 — VPN regression after client update

**Prompt**

Priya Desai from legal just posted in the #it-helpdesk Slack channel: the VPN disconnects every few minutes since yesterday's desktop client update. Several colleagues report the same symptom, but the network status page is green.

You're the endpoint support lead. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

### IT-06 — Departing contractor access handoff

**Prompt**

Emil Navarro from people operations just posted in the #it-helpdesk Slack channel: contractor Lena Ortiz finished her engagement yesterday, but a shared project folder and two engineering repositories still show activity associated with her name. Another active employee has a similar name.

You're the offboarding responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

### IT-07 — Monitoring alert storm deduplication

**Prompt**

Sara Kim from infrastructure just posted in the #ops-alerts Slack channel: the checkout API produced dozens of latency alerts after a metrics label change, even though customer traffic stayed healthy. One alert in the burst may still reflect a real database saturation event.

You're the incident commander. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

### IT-08 — Suspicious CI runner investigation

**Prompt**

Theo Martin from platform engineering just posted in the #security-help Slack channel: a self-hosted CI runner made an outbound connection to an unfamiliar host during a release job. The job later passed, and the same runner name appears in both production and a retired lab environment.

You're the security incident lead. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Preserve evidence and avoid destructive or unrelated changes. Keep the people relying on the result informed.

## Crm Outreach

### CRM-01 — Duplicate enterprise lead handoff

**Prompt**

Amira Cole from inbound sales just posted in the #gtm-ops Slack channel: Northstar Robotics requested an enterprise demo, but HubSpot shows two similar companies and Salesforce already has an account with an open opportunity. She does not want the prospect contacted twice.

You're the revenue operations owner. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### CRM-02 — Stalled enterprise opportunity rescue

**Prompt**

Lucas Wong from account management just posted in the #gtm-ops Slack channel: the Alder Bank expansion has been sitting in negotiation for three weeks even though the champion replied with procurement requirements yesterday. The reply belongs to one of two similarly named Alder accounts.

You're the deal desk coordinator. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. The resolved state should show the procurement blocker and next-step owner on the correct opportunity in both CRM views, with an internal handoff and no external outreach sent. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### CRM-03 — Inbound demo qualification

**Prompt**

Sofia Patel from demand generation just posted in the #gtm-ops Slack channel: Driftline Logistics submitted a demo form and then emailed extra deployment details. Their domain appears on an older disqualified record, while the new request indicates a different business unit and buying timeline.

You're the inbound qualification specialist. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### CRM-04 — Renewal risk escalation

**Prompt**

Mateo Silva from customer success just posted in the #customer-risk Slack channel: Cedar Health's champion reported that the security review is blocking renewal, but the health score in HubSpot is still green and Salesforce has two renewal opportunities for adjacent subsidiaries.

You're the renewal operations lead. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### CRM-05 — Webinar follow-up segmentation

**Prompt**

Hannah Price from field marketing just posted in the #gtm-ops Slack channel: the FinOps webinar produced a list of attendees, no-shows, and existing customers. Sales only wants high-intent non-customers routed, and one attendee used a personal email that is already associated with a corporate contact.

You're the GTM operations specialist. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. The resolved state should contain one complete internal follow-up cohort with every eligible high-intent non-customer and no current customer or no-show, while external outreach remains unsent. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### CRM-06 — Territory ownership conflict

**Prompt**

Noah Grant from enterprise sales just posted in the #gtm-ops Slack channel: BluePeak Energy appears assigned to both the West and Strategic teams after its headquarters moved. A live opportunity has activity from both owners, and a territory request is already open.

You're the revenue systems administrator. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. The resolved state should show one approved Strategic owner across both CRM records, with the territory request resolved and opportunity history unchanged. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### CRM-07 — Bounced contact cleanup

**Prompt**

Talia Mbeki from lifecycle marketing just posted in the #gtm-ops Slack channel: campaign mail to Marco Ruiz at HelioWorks hard-bounced, but another address for him appears in a signed reply thread and Salesforce. The similar Helio Workspaces account should not be touched.

You're the CRM data steward. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### CRM-08 — Closed-lost opportunity reactivation

**Prompt**

Iris Novak from partnerships just posted in the #gtm-ops Slack channel: Orbit Systems asked to restart a previously closed evaluation and proposed two meeting times. There are two closed-lost opportunities and an unrelated active customer account with a similar name.

You're the pipeline operations lead. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. The resolved state should have exactly one verified evaluation active under its accountable owner and one internal hold at the agreed time. Do not send external outreach or disturb unrelated accounts unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

## Marketing

### MKT-01 — Approved product launch post

**Prompt**

Zoe Hart from product marketing just posted in the #marketing-launches Slack channel: the Reliability Suite launch is ready for LinkedIn, but the workspace contains two copy versions and only one has final product and legal approval. The launch Linear issue is still marked as waiting.

You're the launch marketing manager. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

### MKT-02 — Campaign legal hold reconciliation

**Prompt**

Avery Singh from legal just posted in the #marketing-launches Slack channel: the prepared LinkedIn copy for the AI Benchmark campaign uses a customer statistic that is not cleared for public use. A revised version with the same campaign code is still under review.

You're the campaign operations lead. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

### MKT-03 — Broken launch CTA correction

**Prompt**

Caleb Ross from web marketing just posted in the #marketing-launches Slack channel: the Observability launch is blocked because its prepared CTA links to a 404 page. The repository contains a merged route fix and an open similar PR that points to a staging domain.

You're the digital campaign responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

### MKT-04 — Customer story publication

**Prompt**

Mina Park from customer marketing just posted in the #marketing-launches Slack channel: the Redwood Analytics case study has customer approval, but Drive contains a signed final PDF, an unsigned draft with stronger claims, and an old file from another Redwood account.

You're the customer marketing publisher. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

### MKT-05 — Embargoed announcement hold

**Prompt**

Rina Ahmed from communications just posted in the #marketing-security Slack channel: the Project Harbor acquisition copy reached the publishing queue before its embargo. A harmless recruiting post has a similar title, and an open communications ticket references the acquisition packet.

You're the communications operations responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

### MKT-06 — Event recap attribution check

**Prompt**

Diego Romero from community just posted in the #marketing-launches Slack channel: the ScaleOps event recap is ready for LinkedIn, but its pipeline claim may include existing customers and duplicate contacts. The post should only use the verified net-new attendee count.

You're the campaign analytics operator. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

### MKT-07 — Duplicate campaign prevention

**Prompt**

Leah Ford from brand just posted in the #marketing-launches Slack channel: two teams prepared LinkedIn copy for the same accessibility report and both asked to publish it today. Only the corporate narrative has complete approvals.

You're the editorial operations lead. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

### MKT-08 — Executive announcement window preparation

**Prompt**

Ben Ito from executive communications just posted in the #marketing-launches Slack channel: the CEO announcement packet names a publishing time during the company all-hands even though the approved launch window begins afterward. A second Executive Comms calendar belongs to a different region.

You're the executive communications coordinator. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Treat approvals, publishing identity, and embargoes as hard constraints; publish nothing that is not clearly authorized. Keep the people relying on the result informed.

## Developer

### DEV-01 — Production checkout regression triage

**Prompt**

Samira Khan from on-call engineering just posted in the #eng-incidents Slack channel: checkout requests began failing after the latest payments-api deployment. Two pull requests mention the same error code, and one Linear incident is already linked to a resolved staging failure.

You're the software incident responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### DEV-02 — Dependency security fix review

**Prompt**

Basil Okafor from application security just posted in the #eng-security Slack channel: a dependency update claims to fix a critical parser vulnerability, but the lockfile diff also pulls an unapproved package source. A second PR changes documentation only.

You're the security code reviewer. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### DEV-03 — Flaky CI test quarantine

**Prompt**

Jordan Lee from developer experience just posted in the #eng-builds Slack channel: the same integration test failed in six unrelated pull requests overnight, but a similarly named unit test reflects a real product regression. Teams are rerunning jobs manually.

You're the build reliability owner. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### DEV-04 — Release branch backport coordination

**Prompt**

Taylor Brooks from release management just posted in the #eng-releases Slack channel: a customer-impacting fix merged to main but is missing from the supported 4.8 release branch. Two issues request backports, and only one maps to the shipped customer version.

You're the release engineer. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. The resolved state should contain one open, unmerged 4.8 backport with its approval trail and clear release status. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### DEV-05 — Missing code-owner review

**Prompt**

Morgan Yu from API engineering just posted in the #eng-reviews Slack channel: the settlement-state rollout for release batch paycore-2026.08-r17 is green, but its review request keeps bouncing between data-platform and billing storage. Several open pull requests mention the same tracker, and the team cannot afford to block the wrong one.

You're the merge-readiness reviewer. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### DEV-06 — API contract drift resolution

**Prompt**

Nora Evans from SDK engineering just posted in the #eng-api Slack channel: the generated SDK no longer matches the documented pagination response. The repository contains two API specs, and one is explicitly marked as a retired partner draft.

You're the API governance maintainer. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. The resolved state should contain one open drift record for the active contract, connected to repository evidence, with the specifications unchanged. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### DEV-07 — Unsafe hotfix revert request

**Prompt**

Cole Bennett from production support just posted in the #eng-incidents Slack channel: a customer asks for an immediate revert of yesterday's rate-limit hotfix, but the incident record says the old behavior caused data loss. A safer configuration rollback may already be approved.

You're the production change responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

### DEV-08 — Customer bug escalation linkage

**Prompt**

Fatima Noor from customer success just posted in the #eng-customer-bugs Slack channel: Apex Freight reports that webhook retries stop after the third attempt. HubSpot has two Apex companies, and GitHub has both an old closed issue and a new regression with similar symptoms.

You're the customer engineering liaison. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not merge, deploy, or rewrite source unless the available evidence clearly authorizes it. Keep the people relying on the result informed.

## Ecommerce

### ECOM-01 — Duplicate billing profile reconciliation

**Prompt**

Kim Alvarez from support just posted in the #commerce-ops Slack channel: Morgan Retail appears twice in Stripe after a sales trial was converted, and support cannot tell which profile should remain available to the billing team. A similarly named customer is also present.

You're the commerce data responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.

### ECOM-02 — Billing contact change reconciliation

**Prompt**

Marlon Price from customer success just posted in the #commerce-ops Slack channel: Northwind Studio asked for renewal notices to move to its accounts-payable address. HubSpot contains a similarly named prospect that has never been a customer.

You're the billing operations specialist. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.

### ECOM-03 — Retired catalog product cleanup

**Prompt**

Elena Torres from fraud operations just posted in the #commerce-risk Slack channel: Trailpack Enterprise still appears available for new orders even though the catalog team replaced it last month. A regional product with nearly the same name is still sold.

You're the catalog operations responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.

### ECOM-04 — Tax-exempt customer setup

**Prompt**

Ravi Shah from finance just posted in the #commerce-ops Slack channel: Civic Research Institute's billing profile is missing its tax-exempt registration even though a current certificate is in Salesforce. Salesforce also contains an expired certificate for a different regional account.

You're the billing compliance specialist. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.

### ECOM-05 — Fulfillment meter naming reconciliation

**Prompt**

Jo Park from platform operations just posted in the #commerce-ops Slack channel: the fulfillment dashboard went blank after a query change. Stripe and the production emitter use one event name, while the Linear incident and a failed GitHub workflow show a hyphenated variation.

You're the commerce telemetry responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.

### ECOM-06 — Plan catalog price migration

**Prompt**

Ana Gomez from monetization just posted in the #commerce-ops Slack channel: the Pro Annual catalog needs the approved FY27 price, but Stripe contains legacy, test, and regional products with nearly identical names. Existing subscribers must not be moved automatically.

You're the billing catalog administrator. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.

### ECOM-07 — Customer legal-name correction

**Prompt**

Imani Brooks from chargebacks just posted in the #commerce-risk Slack channel: Harbor Goods changed its legal billing name, and the current signed account letter is in Drive. Stripe also contains a separate UK customer whose name differs by one letter.

You're the billing data specialist. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.

### ECOM-08 — Abandoned trial customer cleanup

**Prompt**

Peter Lang from customer support just posted in the #commerce-ops Slack channel: Beacon Works has an active customer profile and an abandoned evaluation profile in Stripe. HubSpot also has an unrelated prospect with a similar parent-company name.

You're the customer data responder. Pick this up and handle it in the systems we operate. Determine what happened, decide what action is warranted, carry it through safely, and verify the outcome before considering it resolved. Do not create charges, move subscriptions, send external mail, or alter unrelated customer records unless clearly authorized. Keep the people relying on the result informed.
