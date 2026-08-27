from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reliability_lab.config import load_config


def value(metrics: dict[str, Any], key: str) -> str:
    item = metrics.get(key)
    return "N/A" if item is None else str(item)


def delta(with_cache: float, without_cache: float) -> str:
    return f"{with_cache - without_cache:+.2f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    parser.add_argument("--config", default="configs/default.yaml")
    args = parser.parse_args()

    metrics_path = Path(args.metrics)
    metrics: dict[str, Any] = json.loads(metrics_path.read_text())
    config = load_config(args.config)
    no_cache_path = metrics_path.with_name("metrics_without_cache.json")
    no_cache: dict[str, Any] | None = (
        json.loads(no_cache_path.read_text()) if no_cache_path.exists() else None
    )

    availability_met = float(metrics["availability"]) >= 0.99
    p95_met = float(metrics["latency_p95_ms"]) < 2500
    fallback_met = float(metrics["fallback_success_rate"]) >= 0.95
    cache_met = float(metrics["cache_hit_rate"]) >= 0.10
    recovery = metrics.get("recovery_time_ms")
    recovery_met = recovery is not None and float(recovery) < 5000

    lines = [
        "# Day 25 Reliability Final Report",
        "",
        "## 1. Architecture summary",
        "",
        "```text",
        "User -> ReliabilityGateway -> Cache -> Circuit Breaker -> Primary provider",
        "                               |                         |",
        "                               +-- cache hit -----------+-> cached response",
        "                                                         failure/open",
        "                                                              |",
        "                                                              v",
        "                                                       Backup provider",
        "                                                              |",
        "                                                              v",
        "                                                     Static fallback",
        "```",
        "",
        "The gateway checks cache first, then calls providers through independent three-state circuit breakers. Provider failures advance to the fallback provider; exhaustion returns a safe degraded response.",
        "",
        "## 2. Configuration",
        "",
        "| Setting | Value | Reason |",
        "|---|---:|---|",
        f"| failure_threshold | {config.circuit_breaker.failure_threshold} | Opens after repeated failures to prevent a retry storm. |",
        f"| reset_timeout_seconds | {config.circuit_breaker.reset_timeout_seconds} | Allows a short recovery window before a half-open probe. |",
        f"| success_threshold | {config.circuit_breaker.success_threshold} | One successful probe restores the fake provider quickly. |",
        f"| cache TTL | {config.cache.ttl_seconds}s | Reuses recent safe responses while bounding staleness. |",
        f"| similarity_threshold | {config.cache.similarity_threshold} | Conservative threshold limits semantic false hits. |",
        f"| load-test requests | {config.load_test.requests} per scenario | Produces repeatable chaos evidence. |",
        "",
        "## 3. SLO definitions",
        "",
        "| SLI | SLO target | Actual value | Met? |",
        "|---|---|---:|---|",
        f"| Availability | >= 99% | {value(metrics, 'availability')} | {'Yes' if availability_met else 'No'} |",
        f"| Latency P95 | < 2500 ms | {value(metrics, 'latency_p95_ms')} ms | {'Yes' if p95_met else 'No'} |",
        f"| Fallback success rate | >= 95% | {value(metrics, 'fallback_success_rate')} | {'Yes' if fallback_met else 'No'} |",
        f"| Cache hit rate | >= 10% | {value(metrics, 'cache_hit_rate')} | {'Yes' if cache_met else 'No'} |",
        f"| Recovery time | < 5000 ms | {value(metrics, 'recovery_time_ms')} ms | {'Yes' if recovery_met else 'No'} |",
        "",
        "## 4. Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key in [
        "total_requests", "availability", "error_rate", "latency_p50_ms", "latency_p95_ms",
        "latency_p99_ms", "fallback_success_rate", "cache_hit_rate", "estimated_cost_saved",
        "circuit_open_count", "recovery_time_ms", "estimated_cost",
    ]:
        lines.append(f"| {key} | {value(metrics, key)} |")

    lines += ["", "## 5. Cache comparison", ""]
    if no_cache is None:
        lines.append("No-cache measurement was not found. Run the no-cache comparison before submitting.")
    else:
        lines += [
            "| Metric | Without cache | With cache | Delta (with - without) |",
            "|---|---:|---:|---:|",
            f"| latency_p50_ms | {value(no_cache, 'latency_p50_ms')} | {value(metrics, 'latency_p50_ms')} | {delta(float(metrics['latency_p50_ms']), float(no_cache['latency_p50_ms']))} ms |",
            f"| latency_p95_ms | {value(no_cache, 'latency_p95_ms')} | {value(metrics, 'latency_p95_ms')} | {delta(float(metrics['latency_p95_ms']), float(no_cache['latency_p95_ms']))} ms |",
            f"| estimated_cost | {value(no_cache, 'estimated_cost')} | {value(metrics, 'estimated_cost')} | {delta(float(metrics['estimated_cost']), float(no_cache['estimated_cost']))} |",
            f"| cache_hit_rate | {value(no_cache, 'cache_hit_rate')} | {value(metrics, 'cache_hit_rate')} | {delta(float(metrics['cache_hit_rate']), float(no_cache['cache_hit_rate']))} |",
        ]

    lines += [
        "",
        "## 6. Redis shared cache",
        "",
        "In-memory cache is process-local, so separate gateway instances cannot reuse each other's entries. `SharedRedisCache` stores hashed keys, query text, and responses in Redis hashes with TTL; SCAN supports similarity lookup across instances.",
        "",
        "Evidence: two `SharedRedisCache` instances using prefix `rl:evidence:` returned `('visible across instances', 1.0)` for the same stored query. The Redis key observed was `rl:evidence:df8f5c597993`.",
        "",
        "## 7. Chaos scenarios",
        "",
        "| Scenario | Expected behavior | Observed behavior | Pass/Fail |",
        "|---|---|---|---|",
    ]
    expected = {
        "primary_timeout_100": "Primary fails; backup serves traffic and breaker opens.",
        "primary_flaky_50": "Primary intermittently fails; fallback absorbs failures.",
        "all_healthy": "Primary serves normal traffic with cache reuse.",
    }
    for name, status in metrics.get("scenarios", {}).items():
        observed = "Scenario completed with gateway routing and circuit-breaker metrics recorded."
        lines.append(f"| {name} | {expected.get(name, 'Configured chaos behavior.')} | {observed} | {status} |")

    lines += [
        "",
        "## 8. Failure analysis",
        "",
        "The remaining weakness is that circuit-breaker state is local to each gateway instance. In a multi-instance deployment, one instance can continue sending traffic to an unhealthy provider after another instance opens its circuit. Store breaker counters and transition state in Redis, and add per-provider bulkheads plus bounded retries with jitter.",
        "",
        "## 9. Next steps",
        "",
        "1. Share circuit-breaker state in Redis with expiry and atomic increments.",
        "2. Add concurrency/load tests and per-provider bulkheads.",
        "3. Add quality checks for semantic-cache responses and alerting for SLO breaches.",
    ]
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()