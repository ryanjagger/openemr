FROM openemr/openemr:flex

COPY --chown=apache:apache . /var/www/localhost/htdocs/openemr/
