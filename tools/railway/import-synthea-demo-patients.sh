#!/usr/bin/env bash
set -euo pipefail

DEFAULT_PROJECT_NAME="deploy3"
DEFAULT_OPENEMR_SERVICE="openemr"
DEFAULT_SITE="default"
DEFAULT_OPENEMR_PATH="/var/www/localhost/htdocs/openemr"
DEFAULT_COUNT="50"
DEFAULT_COMPOSE_FILE="docker/development-easy/docker-compose.yml"

project_name="${RAILWAY_PROJECT_NAME:-$DEFAULT_PROJECT_NAME}"
openemr_service="${RAILWAY_OPENEMR_SERVICE:-$DEFAULT_OPENEMR_SERVICE}"
site="$DEFAULT_SITE"
openemr_path="$DEFAULT_OPENEMR_PATH"
count="$DEFAULT_COUNT"
compose_file="$DEFAULT_COMPOSE_FILE"
ccda_tar=""
dedup="false"
enable_moves="false"
cleanup_remote="true"
keep_local="false"
force_redeploy="false"

usage() {
    cat <<'USAGE'
Import Synthea-generated CCDA demo patients into the linked Railway OpenEMR service.

This script is intentionally guarded to the linked Railway project name "deploy3"
by default. Pass --project-name if you intentionally use a different project.

Usage:
  tools/railway/import-synthea-demo-patients.sh [options]

Options:
  --count N              Generate N local Synthea patients before uploading. Default: 50
  --ccda-tar PATH        Use an existing .tar.gz of CCDA XML files instead of generating locally
  --compose-file PATH    Docker Compose file for local generation. Default: docker/development-easy/docker-compose.yml
  --project-name NAME    Required linked Railway project name. Default: deploy3
  --service NAME         Railway OpenEMR service name. Default: openemr
  --site SITE            OpenEMR site id. Default: default
  --openemr-path PATH    Remote OpenEMR web root. Default: /var/www/localhost/htdocs/openemr
  --dedup                Enable importer duplicate checking
  --enable-moves         Move processed/duplicate CCDA files inside the remote import directory
  --keep-remote          Leave uploaded/extracted remote files in /tmp
  --keep-local           Keep generated local temporary files
  --force-redeploy       Redeploy OpenEMR even if OPENEMR_ENABLE_CCDA_IMPORT is already live
  -h, --help             Show this help

Examples:
  tools/railway/import-synthea-demo-patients.sh --count 50
  tools/railway/import-synthea-demo-patients.sh --ccda-tar /tmp/synthea-ccdas.tar.gz
USAGE
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '[synthea-railway] %s\n' "$*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --count)
            [[ $# -ge 2 ]] || die "--count requires a value"
            count="$2"
            shift 2
            ;;
        --ccda-tar)
            [[ $# -ge 2 ]] || die "--ccda-tar requires a path"
            ccda_tar="$2"
            shift 2
            ;;
        --compose-file)
            [[ $# -ge 2 ]] || die "--compose-file requires a path"
            compose_file="$2"
            shift 2
            ;;
        --project-name)
            [[ $# -ge 2 ]] || die "--project-name requires a value"
            project_name="$2"
            shift 2
            ;;
        --service)
            [[ $# -ge 2 ]] || die "--service requires a value"
            openemr_service="$2"
            shift 2
            ;;
        --site)
            [[ $# -ge 2 ]] || die "--site requires a value"
            site="$2"
            shift 2
            ;;
        --openemr-path)
            [[ $# -ge 2 ]] || die "--openemr-path requires a path"
            openemr_path="$2"
            shift 2
            ;;
        --dedup)
            dedup="true"
            shift
            ;;
        --enable-moves)
            enable_moves="true"
            shift
            ;;
        --keep-remote)
            cleanup_remote="false"
            shift
            ;;
        --keep-local)
            keep_local="true"
            shift
            ;;
        --force-redeploy)
            force_redeploy="true"
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

[[ "$count" =~ ^[1-9][0-9]*$ ]] || die "--count must be a positive integer"

require_command railway
require_command jq
require_command base64
require_command tar

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

local_workdir=""
created_local_tar="false"

cleanup_local() {
    if [[ "$keep_local" == "false" && -n "$local_workdir" && -d "$local_workdir" ]]; then
        rm -rf "$local_workdir"
    fi
}
trap cleanup_local EXIT

railway_status_json="$(railway status --json)"
linked_project_name="$(jq -r '.name' <<<"$railway_status_json")"
linked_project_id="$(jq -r '.id' <<<"$railway_status_json")"

if [[ "$linked_project_name" != "$project_name" ]]; then
    die "linked Railway project is '$linked_project_name' ($linked_project_id), expected '$project_name'. Run railway link for the intended project or pass --project-name."
fi

remote_exec() {
    railway ssh --service "$openemr_service" -- "$@"
}

wait_for_service_success() {
    local service="$1"
    local timeout_seconds="${2:-900}"
    local previous_deployment_id="${3:-}"
    local start
    start="$(date +%s)"

    while true; do
        local service_json status deployment_id running crashed exited
        service_json="$(railway service list --json | jq --arg service "$service" -r '.[] | select(.name == $service)')"
        [[ -n "$service_json" ]] || die "Railway service not found: $service"

        status="$(jq -r '.status // "UNKNOWN"' <<<"$service_json")"
        deployment_id="$(jq -r '.deploymentId // ""' <<<"$service_json")"
        running="$(jq -r '.replicas.running // 0' <<<"$service_json")"
        crashed="$(jq -r '.replicas.crashed // 0' <<<"$service_json")"
        exited="$(jq -r '.replicas.exited // 0' <<<"$service_json")"

        if [[ "$status" == "SUCCESS" && "$running" -ge 1 && ( -z "$previous_deployment_id" || "$deployment_id" != "$previous_deployment_id" ) ]]; then
            return 0
        fi

        if [[ "$status" == "CRASHED" || "$crashed" -gt 0 || "$exited" -gt 0 ]]; then
            die "$service is not healthy: status=$status running=$running crashed=$crashed exited=$exited"
        fi

        if (( "$(date +%s)" - start > timeout_seconds )); then
            die "timed out waiting for $service to reach SUCCESS"
        fi

        info "waiting for $service: status=$status deployment=$deployment_id running=$running"
        sleep 10
    done
}

ensure_importer_enabled() {
    local is_live="false"

    if remote_exec 'test "${OPENEMR_ENABLE_CCDA_IMPORT:-}" = "1"' >/dev/null 2>&1; then
        is_live="true"
    fi

    if [[ "$is_live" == "true" && "$force_redeploy" == "false" ]]; then
        info "OPENEMR_ENABLE_CCDA_IMPORT is already live on $openemr_service"
        return 0
    fi

    info "enabling OPENEMR_ENABLE_CCDA_IMPORT on $openemr_service"
    railway variable set \
        --service "$openemr_service" \
        --skip-deploys \
        OPENEMR_ENABLE_CCDA_IMPORT=1 >/dev/null

    local previous_deployment_id
    previous_deployment_id="$(railway service list --json | jq --arg service "$openemr_service" -r '.[] | select(.name == $service) | .deploymentId // ""')"

    info "redeploying $openemr_service so the importer flag enters the running snapshot"
    railway redeploy --service "$openemr_service" --yes >/dev/null
    wait_for_service_success "$openemr_service" 1200 "$previous_deployment_id"

    remote_exec 'test "${OPENEMR_ENABLE_CCDA_IMPORT:-}" = "1"' >/dev/null \
        || die "OPENEMR_ENABLE_CCDA_IMPORT did not become visible in the running container"
}

generate_ccda_tarball() {
    require_command docker
    [[ -f "$compose_file" ]] || die "compose file not found: $compose_file"

    local_workdir="$(mktemp -d /private/tmp/openemr-synthea-railway.XXXXXX)"
    local ccda_dir="$local_workdir/ccdas"
    local tar_path="$local_workdir/synthea-ccdas.tar.gz"

    info "starting local OpenEMR dev containers"
    docker compose -f "$compose_file" up --detach --wait

    info "clearing previous local Synthea CCDA output"
    docker compose -f "$compose_file" exec -T openemr \
        sh -lc 'rm -rf /root/synthea/output/ccda && mkdir -p /root/synthea/output/ccda'

    info "generating and locally importing $count Synthea patients via /root/devtools"
    docker compose -f "$compose_file" exec -T openemr \
        /root/devtools import-random-patients "$count"

    mkdir -p "$ccda_dir"
    info "copying generated CCDA XML files from local container"
    docker compose -f "$compose_file" cp openemr:/root/synthea/output/ccda/. "$ccda_dir/"

    local file_count
    file_count="$(find "$ccda_dir" -type f | wc -l | tr -d ' ')"
    [[ "$file_count" -gt 0 ]] || die "no CCDA files were generated"

    COPYFILE_DISABLE=1 tar -czf "$tar_path" -C "$ccda_dir" .
    ccda_tar="$tar_path"
    created_local_tar="true"
    info "created $ccda_tar from $file_count CCDA files"
}

print_remote_counts() {
    local label="$1"
    info "$label"
    remote_exec 'mariadb -h"$MYSQL_HOST" -P"${MYSQL_PORT:-3306}" -u"$MYSQL_USER" -p"$MYSQL_PASS" openemr -N -B -e '"'"'
SELECT "patients", COUNT(*) FROM patient_data
UNION ALL SELECT "encounters", COUNT(*) FROM form_encounter
UNION ALL SELECT "lists", COUNT(*) FROM lists;
'"'"''
}

if [[ -z "$ccda_tar" ]]; then
    generate_ccda_tarball
else
    [[ -f "$ccda_tar" ]] || die "CCDA tarball not found: $ccda_tar"
fi

case "$ccda_tar" in
    *.tar.gz|*.tgz) ;;
    *) die "--ccda-tar must point to a .tar.gz or .tgz archive" ;;
esac

ensure_importer_enabled

remote_stamp="$(date +%Y%m%d%H%M%S)"
remote_tar="/tmp/synthea-ccdas-${remote_stamp}.tar.gz"
remote_dir="/tmp/synthea-ccdas-${remote_stamp}"

print_remote_counts "remote row counts before import"

info "uploading $(du -h "$ccda_tar" | awk '{print $1}') CCDA archive to $openemr_service:$remote_tar"
base64 < "$ccda_tar" | remote_exec "base64 -d > '$remote_tar'"

info "verifying uploaded archive checksum"
local_hash="$(md5 "$ccda_tar" 2>/dev/null | awk '{print $NF}' || md5sum "$ccda_tar" | awk '{print $1}')"
remote_hash="$(remote_exec "md5sum '$remote_tar' | awk '{print \$1}'" | tail -n 1 | tr -d '\r')"
[[ "$local_hash" == "$remote_hash" ]] || die "checksum mismatch after upload"

info "extracting and importing CCDAs on Railway"
remote_exec "set -e
rm -rf '$remote_dir'
mkdir -p '$remote_dir'
tar -xzf '$remote_tar' -C '$remote_dir'
cd '$openemr_path'
php contrib/util/ccda_import/import_ccda.php \
  --sourcePath='$remote_dir' \
  --site='$site' \
  --openemrPath='$openemr_path' \
  --isDev=true \
  --enableMoves='$enable_moves' \
  --dedup='$dedup'"

print_remote_counts "remote row counts after import"

info "remote importer log tail"
remote_exec "tail -n 40 '$openemr_path/log.txt' 2>/dev/null || tail -n 40 '$remote_dir/log.txt' 2>/dev/null || true"

if [[ "$cleanup_remote" == "true" ]]; then
    info "cleaning remote temporary files"
    remote_exec "rm -rf '$remote_dir' '$remote_tar'"
else
    info "kept remote files: $remote_tar and $remote_dir"
fi

if [[ "$created_local_tar" == "true" && "$keep_local" == "true" ]]; then
    info "kept local generated archive: $ccda_tar"
fi

info "done"
