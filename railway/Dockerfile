FROM openemr/openemr:latest

# The official OpenEMR image has its own entrypoint at /usr/local/bin/docker-entrypoint.sh
# We just need to ensure our environment variables are mapped correctly
# Railway provides: MYSQLHOST, MYSQLPORT, MYSQLUSER, MYSQLPASSWORD, MYSQLDATABASE
# OpenEMR expects: MYSQL_HOST, MYSQL_ROOT_PASS, MYSQL_USER, MYSQL_PASS, OE_USER, OE_PASS

# Create a wrapper script that maps Railway env vars to OpenEMR format
COPY railway/railway-env-wrapper.sh /railway-env-wrapper.sh
RUN chmod +x /railway-env-wrapper.sh

# Override the entrypoint to use our wrapper
ENTRYPOINT ["/railway-env-wrapper.sh"]
