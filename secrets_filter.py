"""
Secrets filter for Open Brain.

Scans text for credentials, API keys, tokens, and other secrets before
they reach the embedding model or database. Two modes:

  - reject: raise SecretDetectedError (blocks storage entirely)
  - redact: replace matched secrets with [REDACTED] (stores sanitized text)

Default mode is reject. Set OPEN_BRAIN_SECRETS_MODE=redact in .env to change.
"""

import os
import re
from dataclasses import dataclass


class SecretDetectedError(Exception):
    """Raised when content contains detected secrets."""
    pass


@dataclass
class SecretMatch:
    pattern_name: str
    matched_text: str  # first/last few chars only, not the full secret
    line_number: int | None = None


# --- Patterns ---
# Each tuple: (name, compiled regex, description)
# Patterns are ordered roughly by specificity to reduce false positives.

_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # Anthropic API keys
    ("anthropic_api_key",
     re.compile(r"\bsk-ant-api\d{2}-[A-Za-z0-9_-]{20,}"),
     "Anthropic API key"),

    # OpenAI API keys
    ("openai_api_key",
     re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
     "OpenAI API key"),

    # AWS access keys
    ("aws_access_key",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "AWS access key ID"),

    # AWS secret keys (40 chars, base64-ish)
    ("aws_secret_key",
     re.compile(r"(?:aws_secret_access_key|secret_key)\s*[=:]\s*[A-Za-z0-9/+=]{40}",
                re.IGNORECASE),
     "AWS secret access key"),

    # Google Cloud / Firebase service account keys
    ("gcp_service_account",
     re.compile(r'"private_key"\s*:\s*"-----BEGIN'),
     "GCP service account private key"),

    # Generic private keys (PEM format)
    ("private_key_pem",
     re.compile(r"-----BEGIN\s+(RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE KEY-----"),
     "Private key (PEM)"),

    # GitHub tokens
    ("github_token",
     re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
     "GitHub token"),

    # Slack tokens
    ("slack_token",
     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
     "Slack token"),

    # Generic Bearer/Authorization tokens
    ("bearer_token",
     re.compile(r"(?:Bearer|Authorization)[:\s]+[A-Za-z0-9_.~+/=-]{20,}",
                re.IGNORECASE),
     "Bearer/Authorization token"),

    # Database connection strings with embedded passwords
    ("database_url_with_password",
     re.compile(r"(?:postgresql|mysql|mongodb|redis)://\w+:[^@\s]{8,}@",
                re.IGNORECASE),
     "Database URL with embedded password"),

    # Generic secret assignment patterns (KEY=value in env-file style)
    ("env_secret_assignment",
     re.compile(
         r"^[ \t]*(?:export\s+)?"
         r"(?:ANTHROPIC_API_KEY|OPENAI_API_KEY|SECRET_KEY|API_KEY|API_SECRET|"
         r"AWS_SECRET_ACCESS_KEY|DATABASE_PASSWORD|DB_PASSWORD|AUTH_TOKEN|"
         r"PRIVATE_KEY|CLIENT_SECRET|ENCRYPTION_KEY|JWT_SECRET|SESSION_SECRET|"
         r"STRIPE_SECRET_KEY|SENDGRID_API_KEY|TWILIO_AUTH_TOKEN|"
         r"GITHUB_TOKEN|SLACK_TOKEN|DISCORD_TOKEN|TELEGRAM_TOKEN)"
         r"\s*=\s*\S+",
         re.MULTILINE | re.IGNORECASE,
     ),
     "Environment variable secret assignment"),

    # Hex-encoded secrets (32+ hex chars after a key-like label)
    ("hex_secret",
     re.compile(
         r"(?:key|token|secret|password|credential)\s*[=:]\s*[0-9a-f]{32,}\b",
         re.IGNORECASE,
     ),
     "Hex-encoded secret value"),
]


def scan_content(text: str) -> list[SecretMatch]:
    """Scan text for secret patterns. Returns list of matches (empty = clean)."""
    matches = []
    for name, pattern, description in _PATTERNS:
        for m in pattern.finditer(text):
            matched = m.group()
            # Show only a safe preview: first 6 and last 4 chars
            if len(matched) > 16:
                preview = matched[:6] + "..." + matched[-4:]
            else:
                preview = matched[:4] + "..."
            # Find line number
            line_num = text[:m.start()].count("\n") + 1
            matches.append(SecretMatch(
                pattern_name=name,
                matched_text=preview,
                line_number=line_num,
            ))
    return matches


def redact_content(text: str) -> str:
    """Replace all detected secrets with [REDACTED]."""
    result = text
    for name, pattern, description in _PATTERNS:
        result = pattern.sub(f"[REDACTED:{name}]", result)
    return result


def check_content(text: str) -> str:
    """Check content for secrets. Returns cleaned text or raises SecretDetectedError.

    Behavior depends on OPEN_BRAIN_SECRETS_MODE env var:
      - 'reject' (default): raises SecretDetectedError
      - 'redact': returns text with secrets replaced by [REDACTED:pattern_name]
    """
    matches = scan_content(text)
    if not matches:
        return text

    mode = os.getenv("OPEN_BRAIN_SECRETS_MODE", "reject").lower()

    if mode == "redact":
        return redact_content(text)

    # Reject mode: build a clear error message
    details = []
    for m in matches:
        details.append(f"  - {m.pattern_name}: {m.matched_text} (line {m.line_number})")
    raise SecretDetectedError(
        f"Content contains {len(matches)} detected secret(s). "
        f"Storage blocked to protect credentials.\n"
        + "\n".join(details)
        + "\n\nTo redact instead of reject, set OPEN_BRAIN_SECRETS_MODE=redact"
    )
