from app.models.enums import FunctionStatus
from app.repositories.ministry import MinistryRepository
from app.schemas.ministry import FunctionOption, MinistryRegistrationInfo


class MinistryRegistrationService:
    """Coordinate the ministry listing use case for registration."""

    def __init__(self, repository: MinistryRepository):
        self.repository = repository

    def list_for_registration(self) -> list[MinistryRegistrationInfo]:
        result = []
        for ministry in self.repository.list_active():
            functions = [
                function for function in ministry.functions
                if function.status == FunctionStatus.ACTIVE and function.deleted_at is None
            ]
            result.append(MinistryRegistrationInfo(
                id=ministry.id,
                name=ministry.name,
                description=ministry.description,
                functions=[function.name for function in functions],
                function_options=[FunctionOption(id=function.id, name=function.name) for function in functions],
            ))
        return result
