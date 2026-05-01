"""Qt worker threads bridging generator-style core orchestrators into
``QThread`` signals.

``WorkerThread`` runs an arbitrary generator function and turns yielded
``int``/``str``/tuple updates into ``progress``, ``status``,
``batch_info`` or ``request_input`` Qt signals. ``_ModelFetcherThread``
is a one-shot thread that calls :meth:`TranslationService.list_models`
and emits the resulting list (plus an optional error message).
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class WorkerThread(QThread):
    progress = Signal(int)
    status = Signal(str)
    batch_info = Signal(int, str)
    request_input = Signal(str, object)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            for update in self.func(*self.args, **self.kwargs):
                if isinstance(update, int):
                    self.progress.emit(update)
                elif isinstance(update, str):
                    self.status.emit(update)
                elif (
                    isinstance(update, tuple)
                    and len(update) == 3
                    and update[0] == "batch"
                ):
                    self.batch_info.emit(update[1], update[2])
                elif (
                    isinstance(update, tuple)
                    and len(update) == 3
                    and update[0] == "input"
                ):
                    self.request_input.emit(update[1], update[2])
                elif (
                    isinstance(update, tuple)
                    and len(update) == 3
                    and update[0] == "settings_update"
                ):
                    self.request_input.emit(
                        "update_ui_settings", (update[1], update[2])
                    )
            self.finished_ok.emit("done")
        except Exception as e:
            self.failed.emit(str(e))


class _ModelFetcherThread(QThread):
    """One-shot thread: calls ``translator.list_models()`` and emits the result.

    Emits ``done(models: list, error: str)``. On success ``error`` is
    empty. On failure ``models`` is empty and ``error`` contains the
    message.
    """

    done = Signal(list, str)

    def __init__(self, translator):
        super().__init__()
        self._translator = translator

    def run(self):
        try:
            models = self._translator.list_models()
            self.done.emit(models, "")
        except Exception as e:
            self.done.emit([], str(e))
