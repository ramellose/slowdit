#!/usr/bin/env bash
# deploy.sh — build and (re)deploy the slowdit server image on this node.
#
# Usage:
#   scripts/deploy.sh [--port 8080] [--image slowdit:latest]
#                     [--ssh-dir DIR | --no-ssh]
#
# Steps:
#   1. check the host has a reachable docker daemon
#   2. build the server image (.docker/Dockerfile)
#   3. recreate the container with the standard mounts:
#        - docker.sock      docker-out-of-docker — the container drives the
#                           HOST daemon (the image ships the client only)
#        - run dir          the one writable mount besides the sock
#        - ssh dir (ro)     git code sources are staged INSIDE the container,
#                           so ssh:// URLs need an identity there
#   4. wait for /health (docker: true), then run `slowdit doctor` inside the
#      container — the deploy is done when the audit passes, not when the
#      container starts.
set -euo pipefail

PORT=8080
IMAGE=slowdit:latest
SSH_DIR="${HOME}/.ssh"
NO_SSH=0
CONTAINER=slowdit
RUN_DIR=/var/tmp/slowdit/runs

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)    PORT="$2"; shift 2 ;;
    --image)   IMAGE="$2"; shift 2 ;;
    --ssh-dir) SSH_DIR="$2"; shift 2 ;;
    --no-ssh)  NO_SSH=1; shift ;;
    -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> 1/4 host prerequisites"
if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  echo "    FAIL: docker daemon not reachable on this host" >&2
  exit 1
fi
echo "    ok: docker daemon reachable"

echo "==> 2/4 build ${IMAGE}"
docker build -f "${REPO_ROOT}/.docker/Dockerfile" -t "${IMAGE}" "${REPO_ROOT}"

echo "==> 3/4 start container (recreates '${CONTAINER}')"
MOUNTS=(-v /var/run/docker.sock:/var/run/docker.sock
        -v "${RUN_DIR}:${RUN_DIR}")
if [[ "${NO_SSH}" -eq 1 ]]; then
  echo "    note: --no-ssh — ssh:// git sources will not work (use https + token)"
elif [[ ! -d "${SSH_DIR}" ]]; then
  echo "    WARN: ${SSH_DIR} not found — ssh:// git sources will not work"
  echo "          (pass --ssh-dir DIR, or use https:// URLs with a token)"
else
  MOUNTS+=(-v "${SSH_DIR}:/root/.ssh:ro")
  echo "    ok: mounting ${SSH_DIR} -> /root/.ssh (ro)"
fi
mkdir -p "${RUN_DIR}"
docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
docker run -d --name "${CONTAINER}" --restart unless-stopped \
  -p "${PORT}:${PORT}" \
  -e GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new' \
  "${MOUNTS[@]}" \
  "${IMAGE}" >/dev/null
echo "    ok: container '${CONTAINER}' running on port ${PORT}"

echo "==> 4/4 verify (/health + slowdit doctor inside the container)"
HEALTH=""
for _ in $(seq 1 30); do
  HEALTH=$(curl -s "http://localhost:${PORT}/health" || true)
  if [[ "${HEALTH}" == *'"docker":true'* || "${HEALTH}" == *'"docker": true'* ]]; then
    break
  fi
  sleep 1
done
echo "    /health: ${HEALTH}"
if ! docker exec "${CONTAINER}" slowdit doctor; then
  echo "    FAIL: doctor reported an unhealthy node" >&2
  exit 1
fi

echo
echo "deploy complete — smoke test: see the README 'Local profile — verify' section"
