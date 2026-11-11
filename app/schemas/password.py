from typing import Annotated

from pydantic import Field, SecretStr


NewPassword = Annotated[SecretStr, Field(min_length=8, max_length=128)]
