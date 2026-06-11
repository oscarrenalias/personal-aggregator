#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv &>/dev/null; then
    echo "Error: uv is not on PATH. Install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

IMAGE_PREFIX=${IMAGE_PREFIX:-personal-aggregator}
VERSION="v$(uv version --short)"
SERVICES=(retriever processor summarize-rank admin)

echo "Building images version=${VERSION} prefix=${IMAGE_PREFIX}"

for SERVICE in "${SERVICES[@]}"; do
    echo "  -> ${IMAGE_PREFIX}/aggregator-${SERVICE}:${VERSION}"
    docker buildx build \
        --platform linux/arm64 \
        --build-arg APP_VERSION="${VERSION}" \
        -t "${IMAGE_PREFIX}/aggregator-${SERVICE}:${VERSION}" \
        -t "${IMAGE_PREFIX}/aggregator-${SERVICE}:latest" \
        -f "packages/aggregator-${SERVICE}/Dockerfile" \
        .
done

echo "Done."
