#!/bin/sh
# Seed Railway's empty sites/ volume from the image-baked template on first boot,
# then hand off to OpenEMR's auto-installer entrypoint.
set -eu

SITES_DIR="/var/www/localhost/htdocs/openemr/sites"
TEMPLATE_DIR="/var/sites-template"

if [ -d "$TEMPLATE_DIR" ] && [ ! -f "$SITES_DIR/default/sqlconf.php" ]; then
    echo "[railway-entrypoint] sites/ volume empty; seeding from $TEMPLATE_DIR"
    mkdir -p "$SITES_DIR"
    cp -a "$TEMPLATE_DIR/." "$SITES_DIR/"
    chown -R apache:apache "$SITES_DIR" 2>/dev/null || true
fi

cd /var/www/localhost/htdocs/openemr
exec ./openemr.sh "$@"
