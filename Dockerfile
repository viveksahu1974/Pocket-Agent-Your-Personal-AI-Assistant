FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY pocket_agent/ ./pocket_agent/
RUN pip install --no-cache-dir '.[all]'
RUN useradd -r -u 10001 -m agent && mkdir -p /app/agent-data && chown -R agent:agent /app/agent-data
USER agent
ENV PYTHONUNBUFFERED=1
CMD ["pocket-agent", "run"]
