from app.services.ministry_fields.base import MinistryFieldsProvider


class DefaultFieldsProvider(MinistryFieldsProvider):
    """Ministerio sin campos extra registrados todavía."""

    def provide(self) -> list:
        return []


_REGISTRY: dict[str, type[MinistryFieldsProvider]] = {}


def register_ministry_fields(ministry_name: str):
    """Decorador: registra un provider para un ministerio, sin tocar el registro."""

    def decorator(provider_cls: type[MinistryFieldsProvider]) -> type[MinistryFieldsProvider]:
        _REGISTRY[ministry_name] = provider_cls
        return provider_cls

    return decorator


class MinistryFieldsRegistry:
    def get_provider(self, ministry_name: str) -> MinistryFieldsProvider:
        provider_cls = _REGISTRY.get(ministry_name, DefaultFieldsProvider)
        return provider_cls()