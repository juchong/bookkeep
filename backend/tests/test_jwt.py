import jwt as pyjwt

from app.jwt import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_access_token,
    verify_refresh_token,
)


def test_pyjwt_access_token_round_trip():
    token = create_access_token(42, "reader", False)

    token_data = verify_access_token(token)

    assert token_data is not None
    assert token_data.user_id == 42
    assert token_data.username == "reader"
    assert token_data.token_type == "access"


def test_access_and_refresh_tokens_are_not_interchangeable():
    access = create_access_token(42, "reader", False)
    refresh = create_refresh_token(42, "reader", False)

    assert verify_refresh_token(access) is None
    assert verify_access_token(refresh) is None


def test_decode_rejects_token_signed_with_another_key():
    forged = pyjwt.encode(
        {"sub": "42", "username": "reader", "token_type": "access"},
        SECRET_KEY + "-attacker",
        algorithm=ALGORITHM,
    )

    assert decode_token(forged) is None
