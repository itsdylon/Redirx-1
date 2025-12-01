import stages
from typing import Optional
from uuid import UUID

class Pipeline:
    def __init__(
        self,
        input: any,
        stages: Optional[list[stages.Stage]] = None,
        session_id: Optional[UUID] = None
    ):
        """
        Initialize the pipeline.

        Args:
            input: Initial input data (tuple of old URLs and new URLs).
            stages: Optional custom stage list. If None, uses default pipeline.
            session_id: Optional migration session ID. Required for EmbedStage and PairingStage.
        """
        if stages is None:
            self.__stages = Pipeline.default_pipeline(session_id=session_id)
        else:
            self.__stages = stages

        self.__index = 0
        self.state = input
        self.session_id = session_id

    """
    Returns the default pipeline.
    """
    @classmethod
    def default_pipeline(session_id: Optional[UUID] = None) -> list[stages.Stage]:
        """
        Create the default pipeline with optional session ID.

        Args:
            session_id: Migration session ID for database operations.

        Returns:
            List of Stage instances.
        """
        return [
            stages.UrlPruneStage(),
            stages.WebScraperStage(),
            stages.HtmlPruneStage(),
            stages.EmbedStage(session_id=session_id),
            stages.PairingStage(session_id=session_id),
        ]
    
    """
    Used to contol pipeline advancement. Currently just yields the internal state,
    but in the future should yield debug information about the iteration.
    """
    async def iterate(self) -> any:
        while self.__index < len(self.__stages):
            self.state = await self.__stages[self.__index].execute(self.state)
            self.__index += 1
            yield self.state