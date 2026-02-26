import os
from dotenv import load_dotenv
from typing import Optional

# Load .env from the project root (parent of src/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_PROJECT_ROOT, '.env'))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


class Config:
    """
    Configuration management for Redirx.
    Loads settings from environment variables with sensible defaults.
    """

    # Supabase Configuration
    SUPABASE_URL: Optional[str] = os.getenv('SUPABASE_URL')
    SUPABASE_KEY: Optional[str] = os.getenv('SUPABASE_KEY')

    # OpenAI Configuration
    OPENAI_API_KEY: Optional[str] = os.getenv('OPENAI_API_KEY')

    # Embedding Configuration
    EMBEDDING_MODEL: str = os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small')
    EMBEDDING_DIMENSION: int = int(os.getenv('EMBEDDING_DIMENSION', '1536'))

    # Stripe Configuration
    STRIPE_SECRET_KEY: Optional[str] = os.getenv('STRIPE_SECRET_KEY')
    STRIPE_WEBHOOK_SECRET: Optional[str] = os.getenv('STRIPE_WEBHOOK_SECRET')

    # Stripe Price IDs (set in .env, created in Stripe Dashboard)
    STRIPE_PRICE_ID_STARTER_MONTHLY: Optional[str] = os.getenv('STRIPE_PRICE_ID_STARTER_MONTHLY')
    STRIPE_PRICE_ID_STARTER_ANNUAL: Optional[str] = os.getenv('STRIPE_PRICE_ID_STARTER_ANNUAL')
    STRIPE_PRICE_ID_GROWTH_MONTHLY: Optional[str] = os.getenv('STRIPE_PRICE_ID_GROWTH_MONTHLY')
    STRIPE_PRICE_ID_GROWTH_ANNUAL: Optional[str] = os.getenv('STRIPE_PRICE_ID_GROWTH_ANNUAL')
    STRIPE_PRICE_ID_SCALE_MONTHLY: Optional[str] = os.getenv('STRIPE_PRICE_ID_SCALE_MONTHLY')
    STRIPE_PRICE_ID_SCALE_ANNUAL: Optional[str] = os.getenv('STRIPE_PRICE_ID_SCALE_ANNUAL')
    STRIPE_PRICE_ID_FOUNDER: Optional[str] = os.getenv('STRIPE_PRICE_ID_FOUNDER')
    STRIPE_PRICE_ID_CREDITS: Optional[str] = os.getenv('STRIPE_PRICE_ID_CREDITS')

    # Trial Invite System
    TRIAL_INVITE_PEPPER: Optional[str] = os.getenv('TRIAL_INVITE_PEPPER')
    CRON_SECRET: Optional[str] = os.getenv('CRON_SECRET')

    # Email / Resend
    RESEND_API_KEY: Optional[str] = os.getenv('RESEND_API_KEY')
    EMAIL_FROM_ADDRESS: str = os.getenv('EMAIL_FROM_ADDRESS', 'Dylon @ RedirX <noreply@redirx.dev>')
    APP_BASE_URL: str = os.getenv('APP_BASE_URL', 'http://localhost:3000')

    # Matching Thresholds
    # HIGH = 0.9+, MEDIUM = 0.85-0.9, LOW = 0.7-0.85, < 0.7 = rejected (orphaned)
    HIGH_CONFIDENCE_THRESHOLD: float = float(os.getenv('HIGH_CONFIDENCE_THRESHOLD', '0.85'))
    MEDIUM_CONFIDENCE_THRESHOLD: float = float(os.getenv('MEDIUM_CONFIDENCE_THRESHOLD', '0.7'))
    AMBIGUITY_GAP_THRESHOLD: float = float(os.getenv('AMBIGUITY_GAP_THRESHOLD', '0.1'))

    # Deep Match Preview Funnel
    ENABLE_DEEP_MATCH_PREVIEW: bool = _env_bool('ENABLE_DEEP_MATCH_PREVIEW', False)
    PREVIEW_MAX_OLD_CANDIDATES: int = int(os.getenv('PREVIEW_MAX_OLD_CANDIDATES', '12'))
    PREVIEW_MAX_NEW_URLS_FULL_SCAN: int = int(os.getenv('PREVIEW_MAX_NEW_URLS_FULL_SCAN', '300'))
    PREVIEW_MAX_NEW_URLS_CAPPED: int = int(os.getenv('PREVIEW_MAX_NEW_URLS_CAPPED', '180'))
    PREVIEW_FREE_ROWS: int = int(os.getenv('PREVIEW_FREE_ROWS', '2'))
    PREVIEW_MAX_JOBS_PER_USER_PER_DAY: int = int(os.getenv('PREVIEW_MAX_JOBS_PER_USER_PER_DAY', '2'))

    @classmethod
    def validate(cls) -> None:
        """
        Validates that required configuration values are set.

        Raises:
            ValueError: If required configuration is missing.
        """
        required_config = {
            'SUPABASE_URL': cls.SUPABASE_URL,
            'SUPABASE_KEY': cls.SUPABASE_KEY,
        }

        missing = [key for key, value in required_config.items() if not value]

        if missing:
            raise ValueError(
                f"Missing required configuration: {', '.join(missing)}. "
                f"Please set these in your .env file. See .env.example for reference."
            )

    @classmethod
    def validate_embeddings(cls) -> None:
        """
        Validates that embedding-related configuration is set.

        Raises:
            ValueError: If embedding configuration is missing.
        """
        if not cls.OPENAI_API_KEY:
            raise ValueError(
                "Missing OPENAI_API_KEY. "
                "Please set this in your .env file to use embedding features."
            )
