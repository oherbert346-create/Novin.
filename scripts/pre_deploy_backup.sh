#!/bin/bash

set -euo pipefail

STATE_DIR=".deploy_state/backups"
timestamp="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$STATE_DIR"

volume_name="$(docker volume ls --format '{{.Name}}' | awk '/(^|_)novin_data$/ {print; exit}')"
if [ -z "$volume_name" ]; then
  echo "No novin_data Docker volume found, skipping DB backup."
  exit 0
fi

archive_name="novin-db-${timestamp}.tar.gz"
docker run --rm \
  -v "${volume_name}:/data" \
  -v "$(pwd)/${STATE_DIR}:/backup" \
  alpine:3.20 \
  sh -c "if [ -f /data/novin.db ]; then tar -czf /backup/${archive_name} -C /data novin.db; else echo 'No novin.db found in volume, skipping DB backup.'; fi"

if [ -f "${STATE_DIR}/${archive_name}" ]; then
  ln -sfn "${archive_name}" "${STATE_DIR}/latest.tar.gz"
  echo "Backup created: ${STATE_DIR}/${archive_name}"
else
  echo "Backup skipped because novin.db was not found."
fi
