class PipelineError(RuntimeError):
    """An expected, actionable pipeline error."""


class SourceError(PipelineError):
    """Failure while accessing or validating an external source."""


class SchemaError(PipelineError):
    """The source does not comply with the expected column contract."""


class DataQualityError(PipelineError):
    """The data does not meet the minimum quality requirements."""
