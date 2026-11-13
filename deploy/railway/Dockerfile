# nostr-remote-signer for Railway, outside any enclave: bunker-relay and bunkerd in one image.
# The role of each Railway service is chosen by the BUNKER_ROLE variable (relay or vault).
#
# Built from a pinned commit of the public repository, so this project never copies its code.
# Local build:  docker build -t skoolathome-bunker deploy/railway
FROM rust:1.90-slim-bookworm AS build
ARG SIGNER_REPO=https://github.com/bonpiedlaroute/nostr-remote-signer.git
ARG SIGNER_REF=af40626b362b7167c3f782f6a3eb32ff55a5ed1c
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init -q . \
 && git remote add origin "$SIGNER_REPO" \
 && git fetch -q --depth 1 origin "$SIGNER_REF" \
 && git checkout -q FETCH_HEAD
RUN cargo build --release --locked -p nostr-remote-signer \
      --bin bunkerd --bin bunker-relay --example seal --example sign

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/bunkerd /src/target/release/bunker-relay /usr/local/bin/
# seal: to seal keys locally with the same binary ; sign: the signer's own test client.
COPY --from=build /src/target/release/examples/seal /src/target/release/examples/sign /usr/local/bin/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
CMD ["/usr/local/bin/entrypoint.sh"]
