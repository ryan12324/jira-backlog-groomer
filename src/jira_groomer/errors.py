class GroomerError(Exception):
    """Base exception for expected, user-facing failures."""


class ConfigurationError(GroomerError):
    """Configuration or environment variables are invalid."""


class JiraError(GroomerError):
    """A Jira request failed."""


class AIError(GroomerError):
    """An AI analysis request failed or returned unusable output."""


class PolicyError(GroomerError):
    """A proposed or requested mutation violates the configured policy."""
