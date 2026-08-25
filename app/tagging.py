from __future__ import annotations

import json

import httpx

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER

RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("餐饮", ("餐", "咖啡", "茶", "美团", "饿了么"), ("消费", "餐饮")),
    ("交通出行", ("滴滴", "地铁", "公交", "加油", "铁路"), ("消费", "出行")),
    ("购物", ("淘宝", "京东", "超市", "拼多多"), ("消费", "购物")),
    ("居住", ("房租", "物业", "水电", "燃气"), ("消费", "居住")),
    ("收入", ("工资", "报销", "退款", "奖金"), ("收入",)),
)


def classify(merchant: str, note: str = "") -> tuple[str, list[str], str]:
    """Classify locally by default, or call an OpenAI-compatible endpoint when configured."""
    if LLM_PROVIDER == "openai_compatible" and LLM_BASE_URL and LLM_MODEL and LLM_API_KEY:
        try:
            return _classify_remote(merchant, note)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            # Personal-data workflows should remain usable when the optional
            # provider is unavailable; callers can surface fallback-rules.
            pass
    return _classify_rules(merchant, note)


def _classify_remote(merchant: str, note: str) -> tuple[str, list[str], str]:
    prompt = "Return JSON only: {\"category\": string, \"tags\": [string]}. Classify this personal-finance transaction."
    response = httpx.post(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        json={"model": LLM_MODEL, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": f"merchant={merchant}; note={note}"}], "response_format": {"type": "json_object"}, "temperature": 0},
        timeout=20,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)
    category = str(result["category"]).strip() or "未分类"
    tags = [str(tag).strip() for tag in result.get("tags", []) if str(tag).strip()]
    return category, tags or ["待确认"], "openai-compatible"


def _classify_rules(merchant: str, note: str) -> tuple[str, list[str], str]:
    text = f"{merchant} {note}".lower()
    for category, keywords, tags in RULES:
        if any(keyword in text for keyword in keywords):
            return category, list(tags), "mock-rules"
    return "未分类", ["待确认"], "mock-rules" if LLM_PROVIDER == "mock" else "fallback-rules"
