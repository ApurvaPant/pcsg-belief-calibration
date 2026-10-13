#!/usr/bin/env python
"""Fill backend/config.yaml `experiment.prices_per_mtok` from OpenRouter's live
model pricing. Run LATER (from scripts/go_live.sh), never during an offline
session — it makes a network request to the models metadata endpoint.

OpenRouter `/models` returns, per model:
    {"id": "openai/gpt-4o", "pricing": {"prompt": "0.0000025", "completion": "0.00001"}}
where prompt/completion are USD *per token*. We convert to USD per 1e6 tokens.

This is a PRICING/METADATA endpoint, not a model inference call, so it does not
go through app/llm.py (which is reserved for completion/embedding calls).

    python scripts/fill_prices.py            # fetch + write config.yaml
    python scripts/fill_prices.py --dry-run  # fetch + print, do not write

The parsing (`parse_prices`) is pure and unit-tested against a saved fixture;
`fetch_models` is the only networked part and is never exercised in tests.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG = REPO / "backend" / "config.yaml"
PER_MTOK = 1_000_000.0


def parse_prices(models_json: dict, wanted: list[str]) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Pure: map OpenRouter /models JSON → {model: {input, output}} in $/Mtok,
    restricted to `wanted` model slugs. Returns (prices, missing) where `missing`
    lists wanted models absent from the endpoint or lacking numeric pricing."""
    by_id: dict[str, dict] = {}
    for m in models_json.get("data", []) or []:
        mid = m.get("id")
        if mid:
            by_id[mid] = m
    prices: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    for slug in wanted:
        m = by_id.get(slug)
        pricing = (m or {}).get("pricing") or {}
        try:
            inp = float(pricing["prompt"]) * PER_MTOK
            out = float(pricing["completion"]) * PER_MTOK
        except (KeyError, TypeError, ValueError):
            missing.append(slug)
            continue
        if inp <= 0 or out <= 0:
            missing.append(slug)
            continue
        prices[slug] = {"input": round(inp, 6), "output": round(out, 6)}
    return prices, missing


def configured_models(config_path: pathlib.Path = CONFIG) -> list[str]:
    """Every distinct model slug referenced under llm.models in config.yaml."""
    import yaml
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    models = (raw.get("llm", {}) or {}).get("models", {}) or {}
    out, seen = [], set()
    for v in models.values():
        if isinstance(v, str) and v and v != "mock" and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def fetch_models(api_key: str | None = None) -> dict:  # pragma: no cover - network
    """Network: GET OpenRouter's public models/pricing list. Never called in tests."""
    import os
    import httpx
    headers = {}
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    with httpx.Client(timeout=30) as client:
        resp = client.get(OPENROUTER_MODELS_URL, headers=headers)
        resp.raise_for_status()
        return resp.json()


def write_prices(prices: dict[str, dict[str, float]], config_path: pathlib.Path = CONFIG) -> None:
    """Merge fetched prices into experiment.prices_per_mtok, preserving `default`."""
    import yaml
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    exp = raw.setdefault("experiment", {})
    pm = exp.setdefault("prices_per_mtok", {})
    pm.update(prices)
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def main(argv=None) -> int:  # pragma: no cover - entrypoint (networked)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch + print, do not write config")
    args = ap.parse_args(argv)
    wanted = configured_models()
    data = fetch_models()
    prices, missing = parse_prices(data, wanted)
    print(json.dumps(prices, indent=2))
    if missing:
        print(f"WARNING: no live price for: {missing}", file=sys.stderr)
    if not args.dry_run:
        write_prices(prices)
        print(f"wrote {len(prices)} prices to {CONFIG}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
