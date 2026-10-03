# History comes from an artifact the action uploads on main

Reading raw JUnit from past runs only works if every user uploads it under an agreed name. Instead, the action uploads a small `failtriage-history` artifact (test id, status, attempts, sha, run id) on runs of main, and PR runs read the last N of them. It needs only `actions: read` on PRs. Artifacts expire after the retention period and fork PRs cannot read them, so a cold start has no history and the report says `history: none` rather than implying a clean record.
