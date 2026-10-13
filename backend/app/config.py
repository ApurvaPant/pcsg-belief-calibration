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
    # Per-model vendor request params merged into every call to that model (e.g.
    # {"deepseek-ai/deepseek-v4.1-flash": {"reasoning_effort": 0}}). "default"
    # applies to any model without its own entry. Keeps reasoning effort IDENTICAL
    # for every pipeline call.
    request_params: dict = field(default_factory=dict)

    def params_for(self, model: str) -> dict:
        merged = dict(self.request_params.get("default", {}) or {})
        merged.update(self.request_params.get(model, {}) or {})
        return merged


@dataclass
class PipelineSettings:
    salience_threshold: float
    calibration_prior: float
    calibration_shrink: float
    reinforce_step: float
    resolve_similarity: float
    destructive_min_conf: float


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
    # Free-tier providers (e.g. NVIDIA build) have no dollar cost: the price-guard
    # is skipped and the run is bounded by a TOKEN budget instead of dollars.
    free_tier: bool = False
    token_budget: int = 80_000_000

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

    # Key resolution by provider, then generic fallbacks. Each provider uses an
    # OpenAI-compatible endpoint, so the vendor key plugs into the same client.
    # Keys are stripped of stray whitespace/newlines from .env.
    provider_name = os.getenv("LLM_PROVIDER", llm_raw.get("provider", "mock"))
    if provider_name == "nvidia":
        api_key = os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    elif provider_name == "gemini":
        api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                   or os.getenv("OPENAI_API_KEY", ""))
    else:
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    api_key = (api_key or "").strip()

    # Per-provider base URL. OPENAI_BASE_URL overrides ONLY for the generic
    # openai/openrouter providers, so a stale .env OPENAI_BASE_URL (e.g. a gemini
    # URL) can never bleed into nvidia/gemini.
    _BASE_URLS = {
        "nvidia": "https://integrate.api.nvidia.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    if provider_name in ("openai", "openrouter"):
        base_url = os.getenv("OPENAI_BASE_URL") or llm_raw.get("base_url", "") or _BASE_URLS.get(provider_name, "")
    else:
        base_url = llm_raw.get("base_url", "") or _BASE_URLS.get(provider_name, "")

    llm = LLMSettings(
        provider=provider_name,
        base_url=base_url,
        api_key=api_key,
        models=models,
        timeout_seconds=int(llm_raw.get("timeout_seconds", 60)),
        temperature=float(llm_raw.get("temperature", 0.2)),
        max_tokens=int(llm_raw.get("max_tokens", 1024)),
        request_params=dict(llm_raw.get("request_params", {}) or {}),
    )
    # NOTE: no silent fallback to mock. A real provider with a missing/invalid
    # key must fail loudly at startup (see llm.startup_check).

    pipeline = PipelineSettings(
        salience_threshold=float(pipe_raw.get("salience_threshold", 0.5)),
        calibration_prior=float(pipe_raw.get("calibration_prior", 0.5)),
        calibration_shrink=float(pipe_raw.get("calibration_shrink", 0.8)),
        reinforce_step=float(pipe_raw.get("reinforce_step", 0.1)),
        resolve_similarity=float(pipe_raw.get("resolve_similarity", 0.92)),
        destructive_min_conf=float(pipe_raw.get("destructive_min_conf", 0.80)),
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
        free_tier=bool(exp_raw.get("free_tier", False)),
        token_budget=int(os.getenv("TOKEN_BUDGET", exp_raw.get("token_budget", 80_000_000))),
    )

    db_path_str = os.getenv("DB_PATH", store_raw.get("db_path", "userstate.db"))
    db_path = Path(db_path_str)
    if not db_path.is_absolute():
        db_path = BACKEND_DIR / db_path

    return Settings(llm=llm, pipeline=pipeline, server=server,
                    experiment=experiment, db_path=db_path)


settings = _load()
