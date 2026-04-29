FROM openemr/openemr:latest

# Copy our Railway-specific entrypoint
COPY railway/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Keep the original entrypoint available
RUN cp /etc/docker-entrypoint.sh /original-entrypoint.sh

EXPOSE 80 443
