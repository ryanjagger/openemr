#!/usr/bin/env bash
set -euo pipefail

DEFAULT_PROJECT_ID="1dcb624a-bc85-4329-912e-ca2beb04a2c1"
DEFAULT_PROJECT_NAME="deploy3"
DEFAULT_ENVIRONMENT="production"

target="${1:-}"
project_id="$DEFAULT_PROJECT_ID"
project_name="$DEFAULT_PROJECT_NAME"
environment="$DEFAULT_ENVIRONMENT"
message=""
dry_run="false"
keep_stage="false"
force="false"

usage() {
    cat <<'USAGE'
Manually deploy OpenEMR services to the deploy3 Railway project.

Usage:
  tools/railway/deploy.sh openemr [options]
  tools/railway/deploy.sh oe-ai-agent [options]
  tools/railway/deploy.sh all [options]

Options:
  --message TEXT         Deployment message. Defaults to "Manual deploy <service>: <git-sha>"
  --project-id ID        Railway project ID. Default: deploy3 project ID
  --project-name NAME    Required linked Railway project name. Default: deploy3
  --environment NAME     Railway environment. Default: production
  --dry-run              Build staging directories and print railway up commands without deploying
  --force                Force a rebuild by adding a timestamp label to the staged Dockerfile
  --keep-stage           Keep temporary staging directories after deploy
  -h, --help             Show this help

Examples:
  tools/railway/deploy.sh openemr
  tools/railway/deploy.sh oe-ai-agent --message "Deploy agent prompt changes"
  tools/railway/deploy.sh all
USAGE
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '[railway-deploy] %s\n' "$*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

if [[ -z "$target" || "$target" == "-h" || "$target" == "--help" ]]; then
    usage
    exit 0
fi

case "$target" in
    openemr|oe-ai-agent|all)
        shift
        ;;
    *)
        die "unknown target '$target'; expected openemr, oe-ai-agent, or all"
        ;;
esac

while [[ $# -gt 0 ]]; do
    case "$1" in
        --message)
            [[ $# -ge 2 ]] || die "--message requires a value"
            message="$2"
            shift 2
            ;;
        --project-id)
            [[ $# -ge 2 ]] || die "--project-id requires a value"
            project_id="$2"
            shift 2
            ;;
        --project-name)
            [[ $# -ge 2 ]] || die "--project-name requires a value"
            project_name="$2"
            shift 2
            ;;
        --environment)
            [[ $# -ge 2 ]] || die "--environment requires a value"
            environment="$2"
            shift 2
            ;;
        --dry-run)
            dry_run="true"
            shift
            ;;
        --force)
            force="true"
            shift
            ;;
        --keep-stage)
            keep_stage="true"
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

require_command git
require_command jq
require_command railway
require_command rsync

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

stages=()
staged_path=""

cleanup() {
    if [[ "$keep_stage" == "false" ]]; then
        for stage in "${stages[@]+"${stages[@]}"}"; do
            [[ -n "$stage" && -d "$stage" ]] && rm -rf "$stage"
        done
    fi
}
trap cleanup EXIT

require_linked_project() {
    local status_json linked_name linked_id
    status_json="$(railway status --json)"
    linked_name="$(jq -r '.name' <<<"$status_json")"
    linked_id="$(jq -r '.id' <<<"$status_json")"

    if [[ "$linked_name" != "$project_name" || "$linked_id" != "$project_id" ]]; then
        die "linked Railway project is '$linked_name' ($linked_id), expected '$project_name' ($project_id). Run railway link for deploy3 before deploying."
    fi
}

git_sha() {
    git rev-parse --short HEAD 2>/dev/null || printf 'unknown'
}

deploy_message_for() {
    local service="$1"
    if [[ -n "$message" ]]; then
        printf '%s' "$message"
        return 0
    fi
    printf 'Manual deploy %s: %s' "$service" "$(git_sha)"
}

stage_openemr() {
    local stage
    stage="$(mktemp -d /private/tmp/openemr-railway-stage.XXXXXX)"
    stages+=("$stage")

    rsync -aR \
        railway.json \
        Dockerfile.railway \
        railway-entrypoint.sh \
        interface/modules/custom_modules/oe-module-ai-agent \
        interface/patient_file/summary/copilot.php \
        interface/main/tabs/menu/menus/patient_menus/standard.json \
        sql \
        "$stage/"

    force_dockerfile "$stage/Dockerfile.railway"
    staged_path="$stage"
}

stage_oe_ai_agent() {
    local stage
    stage="$(mktemp -d /private/tmp/oe-ai-agent-railway-stage.XXXXXX)"
    stages+=("$stage")

    rsync -aR \
        oe-ai-agent/Dockerfile \
        oe-ai-agent/railway.json \
        oe-ai-agent/pyproject.toml \
        oe-ai-agent/uv.lock \
        oe-ai-agent/src \
        "$stage/"

    force_dockerfile "$stage/oe-ai-agent/Dockerfile"
    staged_path="$stage/oe-ai-agent"
}

force_dockerfile() {
    local dockerfile="$1"

    if [[ "$force" != "true" ]]; then
        return 0
    fi

    [[ -f "$dockerfile" ]] || die "Dockerfile not found while forcing rebuild: $dockerfile"
    printf '\nLABEL openemr.railway.force-rebuild="%s"\n' "$(date -u +%Y%m%dT%H%M%SZ)" >> "$dockerfile"
}

run_railway_up() {
    local service="$1"
    local path="$2"
    local deploy_message
    deploy_message="$(deploy_message_for "$service")"

    info "staged $service at $path ($(du -sh "$path" | awk '{print $1}'))"
    if [[ "$force" == "true" ]]; then
        info "force enabled for $service; staged Dockerfile includes a timestamp label"
    fi

    local command=(
        railway up "$path"
        --project "$project_id"
        --environment "$environment"
        --service "$service"
        --path-as-root
        --ci
        --message "$deploy_message"
    )

    if [[ "$dry_run" == "true" ]]; then
        printf '%q ' "${command[@]}"
        printf '\n'
        return 0
    fi

    "${command[@]}"
}

deploy_openemr() {
    stage_openemr
    run_railway_up "openemr" "$staged_path"
}

deploy_oe_ai_agent() {
    stage_oe_ai_agent
    run_railway_up "oe-ai-agent" "$staged_path"
}

require_linked_project

case "$target" in
    openemr)
        deploy_openemr
        ;;
    oe-ai-agent)
        deploy_oe_ai_agent
        ;;
    all)
        deploy_openemr
        deploy_oe_ai_agent
        ;;
esac

info "done"
