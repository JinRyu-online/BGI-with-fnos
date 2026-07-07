import pytest

from auth import AuthState, AuthError


def test_correct_key_known_ip_passes():
    state = AuthState(api_key="secret", trusted_ips=["192.168.1.5"])

    state.verify(token="secret", client_ip="192.168.1.5")  # no raise


def test_wrong_key_rejected():
    state = AuthState(api_key="secret", trusted_ips=["192.168.1.5"])

    with pytest.raises(AuthError):
        state.verify(token="wrong", client_ip="192.168.1.5")


def test_untrusted_ip_with_correct_key_auto_learns():
    # Per design: the key is the real credential. A correct key from a new IP
    # is auto-learned (the whitelist is an audit/visibility layer, not a hard
    # pre-auth gate). The user explicitly accepted key-only auth on the LAN.
    state = AuthState(api_key="secret", trusted_ips=["192.168.1.5"])

    state.verify(token="secret", client_ip="10.0.0.9")  # no raise

    assert "10.0.0.9" in state.trusted_ips


def test_first_correct_key_from_new_ip_auto_learns(tmp_path):
    state = AuthState(api_key="secret", trusted_ips=[])

    # first request from a new IP with correct key -> auto-learned, passes
    state.verify(token="secret", client_ip="192.168.1.50")

    assert "192.168.1.50" in state.trusted_ips
    # subsequent requests from that IP pass
    state.verify(token="secret", client_ip="192.168.1.50")


def test_first_request_wrong_key_does_not_auto_learn():
    state = AuthState(api_key="secret", trusted_ips=[])

    with pytest.raises(AuthError):
        state.verify(token="wrong", client_ip="192.168.1.50")

    assert "192.168.1.50" not in state.trusted_ips


def test_persist_round_trip(tmp_path):
    path = tmp_path / "trusted.json"
    state = AuthState(api_key="secret", trusted_ips=["192.168.1.5"], store_path=path)
    state.verify(token="secret", client_ip="192.168.1.99")  # auto-learn + persist

    reloaded = AuthState(api_key="secret", trusted_ips=[], store_path=path)
    assert "192.168.1.5" in reloaded.trusted_ips
    assert "192.168.1.99" in reloaded.trusted_ips


def test_clear_trusted_ips(tmp_path):
    path = tmp_path / "trusted.json"
    state = AuthState(api_key="secret", trusted_ips=["1.1.1.1"], store_path=path)

    state.clear_trusted_ips()

    assert state.trusted_ips == []
    reloaded = AuthState(api_key="secret", trusted_ips=[], store_path=path)
    assert reloaded.trusted_ips == []
