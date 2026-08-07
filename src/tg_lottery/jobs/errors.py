class JobRetry(Exception):
    def __init__(self, message: str, *, delay_seconds: int = 30) -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds


class JobBlocked(Exception):
    pass
