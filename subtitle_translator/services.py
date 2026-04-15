import re

import requests
import srt
from typing import List, Optional, Tuple, Union

from .models import AppSettings


class TranslationService:
    def __init__(self, settings: AppSettings):
        self.settings = settings

    def build_prompt(self, group: List[srt.Subtitle], target_lang: str) -> str:
        lines = []
        for e in group:
            idx_header = f"{e.index}:"
            content = e.content.replace("\r\n", "\n").replace("\r", "\n")
            lines.append(idx_header)
            lines.append(content)
        src_block = "\n".join(lines)
        extra = (getattr(self.settings, "extra_prompt", "") or "").strip()
        extra_clause = (
            "\n- IMPORTANT: "
            + extra
            + " (this instruction is mandatory even if exact translation suffers)"
            if extra
            else ""
        )
        template = getattr(self.settings, "main_prompt_template", "") or (
            "{header}\n"
            "- Keep numbering (e.g., 12:, 43:, ...)\n"
            "- Do not change the number of lines or merge/split cues\n"
            "- Preserve line breaks within each numbered block exactly as in the input\n"
            "- Return ONLY the translated text blocks with the same numbering, no timestamps, no extra comments{extra}\n\n"
            "- New subtitles don't have to contain any characters in original language\n"
            "Example:\n"
            "1:\nHello!\n42:\nHow are you?\n\n"
            "Text:\n{src_block}"
        )
        header = f"Translate into {target_lang}. Rules:"
        return template.format(header=header, extra=extra_clause, src_block=src_block)

    def list_models(self) -> List[str]:
        """Fetch available models from `{api_base}/models`.

        Returns a sorted list of model ids. Raises RuntimeError on any
        network or API error. Caller decides how to handle it (typically
        show a QMessageBox)."""
        api_key = self.settings.api_key
        api_base = self.settings.api_base.rstrip("/")
        if not api_key:
            raise RuntimeError("API key is not set in settings")
        url = f"{api_base}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        items = data.get("data") or []
        ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return sorted(ids)

    def chat_translate(self, prompt: str) -> Union[str, Tuple[str, dict]]:
        api_key = self.settings.api_key
        model = self.settings.model
        api_base = self.settings.api_base.rstrip("/")
        if not api_key:
            raise RuntimeError("API key is not set in settings")
        url = f"{api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        system_role = getattr(self.settings, "system_role", None) or (
            "You translate subtitles. Output must be ONLY the translated lines, one per input line, without indices, timestamps, or any additional labels."
        )
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_role},
                {"role": "user", "content": prompt},
            ],
            "temperature": 1,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"].strip()
        except Exception:
            raise RuntimeError("Unexpected API response format")
        if getattr(self.settings, "fulllog", False):
            redacted_headers = dict(headers)
            if "Authorization" in redacted_headers:
                redacted_headers["Authorization"] = "***"
            dbg = {
                "url": url,
                "headers": redacted_headers,
                "body": body,
                "status": resp.status_code,
                "response_json": data,
            }
            return content, dbg
        return content

    def chat_normalize_lang(self, raw_lang: str) -> Union[str, Tuple[str, dict]]:
        api_key = self.settings.api_key
        model = self.settings.model
        api_base = self.settings.api_base.rstrip("/")
        if not api_key:
            raise RuntimeError("API key is not set in settings")
        url = f"{api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        system_msg = (
            "You convert any user-provided language/style description into a short English phrase suitable "
            "for an MKV metadata language tag. Rules: respond in lowercase ascii only, max 30 characters, "
            "use only letters, numbers and spaces, no punctuation, no quotes, no code blocks. Return ONLY the phrase."
        )
        examples = (
            "Examples:\n"
            "- 'феня с матами' -> fen bad words\n"
            "- 'русский' -> russian\n"
            "- '日本語' -> japanese\n"
            "- 'русский без мата' -> russian no profanity\n"
        )
        user_msg = f"Given a 'language' field: {raw_lang!r}. {examples}\nReturn only the English phrase."
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 1,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"].strip()
        except Exception:
            raise RuntimeError("Unexpected API response format")
        if getattr(self.settings, "fulllog", False):
            redacted_headers = dict(headers)
            if "Authorization" in redacted_headers:
                redacted_headers["Authorization"] = "***"
            dbg = {
                "url": url,
                "headers": redacted_headers,
                "body": body,
                "status": resp.status_code,
                "response_json": data,
            }
            return content, dbg
        return content

    def chat_infer_iso3(self, raw_lang: str) -> Union[str, Tuple[str, dict]]:
        """Infer ISO 639-2 three-letter code from arbitrary language input via chat API."""
        api_key = self.settings.api_key
        model = self.settings.model
        api_base = self.settings.api_base.rstrip("/")
        if not api_key:
            raise RuntimeError("API key is not set in settings")
        url = f"{api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        system_msg = (
            "You are a language code normalizer. Given any user-provided language/style description, "
            "respond with the ISO 639-2 (bibliographic or terminologic) three-letter lowercase code for the primary language. "
            "If uncertain, respond with 'und'. Output MUST be only the 3-letter code or 'und', no extra text."
        )
        examples = (
            "Examples:\n"
            "- 'феня с матами' -> rus\n"
            "- 'русский' -> rus\n"
            "- 'english sdH' -> eng\n"
            "- '日本語' -> jpn\n"
            "- 'китайский мандарин' -> zho\n"
        )
        user_msg = (
            f"Language field: {raw_lang!r}. Return only ISO 639-2 code. {examples}"
        )
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 1,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"].strip().lower()
        except Exception:
            raise RuntimeError("Unexpected API response format")
        # Sanitize: only allow 3-letter ascii or 'und'
        if content != "und" and not (len(content) == 3 and content.isalpha()):
            m = re.search(r"\b([a-z]{3})\b", content)
            content = m.group(1) if m else "und"
        if getattr(self.settings, "fulllog", False):
            redacted_headers = dict(headers)
            if "Authorization" in redacted_headers:
                redacted_headers["Authorization"] = "***"
            dbg = {
                "url": url,
                "headers": redacted_headers,
                "body": body,
                "status": resp.status_code,
                "response_json": data,
            }
            return content, dbg
        return content
