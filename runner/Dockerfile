# Orchestrator image: bin/run.sh and friends run inside the cluster, so a
# laptop that sleeps cannot shift a snapshot past the end of a k6 window.
# GNU coreutils for date +%s%N and sleep, gettext for envsubst, python3 for
# the delta/gate scripts, kubectl talking to the in-cluster API.
FROM alpine:3.20

ARG KUBECTL_VERSION=v1.33.4
ARG TARGETARCH=amd64

RUN apk add --no-cache bash python3 curl jq gettext coreutils ca-certificates \
    && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && curl -fsSL -o /tmp/kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/kubectl \
    && rm /tmp/kubectl.sha256

WORKDIR /work
CMD ["sleep", "infinity"]
