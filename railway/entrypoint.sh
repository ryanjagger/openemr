#!/bin/bash
set -e

# Map Railway MySQL environment variables to OpenEMR expected format
# Railway provides: MYSQLHOST, MYSQLPORT, MYSQLUSER, MYSQLPASSWORD, MYSQLDATABASE

export MYSQL_HOST="${MYSQLHOST:-mysql}"
export MYSQL_ROOT_PASS="${MYSQLPASSWORD:-root}"
export MYSQL_USER="${MYSQLUSER:-openemr}"
export MYSQL_PASS="${MYSQLPASSWORD:-openemr}"

# OpenEMR admin credentials (set via Railway variables)
export OE_USER="${OE_USER:-admin}"
export OE_PASS="${OE_PASS:-pass}"

echo "========================================"
echo "OpenEMR Railway Configuration"
echo "========================================"
echo "MySQL Host: $MYSQL_HOST"
echo "MySQL Port: ${MYSQLPORT:-3306}"
echo "MySQL User: $MYSQL_USER"
echo "MySQL Database: ${MYSQLDATABASE:-openemr}"
echo "OE_USER: $OE_USER"
echo "========================================"

# Run the original OpenEMR entrypoint
exec /original-entrypoint.sh
