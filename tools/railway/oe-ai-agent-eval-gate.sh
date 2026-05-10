#!/usr/bin/env bash
set -euo pipefail

# Chat eval gate for oe-ai-agent deployments.
# Runs deterministic mock chat evals to catch harness/tool-routing regressions.
#
# Usage: tools/railway/oe-ai-agent-eval-gate.sh [--keep-artifacts]
#
# Returns: exit 0 on pass, exit 1 on failure
# Requirements: uv, git

cd "$(dirname "$0")/../.."
REPO_ROOT="$(pwd)"
EVAL_DIR="$REPO_ROOT/oe-ai-agent"
OUTPUT_DIR="$EVAL_DIR/evals/runs"
KEEP_ARTIFACTS="false"

usage() {
    cat <<'USAGE'
Run deterministic mock chat evals as a deployment gate.

Usage:
  tools/railway/oe-ai-agent-eval-gate.sh [options]

Options:
  --keep-artifacts    Keep the JSONL output file after successful run
  -h, --help          Show this help

Examples:
  tools/railway/oe-ai-agent-eval-gate.sh
  tools/railway/oe-ai-agent-eval-gate.sh --keep-artifacts

Returns exit 0 if all expectations pass, exit 1 otherwise.
The mock provider is always used (no API costs, deterministic).
USAGE
}

die() {
    printf 'eval-gate error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '[eval-gate] %s\n' "$*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-artifacts)
            KEEP_ARTIFACTS="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

require_command uv
require_command git

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Build label with git SHA for traceability
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
LABEL="gate-${GIT_SHA}"
OUTPUT_FILE="$OUTPUT_DIR/pre-deploy-gate.jsonl"

cd "$EVAL_DIR"

info "running chat evals with mock provider..."
info "label: $LABEL"
info "output: $OUTPUT_FILE"

# Run the evals without allowing uv to rewrite uv.lock during the deploy gate.
if uv run --locked python evals/run_chat_eval.py \
    --label "$LABEL" \
    --provider mock \
    --fail-on-expectations \
    --output "$OUTPUT_FILE" 2>&1; then
    info "all expectations passed"

    # Cleanup unless --keep-artifacts
    if [[ "$KEEP_ARTIFACTS" != "true" && -f "$OUTPUT_FILE" ]]; then
        rm -f "$OUTPUT_FILE"
        info "cleaned up output file (use --keep-artifacts to preserve)"
    fi

    exit 0
else
    EXIT_CODE=$?
    info "eval gate failed with exit code $EXIT_CODE"

    if [[ -f "$OUTPUT_FILE" ]]; then
        info "output preserved at: $OUTPUT_FILE"

        # Quick summary of failures
        if command -v jq >/dev/null 2>&1; then
            FAILED_COUNT=$(jq -r 'select(.known_limitation == false and (.expectations_met | to_entries | any(.value == false))) | .fixture_id' "$OUTPUT_FILE" 2>/dev/null | wc -l | tr -d ' ')
            if [[ "$FAILED_COUNT" -gt 0 ]]; then
                info "found $FAILED_COUNT turn(s) with failed expectations"
            fi
        fi
    fi

    exit 1
fi
