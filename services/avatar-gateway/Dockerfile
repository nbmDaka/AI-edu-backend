FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY gateway.cjs speech-queue.cjs entrypoint.sh ./
RUN mkdir -p /data && chown node:node /data
USER node
ENTRYPOINT ["bash", "/app/entrypoint.sh"]
