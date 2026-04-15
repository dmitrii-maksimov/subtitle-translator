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
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    workers: int = 5
    window: int = 25
    overlap: int = 10

    target_language: str = "ru"
    last_dir: str = ""
    fulllog: bool = False
    extra_prompt: str = ""
    overwrite_original: bool = False
    main_prompt_template: str = ""
    system_role: str = ""

    default_source_lang: str = "eng"
    default_source_title: str = "Full"

    cached_tag_lang: str = ""
    cached_iso3: str = ""
    cached_source_lang_input: str = ""

    # Model picker: cached list from /v1/models (so the combo isn't empty
    # on launch) and whether the user wants to type a custom model id.
    cached_models: List[str] = field(default_factory=list)
    use_custom_model: bool = False

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
            # Ignore unknown keys so older/newer configs don't crash the app.
            valid = {k for k in AppSettings.__dataclass_fields__}
            data = {k: v for k, v in data.items() if k in valid}
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
