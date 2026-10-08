"""Configuration loader: merges config.yaml with .env overrides.

Precedence (highest first): environment variable / .env -> config.yaml.
Import `settings` anywhere; it is loaded once at process start.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BACKEND_DIR / "config.yaml"

# Load .env from the backend directory if present (no error if missing).
load_dotenv(BACKEND_DIR / ".env")


def _yaml() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class LLMSettings:
    provider: str
    base_url: str
    api_key: str
    models: dict[str, str]
    timeout_seconds: int
    temperature: float
    max_tokens: int


@dataclass
class PipelineSettings:
    salience_threshold: float
    calibration_prior: float
    calibration_shrink: float
    reinforce_step: float
    resolve_similarity: float


@dataclass
class ServerSettings:
    host: str
    port: int
    cors_origins: list[str]


@dataclass
class ExperimentSettings:
    # Hard spend ceiling for paid evaluation/sweeps. 0.0 == unset (refuse paid runs).
    budget_usd: float
    # {model_slug: {"input": usd_per_mtok, "output": usd_per_mtok}}, "default" fallback.
    prices_per_mtok: dict[str, dict[str, float]]

    def price(self, model: str) -> dict[str, float]:
        return self.prices_per_mtok.get(model) or self.prices_per_mtok.get(
            "default", {"input": 0.5, "output": 1.5})

    def cost_usd(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        p = self.price(model)
        return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000.0


@dataclass
class Settings:
    llm: LLMSettings
    pipeline: PipelineSettings
    server: ServerSettings
    experiment: ExperimentSettings
    db_path: Path


def _load() -> Settings:
    raw = _yaml()
    llm_raw = raw.get("llm", {})
    pipe_raw = raw.get("pipeline", {})
    srv_raw = raw.get("server", {})
    store_raw = raw.get("storage", {})

    models = dict(llm_raw.get("models", {}))
    # Per-role env overrides.
    for key, env in {
        "chat": "LLM_MODEL_CHAT",
        "classifier": "LLM_MODEL_CLASSIFIER",
        "extractor": "LLM_MODEL_EXTRACTOR",
        "embedding": "LLM_MODEL_EMBEDDING",
    }.items():
        if os.getenv(env):
            models[key] = os.getenv(env)  # type: ignore[assignment]

    # Key resolution: OpenRouter first, then generic OpenAI-compatible.
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")

    llm = LLMSettings(
        provider=os.getenv("LLM_PROVIDER", llm_raw.get("provider", "mock")),
        base_url=os.getenv("OPENAI_BASE_URL", llm_raw.get("base_url", "")),
        api_key=api_key,
        models=models,
        timeout_seconds=int(llm_raw.get("timeout_seconds", 60)),
        temperature=float(llm_raw.get("temperature", 0.2)),
        max_tokens=int(llm_raw.get("max_tokens", 1024)),
    )
    # NOTE: no silent fallback to mock. A real provider with a missing/invalid
    # key must fail loudly at startup (see llm.startup_check).

    pipeline = PipelineSettings(
        salience_threshold=float(pipe_raw.get("salience_threshold", 0.5)),
        calibration_prior=float(pipe_raw.get("calibration_prior", 0.5)),
        calibration_shrink=float(pipe_raw.get("calibration_shrink", 0.8)),
        reinforce_step=float(pipe_raw.get("reinforce_step", 0.1)),
        resolve_similarity=float(pipe_raw.get("resolve_similarity", 0.92)),
    )

    server = ServerSettings(
        host=srv_raw.get("host", "127.0.0.1"),
        port=int(srv_raw.get("port", 8000)),
        cors_origins=list(srv_raw.get("cors_origins", ["http://localhost:5173"])),
    )

    exp_raw = raw.get("experiment", {})
    experiment = ExperimentSettings(
        budget_usd=float(os.getenv("BUDGET_USD", exp_raw.get("budget_usd", 0.0))),
        prices_per_mtok={k: {"input": float(v.get("input", 0.5)),
                             "output": float(v.get("output", 1.5))}
                         for k, v in (exp_raw.get("prices_per_mtok", {}) or {}).items()},
    )

    db_path_str = os.getenv("DB_PATH", store_raw.get("db_path", "userstate.db"))
    db_path = Path(db_path_str)
    if not db_path.is_absolute():
        db_path = BACKEND_DIR / db_path

    return Settings(llm=llm, pipeline=pipeline, server=server,
                    experiment=experiment, db_path=db_path)


settings = _load()
