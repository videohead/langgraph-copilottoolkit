FROM node:22-alpine

WORKDIR /app

# Install dependencies as a cacheable layer
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy source (overridden in dev by volume mount)
COPY . .

EXPOSE 3000

# Disable Next.js telemetry inside containers
ENV NEXT_TELEMETRY_DISABLED=1
# Use polling instead of inotify for file-watching inside Docker
ENV WATCHPACK_POLLING=true

CMD ["npm", "run", "dev"]
