"""rnotegen: create Rnote (.rnote) documents from an LLM-friendly script, Markdown or Mermaid."""

__version__ = "0.1.0"

from .api import BuildResult, build, detect_kind  # noqa: E402

__all__ = ["build", "detect_kind", "BuildResult", "__version__"]
