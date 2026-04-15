from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json
import os


@dataclass
class FileDecision:
    """Per-file user choice from TrackSelectionDialog. Not persisted."""

    file_path: str
    translate_stream_index: Optional[int] = None
    delete_stream_indexes: List[int] = field(default_factory=list)
    skipped: bool = False
    cancelled: bool = False  # True if the user aborted the whole batch


@dataclass
class AppSettings:
    # Core API settings
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    # Translation windowing/parallelism
    workers: int = 5
    window: int = 25
    overlap: int = 10

    # UI and behavior
    target_language: str = "ru"
    last_dir: str = ""
    fulllog: bool = False
    extra_prompt: str = ""
    overwrite_original: bool = False
    main_prompt_template: str = ""
    system_role: str = ""

    # Batch processing defaults
    default_source_lang: str = "eng"
    default_source_title: str = "Full"

    # Persistent cache for language normalization
    cached_tag_lang: str = ""
    cached_iso3: str = ""
    cached_source_lang_input: str = ""

    @staticmethod
    def load():
        path = os.path.join(
            os.path.expanduser("~"), ".subtitle_translator_settings.json"
        )
        if not os.path.exists(path):
            return AppSettings()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppSettings(**data)
        except Exception:
            return AppSettings()

    def save(self):
        path = os.path.join(
            os.path.expanduser("~"), ".subtitle_translator_settings.json"
        )
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        except Exception:
            # Silently ignore save errors to not disturb UI
            pass
