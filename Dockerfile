FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    BLABLALINK_CONFIG_DIR=/config TZ=Asia/Taipei
WORKDIR /app
COPY pyproject.toml ./
COPY blablalink_mcp ./blablalink_mcp
RUN pip install --no-cache-dir . && useradd -r -u 10001 mcp
USER mcp
EXPOSE 8765
CMD ["blablalink-mcp", "--http", "--host", "0.0.0.0", "--port", "8765"]
