from tg_lottery.domain.public_refs import (
    INVITE_LINK_NAME_MAX_LENGTH,
    is_campaign_public_ref,
    is_neutral_invite_link_name,
    new_invite_link_name,
)


def test_campaign_public_reference_format_is_strict() -> None:
    assert is_campaign_public_ref("AbCdEfGhIjKlMnOpQrSt-_")
    assert not is_campaign_public_ref("AbCdEfGhIjKlMnOpQrSt-_X")
    assert not is_campaign_public_ref("fc99b181-47cc-faf6-b7c3-ab660c4ed6e6")


def test_invite_link_name_is_neutral_and_bounded() -> None:
    name = new_invite_link_name(lambda _: "neutral_token_12")

    assert name == "lottery_neutral_token_12"
    assert len(name) <= INVITE_LINK_NAME_MAX_LENGTH
    assert is_neutral_invite_link_name(name)
    assert not is_neutral_invite_link_name("lot_4dc7878e75ad4a04a6f7")
