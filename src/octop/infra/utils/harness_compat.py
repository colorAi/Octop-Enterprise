"""Single gate for the private Harness contracts used by this release."""

from importlib.metadata import version

SUPPORTED_HARNESS_VERSION = "1.0.3"


def require_supported_harness() -> None:
    actual = version("octop-harness")
    if actual != SUPPORTED_HARNESS_VERSION:
        raise RuntimeError(
            f"Unsupported octop-harness {actual}; this release requires {SUPPORTED_HARNESS_VERSION}"
        )
