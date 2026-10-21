#!/usr/bin/env bash
# Starts a named CodeBuild project, polls until it finishes, and exits non-zero
# unless the build status is SUCCEEDED. deploy-all.sh uses it for every CodeBuild
# job: the decider and session host image builds, the decider model upload and the
# catalog load. A failed status check (for example a brief network error) is
# retried rather than ending the wait while the build is still running.
#
# Usage: build-image.sh <codebuild-project-name> [poll-interval-seconds]
set -euo pipefail

PROJECT_NAME="${1:?Usage: build-image.sh <codebuild-project-name> [poll-interval-seconds]}"
POLL_INTERVAL_SECONDS="${2:-15}"

echo "Starting CodeBuild project: ${PROJECT_NAME}"
BUILD_ID=$(aws codebuild start-build \
  --project-name "${PROJECT_NAME}" \
  --query 'build.id' \
  --output text)

echo "Started build: ${BUILD_ID}"
echo "Polling every ${POLL_INTERVAL_SECONDS}s until the build finishes..."

FAILED_CHECKS=0
while true; do
  if ! STATUS=$(aws codebuild batch-get-builds \
      --ids "${BUILD_ID}" \
      --query 'builds[0].buildStatus' \
      --output text); then
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
    if (( FAILED_CHECKS >= 10 )); then
      echo "Could not read the status of ${BUILD_ID} 10 times in a row; it may still be running." >&2
      exit 1
    fi
    sleep "${POLL_INTERVAL_SECONDS}"
    continue
  fi
  FAILED_CHECKS=0

  case "${STATUS}" in
    IN_PROGRESS)
      echo "  ... still in progress"
      sleep "${POLL_INTERVAL_SECONDS}"
      ;;
    SUCCEEDED)
      echo "Build ${BUILD_ID} succeeded."
      exit 0
      ;;
    FAILED|FAULT|TIMED_OUT|STOPPED)
      echo "Build ${BUILD_ID} finished with status ${STATUS}." >&2
      exit 1
      ;;
    *)
      echo "Build ${BUILD_ID} returned unexpected status ${STATUS}." >&2
      exit 1
      ;;
  esac
done
