from pydantic import BaseModel


class FieldDefinition(BaseModel):
    name: str
    label: str
    type: str
    required: bool = False


class MinistryRegistrationInfo(BaseModel):
    id: int
    name: str
    description: str | None
    functions: list[str]
    extra_fields: list[FieldDefinition]