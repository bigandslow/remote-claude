# Remote Claude - Network Filtering Proxy
# Squid proxy for domain allowlisting in Claude containers

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install ca-certificates first to enable HTTPS package fetching
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && apt-get update && apt-get install -y --no-install-recommends \
    squid \
    && rm -rf /var/lib/apt/lists/*

# Copy configuration template
COPY squid.conf /etc/squid/squid.conf

# Squid runs as proxy user
RUN chown -R proxy:proxy /var/spool/squid /var/log/squid

EXPOSE 3128

# Start squid in foreground
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
