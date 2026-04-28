# syntax=docker/dockerfile:1
#
# Demo/MVP image: bakes the local working tree on top of the upstream flex-edge
# image so PaaS deploys (Railway / Fly.io) can run a self-contained container
# without bind mounts.
#
# The flex-edge entrypoint already wires up Apache + PHP + the
# auto_configure.php first-boot installer. We replace its baked source with
# this repo's working tree, install runtime deps, build front-end assets, and
# drop build-only artefacts to keep the layer small.

FROM openemr/openemr:flex-edge

# The upstream WorkingDir is /var/www/localhost/htdocs and the image's CMD is
# ./openemr.sh — sitting alongside the openemr/ source tree. Do NOT change
# WORKDIR or that CMD will fail to resolve. We cd into the source dir only for
# the build step.
COPY . /var/www/localhost/htdocs/openemr/

# Install runtime dependencies and build assets. Everything happens in one
# layer so the build caches and intermediate node_modules don't bloat the
# final image.
RUN set -eux \
 && cd /var/www/localhost/htdocs/openemr \
 && composer install --no-dev --no-interaction --optimize-autoloader --no-progress \
 && npm ci --no-audit --no-fund \
 && npm run build \
 && rm -rf node_modules \
 && composer clear-cache \
 && npm cache clean --force

EXPOSE 80 443
