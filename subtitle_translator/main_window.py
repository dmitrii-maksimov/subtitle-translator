"""Legacy compatibility shim. The real implementation now lives in
``subtitle_translator.ui.main_window``. This module re-exports
``MainWindow`` so older entry points and external imports keep working.
"""

from .ui.main_window import MainWindow  # noqa: F401

# Legacy re-exports (function helpers and Qt classes) — kept so any
# in-tree caller using the old import paths continues to work.
from .core.translation_engine import translate_subs  # noqa: F401
from .core.live_loop import live_translate_mkv  # noqa: F401
from .core.kodi_follow import (  # noqa: F401
    kodi_follow_translate,
    kodi_time_to_sec as _kodi_time_to_sec,
    format_kodi_subtitle_state as _format_kodi_subtitle_state,
)
from .core.srt_io import (  # noqa: F401
    SENTINEL_TEXT,
    td_to_hms as _td_to_hms,
    td_to_hms_secs as _td_to_hms_secs,
    is_sentinel as _is_sentinel,
    strip_sentinel as _strip_sentinel,
    make_sentinel as _make_sentinel,
    write_translated_with_sentinel as _write_translated_with_sentinel,
)
from .core.track_matcher import (  # noqa: F401
    match_initial_state,
    stream_match_key as _stream_match_key,
    pick_source_subtitle_stream as _pick_source_subtitle_stream,
)
from .ffmpeg.probe import ffprobe_subs_partial  # noqa: F401
from .ffmpeg.extract import extract_srt_lenient  # noqa: F401
from .ui.workers import WorkerThread, _ModelFetcherThread  # noqa: F401
from .ui.widgets.elided_label import ElidedLabel  # noqa: F401
from .ui.widgets.model_price_delegate import (  # noqa: F401
    ModelPriceDelegate as _ModelPriceDelegate,
    MODEL_PRICE_ROLE as _MODEL_PRICE_ROLE,
)
from .ui.dialogs.kodi_discovery import KodiDiscoveryDialog  # noqa: F401
from .ui.dialogs.kodi_browse import KodiBrowseDialog  # noqa: F401
from .ui.dialogs.live_download import LiveDownloadDialog  # noqa: F401
from .ui.dialogs.kodi_follow import KodiFollowDialog  # noqa: F401
from .ui.dialogs.track_selection import TrackSelectionDialog  # noqa: F401
