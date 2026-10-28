FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

COPY pyproject.toml deploy/install_test_dependencies.py /tmp/dependencies/
RUN python /tmp/dependencies/install_test_dependencies.py /tmp/dependencies/pyproject.toml

COPY pyproject.toml /workspace/pyproject.toml
COPY src /workspace/src
RUN python -m pip install --no-deps --no-build-isolation -e .

COPY alembic.ini /workspace/alembic.ini
COPY alembic /workspace/alembic
COPY tests /workspace/tests
COPY scripts /workspace/scripts
COPY deploy/run_remote_tests.py /workspace/deploy/run_remote_tests.py
COPY deploy/check-services.sh deploy/docker-env.sh /workspace/deploy/

ENTRYPOINT ["python", "deploy/run_remote_tests.py"]
