class PipelineError(RuntimeError):
    """Erro esperado e acionavel do pipeline."""


class SourceError(PipelineError):
    """Falha ao acessar ou validar uma fonte externa."""


class SchemaError(PipelineError):
    """A fonte nao possui o contrato de colunas esperado."""


class DataQualityError(PipelineError):
    """Os dados nao atendem aos requisitos minimos de qualidade."""
