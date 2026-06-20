FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY mcp-filesystem/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-filesystem/server.py ./server.py

EXPOSE 8765

CMD ["python", "server.py"]
