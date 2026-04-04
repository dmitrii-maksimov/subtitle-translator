import os
import sys
import shutil
import zipfile
import requests
import subprocess


def get_base_dir():
    """Returns the directory where the executable (or script) is located."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# Extra directories to search on macOS when the app is launched from Finder/Dock
# (inherits a minimal PATH that doesn't include Homebrew).
_MACOS_EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def find_tool(name: str) -> str:
    """
    Return the absolute path to *name* (e.g. "ffmpeg").

    Search order:
      1. A binary placed next to the executable (Windows bundled build).
      2. shutil.which() with PATH augmented by common macOS Homebrew dirs.

    Raises RuntimeError if not found.
    """
    exe_name = name + (".exe" if os.name == "nt" else "")

    # 1. Local / bundled
    local = os.path.join(get_base_dir(), exe_name)
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local

    # 2. PATH (augmented on macOS)
    search_path = os.environ.get("PATH", "")
    if sys.platform == "darwin":
        extra = ":".join(p for p in _MACOS_EXTRA_PATHS if p not in search_path)
        if extra:
            search_path = search_path + ":" + extra if search_path else extra

    found = shutil.which(exe_name, path=search_path)
    if found:
        return found

    raise RuntimeError(
        f"{name} not found.\n"
        + ("Install via Homebrew:  brew install ffmpeg" if sys.platform == "darwin"
           else "Install ffmpeg and make sure it is in PATH.")
    )


def make_startupinfo():
    """Returns subprocess.STARTUPINFO with hidden window on Windows, None elsewhere."""
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return si
    return None


def _run_silent(cmd):
    """Run a command suppressing its window on Windows."""
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": True}
    si = make_startupinfo()
    if si is not None:
        kwargs["startupinfo"] = si
    subprocess.run(cmd, **kwargs)


def _suppress_win_errors():
    """Context manager: suppresses Windows Error Reporting dialogs. No-op on other platforms."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        if os.name == "nt":
            import ctypes
            old_mode = ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
            try:
                yield
            finally:
                ctypes.windll.kernel32.SetErrorMode(old_mode)
        else:
            yield

    return _ctx()


def check_ffmpeg_available() -> bool:
    try:
        ffmpeg = find_tool("ffmpeg")
        ffprobe = find_tool("ffprobe")
    except RuntimeError:
        return False
    try:
        with _suppress_win_errors():
            _run_silent([ffmpeg, "-version"])
            _run_silent([ffprobe, "-version"])
        return True
    except Exception:
        return False


def ensure_ffmpeg_or_raise():
    if not check_ffmpeg_available():
        raise RuntimeError("ffmpeg/ffprobe not found. Please install ffmpeg.")


def install_ffmpeg(progress_callback=None, cancel_event=None):
    """
    Downloads and installs FFmpeg to the application directory.
    On Windows: downloads a prebuilt binary from GitHub.
    On other platforms: raises an error with installation instructions.

    progress_callback: function(int) -> None, accepts percentage 0-100
    cancel_event: threading.Event to check for cancellation
    """
    if os.name != "nt":
        platform = sys.platform
        if platform == "darwin":
            raise RuntimeError(
                "Auto-download is not supported on macOS.\n"
                "Install ffmpeg via Homebrew:  brew install ffmpeg"
            )
        raise RuntimeError(
            "Auto-download is not supported on this platform.\n"
            "Install ffmpeg using your package manager, e.g.:  sudo apt install ffmpeg"
        )

    url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

    base_dir = get_base_dir()
    zip_path = os.path.join(base_dir, "ffmpeg.zip")

    try:
        # Download
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total_length = r.headers.get('content-length')

            if total_length is None:
                with open(zip_path, 'wb') as f:
                    f.write(r.content)
                if progress_callback:
                    progress_callback(50)
            else:
                dl = 0
                total_length = int(total_length)
                with open(zip_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if cancel_event and cancel_event.is_set():
                            raise InterruptedError("Download cancelled")
                        if chunk:
                            dl += len(chunk)
                            f.write(chunk)
                            if progress_callback:
                                progress_callback(int(50 * dl / total_length))

        if progress_callback:
            progress_callback(60)

        # Extract
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()
            ffmpeg_src = next((f for f in file_list if f.endswith("ffmpeg.exe")), None)
            ffprobe_src = next((f for f in file_list if f.endswith("ffprobe.exe")), None)

            if not ffmpeg_src or not ffprobe_src:
                raise RuntimeError("Could not find ffmpeg.exe or ffprobe.exe in the downloaded archive")

            for src, name in [(ffmpeg_src, "ffmpeg.exe"), (ffprobe_src, "ffprobe.exe")]:
                with zip_ref.open(src) as zf, open(os.path.join(base_dir, name), 'wb') as df:
                    shutil.copyfileobj(zf, df)

        if progress_callback:
            progress_callback(90)

    except Exception as e:
        raise RuntimeError(f"Failed to download/install FFmpeg: {e}")
    finally:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        if progress_callback:
            progress_callback(100)
