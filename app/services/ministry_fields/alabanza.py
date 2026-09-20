from app.schemas.ministry import FieldDefinition
from app.services.ministry_fields.base import MinistryFieldsProvider
from app.services.ministry_fields.registry import register_ministry_fields


@register_ministry_fields("Alabanza")
class AlabanzaFieldsProvider(MinistryFieldsProvider):
    def provide(self) -> list[FieldDefinition]:
        return [
            FieldDefinition(name="main_instrument", label="Instrumento principal", type="text", required=True),
        ]