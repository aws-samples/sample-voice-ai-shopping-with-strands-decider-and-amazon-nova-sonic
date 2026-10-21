# VoiceShop session host for AgentCore Runtime (ARM64, built by CodeBuild).
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY *.py calibration.json ./

RUN useradd --create-home --uid 1000 appuser
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1
ENTRYPOINT ["python", "shop_agent.py"]
