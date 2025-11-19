import stages
from typing import Optional

class Pipeline:
    def __init__(self, input: any, stages: Optional[list[stages.Stage]] = None):
        if stages is None:
            self.__stages = Pipeline.default_pipeline()
        else:
            self.__stages = stages

        self.__index = 0
        self.state = input

    """
    Returns the default pipeline.
    """
    @classmethod
    def default_pipeline() -> list[stages.Stage]:
        return [
            stages.UrlPruneStage(),
            stages.WebScraperStage(),
            stages.HtmlPruneStage(),
            stages.EmbedStage(),
            stages.PairingStage(),
        ]
    
    """
    Used to contol pipeline advancement. Currently just yields the internal state but in the future should yield debug information about the iteration.
    """
    async def iterate(self) -> any:
        while self.__index < len(self.__stages):
            self.state = await self.__stages[self.__index].execute(self.state)
            self.__index += 1
            yield self.state