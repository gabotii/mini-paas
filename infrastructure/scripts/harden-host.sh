#!/bin/sh
# Optional host hardening for Linux servers running Mini PaaS (run as root, after `docker compose up`).
#
# User containers and image builds need outbound internet (package installs, external APIs),
# so their networks cannot be `internal`. This script adds DOCKER-USER iptables rules that keep
# that egress while blocking the destinations a malicious app or Dockerfile would target:
#
#   * cloud metadata services (169.254.169.254, fd00:ec2::254) — credential theft on AWS/GCP/Azure
#   * private ranges (RFC 1918, CGNAT, link-local) — the host's LAN and other internal services
#
# Applies to every bridge created by the platform: per-project networks (paas-net-*) and the
# isolated build network (paas-build). Rules are not persistent: add them to your firewall
# configuration (e.g. iptables-persistent) to survive reboots. Review before using in production.
set -eu

BLOCKED="169.254.0.0/16 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10"

bridges() {
  for net in $(docker network ls --format '{{.Name}}' | grep -E '^(paas-net-|paas-build$)' || true); do
    id=$(docker network inspect -f '{{.Id}}' "$net")
    echo "br-$(echo "$id" | cut -c1-12)"
  done
}

for bridge in $(bridges); do
  # Replies to connections initiated from outside (e.g. Traefik → app) stay allowed.
  iptables -C DOCKER-USER -i "$bridge" -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN 2>/dev/null ||
    iptables -I DOCKER-USER -i "$bridge" -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
  for cidr in $BLOCKED; do
    # Traffic inside the same bridge (app ↔ proxy on its project network) is not routed through
    # DOCKER-USER, so blocking private ranges here only affects traffic leaving the bridge.
    iptables -C DOCKER-USER -i "$bridge" -d "$cidr" -j DROP 2>/dev/null ||
      iptables -A DOCKER-USER -i "$bridge" -d "$cidr" -j DROP
  done
  echo "hardened $bridge"
done

echo "Done. Re-run after new projects are deployed (new project networks), e.g. from cron."
