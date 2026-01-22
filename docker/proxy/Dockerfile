# Remote Claude - Network Filtering Proxy
# Squid proxy for domain allowlisting in Claude containers

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    squid \
    && rm -rf /var/lib/apt/lists/*

# Copy configuration template
COPY squid.conf /etc/squid/squid.conf

# Squid runs as proxy user
RUN chown -R proxy:proxy /var/spool/squid /var/log/squid

EXPOSE 3128

# Start squid in foreground
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
