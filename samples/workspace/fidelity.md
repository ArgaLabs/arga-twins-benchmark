# Frontend verification status

The selected sample twins are GitHub, Gmail, Google Calendar, Google Sheets and Google Docs. Frontend completion also covers the removed Notion, Linear and Stripe twins.

Full provider parity remains unverified. The implementation inventory is in the companion twin PR's docs/twins/lab-sample/completion-tracker.md. Functional API checks do not establish pixel parity, mobile coverage or complete hosted candidate success. No frontend gate is closed merely because a button exists.

The packager reads readiness.json and rejects release until all required checks carry verified, content-hashed evidence. Text and JSON evidence must live under samples/workspace/evidence and are included in the ZIP. Screenshot files are excluded. The gate also binds evidence to a SHA-256 digest of the current runner source, fixtures, task contracts and dependencies. There is no override flag.
