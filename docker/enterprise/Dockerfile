# Build the application from this checkout first; never use a historical patch image.
ARG APP_IMAGE=octop:source
FROM ${APP_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 octop && useradd -u 1000 -g octop -d /data octop \
    && chown -R octop:octop /data
USER 1000:1000
# Bootstrap is an explicit one-off command, including on PostgreSQL installations.
ENTRYPOINT []
CMD ["octop", "run", "--host", "0.0.0.0", "--port", "8088"]
