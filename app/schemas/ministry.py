from pydantic import BaseModel


class FunctionOption(BaseModel):
    id: int
    name: str


class MinistryRegistrationInfo(BaseModel):
    id: int
    name: str
    description: str | None
    functions: list[str]
    function_options: list[FunctionOption]
