"""Validated WebUI entry addresses and same-path switching."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


class AccessEntryError(ValueError):
    pass


def normalize_entry_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise AccessEntryError("访问地址必须是完整的 http:// 或 https:// URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise AccessEntryError("访问地址不能包含账号、查询参数或片段")
    if parts.path not in {"", "/"}:
        raise AccessEntryError("访问地址只能填写来源地址，不能包含页面路径")
    return urlunsplit((parts.scheme, parts.netloc.lower(), "", "", ""))


def entry_mode(current_origin: object, internal_url: object, external_url: object) -> str | None:
    current = normalize_entry_url(current_origin)
    internal = normalize_entry_url(internal_url)
    external = normalize_entry_url(external_url)
    if not internal or not external:
        return None
    if current == internal:
        return "internal"
    if current == external:
        return "external"
    return None


def switch_url(target_entry: object, path_and_query: object) -> str:
    target = normalize_entry_url(target_entry)
    path = str(path_and_query or "/")
    if not path.startswith("/") or path.startswith("//"):
        raise AccessEntryError("切换路径必须是本站页面路径")
    return target + path
