"""Application error types."""

from __future__ import annotations


class RepositoryError(Exception):
    """Base class for persistence repository failures."""


class RepositoryNotFoundError(RepositoryError):
    """Raised when a requested persisted record does not exist."""


class RepositoryConflictError(RepositoryError):
    """Raised when a write conflicts with an existing persisted record."""


class ArtifactStoreError(Exception):
    """Base class for artifact content store failures."""


class ArtifactStoreConflictError(ArtifactStoreError):
    """Raised when artifact content would overwrite existing content."""


class ArtifactStoreInvalidStorageError(ArtifactStoreError):
    """Raised when persisted artifact storage metadata is invalid."""


class ArtifactStoreUnsupportedProviderError(ArtifactStoreError):
    """Raised when an artifact uses a storage provider unsupported by the store."""


class ArtifactStoreContentError(ArtifactStoreError):
    """Raised when artifact content cannot be read or decoded."""
