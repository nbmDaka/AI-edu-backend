FROM node:24-bookworm-slim
WORKDIR /app
COPY --chown=node:node server.mjs speech.mjs ./
RUN mkdir /data && chown node:node /data
USER node
ENV NODE_ENV=production NODE_OPTIONS=--max-old-space-size=64 PORT=8002
CMD ["node", "server.mjs"]
