FROM python:3.13-slim AS builder

RUN pip install uv

WORKDIR /build

COPY pyproject.toml uv.lock ./
COPY packages/ packages/

RUN uv sync --frozen --no-dev --package aggregator-retriever

FROM python:3.13-slim AS runtime

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

LABEL org.opencontainers.image.title="aggregator-retriever" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="" \
      org.opencontainers.image.source="https://github.com/oscarrenalias/personal-aggregator"

RUN useradd --system --no-create-home --shell /sbin/nologin appuser

COPY --from=builder /build/.venv /app/.venv

USER appuser

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["aggregator-retriever"]
