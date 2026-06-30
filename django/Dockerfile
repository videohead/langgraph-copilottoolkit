# Build context: repo root (so both src/ and django/ are available)
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends curl \
	&& rm -rf /var/lib/apt/lists/*

# Install dependencies as a cacheable layer
COPY django/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the LangGraph graph modules used by the Django API
COPY src/ ./src/

# Copy Django project files (overridden in dev by volume mount)
COPY django/ .

ENV PYTHONPATH=/app

EXPOSE 8080

# --reload watches for file changes when src/ and django/ are volume-mounted
CMD ["uvicorn", "langgraph_api.asgi:application", "--host", "0.0.0.0", "--port", "8080", "--reload"]
