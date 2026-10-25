"""Measures Strands Decider latency on the SageMaker real-time endpoint.

For the endpoint's current instance type, measures warm p50/p95 over N calls
each for a 3-question understand round, best_fit over 50 options and
pointed_item over 12 options, plus InvokeEndpoint overhead (client round-trip
time minus the decider's own server-side latency_ms).

Every response's "gpu" field is checked on every call: a missing value would
mean the container fell back to CPU, and the run is aborted rather than
reporting CPU-speed numbers as GPU numbers.
"""

import argparse
import json
import logging
import statistics
import time
from dataclasses import dataclass, field

import boto3
from botocore.config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# No SDK retries, as in the session host's decider_client.py (which also uses 1 s / 3 s timeouts).
_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=10,
    retries={"total_max_attempts": 1, "mode": "standard"},
)


def _percentiles(label: str, values: list[float]) -> dict:
    if not values:
        return {f"{label}_p50_ms": None, f"{label}_p95_ms": None}
    ordered = sorted(values)
    return {
        f"{label}_p50_ms": round(statistics.median(ordered), 1),
        f"{label}_p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 1),
    }


@dataclass
class CallResult:
    client_ms: float
    server_latency_ms: float | None
    gpu: str | None
    error: str | None = None


@dataclass
class RoundResults:
    name: str
    calls: list[CallResult] = field(default_factory=list)

    def client_ms_values(self) -> list[float]:
        return [c.client_ms for c in self.calls if c.error is None]

    def summary(self) -> dict:
        values = self.client_ms_values()
        if not values:
            return {"name": self.name, "n": 0, "p50_ms": None, "p95_ms": None}
        sorted_values = sorted(values)
        return {
            "name": self.name,
            "n": len(values),
            "p50_ms": round(statistics.median(sorted_values), 1),
            "p95_ms": round(sorted_values[int(len(sorted_values) * 0.95) - 1], 1),
            # Server-side (the decider's own latency_ms): what the session host's
            # latency budget actually depends on, minus client/network noise.
            **_percentiles("server", [c.server_latency_ms for c in self.calls if c.error is None and c.server_latency_ms is not None]),
            "errors": sum(1 for c in self.calls if c.error is not None),
        }


def _understand_request(session_hash: int, turn: str) -> dict:
    """3-question understand round: intent, recipient, category -- matches the
    design's selection rule for an early-session turn with recipient still open."""
    return {
        "state": f"Latest shopper sentence: \"{turn}\"\nCommitted: none\nRemembered: none\nOn screen (4 columns): none\nOpen product: none",
        "model": "strands-decider-latest",
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "What is the shopper trying to do with their latest sentence?",
                "criteria": {"narrowing": None, "pointing": None, "confirm": None, "place_order": None, "go_back": None, "start_over": None, "other": None},
            },
            "recipient": {
                "type": "choice",
                "instructions": "Who is this shopping trip for?",
                "criteria": {"self": None, "parent": None, "partner": None, "child": None, "friend": None, "coworker": None, "other": None, "not_mentioned": None},
            },
            "category": {
                "type": "choice",
                "instructions": "Which product category does the shopper want?",
                "criteria": {"Dresses": None, "Tops": None, "Shoes": None, "Handbags": None, "Jewelry": None, "Watches": None, "Scarves & Hats": None, "Sunglasses": None, "Activewear": None, "Outerwear": None, "Accessories": None, "not_mentioned": None},
            },
        },
    }


def _best_fit_request(n_options: int = 50) -> dict:
    criteria = {f"product_{i:02d}": f"Item {i} | Brand {i % 7} | Color {i % 5} | ${10 + i}" for i in range(n_options)}
    return {
        "state": "Latest shopper sentence: \"something in blue, under 80 dollars\"\nCommitted: category=Tops (0.9)\nOn screen (4 columns): none\nOpen product: none",
        "model": "strands-decider-latest",
        "questions": {
            "best_fit": {"type": "choice", "instructions": "Which product best matches the shopper's latest sentence?", "criteria": criteria},
        },
    }


