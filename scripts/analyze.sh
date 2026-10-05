#!/usr/bin/env bash
# Runs failtriage for action.yml. Every input arrives as an environment variable,
# so nothing from the workflow or the pull request is ever parsed as shell.
set -euo pipefail

# One report path per line. A path stays one argument, whatever characters it has.
source_args=()
add_files() {
  local flag=$1 line
  while IFS= read -r line; do
    if [[ -n "${line//[[:space:]]/}" ]]; then
      source_args+=("$flag" "$line")
    fi
  done <<< "$2"
}
junit_paths=${JUNIT:-}
playwright_paths=${PLAYWRIGHT:-}
allure_paths=${ALLURE:-}
add_files --junit "$junit_paths"
add_files --playwright "$playwright_paths"
add_files --allure "$allure_paths"

formats=0
for paths in "$junit_paths" "$playwright_paths" "$allure_paths"; do
  if [[ -n "${paths//[[:space:]]/}" ]]; then
    formats=$((formats + 1))
  fi
done
if [ "$formats" -gt 1 ]; then
  echo "::error::set only one of junit, playwright and allure"
  exit 1
fi
if [ "$formats" -eq 0 ]; then
  echo "::error::set junit, playwright or allure to the report to analyze"
  exit 1
fi

report="$RUNNER_TEMP/failtriage-report.json"
args=(analyze "${source_args[@]}" --model "$MODEL" --json --summary)

if [ "$EVENT_NAME" = "pull_request" ]; then
  args+=(--repo "$REPO" --pr "$PR_NUMBER")
  # Fork and dependabot runs get a read-only token and no secrets. The same goes
  # for a workflow that dropped pull-requests: write and set comment to false.
  if [ "$COMMENT" != "true" ] || [ "$HEAD_REPO" != "$REPO" ] || [ "$ACTOR" = "dependabot[bot]" ]; then
    unset ANTHROPIC_API_KEY
  else
    args+=(--comment)
    if [ -n "$COMMENT_KEY" ]; then
      args+=(--comment-key "$COMMENT_KEY")
    fi
  fi
fi

uv run --project "$GITHUB_ACTION_PATH" --locked --no-dev failtriage "${args[@]}" > "$report"

groups=$(uv run --project "$GITHUB_ACTION_PATH" --locked --no-dev python -c \
  'import json, sys; print(len(json.load(open(sys.argv[1]))["groups"]))' "$report")

{
  echo "report=$report"
  echo "groups=$groups"
} >> "$GITHUB_OUTPUT"

# Only a run of main feeds the history that pull request runs read (ADR 0002).
if [ "$EVENT_NAME" != "pull_request" ] && [ "$REF" = "refs/heads/main" ]; then
  history="$RUNNER_TEMP/failtriage-history/history.json"
  mkdir -p "$(dirname "$history")"
  uv run --project "$GITHUB_ACTION_PATH" --locked --no-dev failtriage history \
    "${source_args[@]}" --sha "$SHA" --run-id "$RUN_ID" > "$history"
  echo "history=$history" >> "$GITHUB_OUTPUT"
fi
