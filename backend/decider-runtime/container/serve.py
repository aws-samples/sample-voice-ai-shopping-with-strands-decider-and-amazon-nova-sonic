"""SageMaker inference server for the Strands Decider (ADR-22).

SageMaker's container contract: listen on 8080, answer GET /ping with 200 only
when ready to serve, and accept POST /invocations. The model files are already
on local disk, mounted read-only at /opt/ml/model from S3:

    /opt/ml/model/checkpoint/   strands-decider checkpoint (LoRA adapter + pointer head)
    /opt/ml/model/hf-cache/     Hugging Face cache holding the Qwen3.5-2B base model

The model is loaded before the server starts listening, so /ping cannot answer
until the decider is ready; SageMaker waits (ContainerStartupHealthCheckTimeout)
and only then routes traffic. The endpoint runs with network isolation, so the
container makes no outbound calls at all.
"""

import json
import logging
import os
import sys

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
CHECKPOINT_DIR = os.path.join(MODEL_DIR, "checkpoint")
READONLY_HF_CACHE = os.path.join(MODEL_DIR, "hf-cache")
# huggingface_hub writes lock files at the cache root even when offline, and
# /opt/ml/model is read-only: give it a writable root whose model directories
# are symlinks into the read-only mount.
HF_CACHE = "/tmp/hf-cache"
DECIDER_ROUTE = "/v1/systemone"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("decider")


def prepare_hf_cache() -> None:
    os.makedirs(HF_CACHE, exist_ok=True)
    for name in os.listdir(READONLY_HF_CACHE):
        link = os.path.join(HF_CACHE, name)
        if name.startswith("models--") and not os.path.exists(link):
            os.symlink(os.path.join(READONLY_HF_CACHE, name), link)
    # Read at huggingface_hub import time, so set before strands_decider is imported.
    os.environ["HF_HOME"] = HF_CACHE
    os.environ["HF_HUB_CACHE"] = HF_CACHE
    os.environ["HF_HUB_OFFLINE"] = "1"


def gpu_name() -> str:
    """Fails closed: a GPU endpoint silently running on CPU would report CPU latency."""
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("GPU not visible to torch: refusing to serve on CPU")
    return torch.cuda.get_device_name(0)


def build_app():
    from fastapi.responses import JSONResponse
    from strands_decider.server import create_app

    device = gpu_name()
    logger.info("✅ GPU visible: %s; loading %s", device, CHECKPOINT_DIR)
    app = create_app(CHECKPOINT_DIR, device="cuda")
    logger.info("✅ decider loaded")

    @app.get("/ping")
    async def ping():
        return JSONResponse({"status": "Healthy", "gpu": device})

    return SageMakerRoutes(app, device)


class SageMakerRoutes:
    """ASGI wrapper: SageMaker's POST /invocations -> the decider's own POST
    /v1/systemone (the only inference route create_app serves), with a "gpu"
    field added to the JSON response, which the measurement scripts check on
    every call to catch a CPU fallback."""

    def __init__(self, app, device: str) -> None:
        self.app = app
        self.device = device

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] == "/invocations":
            scope = dict(scope, path=DECIDER_ROUTE, raw_path=DECIDER_ROUTE.encode())
            send = self._with_gpu_field(send)
        await self.app(scope, receive, send)

    def _with_gpu_field(self, send):
        start = None
        parts = []

        async def wrapped(message):
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = b"".join(parts)
            headers = list(start.get("headers", []))
            if any(k.lower() == b"content-type" and v.startswith(b"application/json") for k, v in headers):
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    payload["gpu"] = self.device
                    body = json.dumps(payload).encode()
                    headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
                    headers.append((b"content-length", str(len(body)).encode()))
            await send(dict(start, headers=headers))
            await send({"type": "http.response.body", "body": body, "more_body": False})

        return wrapped


def main() -> int:
    import uvicorn

    prepare_hf_cache()
    app = build_app()
    # One worker: the model owns the GPU (strands_decider.server's own serve() does the same).
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
