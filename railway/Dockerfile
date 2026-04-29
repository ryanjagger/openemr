FROM openemr/openemr:latest

# Copy our Railway-specific entrypoint
COPY railway/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80 443
