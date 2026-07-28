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

## All-provider starting-document smoke

At `2026-07-28T00:43:41Z`, the exact gateway searched each of the 11 provider
catalogs and fetched its first provider-owned starting document. All 11 fetches
returned HTTP 200 and nonempty candidate-visible content. The table records
only provenance and bounds; no documentation body is copied into the
benchmark.

| Provider | Starting document | Retrieved bytes | Candidate-visible chars | SHA-256 | Raw retrieval truncated |
| --- | --- | ---: | ---: | --- | --- |
| Discord | `https://docs.discord.com/developers/reference` | 524,288 | 20,000 | `c59acfc9c6313a8dd13f22ff7589aa753be57201aa13a8f9c1847f9f0d54e842` | Yes |
| GitHub | `https://docs.github.com/en/rest?apiVersion=2022-11-28` | 412,113 | 6,265 | `0163e34f8d32e2d7fe483ca48b0d1c550e6708bf8220d0d7f78562f4f66c0f70` | No |
| GitLab | `https://docs.gitlab.com/api/rest/` | 59,484 | 16,847 | `dbe2d46792df25bf6d7a2f3e8c630549e8541f1a8a8ddf897f4a4729cb6a9ef4` | No |
| Gmail | `https://developers.google.com/workspace/gmail/api/reference/rest` | 370,050 | 20,000 | `345c343032827a880dc73d6106b41444038fd6e3f223b7c03a7d51506d2d2ce3` | No |
| Google Calendar | `https://developers.google.com/workspace/calendar/api/v3/reference` | 177,077 | 12,610 | `a7d2c8579f0a1e64efebb17d153ed235a47acb722baf3abc6f309f50dfa0f039` | No |
| Google Drive | `https://developers.google.com/workspace/drive/api/reference/rest/v3` | 317,577 | 17,262 | `d5d3f0658826fdf96ff9e22988406b1cd98503b19bbe7882941f69b68ce160c4` | No |
| Jira | `https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/` | 2,916,920 | 13,853 | `8e478a53945537c95ba059c5e3a45fcf97ad8dc9a573aae52ea02cc4175bd0c2` | No |
| Linear | `https://linear.app/developers/graphql?noRedirect=1` | 9,805 | 9,801 | `9b796ed7a3b442e5047735ceac15f5be1e3779410eb29f68218118989f91d5a6` | No |
| Notion | `https://developers.notion.com/llms.txt` | 30,261 | 20,000 | `35eb6738090550e23e75005784cd40ca553135c74a17a8c0e4ec1c75b714f7e0` | No |
| Slack | `https://docs.slack.dev/reference/methods/` | 43,513 | 1,284 | `6a7d7ff6712198c5a622bbe41f81ff7edd130c750205a184e4c5b991f5151203` | No |
| Stripe | `https://docs.stripe.com/api` | 524,288 | 8,737 | `0a71b79620fd23298e55d1bf41e26b2a821f794fdbdefa18bba63a13f1195272` | Yes |
