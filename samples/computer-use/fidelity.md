# Frontend coverage

This sample uses five existing twins. The companion PR adds working controls to the task-relevant surfaces, preserving shared UI/API state. It does not certify every feature of five production products.

| Twin | Implemented or retained workflow | Validation |
| --- | --- | --- |
| Linear | Search title/body/comments; team/project/my-issue filters; create/edit issues; status, priority, assignee, project; comment/edit own comment; archive/restore | Form-to-GraphQL state roundtrips, invalid input, escaping, list filtering |
| GitHub | Existing repository/file/branch/PR/diff UI; new issue search/discussion/edit forms; request/remove people/team reviews | Shared REST handler roundtrips; existing diff regression tests |
| Slack | Existing channel, message, thread, search, and composer UI from current main | Existing twin implementation retained; no Slack UI change in this PR |
| Stripe | Existing dashboard; searchable products/customers; product and annual-price creation; price status/nickname/lookup edits; default-price selection; customer edits; meter search/create/edit/activation | UI-to-API roundtrips, protected product unchanged, invalid default-price rejection, escaping, nested seed form encoding |
| Notion | Page creation; existing page/property/block/comment editing; page search; icon/cover controls; page link and update details | Persisted appearance and invalid-cover tests |

Provider references consulted:

- [Linear editing](https://linear.app/docs/editing-issues), [comments](https://linear.app/docs/comment-on-issues), [search](https://linear.app/docs/search).
- [Stripe product updates](https://docs.stripe.com/api/products/update), [products and prices](https://support.stripe.com/questions/how-to-create-products-and-prices), [meter updates](https://docs.stripe.com/api/billing/meter/update).
- [Notion icons and covers](https://www.notion.com/help/guides/page-icons-and-covers).

Known gaps: full provider settings/admin/billing/analytics/AI/collaboration feature sets are not reproduced. Notion's Share menu exposes a page link, not a complete permission-management UI. Linear rich-text/slash-command collaboration is not implemented by the new plain-text editor. GitHub advanced review, project, and Actions UI coverage remains provider-specific. Local Chrome checks verified Linear commenting, Stripe annual-price creation, and Notion page creation, with screenshots inspected. CUA capture failed, so these checks used an isolated Playwright Chrome session. Mobile, pixel-by-pixel parity, all visible controls across every existing page, and five complete hosted agent rollouts remain unverified. Deployment of the twin PR is required for the added hosted controls.
