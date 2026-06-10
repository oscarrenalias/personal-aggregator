#!/usr/bin/env bash
set -euo pipefail

IMAGE_PREFIX=${IMAGE_PREFIX:-personal-aggregator}
VERSION=$(git describe --tags --always --dirty)
SERVICES=(retriever processor summarize-rank admin)

echo "Building images version=${VERSION} prefix=${IMAGE_PREFIX}"

for SERVICE in "${SERVICES[@]}"; do
    echo "  -> ${IMAGE_PREFIX}/aggregator-${SERVICE}:${VERSION}"
    docker buildx build \
        --platform linux/arm64 \
        --build-arg APP_VERSION="${VERSION}" \
        -t "${IMAGE_PREFIX}/aggregator-${SERVICE}:${VERSION}" \
        -t "${IMAGE_PREFIX}/aggregator-${SERVICE}:dev" \
        -f "packages/aggregator-${SERVICE}/Dockerfile" \
        .
done

echo "Done."
