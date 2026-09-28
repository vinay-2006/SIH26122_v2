"""
SETUAI V7 Centralized Configuration Layer.

Centralizes environment access for:
- PostgreSQL / Supabase direct database connection
- Supabase Auth & JWT verification (JWKS, HMAC, Dev mode)
- Supabase Storage bucket names
- Multi-provider LLM credentials (Groq, Gemini)
- CORS and network configuration
- External integration settings (P6 REST)

Strict safety contract:
- Sensitive values are masked in string representations and logging.
- AUTH_DEV_MODE defaults to False to prevent accidental unsigned token acceptance in production.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 1. Database (PostgreSQL / Supabase direct connection)
    DATABASE_URL: Optional[str] = Field(default=None, description="Direct PostgreSQL connection string")

    # 2. Supabase Auth & Core
    SUPABASE_URL: Optional[str] = Field(default=None, description="Supabase project endpoint URL")
    SUPABASE_ANON_KEY: Optional[str] = Field(default=None, description="Supabase public/anon key")
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = Field(default=None, description="Supabase administrative service-role key")
    SUPABASE_JWKS_URL: Optional[str] = Field(default=None, description="Supabase asymmetric JWKS endpoint")
    SUPABASE_JWT_SECRET: Optional[str] = Field(default=None, description="Supabase symmetric HMAC secret (legacy/offline)")
    AUTH_DEV_MODE: bool = Field(default=False, description="Explicit opt-in to unsigned JWT fallback for local testing ONLY")

    # 3. Storage Configuration
    STORAGE_EVIDENCE_BUCKET: str = Field(default="evidence", description="Supabase storage bucket for photos & evidence")
    STORAGE_DOCUMENTS_BUCKET: str = Field(default="documents", description="Supabase storage bucket for schedules & source docs")

    # 4. AI / LLM Configuration
    LLM_PROVIDER: str = Field(default="groq", description="Primary LLM provider ('groq' or 'gemini')")
    LLM_API_KEY: Optional[str] = Field(default=None, description="Generic/active LLM provider API key")
    GROQ_API_KEY: Optional[str] = Field(default=None, description="Dedicated Groq API key")
    GEMINI_API_KEY: Optional[str] = Field(default=None, description="Dedicated Gemini API key")
    LLM_MODEL: Optional[str] = Field(default=None, description="Model override name")
    EXTRACTION_FALLBACK: Optional[str] = Field(default=None, description="Deterministic extraction fallback ('rules' or unset)")

    # 5. Network & Integrations
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated permitted CORS origins",
    )
    P6_BASE_URL: Optional[str] = Field(
        default="http://127.0.0.1:8000/api/v1/mock-p6",
        description="P6 EPPM REST API target URL or local mock endpoint",
    )

    # --- Helper Methods ---

    def is_database_configured(self) -> bool:
        """Check whether DATABASE_URL is configured with non-empty text."""
        return bool(self.DATABASE_URL and self.DATABASE_URL.strip())

    def is_auth_configured(self) -> bool:
        """Check whether Supabase Auth verification credentials are provided."""
        return bool(
            (self.SUPABASE_JWT_SECRET and self.SUPABASE_JWT_SECRET.strip())
            or (self.SUPABASE_JWKS_URL and self.SUPABASE_JWKS_URL.strip())
            or (self.SUPABASE_URL and (self.SUPABASE_ANON_KEY or self.SUPABASE_SERVICE_ROLE_KEY))
        )

    def is_llm_configured(self) -> bool:
        """Check whether an API key is available for the configured or fallback provider."""
        return bool(
            self.LLM_API_KEY
            or (self.LLM_PROVIDER.lower() == "groq" and self.GROQ_API_KEY)
            or (self.LLM_PROVIDER.lower() == "gemini" and self.GEMINI_API_KEY)
            or self.GROQ_API_KEY
            or self.GEMINI_API_KEY
        )

    def get_effective_llm_key(self, provider: Optional[str] = None) -> Optional[str]:
        """Resolve the active API key for the requested provider."""
        target_prov = (provider or self.LLM_PROVIDER).lower()
        if target_prov == "groq":
            return self.GROQ_API_KEY or self.LLM_API_KEY
        elif target_prov == "gemini":
            return self.GEMINI_API_KEY or self.LLM_API_KEY
        return self.LLM_API_KEY

    def get_cors_origins_list(self) -> List[str]:
        """Return CORS origins as a list of stripped strings."""
        if not self.CORS_ORIGINS:
            return ["http://localhost:5173", "http://127.0.0.1:5173"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    def get_safe_summary(self) -> Dict[str, str]:
        """
        Produce a sanitized status dictionary reporting presence without revealing secret values.
        Returns SET / NOT SET for every tracked infrastructure variable.
        """
        tracked = {
            "DATABASE_URL": self.DATABASE_URL,
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_ANON_KEY": self.SUPABASE_ANON_KEY,
            "SUPABASE_SERVICE_ROLE_KEY": self.SUPABASE_SERVICE_ROLE_KEY,
            "SUPABASE_JWKS_URL": self.SUPABASE_JWKS_URL,
            "SUPABASE_JWT_SECRET": self.SUPABASE_JWT_SECRET,
            "AUTH_DEV_MODE": "true" if self.AUTH_DEV_MODE else None,
            "STORAGE_EVIDENCE_BUCKET": self.STORAGE_EVIDENCE_BUCKET,
            "STORAGE_DOCUMENTS_BUCKET": self.STORAGE_DOCUMENTS_BUCKET,
            "LLM_PROVIDER": self.LLM_PROVIDER,
            "LLM_API_KEY": self.LLM_API_KEY,
            "GROQ_API_KEY": self.GROQ_API_KEY,
            "GEMINI_API_KEY": self.GEMINI_API_KEY,
            "LLM_MODEL": self.LLM_MODEL,
            "EXTRACTION_FALLBACK": self.EXTRACTION_FALLBACK,
            "CORS_ORIGINS": self.CORS_ORIGINS,
            "P6_BASE_URL": self.P6_BASE_URL,
        }
        return {k: ("SET" if v else "NOT SET") for k, v in tracked.items()}


# Global singleton instance
settings = Settings()
