#!/bin/bash
# Wrapper script to map Railway MySQL environment variables to OpenEMR expected format

# Map Railway MySQL environment variables to OpenEMR expected format
# Railway provides: MYSQLHOST, MYSQLPORT, MYSQLUSER, MYSQLPASSWORD, MYSQLDATABASE
# OpenEMR expects: MYSQL_HOST, MYSQL_ROOT_PASS, MYSQL_USER, MYSQL_PASS

export MYSQL_HOST="${MYSQLHOST:-mysql}"
export MYSQL_PORT="${MYSQLPORT:-3306}"
export MYSQL_ROOT_PASS="${MYSQLPASSWORD:-root}"
export MYSQL_USER="${MYSQLUSER:-openemr}"
export MYSQL_PASS="${MYSQLPASSWORD:-openemr}"
export MYSQL_DATABASE="${MYSQLDATABASE:-openemr}"

# OpenEMR admin credentials (set via Railway variables or use defaults)
export OE_USER="${OE_USER:-admin}"
export OE_PASS="${OE_PASS:-pass}"

echo "========================================"
echo "OpenEMR Railway Configuration"
echo "========================================"
echo "MySQL Host: $MYSQL_HOST"
echo "MySQL Port: $MYSQL_PORT"
echo "MySQL User: $MYSQL_USER"
echo "MySQL Database: $MYSQL_DATABASE"
echo "OE_USER: $OE_USER"
echo "========================================"

# Execute the original OpenEMR entrypoint with all arguments
exec /usr/local/bin/docker-entrypoint.sh "$@"
