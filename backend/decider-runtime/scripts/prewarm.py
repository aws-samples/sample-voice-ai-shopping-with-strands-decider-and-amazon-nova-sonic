#!/usr/bin/env python3
"""Confirms the decider endpoint is ready before any shopper connects: waits for it to be InService, sends one fixed
three-question request (checking a GPU answered), then 20 timed warm requests,
and prints client p50/p95.

Usage: python3 prewarm.py [--endpoint-name voiceshop-decider] [--region us-east-1] [--wait-minutes 30]
"""

import argparse
import json
import statistics
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REQUEST = {
    "state": 'Latest shopper sentence: "a gift for my mom under 80 dollars"\nCommitted: none',
    "model": "strands-decider-latest",
    "questions": {
        "intent": {"type": "choice", "instructions": "What is the shopper trying to do with their latest sentence?",
                   "criteria": {"narrowing": None, "confirm": None, "place_order": None, "go_back": None, "start_over": None, "other": None}},
        "recipient": {"type": "choice", "instructions": "Who is this shopping trip for?",
                      "criteria": {"self": None, "parent": None, "partner": None, "child": None, "friend": None, "coworker": None, "other": None, "not_mentioned": None}},
        "gender": {"type": "choice", "instructions": "Which gender or demographic should the products be for?",
                   "criteria": {"women": None, "men": None, "unisex": None, "girls": None, "boys": None, "not_mentioned": None}},
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint-name", default="voiceshop-decider")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--wait-minutes", type=int, default=30)
    args = parser.parse_args()

    sagemaker = boto3.client("sagemaker", region_name=args.region)
    deadline = time.time() + args.wait_minutes * 60
    while True:
        status = sagemaker.describe_endpoint(EndpointName=args.endpoint_name)["EndpointStatus"]
        if status == "InService":
            break
        if status in ("Failed", "OutOfService") or time.time() > deadline:
            print(f"❌ endpoint {args.endpoint_name} is {status}", file=sys.stderr)
            return 1
        print(f"   ... {status}, waiting")
        time.sleep(30)

    # The first call compiles the GPU kernels (about 40 s); "standard" mode with one attempt means
    # no hidden retry that would queue behind it.
    runtime = boto3.client("sagemaker-runtime", region_name=args.region,
                           config=Config(read_timeout=120, retries={"max_attempts": 1, "mode": "standard"}))

    def invoke():
        started = time.perf_counter()
        response = runtime.invoke_endpoint(EndpointName=args.endpoint_name, ContentType="application/json",
                                           Accept="application/json", Body=json.dumps(REQUEST).encode())
        body = json.loads(response["Body"].read())
        return (time.perf_counter() - started) * 1000, body

    # The decider answers 503 busy while its queue is full (serve.py); wait and try again.
    for attempt in range(10):
        try:
            first_ms, body = invoke()
            break
        except ClientError as exc:
            if exc.response.get("OriginalStatusCode") != 503 or attempt == 9:
                raise
            print("   ... decider busy (still warming up), retrying in 10 s")
            time.sleep(10)
    if not body.get("gpu"):
        print(f"❌ the decider answered without a GPU: {body}", file=sys.stderr)
        return 1
    answers = {q: a.get("choice") for q, a in body.get("answers", {}).items()}
    print(f"✅ first request {first_ms:.0f} ms on {body['gpu']}: {answers}")

    timings = sorted(invoke()[0] for _ in range(20))
    p50, p95 = statistics.median(timings), timings[int(len(timings) * 0.95) - 1]
    print(f"✅ warm: p50 {p50:.0f} ms, p95 {p95:.0f} ms (client side, 20 requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
