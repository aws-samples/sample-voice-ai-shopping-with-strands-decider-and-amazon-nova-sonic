# Strands Decider 2B inference image for an Amazon SageMaker real-time endpoint
# (ADR-22). x86_64, built by CodeBuild and pushed to ECR (no local Docker build).
#
# Stage 1 builds the Python 3.12 environment: strands-decider[cuda] at a pinned
# commit, torch 2.7.1 (cu126 wheels, which bundle their own CUDA runtime) and
# causal-conv1d, compiled against the CUDA devel toolkit.
# Stage 2 copies that environment into a CUDA runtime base. The model weights are
# NOT in the image: SageMaker mounts them read-only at /opt/ml/model from S3
# before the container starts (the endpoint's ModelDataSource).
#
# SageMaker hosting has no 2 GB image cap (unlike AgentCore Runtime, which forced
# the earlier S3-pulled venv design), so the environment ships in the image.

ARG CUDA_VERSION=12.4.1

FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04 AS builder
ARG STRANDS_DECIDER_COMMIT=930e6d5de16309be32579f5631a7369a3322140c
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && for attempt in 1 2 3 4 5; do \
         apt-get update && apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
           python3.12 python3.12-venv python3.12-dev git && break || sleep 15; \
       done \
    && rm -rf /var/lib/apt/lists/*
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN pip install --no-cache-dir --upgrade pip
RUN git clone https://github.com/strands-labs/strands-decider.git /opt/strands-decider \
    && cd /opt/strands-decider \
    && git checkout "${STRANDS_DECIDER_COMMIT}" \
    && pip install --no-cache-dir ".[cuda]"
RUN pip install --no-cache-dir --force-reinstall torch==2.7.1 --index-url https://download.pytorch.org/whl/cu126
RUN pip install --no-cache-dir causal-conv1d --no-build-isolation
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

FROM nvcr.io/nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && for attempt in 1 2 3 4 5; do \
         apt-get update && apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
           python3.12 && break || sleep 15; \
       done \
    && apt-get purge -y software-properties-common && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY serve.py /opt/program/serve.py
WORKDIR /opt/program
EXPOSE 8080
# SageMaker starts the container as `docker run <image> serve`; the argument is ignored.
ENTRYPOINT ["python3.12", "/opt/program/serve.py"]
