"""破甲提示词注入引擎（纯函数）。

核心职责：把破甲提示词注入到请求体 system / 首条 system 消息的最前或最后，
并可选择替换。不就地修改原对象（重试安全）。支持 OpenAI chat/completions
与 Anthropic messages 两种协议。

注入位置 position ∈ {"prepend", "append", "replace"}：
  - prepend: 破甲词在前，下游 system 在后（默认，顶到最前）
  - append:  下游 system 在前，破甲词在后
  - replace: 只保留破甲词，丢弃下游 system
"""

from __future__ import annotations

import copy
import re
from typing import Any

from . import config

_SEPARATOR = "\n\n"

# ChatML 信封：部分破甲稿（如 dsh-purge 的 DeepSeek 稿）以
# `<|im_start|>system:<project_instructions>` 开头。中转注入时这些标记会被
# 上游当作对话分隔符，污染 system 文本，必须先剥离。
_CHATML_HEAD_RE = re.compile(
    r"^\s*<\|im_start\|>\s*system\s*:\s*(?:<project_instructions>\s*)?",
    re.IGNORECASE,
)
_CHATML_TAIL_RE = re.compile(
    r"(?:</project_instructions>\s*)?<\|im_end\|>\s*$",
    re.IGNORECASE,
)
_CHATML_ANY_RE = re.compile(r"<\|im_(?:start|end)\|>", re.IGNORECASE)
_PROJECT_TAG_RE = re.compile(r"</?project_instructions>", re.IGNORECASE)


def sanitize_prompt(text: str) -> str:
    """剥离 ChatML 信封与 project_instructions 标签，保留正文。"""
    if not text:
        return ""
    out = _CHATML_HEAD_RE.sub("", text)
    out = _CHATML_TAIL_RE.sub("", out)
    out = _CHATML_ANY_RE.sub("", out)
    out = _PROJECT_TAG_RE.sub("", out)
    return out.strip()


def normalize_crlf(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def is_injected(assembly: str, inject: str) -> bool:
    """双 needle 判重：归一化后 inject 头 80 字符和尾 96 字符都在 assembly 里。

    inject 文本 < 96 字符时 head==tail，退化为全文匹配。
    """
    if not inject:
        return False
    norm = normalize_crlf(assembly)
    head = normalize_crlf(inject[:80])
    tail = normalize_crlf(inject[-96:])
    return head in norm and tail in norm


def _blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text", "")))
        return "\n".join(parts)
    return ""


def _openai_system_assembly(body: dict[str, Any]) -> str:
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return ""
    parts = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "system":
            parts.append(_blocks_to_text(m.get("content")))
    return "\n".join(parts)


def _anthropic_system_assembly(body: dict[str, Any]) -> str:
    s = body.get("system")
    if isinstance(s, str):
        return s
    if isinstance(s, list):
        return "\n".join(b.get("text", "") for b in s if isinstance(b, dict))
    return ""


def _openai_system_assembly_of(body: dict[str, Any]) -> str:
    """兼容入口：OpenAI 判重基准。"""
    return _openai_system_assembly(body)


def _make_text_block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def _join(existing: str, text: str, position: str) -> str:
    if position == "replace":
        return text
    if position == "append":
        return f"{existing}{_SEPARATOR}{text}" if existing else text
    # prepend
    return f"{text}{_SEPARATOR}{existing}" if existing else text


def _rewrite_content(content: Any, text: str, position: str) -> Any:
    """把注入文本按 position 合并进 OpenAI content（str 或 list）。"""
    if position == "replace":
        return text
    if isinstance(content, str):
        return _join(content, text, position)
    if isinstance(content, list):
        block = _make_text_block(text)
        if position == "append":
            return content + [block]
        return [block] + content
    return text


def inject_openai_chat(body: dict[str, Any], text: str, position: str = "prepend") -> dict[str, Any]:
    if is_injected(_openai_system_assembly(body), text):
        return body
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body
    msgs = copy.deepcopy(msgs)
    # 定位第一条 system 消息
    idx = None
    for i, m in enumerate(msgs):
        if isinstance(m, dict) and m.get("role") == "system":
            idx = i
            break
    if idx is not None:
        first = msgs[idx]
        first["content"] = _rewrite_content(first.get("content"), text, position)
        msgs[idx] = first
    else:
        msgs.insert(0, {"role": "system", "content": text})
    body["messages"] = msgs
    return body


def inject_anthropic_messages(body: dict[str, Any], text: str, position: str = "prepend") -> dict[str, Any]:
    if is_injected(_anthropic_system_assembly(body), text):
        return body
    body = copy.deepcopy(body)
    sys = body.get("system")
    if sys is None:
        body["system"] = text
    elif isinstance(sys, str):
        body["system"] = _join(sys, text, position)
    elif isinstance(sys, list):
        block = _make_text_block(text)
        if position == "replace":
            body["system"] = [block]
        elif position == "append":
            body["system"] = sys + [block]
        else:
            body["system"] = [block] + sys
    else:
        body["system"] = text
    return body


def apply_injection(
    body: dict[str, Any],
    protocol: str,
    text: str,
    position: str = "prepend",
) -> dict[str, Any]:
    """统一入口。protocol ∈ {"openai", "anthropic"}。返回新 dict。

    若已在 system 中注入过（判重命中），原样返回 body，不重复注入。
    """
    if not text:
        return body
    text = sanitize_prompt(text)
    if not text:
        return body
    if position not in config.POSITIONS:
        position = config.DEFAULT_POSITION
    if protocol == "anthropic":
        return inject_anthropic_messages(body, text, position)
    return inject_openai_chat(body, text, position)


def extract_user_text(body: dict[str, Any], protocol: str) -> str:
    """提取最后一条 role=user 消息的文本，用于关键词匹配。"""
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return ""
    user_text = ""
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                user_text = c
            elif isinstance(c, list):
                parts = [str(p.get("text", "")) for p in c if isinstance(p, dict)]
                user_text = "\n".join(parts)
    return user_text
