# Strands Decider 2B inference image for an Amazon SageMaker real-time endpoint
# (ADR-22). x86_64, built by CodeBuild and pushed to ECR (no local Docker build).
#
# Base: the AWS Deep Learning Containers base image on Amazon ECR Public (AWS
# verified), which carries the CUDA toolkit (nvcc, for causal-conv1d) and Python 3.12.
# Container images must come from ECR, not from external registries such as
# nvcr.io; the earlier build used nvcr.io/nvidia/cuda 12.4.1 (devel + runtime).
# One stage: the base already includes the build tools, and a second stage would
# need the same Python at the same path.
#
# Installed: strands-decider[cuda] at a pinned commit, torch 2.7.1 (cu126 wheels,
# which bundle their own CUDA runtime) and causal-conv1d, compiled with the base's
# CUDA toolkit. The model weights are NOT in the image: SageMaker mounts them
# read-only at /opt/ml/model from S3 before the container starts (ModelDataSource).

# Pinned by digest as well as tag, so a rebuild cannot pick up a different base image.
ARG BASE_IMAGE=public.ecr.aws/deep-learning-containers/base:12.9.1-gpu-py312-cu129-ubuntu22.04-ec2-v1.88@sha256:ae49e8e7ca0f30e25a8e7dfa4c484b26decfc1cd7b6b1c342c204fd5c1b20fca

FROM ${BASE_IMAGE}
ARG STRANDS_DECIDER_COMMIT=930e6d5de16309be32579f5631a7369a3322140c
# The base image declares NVIDIA_REQUIRE_CUDA=cuda>=12.9, and the NVIDIA container runtime
# refuses to start it on a host whose driver is older (SageMaker: CannotStartContainerError,
# no container log, 2026-10-21). The 12.9 toolkit is needed only to build causal-conv1d;
# at run time torch uses its bundled CUDA 12.6 libraries, as with the earlier 12.4 base.
ENV NVIDIA_DISABLE_REQUIRE=1
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
# The base also ships CUDA forward-compatibility driver libraries (cuda-compat, 12.9) ahead
# of the host's own libcuda; on the SageMaker host that gave "Error 803: unsupported display
# driver / cuda driver combination" and no GPU in torch (2026-10-21). Remove them so the
# host driver's libcuda, mounted by the NVIDIA container runtime, is the one loaded.
RUN rm -rf /usr/local/cuda/compat /usr/local/cuda-*/compat \
    && for f in /etc/ld.so.conf.d/*; do grep -q compat "$f" && rm -f "$f" || true; done \
    && find / -xdev \( -name 'libcuda.so*' -o -name 'libnvidia-ptxjitcompiler.so*' -o -name 'libnvidia-nvvm.so*' \) \
         -not -path '*/stubs/*' -print -delete \
    && ldconfig \
    && (ldconfig -p | grep -i libcuda || echo "no libcuda in the image: the host driver provides it")
RUN command -v git >/dev/null || (apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*)
RUN python3 --version && nvcc --version && python3 -m pip install --no-cache-dir pip==26.2.2
RUN git clone https://github.com/strands-labs/strands-decider.git /opt/strands-decider \
    && cd /opt/strands-decider \
    && git checkout "${STRANDS_DECIDER_COMMIT}" \
    && python3 -m pip install --no-cache-dir ".[cuda]"
RUN python3 -m pip install --no-cache-dir --force-reinstall torch==2.7.1 --index-url https://download.pytorch.org/whl/cu126
RUN python3 -m pip install --no-cache-dir causal-conv1d==1.7.0 --no-build-isolation
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt
COPY serve.py /opt/program/serve.py
# The server runs as an unprivileged user. It reads the model from the read-only mount at
# /opt/ml/model and writes only under /tmp (Hugging Face cache links) and its home (kernel caches).
RUN useradd --create-home --uid 10001 decider
USER decider
ENV HOME=/home/decider
WORKDIR /opt/program
EXPOSE 8080
# SageMaker starts the container as `docker run <image> serve`; the argument is ignored.
ENTRYPOINT ["python3", "/opt/program/serve.py"]
