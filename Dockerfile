FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.1
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY data ./data
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["incidentops", "serve", "--host", "0.0.0.0", "--port", "8000"]
