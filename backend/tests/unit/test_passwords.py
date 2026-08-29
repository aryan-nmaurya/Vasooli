"""Operator password hashing is salted, versioned, and fails closed."""

from app.core.passwords import hash_password, verify_password


def test_password_round_trip():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt-v1$")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong password", encoded) is False


def test_equal_passwords_receive_different_salts():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second


def test_corrupt_or_unknown_hashes_fail_closed():
    assert verify_password("anything", "") is False
    assert verify_password("anything", "unknown$1$2$3$4$5") is False
    assert verify_password("anything", "scrypt-v1$bad$2$3$4$5") is False
