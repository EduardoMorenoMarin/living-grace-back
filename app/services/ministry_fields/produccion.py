from app.schemas.ministry import FieldDefinition
from app.services.ministry_fields.base import MinistryFieldsProvider
from app.services.ministry_fields.registry import register_ministry_fields


@register_ministry_fields("Producción")
class ProduccionFieldsProvider(MinistryFieldsProvider):
    def provide(self) -> list[FieldDefinition]:
        return [
            FieldDefinition(name="technical_area", label="Área técnica", type="text", required=True),
        ]