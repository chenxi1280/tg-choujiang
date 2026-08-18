import re
from collections.abc import Callable
from secrets import token_urlsafe

CAMPAIGN_PUBLIC_REF_LENGTH = 22
REFERRAL_PUBLIC_REF_LENGTH = CAMPAIGN_PUBLIC_REF_LENGTH
INVITE_LINK_NAME_MAX_LENGTH = 32
_PUBLIC_REF_PATTERN = re.compile(r"[A-Za-z0-9_-]{22}\Z")
_INVITE_NAME_PREFIX = "lottery_"
_INVITE_RANDOM_BYTES = 12
_INVITE_NAME_PATTERN = re.compile(r"lottery_[A-Za-z0-9_-]{1,24}\Z")


def is_campaign_public_ref(value: str) -> bool:
    return bool(_PUBLIC_REF_PATTERN.fullmatch(value))


def is_referral_public_ref(value: str) -> bool:
    return bool(_PUBLIC_REF_PATTERN.fullmatch(value))


def new_referral_public_ref(
    random_token: Callable[[int], str] = token_urlsafe,
) -> str:
    value = random_token(16)
    if not is_referral_public_ref(value):
        raise ValueError("referral public reference has an invalid format")
    return value


def is_neutral_invite_link_name(value: str) -> bool:
    return bool(_INVITE_NAME_PATTERN.fullmatch(value))


def new_invite_link_name(
    random_token: Callable[[int], str] = token_urlsafe,
) -> str:
    name = _INVITE_NAME_PREFIX + random_token(_INVITE_RANDOM_BYTES)
    if not is_neutral_invite_link_name(name):
        raise ValueError("invite link name is not a neutral Telegram label")
    return name