def _pointed_item_request(n_items: int = 12) -> dict:
    criteria = {f"row_{i // 4 + 1}_item_{i % 4}": f"row {i // 4 + 1} item {i % 4} | Item {i} | Color {i % 5} | ${10 + i}" for i in range(n_items)}
    criteria["none"] = None
    return {
        "state": "Latest shopper sentence: \"the purple one on the left\"\nCommitted: category=Tops (0.9)\nOn screen (4 columns): 12 items\nOpen product: none",
        "model": "strands-decider-latest",
        "questions": {
            "pointed_item": {"type": "choice", "instructions": "Which on-screen item is the shopper pointing at?", "criteria": criteria},
        },
    }


def _invoke(client, endpoint_name: str, payload: dict) -> CallResult:
    started = time.perf_counter()
    try:
        response = client.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(payload).encode("utf-8"),
        )
        body = json.loads(response["Body"].read())
    except Exception as exc:  # noqa: BLE001 -- recorded as a per-call error, not fatal to the run
        return CallResult(client_ms=(time.perf_counter() - started) * 1000, server_latency_ms=None, gpu=None, error=str(exc))
    return CallResult(
        client_ms=(time.perf_counter() - started) * 1000,
        server_latency_ms=body.get("latency_ms"),
        gpu=body.get("gpu"),
    )


def measure(endpoint_name: str, region: str, n_warm_calls: int) -> dict:
    client = boto3.client("sagemaker-runtime", region_name=region, config=_BOTO_CONFIG)

    logger.info("📋 first call (connection setup, not a cold start: the endpoint keeps an instance warm)")
    first = _invoke(client, endpoint_name, _understand_request(0, "a gift for my mom"))
    if first.error:
        raise RuntimeError(f"First call failed: {first.error}")
    if not first.gpu:
        raise RuntimeError(f"First call reported no GPU (gpu={first.gpu!r}): refusing to report CPU numbers as GPU numbers.")
    logger.info("✅ first call %.0f ms, gpu=%s", first.client_ms, first.gpu)

    rounds = {
        "understand_3q": RoundResults("understand_3q"),
        "best_fit_50": RoundResults("best_fit_50"),
        "pointed_item_12": RoundResults("pointed_item_12"),
    }
    request_builders = {
        "understand_3q": lambda: _understand_request(0, "something to wear, under 100 dollars"),
        "best_fit_50": lambda: _best_fit_request(50),
        "pointed_item_12": lambda: _pointed_item_request(12),
    }

    for round_name, round_result in rounds.items():
        logger.info("📋 warm round: %s (%d calls)", round_name, n_warm_calls)
        for i in range(n_warm_calls):
            result = _invoke(client, endpoint_name, request_builders[round_name]())
            if result.error is None and not result.gpu:
                raise RuntimeError(f"{round_name} call {i} reported no GPU -- aborting (see module docstring).")
            round_result.calls.append(result)

    overhead_values = [
        c.client_ms - c.server_latency_ms
        for r in rounds.values()
        for c in r.calls
        if c.error is None and c.server_latency_ms is not None
    ]

    return {
        "first_call_ms": round(first.client_ms, 1),
        "gpu": first.gpu,
        "rounds": {name: r.summary() for name, r in rounds.items()},
        "invoke_endpoint_overhead_ms": {
            "p50": round(statistics.median(sorted(overhead_values)), 1) if overhead_values else None,
            "p95": round(sorted(overhead_values)[int(len(overhead_values) * 0.95) - 1], 1) if overhead_values else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint-name", default="voiceshop-decider", help="Decider SageMaker endpoint name")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--warm-calls", type=int, default=50, help="Calls per round for p50/p95 (default 50)")
    args = parser.parse_args()
    print(json.dumps(measure(args.endpoint_name, args.region, args.warm_calls), indent=2))


if __name__ == "__main__":
    main()
