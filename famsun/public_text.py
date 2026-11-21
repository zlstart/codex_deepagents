"""Filter provider messages to public assistant text before persistence or display."""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage

MASK = "[已隐藏]"
_PRIVATE_TAGS = {
    "think",
    "thinking",
    "thought",
    "thoughts",
    "analysis",
    "reasoning",
    "reasoning_content",
    "redacted_thinking",
}
_TAG = re.compile(r"<[^<>]*(?:>|$)")
_KEY = r"[\w-]*(?:api[ _-]?key|password|passwd|pwd|passphrase|secret(?:[_-]?key)?|credentials?|token|authorization|cookie|private[_-]?key|access[_-]?key(?:[_-]?id)?|密码|密钥|令牌)"
_ASSIGNMENT = re.compile(
    rf"(?i)(?<![\w-])([\"']?{_KEY}[\"']?\s*(?:[=:＝：]\s*|\bis\s+|是\s*))"
    r'(?:"(?:\\.|[^"\\])*(?:"|$)|\'(?:\\.|[^\'\\])*(?:\'|$)|[^\s,;\]\}\)]+)'
)
_BEARER = re.compile(r"(?i)(\b(?:Bearer|Basic)\s+)[^\s,;\"'\]}]+")
_TOKEN = re.compile(
    r"(?i)\b(?:sk-(?:proj-|ant-)?[\w-]+|(?:gh[pousr]_|github_pat_)[\w]+|AKIA[A-Z0-9]{16}|eyJ[\w-]+\.[\w-]+\.[\w-]+)"
)
_USERINFO = re.compile(r"(?i)(\b[a-z][\w+.-]*://)[^\s/@]+:[^\s/@]+@")
_PEM = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----).*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_COOKIE_HEADER = re.compile(r"(?im)(\b(?:set-cookie|cookie)\s*:\s*)[^\r\n\"']+")
_TRAILING_URL = re.compile(r"(?i)\b[a-z][\w+.-]*://[^\s\"'<>]*\Z")
_TRAILING_ASCII = re.compile(r"[!-~]+\Z")


def redact_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, MASK)
    text = _COOKIE_HEADER.sub(lambda m: m[1] + MASK, text)
    text = _PEM.sub(lambda m: m[1] + MASK + m[2], text)
    text = _BEARER.sub(lambda m: m[1] + MASK, text)
    text = _ASSIGNMENT.sub(lambda m: m[1] + MASK, text)
    text = _TOKEN.sub(MASK, text)
    return _USERINFO.sub(lambda m: m[1] + MASK + "@", text)


class VisibleMarkup:
    """Handle split inline reasoning tags used by some compatible providers."""

    def __init__(self):
        self.tail = ""
        self.hidden: list[str] = []
        self.discard = False

    def feed(self, value: str) -> str:
        if self.discard:
            return ""
        text, self.tail = self.tail + value, ""
        visible = []
        start = 0
        for match in _TAG.finditer(text):
            if not self.hidden:
                visible.append(text[start : match.start()])
            token = match[0]
            completed = token.endswith(">")
            inner = token[1:-1] if completed else token[1:]
            inner = inner.strip().lower()
            closing = inner.startswith("/")
            pieces = inner.lstrip("/").strip().split(None, 1)
            name = pieces[0] if pieces else ""
            if not completed and any(tag.startswith(name) for tag in _PRIVATE_TAGS):
                # Only the recognized tag name is needed to complete a long
                # opening tag with attributes. Never retain a hidden body.
                self.tail = (
                    token
                    if len(token) <= 128
                    else "<" + ("/" if closing else "") + name + " "
                )
            elif completed and name in _PRIVATE_TAGS:
                if closing and self.hidden and self.hidden[-1] == name:
                    self.hidden.pop()
                elif not closing:
                    if len(self.hidden) >= 32:
                        self.discard = True
                        return "".join(visible)
                    self.hidden.append(name)
            elif not self.hidden:
                visible.append(token)
            start = match.end()
        if not self.hidden:
            visible.append(text[start:])
        return "".join(visible)


def message_text(message) -> str:
    """Read only public content, never additional_kwargs, reasoning blocks or tool calls."""
    if not isinstance(message, AIMessage):
        return ""
    for metadata in (message.additional_kwargs, message.response_metadata):
        if (
            metadata.get("channel") in ("analysis", "reasoning", "thinking")
            or metadata.get("thought") is True
        ):
            return ""
    content = message.content
    if isinstance(content, str):
        return content
    pieces = []
    for block in content:
        if isinstance(block, str):
            pieces.append(block)
        elif isinstance(block, dict) and block.get("type") in ("text", "output_text"):
            if (
                block.get("thought")
                or block.get("thinking")
                or block.get("channel") in ("analysis", "reasoning")
            ):
                continue
            if isinstance(block.get("text"), str):
                pieces.append(block["text"])
    return "".join(pieces)


def public_text(message, secrets: tuple[str, ...] = ()) -> str:
    return redact_text(VisibleMarkup().feed(message_text(message)), secrets)


def stable_prefix(text: str, secrets: tuple[str, ...]) -> str:
    """Do not publish an unfinished token that could become a credential."""
    end = len(text)
    word = _TRAILING_ASCII.search(text)
    if word:
        end = word.start()
    url = _TRAILING_URL.search(text)
    if url:
        end = min(end, url.start())
    for secret in secrets:
        # An opaque configured secret can itself contain whitespace or Unicode.
        for size in range(min(len(secret) - 1, len(text)), 0, -1):
            if text.endswith(secret[:size]):
                end = min(end, len(text) - size)
                break
    return text[:end]
