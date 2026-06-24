"""
Utility module for password hashing and validation.

Responsibility:
  Provides secure password handling using bcrypt, including hash generation,
  verification, and strength validation with configurable requirements.

Features:
  - Hash passwords using bcrypt with adjustable work factor (default: 12)
  - Verify plain text against stored hashes securely
  - Validate password complexity following common security standards
"""
from typing import Pattern
import re

import bcrypt
import structlog

logger = structlog.get_logger()
# ─── Constants ─────────────────────────────────────────────────────────────────────
PASSWORD_MIN_LENGTH: int = 8
SALT_ROUNDS_DEFAULT: int = 12

MIN_REQUIREMENTS_PATTERN: Pattern[str] = re.compile(
    r"(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^a-zA-Z0-9])"
)
PASSWORD_VALIDATION_MESSAGE: str = (
    f"Password must contain at least {PASSWORD_MIN_LENGTH} characters, "
    "including uppercase, lowercase, number and special character."
)

# ─── Hash Functions ──────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """
    Generate bcrypt hash for a password with default salt rounds.

    Args:
        password: Plain text password to hash. Must not be empty or None.

    Returns:
        Hashed password string prefixed with $2b$ (bcrypt identifier).

    Raises:
        ValueError: If password is empty, whitespace-only, or too long (>1024 chars).
    """
    if not password or len(password) > 1023:
        logger.warning("Invalid password length for hashing")
        raise ValueError(f"Password must be between 1 and {PASSWORD_MIN_LENGTH} characters.")

    try:
        salt = bcrypt.gensalt(rounds=SALT_ROUNDS_DEFAULT)
        hashed_bytes: bytes = bcrypt.hashpw(password.encode(), salt)
        return hashed_bytes.decode()
    except (ValueError, OverflowError) as e:
        logger.exception(f"Hashing failed with error {e}")
        raise

# ─── Verification Functions ──────────────────────────────────────────────────────────
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its stored hash using constant-time comparison.

    Args:
        plain_password: Plain text password entered by user to check.
        hashed_password: Pre-existing bcrypt hash string from database storage.

    Returns:
        True if passwords match, False otherwise.
"""
    try:
        return bool(
            bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
        )
    except (ValueError, UnicodeDecodeError) as e:
        logger.error(f"Password verification error: {e}")
        return False

# ─── Validation Functions ──────────────────────────────────────────────────────────
def validate_password_strength(password: str) -> tuple[bool, list[str]]:
    """
    Check if password meets security requirements.

    Requirements (configurable via constants):
      - Minimum length of 8 characters
      - At least one uppercase letter [A-Z]
      - At least one lowercase letter [a-z]
      - At least one digit \d\n      - At least one special character from common set

    Args:
        password: Password string to validate.

    Returns:
        Tuple of (is_valid, list_of_failure_reasons)
"""
    failures = []

    if len(password) < PASSWORD_MIN_LENGTH:
        failures.append(f"must be at least {PASSWORD_MIN_LENGTH} characters")

    password_requirements: dict[str, re.Pattern] = {
        "uppercase letter": Pattern("[A-Z]"),
        "lowercase letter": Pattern("[a-z]"),
        "digit": Pattern(r"\d"),
        "special character": Pattern(r"[^a-zA-Z0-9]"),
    }

    for name, pattern in password_requirements.items():
        if not re.search(pattern=pattern, string=password):
            failures.append(f"{name}")

    return (len(failures) == 0, failures)
