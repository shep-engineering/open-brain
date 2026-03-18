"""Tests for the secrets filter module."""

import os
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from secrets_filter import scan_content, check_content, redact_content, SecretDetectedError


# ─── Detection Tests ─────────────────────────────────────────────────────────

class TestScanContent:
    """Test that scan_content detects known secret patterns."""

    def test_anthropic_api_key(self):
        text = "key=sk-ant-api03-r8XuIsn6iGbu5VMd0Du5GdLfTWw5AR0CuI8qT2N9H864w"
        matches = scan_content(text)
        assert len(matches) >= 1
        assert any(m.pattern_name == "anthropic_api_key" for m in matches)

    def test_openai_api_key(self):
        text = "OPENAI_API_KEY=sk-proj1234567890abcdefghijklmn"
        matches = scan_content(text)
        assert len(matches) >= 1
        assert any(m.pattern_name == "openai_api_key" for m in matches)

    def test_aws_access_key(self):
        matches = scan_content("AKIAIOSFODNN7EXAMPLE")
        assert len(matches) >= 1
        assert any(m.pattern_name == "aws_access_key" for m in matches)

    def test_aws_secret_key(self):
        matches = scan_content("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        assert len(matches) >= 1
        assert any(m.pattern_name == "aws_secret_key" for m in matches)

    def test_github_token(self):
        matches = scan_content("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1234")
        assert len(matches) >= 1
        assert any(m.pattern_name == "github_token" for m in matches)

    def test_slack_token(self):
        matches = scan_content("xoxb-PLACEHOLDER-REMOVED-20260422")
        assert len(matches) >= 1
        assert any(m.pattern_name == "slack_token" for m in matches)

    def test_pem_private_key(self):
        matches = scan_content("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK...")
        assert len(matches) >= 1
        assert any(m.pattern_name == "private_key_pem" for m in matches)

    def test_bearer_token(self):
        matches = scan_content("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123")
        assert len(matches) >= 1
        assert any(m.pattern_name == "bearer_token" for m in matches)

    def test_database_url_with_password(self):
        matches = scan_content("postgresql://admin:SuperSecret123@prod.db.example.com:5432/app")
        assert len(matches) >= 1
        assert any(m.pattern_name == "database_url_with_password" for m in matches)

    def test_env_secret_assignment(self):
        matches = scan_content("ANTHROPIC_API_KEY=sk-ant-something-long-here")
        assert len(matches) >= 1
        assert any(m.pattern_name == "env_secret_assignment" for m in matches)

    def test_hex_secret(self):
        matches = scan_content("secret=aabbccdd00112233445566778899aabbccddeeff")
        assert len(matches) >= 1
        assert any(m.pattern_name == "hex_secret" for m in matches)

    def test_gcp_service_account_key(self):
        matches = scan_content('"private_key": "-----BEGIN RSA PRIVATE KEY-----\\n..."')
        assert len(matches) >= 1


# ─── False Positive Tests ────────────────────────────────────────────────────

class TestNoFalsePositives:
    """Ensure normal memory content passes clean."""

    @pytest.mark.parametrize("text", [
        "Decided to use Redis for session caching due to TTL support.",
        "The API key pattern uses sk- prefix which is common across providers.",
        "PostgreSQL runs on port 5432 with pgvector extension.",
        "ANTHROPIC_API_KEY moved from .env to secrets.env",
        "Use ollama pull nomic-embed-text to install the model",
        "config.py loads secrets from secrets.env first",
        "DATABASE_URL should be set in .env",
        "The router scores question complexity and memory volume.",
        "Built a 3-tier model routing system for agentic-lab.",
        "Bearer tokens should be stored securely, not in memory.",
    ])
    def test_clean_content(self, text):
        matches = scan_content(text)
        assert len(matches) == 0, f"False positive on: {text!r} -> {[m.pattern_name for m in matches]}"


# ─── Reject Mode Tests ───────────────────────────────────────────────────────

class TestRejectMode:
    """Test that check_content raises SecretDetectedError in reject mode."""

    def test_rejects_api_key(self):
        with pytest.raises(SecretDetectedError):
            check_content("sk-ant-api03-r8XuIsn6iGbu5VMd0Du5GdLfTWw5AR0CuI8qT2N9H864w")

    def test_passes_clean_content(self):
        result = check_content("Decided to use CustomTkinter for the GUI")
        assert result == "Decided to use CustomTkinter for the GUI"

    def test_error_message_contains_pattern_name(self):
        with pytest.raises(SecretDetectedError, match="anthropic_api_key"):
            check_content("sk-ant-api03-r8XuIsn6iGbu5VMd0Du5GdLfTWw5AR0CuI8qT2N9H864w")

    def test_error_does_not_contain_full_secret(self):
        """The error message should NOT echo the full secret back."""
        secret = "sk-ant-api03-r8XuIsn6iGbu5VMd0Du5GdLfTWw5AR0CuI8qT2N9H864w"
        try:
            check_content(secret)
        except SecretDetectedError as e:
            assert secret not in str(e), "Full secret should not appear in error message"


# ─── Redact Mode Tests ───────────────────────────────────────────────────────

class TestRedactMode:
    """Test that check_content redacts in redact mode."""

    def test_redact_replaces_secret(self, monkeypatch):
        monkeypatch.setenv("OPEN_BRAIN_SECRETS_MODE", "redact")
        result = check_content("key is sk-ant-api03-r8XuIsn6iGbu5VMd0Du5GdLfTWw5AR0CuI8qT2N9H864w ok")
        assert "sk-ant-api03" not in result
        assert "[REDACTED:anthropic_api_key]" in result
        assert "ok" in result  # surrounding text preserved

    def test_redact_function_directly(self):
        result = redact_content("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1234 was used")
        assert "ghp_" not in result
        assert "[REDACTED:github_token]" in result
        assert "was used" in result

    def test_redact_multiple_secrets(self):
        text = "AKIAIOSFODNN7EXAMPLE and ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1234"
        result = redact_content(text)
        assert "AKIA" not in result
        assert "ghp_" not in result


# ─── Match Preview Safety ────────────────────────────────────────────────────

class TestMatchPreviewSafety:
    """Verify that matched_text in SecretMatch is truncated, not the full secret."""

    def test_preview_is_truncated(self):
        matches = scan_content("sk-ant-api03-r8XuIsn6iGbu5VMd0Du5GdLfTWw5AR0CuI8qT2N9H864w")
        assert len(matches) >= 1
        for m in matches:
            # Should be much shorter than the actual secret
            assert len(m.matched_text) < 30, f"Preview too long: {m.matched_text}"
            assert "..." in m.matched_text
