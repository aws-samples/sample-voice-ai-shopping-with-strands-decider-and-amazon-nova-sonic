# Strands Decider 2B inference image for an Amazon SageMaker real-time endpoint
# (ADR-22). x86_64, built by CodeBuild and pushed to ECR (no local Docker build).
#
# Base: the AWS Deep Learning Containers base image on Amazon ECR Public (AWS
# verified), which carries the CUDA toolkit and Python 3.12.
# Container images must come from ECR, not from external registries such as
# nvcr.io; the earlier build used nvcr.io/nvidia/cuda 12.4.1 (devel + runtime).
# One stage: the base already includes the build tools, and a second stage would
# need the same Python at the same path.
#
# Installed: strands-decider[cuda] at a pinned commit, torch 2.7.1 (cu126 wheels,
# which bundle their own CUDA runtime) and causal-conv1d. causal-conv1d's setup.py downloads
# its prebuilt wheel from GitHub Releases without a hash check, so that wheel is installed
# directly, pinned by sha256: a replaced release asset fails the build. The model weights are NOT in the image: SageMaker mounts them
# read-only at /opt/ml/model from S3 before the container starts (ModelDataSource).

# Pinned by digest as well as tag, so a rebuild cannot pick up a different base image.
ARG BASE_IMAGE=public.ecr.aws/deep-learning-containers/base:12.9.1-gpu-py312-cu129-ubuntu22.04-ec2-v1.88@sha256:ae49e8e7ca0f30e25a8e7dfa4c484b26decfc1cd7b6b1c342c204fd5c1b20fca

FROM ${BASE_IMAGE}
ARG STRANDS_DECIDER_COMMIT=930e6d5de16309be32579f5631a7369a3322140c
# The base image declares NVIDIA_REQUIRE_CUDA=cuda>=12.9, and the NVIDIA container runtime
# refuses to start it on a host whose driver is older (SageMaker: CannotStartContainerError,
# no container log, 2026-10-21). The 12.9 toolkit is not used at run time:
# torch uses its bundled CUDA 12.6 libraries, as with the earlier 12.4 base.
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
ARG CAUSAL_CONV1D_WHEEL=https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.7.0/causal_conv1d-1.7.0+cu12torch2.7cxx11abiTRUE-cp312-cp312-linux_x86_64.whl#sha256=b57df4acfae0096cdac7603cb390074df30e5ee8f35d51228a720c3c6f70d3e2
RUN python3 -m pip install --no-cache-dir --no-deps "${CAUSAL_CONV1D_WHEEL}"
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt
COPY serve.py /opt/program/serve.py
# The server runs as an unprivileged user. It reads the model from the read-only mount at
# /opt/ml/model and writes only under its home: the Hugging Face cache links, kernel caches and
# temporary files (on SageMaker hosts /tmp is not writable by an unprivileged user, 2026-10-23).
RUN useradd --create-home --uid 10001 decider && mkdir -p /home/decider/tmp && chown decider /home/decider/tmp
USER decider
ENV HOME=/home/decider TMPDIR=/home/decider/tmp
WORKDIR /opt/program
EXPOSE 8080
# SageMaker starts the container as `docker run <image> serve`; the argument is ignored.
ENTRYPOINT ["python3", "/opt/program/serve.py"]
