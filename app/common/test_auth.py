import pytest
from app.common.auth import AuthService
from app.config import config

def test_get_effective_token_with_sso(mocker):
    # Setup
    token = "sso-provided-token"
    mock_logger = mocker.patch("app.common.auth.logger")
    
    # Execute
    result = AuthService.get_effective_token(token)
    
    # Verify
    assert result == token
    mock_logger.info.assert_called_with("Using provided SSO token from external source.")

def test_get_effective_token_with_none(mocker):
    # Setup
    mock_logger = mocker.patch("app.common.auth.logger")
    expected_fallback = config.auth_fallback_token
    
    # Execute
    result = AuthService.get_effective_token(None)
    
    # Verify
    assert result == expected_fallback
    mock_logger.warning.assert_called_with("SSO token missing or empty. Falling back to default system token.")

def test_get_effective_token_with_empty_string(mocker):
    # Setup
    mock_logger = mocker.patch("app.common.auth.logger")
    expected_fallback = config.auth_fallback_token
    
    # Execute
    result = AuthService.get_effective_token("   ")
    
    # Verify
    assert result == expected_fallback
    mock_logger.warning.assert_called_with("SSO token missing or empty. Falling back to default system token.")
