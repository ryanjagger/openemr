# Session History

## deploy with railway
claude --resume "deploy-openemr-railway"

## module architecture
claude --resume 29bc85b5-a161-46f4-be48-a0fa3882d7b2

## deploy troubleshooting
claude --resume 6d42bbce-3d1d-4b3f-a5f8-a57ef4b2e36f

## hello-world Plan: AI-Generated Patient Summary Card on the Dashboard
claude --resume 29bc85b5-a161-46f4-be48-a0fa3882d7b2
 ~/.claude/plans/okay-so-i-m-in-silly-engelbart.md

# local dev workflow
1. Bring up the dev stack (pulls images + runs initial install, waits for healthchecks):
docker compose -f /Users/ryan/gauntlet/openemr/docker/development-easy/docker-compose.yml up --detach --wait

2. Reset DB + load demo data (wipes and reseeds):
docker compose -f /Users/ryan/gauntlet/openemr/docker/development-easy/docker-compose.yml exec -T openemr /root/devtools dev-reset-install-demodata

Plus the verification query I ran after:
docker compose -f /Users/ryan/gauntlet/openemr/docker/development-easy/docker-compose.yml exec -T mysql mariadb -uopenemr -popenemr openemr -e "SELECT COUNT(*) FROM patient_data;"

Notes:
- -T on exec disables TTY allocation — needed because I was running non-interactively. If you run these by hand from a terminal, you can drop -T.
- If you cd docker/development-easy first, you can shorten to docker compose up --detach --wait and docker compose exec openemr /root/devtools dev-reset-install-demodata (the form used in CONTRIBUTING.md).
- Step 2 is destructive — it drops and recreates the openemr database before reseeding.