ARG PYTHON_TAG=3.12
ARG UNIT_TAG=1.32.1-python${PYTHON_TAG}

# SERVER #

FROM unit:${UNIT_TAG} AS server_builder

# copy only the parts needed for production
COPY ./src/integrations ./src/integrations
COPY ./src/solutions ./src/solutions
COPY ./src/features ./src/features
COPY ./src/server ./src/server

# install dependencies and packages
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT="/usr/local/"

RUN --mount=type=bind,source=./uv.lock,target=./uv.lock --mount=type=bind,source=./pyproject.toml,target=./pyproject.toml uv sync --python python${PYTHON_TAG} --locked --no-editable --no-python-downloads --link-mode copy --compile-bytecode --only-group server

FROM server_builder AS server

# allow access to home directory for asyncpg library
RUN chgrp -R unit ${HOME} && chmod -R 050 ${HOME}

RUN apt-get update \
  && apt-get upgrade -y \
  && apt-get -y autoremove \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# copy configuration
COPY ./config/unit.json /docker-entrypoint.d/config.json

CMD ["unitd", "--no-daemon", "--log", "/dev/stdout"]

# port 80 is already exposed by nginx unit image, can't change it...
