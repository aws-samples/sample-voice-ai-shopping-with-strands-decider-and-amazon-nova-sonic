"""CloudWatch Embedded Metric Format lines, namespace
VoiceShop, dimension Component. Printed to stdout, which AgentCore ships to the
runtime log group; no PutMetricData permission is needed."""

from __future__ import annotations

import json
import sys
import time

NAMESPACE = 'VoiceShop'
COMPONENT = 'SessionHost'


def emit(name: str, value: float, unit: str = 'Count', **dimensions: str) -> None:
    dims = {'Component': COMPONENT, **dimensions}
    record = {
        '_aws': {'Timestamp': int(time.time() * 1000), 'CloudWatchMetrics': [
            {'Namespace': NAMESPACE, 'Dimensions': [list(dims)], 'Metrics': [{'Name': name, 'Unit': unit}]}]},
        name: value,
        **dims,
    }
    sys.stdout.write(json.dumps(record) + '\n')
    sys.stdout.flush()


def latency(name: str, ms: float, **dimensions: str) -> None:
    emit(name, round(ms, 1), 'Milliseconds', **dimensions)
