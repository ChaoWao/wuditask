"""Bounded, attributed prose excerpts from a public Issue or PR body."""

import re
from html import unescape

# These headings describe work, not evidence logs or historical appendices.
_HEADINGS = {
    "goal": ("目标", "goal", "summary", "problem description", "目的"),
    "progress": (
        "当前进展",
        "当前结论",
        "current status",
        "已交付基线",
        "landed optimizations",
    ),
    "remaining": (
        "当前阻塞与解除条件",
        "当前阻塞",
        "剩余工作",
        "remaining work",
        "blockers",
    ),
    "next": ("下一步", "next steps", "近期交付顺序与完成标准", "active workstreams"),
}
_FIELDS = {
    "目标": "goal",
    "状态": "progress",
    "当前结论": "progress",
    "下一步": "next",
    "剩余工作": "remaining",
    "阻塞/需要协助": "remaining",
    "阻塞": "remaining",
}


def _plain(text):
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[*`]+", "", text)
    return " ".join(unescape(text).split()).strip()


def _category(heading):
    heading = _plain(heading).casefold()
    for key, aliases in _HEADINGS.items():
        for alias in aliases:
            if heading == alias or any(
                heading.startswith(alias + sep) for sep in (" —", " -", "：", ":")
            ):
                return key
    return None


def body_summary(body):
    """Extract the author's current overview, never infer progress from closure."""
    if not isinstance(body, str) or not body.strip():
        return {}
    # Bound parsing independently of publication limits. Hidden history and code
    # are not summary sources; all returned text is rendered as text, never HTML.
    body = re.sub(r"<!--.*?(?:-->|$)", "", body[:200_000], flags=re.S)
    sections, intro, fields = {}, [], {}
    current, paragraphs, heading = None, [], ""
    overview_level, overview = None, False
    hidden, fence = 0, None

    def flush():
        nonlocal paragraphs
        if current and current not in sections and any(paragraphs):
            sections[current] = (heading, paragraphs)
        paragraphs = []

    for raw in body.splitlines():
        opens = len(re.findall(r"<(?:details|script|style)\b", raw, re.I))
        closes = len(re.findall(r"</(?:details|script|style)\s*>", raw, re.I))
        if hidden:
            hidden = max(0, hidden + opens - closes)
            continue
        line = raw.strip().lstrip("> ")
        marker = re.match(r"^(`{3,}|~{3,})", line)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        if opens:
            hidden = max(0, opens - closes)
            continue
        h = re.match(r"^(#{1,6})\s+(.+)", line)
        if h:
            flush()
            heading = h[2]
            level = len(h[1])
            if overview_level is not None and level <= overview_level:
                overview = False
            if "当前概况" in heading:
                overview, overview_level = True, level
            current = _category(heading)
            continue
        if overview:
            text = _plain(line)
            key, separator, value = re.sub("／", "/", text).partition("：")
            if not separator:
                key, separator, value = text.partition(":")
            category = _FIELDS.get(key.strip())
            if category and value.strip():
                fields.setdefault(category, []).append(value.strip())
            continue
        target = paragraphs if current else intro if not heading else None
        if target is None:
            continue
        if not line:
            if target and target[-1]:
                target.append("")
            continue
        bullet = re.match(r"^(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s+)(.*)", line)
        text = _plain(bullet[1] if bullet else line)
        if not text or text.startswith("|"):
            continue
        if bullet or not target or not target[-1]:
            target.append(text)
        else:
            target[-1] += " " + text
    flush()
    if "goal" not in sections and any(intro):
        sections["goal"] = ("正文开头", intro)
    for key, paragraphs in fields.items():
        sections[key] = ("当前概况", paragraphs)

    result = {}
    for key, (heading, paragraphs) in sections.items():
        items = list(dict.fromkeys(p for p in paragraphs if p))
        if not items:
            continue
        result[key] = {
            "heading": _plain(heading),
            "items": [p if len(p) <= 420 else p[:420] + "…" for p in items[:3]],
            "truncated": len(items) > 3 or any(len(p) > 420 for p in items[:3]),
        }
    return result
