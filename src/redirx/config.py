import os
from dotenv import load_dotenv
from typing import Optional

# Load environment variables from .env file
load_dotenv()


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

    # Matching Thresholds
    # HIGH = 0.9+, MEDIUM = 0.85-0.9, LOW = 0.7-0.85, < 0.7 = rejected (orphaned)
    HIGH_CONFIDENCE_THRESHOLD: float = float(os.getenv('HIGH_CONFIDENCE_THRESHOLD', '0.85'))
    MEDIUM_CONFIDENCE_THRESHOLD: float = float(os.getenv('MEDIUM_CONFIDENCE_THRESHOLD', '0.7'))
    AMBIGUITY_GAP_THRESHOLD: float = float(os.getenv('AMBIGUITY_GAP_THRESHOLD', '0.1'))

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
