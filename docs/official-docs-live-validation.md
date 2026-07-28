# Official documentation live-retrieval validation

Validation date: 2026-07-27 (America/Vancouver). Retrieval timestamps below
are UTC. Each response was fetched through `OfficialDocsGateway` from the
checked-in provider allowlist. The SHA-256 value covers the exact bounded
official response bytes, not catalog-authored text.

| Provider | Catalog document | Official source/final URL | HTTP/content type | Retrieved bytes | SHA-256 | Retrieval truncated |
| --- | --- | --- | --- | ---: | --- | --- |
| Jira | `issue-comments` | `https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/` | 200 / `text/html` | 3,137,230 | `56530e365827809635c6976ceb3f9e2e2a6a86e69e5602f0e15911d40338dd22` | No |
| Slack | `conversations-list` | `https://docs.slack.dev/reference/methods/conversations.list` → `https://docs.slack.dev/reference/methods/conversations.list/` | 200 / `text/html` | 121,142 | `a2e1056f4cb2845f2720ffb49b049280fcd177d9319ac756cca02577a4af4191` | No |
| GitHub | `pull-requests` | `https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28` | 200 / `text/html` | 524,288 | `99c819cb7f162a173380a71087a9423db4701a1d824a3635fd4c9d5eabe3cc97` | Yes, at the standard non-Jira bound |

Retrieval times were `2026-07-28T00:25:02.022180+00:00` for Jira,
`2026-07-28T00:25:03.109690+00:00` for Slack, and
`2026-07-28T00:25:03.492019+00:00` for GitHub.

Query-centered model extraction was also checked against the retrieved
official bodies:

- Jira returned the documented
  `POST /rest/api/3/issue/{issueIdOrKey}/comment` operation and its
  “adds a comment to an issue” description.
- Slack returned `conversations.list` and its cursor-pagination guidance.
- GitHub returned the pull-request creation documentation.

The retrieval layer uses a 4 MiB hard bound only for Jira because Atlassian's
official Jira reference pages currently place useful endpoint documentation
after a multi-megabyte static application shell. Other providers retain the
512 KiB bound. Independently, model-visible text remains capped at 20,000
characters.
