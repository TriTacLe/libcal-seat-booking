FROM python:3.12-slim-bookworm

WORKDIR /app
ENV TZ=Africa/Johannesburg

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-dev

COPY config.yaml ./

VOLUME ["/app/data"]

ENTRYPOINT [".venv/bin/uct"]
CMD ["book", "--commit"]
