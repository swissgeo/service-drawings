###########################################################
# Container that contains basic configurations used by all other containers
# It should only contain variables that don't change or change very infrequently
# so that the cache is not needlessly invalidated
FROM python:3.14-slim-trixie AS base
ENV USER=swissgeo
ENV GROUP=swissgeo
ENV INSTALL_DIR=/opt/service-drawings

RUN apt-get -qq update > /dev/null \
    && apt-get -qq clean \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r ${GROUP} \
    && useradd -r -s /bin/false -g ${GROUP} ${USER}

###########################################################
# Builder container
FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.4 /uv /uvx /bin/

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy
# Omit development dependencies
ENV UV_NO_DEV=1
# Ensure installed tools can be executed out of the box
ENV UV_TOOL_BIN_DIR=/usr/local/bin

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0

# Install all the dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked

COPY --chown=${USER}:${GROUP} app/ ${INSTALL_DIR}/app/

###########################################################
# Container to use in production
FROM base AS production
LABEL target=production

# Install the .venv at the root because this is expected by fastapi script
COPY --from=builder .venv/ /.venv/

COPY --from=builder ${INSTALL_DIR}/ ${INSTALL_DIR}/

# Activate virtual environment
ENV VIRTUAL_ENV=/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONHOME=""

# Overwrite the version.py from source with the actual version
ARG VERSION=unknown
RUN echo "__version__ = '$VERSION'" > ${INSTALL_DIR}/app/version.py

ARG GIT_HASH=unknown
ARG GIT_BRANCH=unknown
ARG GIT_DIRTY=""
ARG AUTHOR=unknown
LABEL git.hash=$GIT_HASH
LABEL git.branch=$GIT_BRANCH
LABEL git.dirty=$GIT_DIRTY
LABEL author=$AUTHOR
LABEL version=$VERSION
# production container must not run as root
WORKDIR ${INSTALL_DIR}/
USER ${USER}

# expose the default port of uvicorn
EXPOSE 8000

# Here we use uvicorn directly in order to configure its logging configuration file
# This can be done by using the CMD arg during docker run.
ENTRYPOINT ["uvicorn", "app.main:app", "--proxy-headers", "--host", "0.0.0.0"]
