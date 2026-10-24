# Build the application from this checkout first; never use a historical patch image.
ARG APP_IMAGE=octop:source
FROM ${APP_IMAGE}
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/octop-browser
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 octop && useradd -u 1000 -g octop -d /data octop \
    && chown -R octop:octop /data
COPY docker/enterprise/verify_runtime.py /usr/local/libexec/octop/verify_runtime.py
USER 1000:1000
# Verify the actual non-root browser launch as well as embedding imports, offline.
RUN --network=none python /usr/local/libexec/octop/verify_runtime.py
# Bootstrap is an explicit one-off command, including on PostgreSQL installations.
ENTRYPOINT []
CMD ["octop", "run", "--host", "0.0.0.0", "--port", "8088"]
