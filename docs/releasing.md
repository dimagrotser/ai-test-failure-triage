# Releasing

Tags go on a commit of main, after the pull request is merged. I do not tag from a branch.

```
git checkout main && git pull
gh run list --branch main --limit 4
```

`ci` and `self-test` have to be green on the commit you are about to tag, compare `git rev-parse HEAD` with the SHA in the run list. Neither uses an API key, so a green run means the release works without one.

```
git tag -a v1.0.0 -m "v1.0.0" && git push origin v1.0.0
git tag -a v1 -m "v1" && git push origin v1
gh release create v1.0.0 --title v1.0.0 --notes-file docs/releases/v1.0.0.md
```

Then run the example from the README against the new tag:

```
gh workflow run release-check.yml && gh run watch
```

It fails with "unable to resolve action" until `v1` exists.

## Later 1.x releases

Bump `version` in `pyproject.toml`, run `uv lock`, add `docs/releases/v1.x.y.md` and merge. Tag `v1.x.y` the same way. Then move `v1` to the same commit:

```
git tag -fa v1 -m "v1" && git push -f origin v1
```

That force-pushes a tag. It is the one place where it is meant to happen, and it only ever moves `v1` forward.
