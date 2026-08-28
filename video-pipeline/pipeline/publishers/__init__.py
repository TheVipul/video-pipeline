"""Re-publish destinations for transformed videos.

The Publisher interface is intentionally simple - one method, `publish()`.
Implementations:
    - LocalPublisher  : writes to a local directory (always works, no auth)
    - GoogleDrivePublisher : uploads to Google Drive (the default target)
    - S3Publisher     : uploads to any S3-compatible store (MinIO, AWS, R2)
    - YouTubePublisher: stubbed - documents the OAuth flow for production
"""
import inspect

from .base import Publisher, PublishResult
from .gdrive import GoogleDrivePublisher
from .local import LocalPublisher
from .s3 import S3Publisher
from .youtube import YouTubePublisher

__all__ = [
    "Publisher", "PublishResult",
    "LocalPublisher", "GoogleDrivePublisher", "S3Publisher", "YouTubePublisher",
]


_REGISTRY: dict[str, type[Publisher]] = {
    "local": LocalPublisher,
    "gdrive": GoogleDrivePublisher,
    "s3": S3Publisher,
    "youtube": YouTubePublisher,
}


def get_publisher(name: str, **kwargs) -> Publisher:
    """Build a publisher by name.

    Callers pass a superset of options (output_dir, bucket, credentials...)
    because they do not know which backend is selected. Each publisher only
    accepts the options it actually uses, so kwargs are filtered against the
    target constructor rather than splatted blindly - passing `output_dir` to
    the YouTube publisher used to raise TypeError and take the whole run down
    before a single video was processed.
    """
    key = name.lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown publisher: {name!r}. Choose one of: {', '.join(_REGISTRY)}"
        )

    params = inspect.signature(cls.__init__).parameters
    accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    if accepts_var_kw:
        accepted = kwargs
    else:
        accepted = {k: v for k, v in kwargs.items() if k in params}
    return cls(**accepted)
