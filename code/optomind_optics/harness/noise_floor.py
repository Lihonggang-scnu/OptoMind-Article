"""O-07: noise-floor governance for near-miss deltas.

Passively accumulates same-config rerun deltas per (config_hash, metric),
derives a pooled sigma, and governs the "is this delta inside the noise
band?" question. Locked floors never re-judge history: they only inform new
decisions, and a single in-band near-miss can never close a direction.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Tuple

from tmm_engine.hashing import stable_sha256

NOISE_FLOOR_SCHEMA_VERSION = "optomind-noise-floor.v1"
NOISE_FLOOR_EVENT_TYPE = "noise_floor_sample"
NOISE_FLOOR_PROJECTION = "NOISE_FLOOR.json"

MIN_SAMPLES_FOR_SIGMA = 3
MIN_SAMPLES_TO_LOCK = 5
#: Conservative default band before any samples exist (work order O-07).
DEFAULT_BAND = 0.003


def sample_event(
    config_hash: str, metric: str, delta: float, *, source: str = "same_config_rerun"
) -> Dict[str, Any]:
    """The event payload for one observed same-config delta."""

    return {
        "config_hash": str(config_hash),
        "metric": str(metric),
        "delta_abs": abs(float(delta)),
        "source": str(source),
    }


class NoiseFloor:
    """Reads noise_floor_sample events; judges new deltas. Never rewrites
    historical KEEP/DISCARD decisions -- judgement is advisory for NEW ones."""

    def __init__(self, samples: List[Mapping[str, Any]] | None = None) -> None:
        rows: Dict[Tuple[str, str], List[float]] = {}
        for row in samples or []:
            key = (str(row.get("config_hash") or ""), str(row.get("metric") or ""))
            value = row.get("delta_abs")
            if value is None:
                continue
            try:
                rows.setdefault(key, []).append(abs(float(value)))
            except (TypeError, ValueError):
                continue
        self._samples = rows

    def stats(self, config_hash: str, metric: str) -> Dict[str, Any]:
        key = (str(config_hash), str(metric))
        values = self._samples.get(key, [])
        n = len(values)
        if n < MIN_SAMPLES_FOR_SIGMA:
            return {
                "n": n,
                "sigma": None,
                "band": DEFAULT_BAND,
                "locked": False,
                "band_source": "conservative_default",
            }
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        sigma = math.sqrt(max(variance, 0.0))
        # Pool with the conservative default so sparse data cannot shrink the
        # band below what the default already protects.
        band = max(2.0 * sigma, DEFAULT_BAND)
        return {
            "n": n,
            "sigma": round(sigma, 6),
            "band": round(band, 6),
            "locked": n >= MIN_SAMPLES_TO_LOCK,
            "band_source": "pooled_sigma",
        }

    def judge(self, config_hash: str, metric: str, delta: float) -> Dict[str, Any]:
        stats = self.stats(config_hash, metric)
        inside = abs(float(delta)) <= float(stats["band"])
        return {
            **stats,
            "delta": round(float(delta), 6),
            "verdict": "in_band" if inside else "significant",
        }

    def projection(self) -> Dict[str, Any]:
        keys = sorted(self._samples)
        return {
            "schema_version": NOISE_FLOOR_SCHEMA_VERSION,
            "floors": [
                {
                    "config_hash": config_hash,
                    "metric": metric,
                    **self.stats(config_hash, metric),
                }
                for config_hash, metric in keys
            ],
            "default_band": DEFAULT_BAND,
            "rules": [
                "one in-band near-miss never closes a direction (needs >=2 points)",
                "after a near-miss the next probe must be far-side or reversed",
                "locked floors (n>=5) inform new judgements only, never rewrite history",
            ],
        }


def rebuild_noise_floor(events: List[Mapping[str, Any]]) -> NoiseFloor:
    samples = [
        row.get("payload") or {}
        for row in events
        if row.get("event_type") == NOISE_FLOOR_EVENT_TYPE
    ]
    return NoiseFloor(samples)


def write_noise_floor_projection(events: List[Mapping[str, Any]], out_dir) -> Dict[str, Any]:
    from pathlib import Path

    payload = rebuild_noise_floor(events).projection()
    (Path(out_dir) / NOISE_FLOOR_PROJECTION).write_text(
        json_dumps(payload), encoding="utf-8"
    )
    return payload


def json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "DEFAULT_BAND",
    "MIN_SAMPLES_FOR_SIGMA",
    "MIN_SAMPLES_TO_LOCK",
    "NOISE_FLOOR_EVENT_TYPE",
    "NOISE_FLOOR_PROJECTION",
    "NOISE_FLOOR_SCHEMA_VERSION",
    "NoiseFloor",
    "json_dumps",
    "rebuild_noise_floor",
    "sample_event",
    "stable_sha256",
    "write_noise_floor_projection",
]
