from abc import ABC, abstractmethod

from app.schemas.ministry import FieldDefinition


class MinistryFieldsProvider(ABC):
    """Contrato para los campos extra de registro de un ministerio."""

    @abstractmethod
    def provide(self) -> list[FieldDefinition]:
        raise NotImplementedError