#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.test"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.test.yml"

COMPOSE_ARGS=(-f "${COMPOSE_FILE}")
if [[ -f "${ENV_FILE}" ]]; then
  COMPOSE_ARGS=(--env-file "${ENV_FILE}" "${COMPOSE_ARGS[@]}")
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
else
  echo "No ${ENV_FILE} found; using compose defaults."
fi

NETWORK="${CONTENT_ASSESSMENT_DOCKER_NETWORK:-NSTL-overlay}"

echo "============================================"
echo " ContentAssessment test server deployment"
echo " Network: ${NETWORK}"
echo " Backend image:  ${REGISTRY:-docker-hosted.example.invalid/nstl}/${IMAGE_NAMESPACE:-content-assessment}/content-assessment-backend:${IMAGE_TAG:-v1.1.0}"
echo " Frontend image: ${REGISTRY:-docker-hosted.example.invalid/nstl}/${IMAGE_NAMESPACE:-content-assessment}/content-assessment-frontend:${IMAGE_TAG:-v1.1.0}"
echo " Published ports: frontend ${FRONTEND_PUBLISHED_PORT:-18100}, backend ${BACKEND_PUBLISHED_PORT:-17613}"
echo "============================================"

if ! docker network inspect "${NETWORK}" >/dev/null 2>&1; then
  echo "Docker network '${NETWORK}' does not exist."
  echo "Create it first or change CONTENT_ASSESSMENT_DOCKER_NETWORK in .env.test."
  exit 1
fi

mkdir -p "${CONTENT_ASSESSMENT_UPLOADS_DIR:-/data/nstl_service/content-assessment-backend/uploads}"
mkdir -p "${CONTENT_ASSESSMENT_LOGS_DIR:-/data/nstl_service/content-assessment-backend/logs}"

cd "${ROOT_DIR}/docker"

echo "Validating Stack configuration..."
docker compose "${COMPOSE_ARGS[@]}" config >/dev/null

docker compose "${COMPOSE_ARGS[@]}" pull
docker compose "${COMPOSE_ARGS[@]}" up -d
docker compose "${COMPOSE_ARGS[@]}" ps

echo "Waiting for backend healthcheck..."
for _ in $(seq 1 30); do
  health="$(docker inspect --format '{{.State.Health.Status}}' content-assessment-backend-test 2>/dev/null || true)"
  if [[ "${health}" == "healthy" ]]; then
    break
  fi
  if [[ "${health}" == "unhealthy" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" logs --tail=120 content_assessment_backend
    exit 1
  fi
  sleep 2
done

health="$(docker inspect --format '{{.State.Health.Status}}' content-assessment-backend-test 2>/dev/null || true)"
if [[ "${health}" != "healthy" ]]; then
  echo "Backend did not become healthy within 60 seconds."
  docker compose "${COMPOSE_ARGS[@]}" logs --tail=120 content_assessment_backend
  exit 1
fi

if [[ "${CONTENT_ASSESSMENT_SKIP_RETRIEVAL_CHECK:-false}" != "true" ]]; then
  echo "Checking NSTL mixed retrieval from backend container..."
  docker compose "${COMPOSE_ARGS[@]}" exec -T content_assessment_backend python - <<'PY'
from app.common.retrieval import LiteratureSearchClient, SearchRequest

resp = LiteratureSearchClient(timeout=120).search(
    SearchRequest(
        query="artificial intelligence literature retrieval deployment check",
        paper_num=1,
        year_range=(2016, 2026),
        languages=["zh", "en"],
        source_agent="deploy_check",
        user_scope="deploy_check",
        context={"topic": "NSTL mixed retrieval connectivity check"},
    )
)
print({
    "success": resp.success,
    "total": resp.total,
    "error": resp.error,
    "trace": resp.search_trace,
})
if not resp.success or not resp.papers:
    raise SystemExit("NSTL mixed retrieval check failed inside backend container")
PY
fi

echo ""
echo "Deployment complete."
echo "Frontend: http://SERVER_IP:${FRONTEND_PUBLISHED_PORT:-18100}/ai/agent/content_assessment/"
echo "Backend:  http://SERVER_IP:${BACKEND_PUBLISHED_PORT:-17613}"
echo "Docs:     http://SERVER_IP:${BACKEND_PUBLISHED_PORT:-17613}/docs"
