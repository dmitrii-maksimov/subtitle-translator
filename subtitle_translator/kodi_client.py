"""Kodi JSON-RPC client + LAN discovery + path mapping.

Pure-python, only depends on `requests` (already in requirements.txt).
"""

import base64
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests


SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900


class KodiClient:
    def __init__(
        self,
        host: str,
        port: int = 8080,
        user: str = "kodi",
        password: str = "",
        timeout: float = 5.0,
    ):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.timeout = timeout

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/jsonrpc"

    def _auth_header(self) -> Dict[str, str]:
        """Build Basic auth header in UTF-8 (Kodi accepts UTF-8 creds).

        ``requests.auth.HTTPBasicAuth`` encodes via latin-1 and breaks on
        non-ASCII passwords. Kodi's web server (and modern browsers) speak
        UTF-8, so we hand-roll the header to match.
        """
        if not (self.user or self.password):
            return {}
        raw = f"{self.user}:{self.password}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}

    def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        try:
            resp = requests.post(
                self.url,
                json=body,
                headers=self._auth_header(),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise RuntimeError(f"Kodi unreachable: {e}")
        if resp.status_code == 401:
            raise RuntimeError("Kodi: 401 Unauthorized — check user/password.")
        if resp.status_code != 200:
            raise RuntimeError(f"Kodi HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("Kodi returned non-JSON response.")
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"Kodi RPC error {err.get('code')}: {err.get('message')}"
            )
        return data.get("result", {})

    # ---- public API ----

    def ping(self) -> bool:
        """Returns True if Kodi answers JSONRPC.Ping with 'pong'."""
        ok, _ = self.ping_with_reason()
        return ok

    def ping_with_reason(self):
        """Same as ping() but also returns a human-readable reason on failure."""
        try:
            res = self._rpc("JSONRPC.Ping")
            ok = res == "pong" or res == {"result": "pong"} or bool(res)
            return (True, "") if ok else (False, f"Unexpected reply: {res!r}")
        except RuntimeError as e:
            return False, str(e)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def get_version(self) -> str:
        """Return Kodi application version string, or '' if unavailable."""
        try:
            res = self._rpc(
                "Application.GetProperties",
                {"properties": ["version", "name"]},
            )
            v = res.get("version") or {}
            major = v.get("major")
            minor = v.get("minor")
            if major is not None:
                return f"{major}.{minor}" if minor is not None else f"{major}"
            return res.get("name", "")
        except RuntimeError:
            return ""

    def get_sources(self, media: str = "video") -> List[Dict]:
        """List user-defined sources of given media type."""
        res = self._rpc("Files.GetSources", {"media": media})
        return list(res.get("sources", []))

    def get_directory(self, path: str, media: str = "video") -> List[Dict]:
        """List children of a directory by Kodi path."""
        res = self._rpc(
            "Files.GetDirectory",
            {
                "directory": path,
                "media": media,
                "properties": ["file", "title"],
            },
        )
        return list(res.get("files", []))

    def get_active_video_player_id(self) -> Optional[int]:
        try:
            res = self._rpc("Player.GetActivePlayers")
        except RuntimeError:
            return None
        # Kodi returns a list directly as result.
        items = res if isinstance(res, list) else res.get("players", [])
        for p in items:
            if p.get("type") == "video":
                return int(p.get("playerid"))
        return None

    def play_file(self, kodi_path: str) -> None:
        self._rpc("Player.Open", {"item": {"file": kodi_path}})

    def play_pause(self) -> Optional[bool]:
        """Toggle play/pause for the active video player.

        Returns the resulting `speed` (0 = paused, non-zero = playing) or
        ``None`` if no active player.
        """
        pid = self.get_active_video_player_id()
        if pid is None:
            return None
        try:
            res = self._rpc("Player.PlayPause", {"playerid": pid})
        except RuntimeError:
            return None
        if isinstance(res, dict) and "speed" in res:
            return bool(res["speed"])
        return None

    def get_player_progress(self):
        """Return ``(progress_dict | None, error_str | None)``.

        - ``(dict, None)`` — active video player, progress retrieved.
        - ``(None, None)`` — Kodi reachable but no active video player.
        - ``(None, str)`` — Kodi RPC/connection error; ``str`` is the reason.
        """
        try:
            res = self._rpc("Player.GetActivePlayers")
        except RuntimeError as e:
            return None, str(e)
        items = res if isinstance(res, list) else res.get("players", [])
        pid = None
        for p in items:
            if p.get("type") == "video":
                pid = int(p.get("playerid"))
                break
        if pid is None:
            return None, None
        try:
            data = self._rpc(
                "Player.GetProperties",
                {
                    "playerid": pid,
                    "properties": [
                        "time",
                        "totaltime",
                        "percentage",
                        "speed",
                        "subtitles",
                        "currentsubtitle",
                        "subtitleenabled",
                    ],
                },
            )
        except RuntimeError as e:
            return None, str(e)
        # Best-effort: also fetch the currently playing item so the UI can
        # show which file Kodi is on. Failure here is non-fatal.
        try:
            item = self._rpc(
                "Player.GetItem",
                {"playerid": pid, "properties": ["file", "title"]},
            )
            data["_item"] = item.get("item", {})
        except RuntimeError:
            data["_item"] = {}
        return data, None

    def show_notification(
        self,
        title: str,
        message: str,
        displaytime_ms: int = 4000,
        image: str = "info",
    ) -> bool:
        """Pop a Kodi GUI notification. Best-effort, never raises.

        ``image`` can be ``"info"``, ``"warning"``, ``"error"``, or a path to
        a custom icon. Kodi paints the icon as a small badge to the left of
        the message.
        """
        try:
            self._rpc(
                "GUI.ShowNotification",
                {
                    "title": title,
                    "message": message,
                    "displaytime": int(displaytime_ms),
                    "image": image,
                },
            )
            return True
        except Exception:
            return False

    def enable_subtitle_by_lang(self, target_lang: str, log_cb=None) -> bool:
        """Switch the active video player to an already-present subtitle whose
        ``language`` field matches ``target_lang``. Used for embedded mkv
        tracks where Kodi reports a real language tag.

        Returns True if such a subtitle was found and selected, False otherwise.
        """

        def _log(msg):
            if log_cb is not None:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        if not target_lang:
            return False
        pid = self.get_active_video_player_id()
        if pid is None:
            return False
        try:
            props = self._rpc(
                "Player.GetProperties",
                {"playerid": pid, "properties": ["subtitles"]},
            )
            subs = props.get("subtitles") or []
        except RuntimeError as e:
            _log(f"Kodi: GetProperties subtitles failed: {e}")
            return False
        target_l = target_lang.lower().strip()
        chosen_index: Optional[int] = None
        for i, s in enumerate(subs):
            lang = (s.get("language") or "").lower().strip()
            if not lang:
                continue
            if lang.startswith(target_l) or target_l.startswith(lang):
                chosen_index = i
                _log(
                    f"Kodi: existing subtitle index {i} via language match "
                    f"(lang={s.get('language')!r})"
                )
                break
        if chosen_index is None:
            _log(f"Kodi: no existing subtitle matches target lang '{target_l}'")
            return False
        try:
            self._rpc(
                "Player.SetSubtitle",
                {"playerid": pid, "subtitle": chosen_index, "enable": True},
            )
        except RuntimeError as e:
            _log(f"Kodi: index switch failed: {e}")
            return False
        return True

    def set_subtitle(
        self,
        srt_path: str,
        target_lang: Optional[str] = None,
        enable: bool = True,
        log_cb=None,
    ) -> None:
        """Attach (or re-attach) an external subtitle file and switch to it.

        Minimal flow:
          1. ``Player.AddSubtitle`` with the path so Kodi reads the file.
          2. Read the updated subtitles list and pick the entry matching the
             filename, falling back to ``target_lang`` (only when Kodi
             reports a non-empty language), then the last/newest entry.
          3. ``Player.SetSubtitle <index>, enable=True`` so Kodi switches
             to OUR file and shows it.
        """

        def _log(msg):
            if log_cb is not None:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        pid = self.get_active_video_player_id()
        if pid is None:
            raise RuntimeError("No active video player on Kodi.")

        # 1. Attach.
        _log(f"Kodi: AddSubtitle path={srt_path}")
        self._rpc(
            "Player.AddSubtitle",
            {"playerid": pid, "subtitle": srt_path},
        )

        if not enable:
            return

        # 2. Read updated subtitles list and pick our entry.
        chosen_index: Optional[int] = None
        try:
            props = self._rpc(
                "Player.GetProperties",
                {"playerid": pid, "properties": ["subtitles"]},
            )
            subs = props.get("subtitles") or []
        except RuntimeError as e:
            _log(f"Kodi: GetProperties subtitles failed: {e}")
            subs = []
        _log(f"Kodi: subtitles list size={len(subs)}")

        if subs:
            target_l = (target_lang or "").lower().strip()
            base_name = srt_path.rstrip("/").rsplit("/", 1)[-1].lower()
            base_stem = base_name.rsplit(".", 1)[0] if "." in base_name else base_name
            match_reason = None
            # a. Filename match.
            if base_name:
                for i, s in enumerate(subs):
                    name = (s.get("name") or "").lower()
                    if not name:
                        continue
                    if base_name in name or name in base_name:
                        chosen_index = i
                        match_reason = f"filename match (name={s.get('name')!r})"
                        break
                    if base_stem and (base_stem in name or name in base_stem):
                        chosen_index = i
                        match_reason = f"filename stem match (name={s.get('name')!r})"
                        break
            # b. Language match — only if Kodi reports a non-empty language.
            if chosen_index is None and target_l:
                for i, s in enumerate(subs):
                    lang = (s.get("language") or "").lower().strip()
                    if not lang:
                        continue
                    if lang.startswith(target_l) or target_l.startswith(lang):
                        chosen_index = i
                        match_reason = f"language match (lang={s.get('language')!r})"
                        break
            # c. Last resort — most recently added.
            if chosen_index is None:
                chosen_index = len(subs) - 1
                match_reason = "last-added fallback"
            _log(f"Kodi: picked subtitle index {chosen_index} via {match_reason}")

        # 3. Switch to it.
        if chosen_index is not None:
            _log(f"Kodi: SetSubtitle index={chosen_index} enable=True")
            try:
                self._rpc(
                    "Player.SetSubtitle",
                    {
                        "playerid": pid,
                        "subtitle": chosen_index,
                        "enable": True,
                    },
                )
            except RuntimeError as e:
                _log(f"Kodi: index switch failed: {e}")

        # 4. Force overlay re-render — only when playback is paused. While
        # the player is running, the renderer ticks every frame and naturally
        # picks up the new file content; on pause the overlay is frozen on
        # whatever was last drawn (often the sentinel) and only a seek will
        # make Kodi re-read the file.
        try:
            tprops = self._rpc(
                "Player.GetProperties",
                {"playerid": pid, "properties": ["time", "speed"]},
            )
            speed = tprops.get("speed", 1)
            if speed != 0:
                _log(f"Kodi: playing (speed={speed}) — skip seek refresh.")
                return
            t = tprops.get("time") or {}
            cur_ms = (
                int(t.get("hours", 0)) * 3600000
                + int(t.get("minutes", 0)) * 60000
                + int(t.get("seconds", 0)) * 1000
                + int(t.get("milliseconds", 0))
            )
            target_ms = max(0, cur_ms - 500)
            seek_to = {
                "hours": target_ms // 3600000,
                "minutes": (target_ms // 60000) % 60,
                "seconds": (target_ms // 1000) % 60,
                "milliseconds": target_ms % 1000,
            }
            _log(
                f"Kodi: paused (speed=0) — Seek -0.5s triggered "
                f"(from {cur_ms}ms to {target_ms}ms)"
            )
            self._rpc(
                "Player.Seek",
                {"playerid": pid, "value": {"time": seek_to}},
            )
            _log("Kodi: Seek -0.5s done.")
        except RuntimeError as e:
            _log(f"Kodi: seek refresh failed: {e}")


# ---- discovery ----


def _local_subnet() -> Optional[str]:
    """Return the local /24 prefix as 'A.B.C.', or None if unknown."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3]) + "."


def _ssdp_search(timeout: float = 2.5) -> List[str]:
    """Send SSDP M-SEARCH and collect LOCATION URLs from responses."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {int(timeout)}\r\n"
        "ST: ssdp:all\r\n\r\n"
    ).encode("ascii")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        sock.sendto(msg, (SSDP_ADDR, SSDP_PORT))
    except OSError:
        sock.close()
        return []

    locations: List[str] = []
    try:
        while True:
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                break
            text = data.decode("utf-8", errors="ignore")
            m = re.search(r"^LOCATION:\s*(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
            if m:
                loc = m.group(1)
                if loc not in locations:
                    locations.append(loc)
    finally:
        sock.close()
    return locations


def _is_kodi_at(ip: str, port: int, timeout: float = 0.5) -> Optional[Dict]:
    """Probe a single host:port for Kodi JSON-RPC. Returns {ip, port, name, source} or None."""
    url = f"http://{ip}:{port}/jsonrpc"
    body = {"jsonrpc": "2.0", "id": 1, "method": "JSONRPC.Ping"}
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException:
        # Try GET fallback (Kodi returns 401 quickly when auth required)
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.RequestException:
            return None
    code = resp.status_code
    body_text = resp.text or ""
    looks_like_kodi = (
        "JSONRPC" in body_text
        or "kodi" in body_text.lower()
        or "xbmc" in body_text.lower()
        or (code == 401 and "json" in resp.headers.get("WWW-Authenticate", "").lower())
        or (code == 401)
    )
    # Validate JSON-RPC envelope on 200
    if code == 200:
        try:
            j = resp.json()
            if (
                not isinstance(j, dict)
                or "jsonrpc" not in j
                and "result" not in j
                and "error" not in j
            ):
                return None
        except ValueError:
            return None
    elif code != 401:
        return None
    if not looks_like_kodi and code != 401:
        return None
    return {
        "ip": ip,
        "port": port,
        "name": f"Kodi @ {ip}",
        "source": "scan",
    }


def discover_kodi(
    port_hint: int = 8080,
    ssdp_timeout: float = 2.5,
    scan_timeout: float = 0.5,
    do_scan: bool = True,
    progress_cb=None,
) -> List[Dict]:
    """Discover Kodi on local network.

    Strategy:
      1. SSDP M-SEARCH — collect LOCATION URLs, treat unique IPs as candidates.
      2. Subnet scan on `port_hint` if SSDP found nothing (or `do_scan=True`).

    Returns list of {ip, port, name, source} dicts.
    `progress_cb(done, total)` invoked during scan.
    """
    found: List[Dict] = []
    seen_ips = set()

    # Stage 1: SSDP — gather candidate IPs from any UPnP responder.
    locations = _ssdp_search(timeout=ssdp_timeout)
    candidate_ips = []
    for loc in locations:
        try:
            host = urlparse(loc).hostname
        except ValueError:
            continue
        if host and host not in seen_ips:
            seen_ips.add(host)
            candidate_ips.append(host)

    # Probe SSDP candidates on port_hint (Kodi's jsonrpc listens on 8080 by default,
    # not on the UPnP port, so we re-probe).
    for ip in candidate_ips:
        info = _is_kodi_at(ip, port_hint, timeout=scan_timeout)
        if info:
            info["source"] = "ssdp"
            found.append(info)

    # Stage 2: full subnet scan on port_hint.
    if do_scan and not found:
        prefix = _local_subnet()
        if prefix:
            ips = [f"{prefix}{i}" for i in range(1, 255)]
            done = 0
            total = len(ips)
            with ThreadPoolExecutor(max_workers=64) as ex:
                futures = {
                    ex.submit(_is_kodi_at, ip, port_hint, scan_timeout): ip
                    for ip in ips
                }
                for f in as_completed(futures):
                    done += 1
                    if progress_cb:
                        try:
                            progress_cb(done, total)
                        except Exception:
                            pass
                    info = f.result()
                    if info and info["ip"] not in seen_ips:
                        seen_ips.add(info["ip"])
                        found.append(info)

    return found


# ---- path mapping ----


def map_kodi_to_local(
    kodi_file: str,
    kodi_parent: str,
    local_parent: str,
) -> str:
    """Translate a Kodi-visible file path back to the local filesystem path.

    Inverse of :func:`map_local_to_kodi`. Used when we know what Kodi is
    playing and need to operate on the same file from this machine.
    """
    if not local_parent:
        raise ValueError("Local parent folder is not configured.")
    if not kodi_parent:
        raise ValueError("Kodi source path is not configured.")
    parent = kodi_parent if kodi_parent.endswith("/") else kodi_parent + "/"
    if not kodi_file.startswith(parent):
        # Try without trailing slash equality (parent itself).
        if kodi_file.rstrip("/") == kodi_parent.rstrip("/"):
            rel = ""
        else:
            raise ValueError(
                f"Kodi file {kodi_file!r} is outside Kodi source {kodi_parent!r}."
            )
    else:
        rel = kodi_file[len(parent) :]
    rel_local = rel.replace("/", os.sep)
    return os.path.join(local_parent, rel_local)


def map_local_to_kodi(
    local_file: str,
    local_parent: str,
    kodi_parent: str,
) -> str:
    """Translate a local file path to a Kodi-visible path.

    Args:
        local_file:   absolute path on user's machine, e.g. '/Volumes/m/dir/x.mkv'.
        local_parent: local mountpoint corresponding to `kodi_parent`,
                      e.g. '/Volumes/m'.
        kodi_parent:  same folder as Kodi sees it, e.g. 'smb://nas/movies'.

    Returns:
        Kodi-style path with forward slashes and a trailing-slashed parent.
    """
    if not local_parent:
        raise ValueError(
            "Local parent folder is not configured. Set it on the Kodi tab."
        )
    if not kodi_parent:
        raise ValueError("Kodi source path is not configured. Set it on the Kodi tab.")

    local_file_abs = os.path.abspath(local_file)
    local_parent_abs = os.path.abspath(local_parent)

    try:
        rel = os.path.relpath(local_file_abs, local_parent_abs)
    except ValueError:
        # Different drives on Windows.
        raise ValueError(
            "File is on a different drive than the configured local parent folder."
        )

    if rel.startswith("..") or os.path.isabs(rel):
        raise ValueError("File is outside the configured local parent folder.")

    rel_url = rel.replace(os.sep, "/")
    parent = kodi_parent if kodi_parent.endswith("/") else kodi_parent + "/"
    return parent + rel_url
