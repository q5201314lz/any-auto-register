"""Local mailbox pool — register into unified registry."""
from core.local_ms_mailbox import LocalMailboxPool, LocalMicrosoftMailboxPool  # noqa: F401
from providers.registry import register_provider

register_provider("mailbox", "local_ms_pool")(LocalMicrosoftMailboxPool)
register_provider("mailbox", "local_mail_pool")(LocalMailboxPool)
