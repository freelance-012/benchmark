"""Dataset-management exceptions."""


class DatasetError(Exception):
    """Base exception for user-facing dataset errors."""


class ConfigError(DatasetError):
    """The user configuration is missing or invalid."""


class ParseError(DatasetError):
    """A dataset input does not satisfy its fixed format."""


class StorageError(DatasetError):
    """A generated dataset instance cannot be read or written."""
