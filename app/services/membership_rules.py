from collections.abc import Sequence
from typing import Protocol

from app.models.function import Function
from app.models.ministry import Ministry


class MembershipRule(Protocol):
    """An additional requirement for an already validated membership selection.

    Inspect the ministry and selected functions without mutating them. Raise
    RegistrationValidationError on rejection; do not write or commit here.
    """

    def validate(self, ministry: Ministry, functions: Sequence[Function]) -> None:
        ...
