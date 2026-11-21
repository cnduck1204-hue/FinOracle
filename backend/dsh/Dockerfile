FROM node:24-bookworm-slim

WORKDIR /app
RUN npm install --no-audit --no-fund --ignore-scripts @deepseek-ai/dsh@0.2.2-rc.5 \
    && install -d -o node -g node /var/lib/finoracle-dsh
COPY bridge.mjs profile.yml ./

ENV DSH_HOME=/var/lib/finoracle-dsh \
    NARB_NATIVE_CACHE_DIR=/var/lib/finoracle-dsh/native-cache \
    DSH_TELEMETRY_DISABLED=1 \
    FINORACLE_DSH_HOST=0.0.0.0 \
    FINORACLE_DSH_PORT=8091
USER node
EXPOSE 8091
HEALTHCHECK --interval=5s --timeout=3s --retries=8 CMD ["node", "-e", "fetch('http://127.0.0.1:8091/health',{headers:{Authorization:'Bearer '+process.env.FINORACLE_DSH_TOKEN}}).then(r=>{if(r.status!==204)process.exitCode=1}).catch(()=>{process.exitCode=1})"]
CMD ["./node_modules/.bin/dsh", "sdk-minimal", "--patch", "/app/profile.yml"]
