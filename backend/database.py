import os
from functools import lru_cache

from dotenv import load_dotenv
from supabase import Client, create_client


load_dotenv()


def _get_supabase_url() -> str:
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing from .env")
    return supabase_url


def _get_anon_key() -> str:
    anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not anon_key:
        raise RuntimeError("SUPABASE_ANON_KEY is missing from .env")
    return anon_key


def _get_service_role_key() -> str:
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not service_role_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is required for secure backend auth operations"
        )
    return service_role_key


@lru_cache
def get_supabase_client() -> Client:
    """Create one Supabase client using server-side environment variables."""
    supabase_url = _get_supabase_url()
    supabase_key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )

    if not supabase_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY is missing from .env"
        )

    return create_client(supabase_url, supabase_key)


def get_public_supabase_client() -> Client:
    """Create a client for public Auth operations such as login."""
    return create_client(_get_supabase_url(), _get_anon_key())


@lru_cache
def get_service_role_client() -> Client:
    """Create a backend-only client. Never send this key to a frontend."""
    return create_client(_get_supabase_url(), _get_service_role_key())
