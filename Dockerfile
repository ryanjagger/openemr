FROM openemr/openemr:latest

COPY --chown=apache:apache . /var/www/localhost/htdocs/openemr/
