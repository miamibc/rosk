"""Google accounts and access tokens from GNOME Online Accounts."""

from dataclasses import dataclass

import gi

gi.require_version("Goa", "1.0")
from gi.repository import Goa  # noqa: E402


class GoaError(Exception):
    pass


@dataclass
class GoogleAccount:
    goa_id: str
    email: str
    tasks_enabled: bool


def _client():
    return Goa.Client.new_sync(None)


def list_google_accounts():
    result = []
    for obj in _client().get_accounts():
        acc = obj.get_account()
        if acc.props.provider_type != "google":
            continue
        # Google Tasks access in GOA follows the "Calendar" switch.
        result.append(GoogleAccount(acc.props.id, acc.props.presentation_identity,
                                    not acc.props.calendar_disabled))
    return result


def _find(goa_id):
    for obj in _client().get_accounts():
        if obj.get_account().props.id == goa_id:
            return obj
    raise GoaError(f"Google account {goa_id} is not in Online Accounts any more")


def token_provider(goa_id):
    """Returns a callable(force_refresh) -> access token, safe to call from any thread."""

    def provide(force_refresh=False):
        obj = _find(goa_id)
        acc = obj.get_account()
        if acc.props.attention_needed or force_refresh:
            try:
                acc.call_ensure_credentials_sync(None)
            except Exception as e:
                raise GoaError(f"Online Accounts needs attention: {e}") from None
        oauth = obj.get_oauth2_based()
        if oauth is None:
            raise GoaError("account has no OAuth2 interface")
        try:
            token, _expires = oauth.call_get_access_token_sync(None)
        except Exception as e:
            raise GoaError(f"cannot get access token: {e}") from None
        return token

    return provide
