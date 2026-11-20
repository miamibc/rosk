"""Keep master token in the GNOME keyring (libsecret)."""

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret  # noqa: E402

from .. import APP_ID  # noqa: E402

_SCHEMA = Secret.Schema.new(
    APP_ID + ".Keep", Secret.SchemaFlags.NONE,
    {"email": Secret.SchemaAttributeType.STRING},
)


def store_keep_token(email, token):
    Secret.password_store_sync(_SCHEMA, {"email": email}, Secret.COLLECTION_DEFAULT,
                               f"Rosk: Google Keep ({email})", token, None)


def keep_token(email):
    return Secret.password_lookup_sync(_SCHEMA, {"email": email}, None)


def forget_keep_token(email):
    Secret.password_clear_sync(_SCHEMA, {"email": email}, None)


def exchange_oauth_token(email, oauth_token):
    """Turns the one-time `oauth_token` cookie from accounts.google.com/EmbeddedSetup
    into a long-lived master token (gpsoauth)."""
    import secrets as _rnd

    import gpsoauth

    android_id = _rnd.token_hex(8)
    res = gpsoauth.exchange_token(email, oauth_token.strip(), android_id)
    token = res.get("Token")
    if not token:
        raise RuntimeError(res.get("Error") or "Google did not return a token")
    return token
