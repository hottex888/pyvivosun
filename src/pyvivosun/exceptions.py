"""Exception hierarchy for the pyvivosun library."""


class VivosunError(Exception):
    """Base exception for all pyvivosun errors."""


class AuthenticationError(VivosunError):
    """Raised when authentication fails (bad credentials, etc.)."""


class TokenExpiredError(AuthenticationError):
    """Raised when a token has expired and cannot be refreshed."""


class ApiError(VivosunError):
    """Raised when the API returns an error response."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"API error {status}: {message}")


class ConnectionError(VivosunError):  # noqa: A001
    """Raised when a connection to the API or MQTT broker fails."""


class DeviceNotFoundError(VivosunError):
    """Raised when a requested device is not found."""


class CommandError(VivosunError):
    """Raised when a device command fails."""


class InvalidParameterError(VivosunError):
    """Raised when an invalid parameter value is provided."""
