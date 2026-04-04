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


def check_ffmpeg_available() -> bool:
    # Prefer bundled or local ffmpeg/ffprobe
    base_dir = get_base_dir()
    
    # Check for .exe on Windows
    ffmpeg_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    ffprobe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"

    ffmpeg_local = os.path.join(base_dir, ffmpeg_name)
    ffprobe_local = os.path.join(base_dir, ffprobe_name)

# 1. Check local
    if os.path.exists(ffmpeg_local) and os.path.exists(ffprobe_local):
        # Verify they actually work
        try:
            # Suppress Windows Error Reporting dialogs (SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX)
            import ctypes
            old_mode = ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                subprocess.run([ffmpeg_local, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, startupinfo=startupinfo)
                subprocess.run([ffprobe_local, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, startupinfo=startupinfo)
                return True
            finally:
                ctypes.windll.kernel32.SetErrorMode(old_mode)
        except Exception:
            pass # Local exists but broken?

    # 2. Check PATH
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        try:
            import ctypes
            old_mode = ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, startupinfo=startupinfo)
                subprocess.run(["ffprobe", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, startupinfo=startupinfo)
                return True
            finally:
                ctypes.windll.kernel32.SetErrorMode(old_mode)
        except Exception:
            pass
            
    return False


def ensure_ffmpeg_or_raise():
    if not check_ffmpeg_available():
        raise RuntimeError("ffmpeg/ffprobe not found. Please install ffmpeg.")


def install_ffmpeg(progress_callback=None, cancel_event=None):
    """
    Downloads and installs FFmpeg to the application directory.
    progress_callback: function(int) -> None, accepts percentage 0-100
    cancel_event: threading.Event to check for cancellation
    """
    # Revert to GitHub BtbN build (faster download, usually static)
    url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    
    base_dir = get_base_dir()
    zip_path = os.path.join(base_dir, "ffmpeg.zip")

    try:
        # Download
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total_length = r.headers.get('content-length')
            
            if total_length is None:  # no content length header
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
                                # First 80% is downloading
                                done = int(50 * dl / total_length)
                                progress_callback(done)
        
        if progress_callback:
            progress_callback(60)

        # Extract
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # The zip structure is usually ffmpeg-master-.../bin/ffmpeg.exe
            # We need to find where the executables are
            file_list = zip_ref.namelist()
            ffmpeg_src = next((f for f in file_list if f.endswith("ffmpeg.exe")), None)
            ffprobe_src = next((f for f in file_list if f.endswith("ffprobe.exe")), None)
            
            if not ffmpeg_src or not ffprobe_src:
                raise RuntimeError("Could not find ffmpeg.exe or ffprobe.exe in the downloaded archive")

            # Extract specific files to base_dir
            # zipfile extraction usually preserves path, so we might need to read and write
            
            for src, name in [(ffmpeg_src, "ffmpeg.exe"), (ffprobe_src, "ffprobe.exe")]:
                with zip_ref.open(src) as zf, open(os.path.join(base_dir, name), 'wb') as df:
                    shutil.copyfileobj(zf, df)

        if progress_callback:
            progress_callback(90)

    except Exception as e:
        raise RuntimeError(f"Failed to download/install FFmpeg: {e}")
    finally:
        # Cleanup zip
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        if progress_callback:
            progress_callback(100)
