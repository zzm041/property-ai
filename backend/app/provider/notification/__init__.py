"""In-app notification is authoritative. External push is best-effort."""

from app.provider.errors import ProviderUnavailableError


class NotificationProvider:
    def deliver_push(self, *, user_id: int, title: str, body: str) -> None:
        raise NotImplementedError


class DevelopmentNotificationProvider(NotificationProvider):
    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior

    def deliver_push(self, *, user_id: int, title: str, body: str) -> None:
        del user_id, title, body
        if self.behavior in {"unavailable", "failure"}:
            raise ProviderUnavailableError("External notification is unavailable")
