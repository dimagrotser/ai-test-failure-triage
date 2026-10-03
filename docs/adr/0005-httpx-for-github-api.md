# Plain httpx for the GitHub API

The action needs four things: list and update PR comments, list workflow runs, download artifacts and read PR files. A small client over httpx covers that with one pinned dependency and works with `httpx.MockTransport` in tests. PyGithub would pull in crypto packages that every composite-action run has to install. The cost is writing pagination and rate-limit retry myself.
