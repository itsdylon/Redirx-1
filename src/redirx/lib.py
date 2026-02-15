from src.redirx import stages
from src.redirx.url_matcher import UrlMatchConfig
from typing import Optional
from uuid import UUID

class Pipeline:
    def __init__(
        self,
        input: any,
        stages: Optional[list[stages.Stage]] = None,
        session_id: Optional[UUID] = None,
        pipeline_type: str = 'content',
        match_config: Optional[UrlMatchConfig] = None,
    ):
        """
        Initialize the pipeline.

        Args:
            input: Initial input data (tuple of old URLs and new URLs).
            stages: Optional custom stage list. If None, uses pipeline_type to select.
            session_id: Optional migration session ID.
            pipeline_type: 'content' (default 7-stage) or 'url_only' (4-stage, no API cost).
            match_config: Optional UrlMatchConfig for url_only pipeline.
        """
        if stages is not None:
            self.__stages = stages
        elif pipeline_type == 'url_only':
            self.__stages = Pipeline.url_only_pipeline(
                session_id=session_id,
                match_config=match_config,
            )
        else:
            self.__stages = Pipeline.default_pipeline(session_id=session_id)

        self.__index = 0
        self.state = input
        self.session_id = session_id
        self.pipeline_type = pipeline_type

    """
    Returns the default pipeline.
    """
    @classmethod
    def default_pipeline(cls, session_id: Optional[UUID] = None) -> list[stages.Stage]:
        """
        Create the default pipeline with optional session ID.

        Pipeline stages (in order):
        1. UrlPruneStage - Filter out assets (.css, .js, images, etc.)
        2. BlogPruneStage - Filter individual blog posts (keep landing pages)
        3. ExactUrlMatchStage - Match identical URL paths (before scraping)
        4. WebScraperStage - Scrape remaining HTML content
        5. HtmlPruneStage - Match pages with identical HTML
        6. EmbedStage - Generate vector embeddings
        7. PairingStage - Semantic matching via vector similarity

        Args:
            session_id: Migration session ID for database operations.

        Returns:
            List of Stage instances.
        """
        return [
            stages.UrlPruneStage(),
            stages.BlogPruneStage(),
            stages.ExactUrlMatchStage(session_id=session_id),
            stages.WebScraperStage(),
            stages.HtmlPruneStage(),
            stages.EmbedStage(session_id=session_id),
            stages.PairingStage(session_id=session_id),
        ]

    @classmethod
    def url_only_pipeline(
        cls,
        session_id: Optional[UUID] = None,
        match_config: Optional[UrlMatchConfig] = None,
    ) -> list[stages.Stage]:
        """
        Create the URL-only pipeline (free tier, no API cost).

        Pipeline stages (in order):
        1. UrlPruneStage - Filter out assets
        2. BlogPruneStage - Filter blog posts
        3. ExactUrlMatchStage - Match identical URL paths
        4. UrlSimilarityMatchStage - Slug, TF-IDF, and fuzzy matching

        Args:
            session_id: Migration session ID for database operations.
            match_config: Optional UrlMatchConfig for tuning thresholds.

        Returns:
            List of Stage instances.
        """
        return [
            stages.UrlPruneStage(),
            stages.BlogPruneStage(),
            stages.ExactUrlMatchStage(session_id=session_id),
            stages.UrlSimilarityMatchStage(session_id=session_id, config=match_config),
        ]

    @property
    def stage_names(self) -> list[str]:
        """Return the friendly name of each stage."""
        return [s.name for s in self.__stages]

    @property
    def total_stages(self) -> int:
        """Return the total number of stages."""
        return len(self.__stages)

    @property
    def current_stage_index(self) -> int:
        """Return the number of stages completed so far."""
        return self.__index

    """
    Used to contol pipeline advancement. Currently just yields the internal state,
    but in the future should yield debug information about the iteration.
    """
    async def iterate(self) -> any:
        while self.__index < len(self.__stages):
            self.state = await self.__stages[self.__index].execute(self.state)
            self.__index += 1
            yield self.state