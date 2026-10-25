# VoiceShop session host for AgentCore Runtime (ARM64, built by CodeBuild).
# Pinned by digest as well as tag, so a rebuild cannot pick up a different base image.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
# requirements.txt is a lock file: every package, transitive ones included (pip too), at one version
# with its hashes. Wheels only: nothing is built from source with unchecked build tools.
RUN pip install --require-hashes --no-deps --only-binary :all: -r requirements.txt
COPY *.py calibration.json ./

RUN useradd --create-home --uid 1000 appuser
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1
ENTRYPOINT ["python", "shop_agent.py"]
