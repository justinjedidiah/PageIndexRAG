"""Indexing presets: Free (no LLM), Budget (short summaries) and Full (detailed summaries and image descriptions).

The tree settings are the same in every preset; presets differ only in what they spend on the LLM.
The advanced panel and the CLI can change any single option on top of a preset.
"""

from typing import Literal, get_args

from ..config import Settings
from ..models import IndexOptions

PresetName = Literal["free", "budget", "full"]
PRESET_NAMES: tuple[PresetName, ...] = get_args(PresetName)
DEFAULT_PRESET: PresetName = "full"

PRESET_LABELS: dict[PresetName, str] = {
    "free": "Free",
    "budget": "Budget",
    "full": "Full",
}
PRESET_DESCRIPTIONS: dict[PresetName, str] = {
    "free": "Sections show their first sentences instead of summaries.",
    "budget": "Short summaries, no image descriptions.",
    "full": "Detailed summaries and image descriptions.",
}

_LLM_SETTINGS: dict[PresetName, dict] = {
    "free": {"summaries": "preview", "describe_images": False},
    "budget": {"summaries": "llm", "summary_length": "short", "describe_images": False},
    "full": {"summaries": "llm", "summary_length": "detailed", "describe_images": True},
}


def preset_options(name: PresetName = DEFAULT_PRESET, settings: Settings | None = None, **overrides) -> IndexOptions:
    """The options for a preset, with the tree thresholds taken from settings, then any overrides applied."""
    if name not in _LLM_SETTINGS:
        raise ValueError(f"Unknown preset {name!r}. Choose one of: {', '.join(PRESET_NAMES)}.")
    tree = {}
    if settings is not None:
        tree = {"max_leaf_tokens": settings.max_leaf_tokens, "min_leaf_tokens": settings.min_leaf_tokens}
    return IndexOptions(**{**tree, **_LLM_SETTINGS[name], **overrides})


def matching_preset(options: IndexOptions) -> PresetName | None:
    """The preset whose LLM settings match these options, or None when they were customised."""
    for name, values in _LLM_SETTINGS.items():
        relevant = {k: v for k, v in values.items() if k != "summary_length" or options.summaries == "llm"}
        if all(getattr(options, key) == value for key, value in relevant.items()):
            return name
    return None
