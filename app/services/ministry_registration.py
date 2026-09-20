from app.models.enums import FunctionStatus
from app.repositories.ministry import MinistryRepository
from app.schemas.ministry import MinistryRegistrationInfo
from app.services.ministry_fields.registry import MinistryFieldsRegistry


class MinistryRegistrationService:
    """Coordinate the ministry listing use case for registration."""

    def __init__(self, repository: MinistryRepository, fields_registry: MinistryFieldsRegistry):
        self.repository = repository
        self.fields_registry = fields_registry

    def list_for_registration(self) -> list[MinistryRegistrationInfo]:
        return [
            MinistryRegistrationInfo(
                id=ministry.id,
                name=ministry.name,
                description=ministry.description,
                functions=[f.name for f in ministry.functions if f.status == FunctionStatus.ACTIVE],
                extra_fields=self.fields_registry.get_provider(ministry.name).provide(),
            )
            for ministry in self.repository.list_active()
        ]