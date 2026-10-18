FROM golang:1.27.2-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags='-s -w' -o /out/im-server ./cmd/im-server
FROM debian:bookworm-slim
RUN groupadd -g 1000 im && useradd -u 1000 -g im -M im && mkdir -p /data/media && chown im:im /data/media
COPY --from=build /out/im-server /usr/local/bin/im-server
USER 1000:1000
ENTRYPOINT ["im-server"]
