class AggregatorError(Exception):
    pass


class NotFoundError(AggregatorError):
    pass


class ConflictError(AggregatorError):
    pass
