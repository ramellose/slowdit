# slowdit server image.
#
# Local profile (v1): the container talks to the HOST's docker daemon —
# deploy with `-v /var/run/docker.sock:/var/run/docker.sock` (docker-out-of-
# docker). The run dir is the one writable mount besides the sock:
#   -v /var/tmp/slowdit/runs:/var/tmp/slowdit/runs
FROM python:3.12-slim

# The executor and /health shell out to the docker CLI (to drive the HOST
# daemon, docker-out-of-docker) and code staging shells out to git; the
# base image ships neither, so install them: the docker client only (no
# daemon — the host's does the work), and git + openssh-client (explicit:
# git only "recommends" the ssh client, and ssh:// sources need it).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
# -e: the server runs the source in /app. 
# The [test] extra makes the image double as its own test environment
RUN pip install --no-cache-dir -e '.[test]'

ENV SLOWDIT_RUN_DIR=/var/tmp/slowdit/runs
EXPOSE 8080

CMD ["slowdit", "serve", "--http", "--host", "0.0.0.0", "--port", "8080"]
