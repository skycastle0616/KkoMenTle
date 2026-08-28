"""Gemini 호출 · 프롬프트 · 누출 가드.

호출을 둘로 나눈다. 판정은 흔들리면 안 되니 temperature 0.3, 힌트는 창의성이
필요하니 0.9. 하루 2회라 무료 할당량에는 무관하다.
"""
from __future__ import annotations

import json
import os
import time

import requests

MODELS = ["gemini-3.6-flash", "gemini-3.5-flash"]
# 무료 티어는 분당 요청 수가 얕아서 429 가, 가끔 503 도 튄다. 하루 한 번뿐인 빌드가
# 일시적 오류로 축소 판정에 빠지면 손해라 라운드 사이를 띄우고 다시 돈다.
BACKOFF = (0, 15, 30)
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = 90
SAMPLE_N = 16


class GeminiError(RuntimeError):
    pass


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


def _call(prompt: str, temperature: float, schema: dict) -> dict:
    key = api_key()
    if not key:
        raise GeminiError("GEMINI_API_KEY 가 없다")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    errors = []
    for wait in BACKOFF:
        if wait:
            time.sleep(wait)
        for model in MODELS:
            try:
                r = requests.post(
                    ENDPOINT.format(model=model),
                    headers={"Content-Type": "application/json", "x-goog-api-key": key},
                    json=body,
                    timeout=TIMEOUT,
                )
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            except Exception as exc:  # 모델 폐기·할당량·일시 오류 → 다음 모델로
                errors.append(f"{model}: {type(exc).__name__} {exc}")
    raise GeminiError(" | ".join(errors[-len(MODELS):]) or "호출 실패")


# ---------------------------------------------------------------- 누출 가드

def leak_terms(answer: str) -> list[str]:
    """힌트 안에 나오면 안 되는 문자열들.

    정답 자체, 그리고 길이 2 이상의 접두사. 접미사는 정답이 '다'로 끝나지 않을 때만
    본다 — `속이다`의 접미사 `이다`까지 막으면 평범한 한국어 문장이 전부 걸린다.
    """
    terms = {answer}
    n = len(answer)
    for i in range(2, n):
        terms.add(answer[:i])
        if not answer.endswith("다"):
            terms.add(answer[n - i:])
    return sorted(terms, key=len, reverse=True)


def leaks(text: str, terms: list[str]) -> str | None:
    """걸린 금지어를 돌려준다. 깨끗하면 None."""
    if not isinstance(text, str):
        return None
    flat = text.replace(" ", "")
    for t in terms:
        if t in text or t in flat:
            return t
    return None


def cloze_ok(cloze: str, answer: str) -> bool:
    return cloze.count("○") == len(answer)


# ---------------------------------------------------------------- 판정 호출

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "fairness": {"type": "number"},
        "fairness_reason": {"type": "string"},
        "semantic_match": {"type": "integer"},
        "match_reason": {"type": "string"},
        "verdict_line": {"type": "string"},
    },
    "required": [
        "fairness",
        "fairness_reason",
        "semantic_match",
        "match_reason",
        "verdict_line",
    ],
}

JUDGE_PROMPT = """너는 한국어 단어 유사도 게임 '꼬맨틀'의 오늘 문제가 풀 만한지 판정한다.
이 게임은 FastText 임베딩으로 정답과의 유사도를 매기는데, 날에 따라 임베딩이
정답의 실제 뜻을 전혀 따라가지 못해 문제가 망가진다. 그런 날을 골라내는 게 목적이다.

정답: {answer}
유사도 상위 {n}개 (순위. 단어 (점수)):
{neighbors}

다음을 매겨라.

fairness (0.0~1.0)
  이 단어를 '오늘날의 표준 한국어'로서 게임 정답에 내걸기에 공정한가.
  ★ 떠올리기까지 오래 걸리는 것은 난이도이고 그 자체가 게임의 재미다. 그건 깎지 마라.
    깎아야 할 것은 정답으로 삼는 것 자체가 부당한 경우뿐이다.
  1.0      현대 표준어. 사전에 있고 지금도 평범하게 쓰인다. 흔하지 않아도 여기 해당한다.
  0.6~0.8  표준어이긴 하나 현대 한국어에서 다른 말에 거의 밀려났다.
           (예: '네거리' — '사거리'가 사실상 대체했다)
  0.3~0.5  지역 방언, 특정 분야 전문용어, 문어체 전용, 사전에만 남은 말.
  0.0~0.2  고어·폐어, 비표준 표기, 사람들이 하나의 낱말로 인식하지 않는 형태.

semantic_match (0~{n} 정수)
  위 목록 {n}개 중 정답의 '실제 뜻'과 의미적으로 통하는 단어의 개수.
  형태만 닮고 뜻이 다른 것, 엉뚱한 분야로 튄 것, 고유명사·지명은 세지 마라.

fairness_reason / match_reason
  각각 한 문장. 채점 근거.

verdict_line
  오늘 문제의 성격을 한 문장(공백 포함 40자 이내)으로.
  ★ 정답이 무엇인지, 정답이 어떤 분야·주제의 말인지 절대 드러내지 마라.
    유사도 목록이 정답의 뜻을 따라가는지 여부에 대해서만 말하라.
"""


def judge_signals(answer: str, neighbors: list) -> dict:
    lines = "\n".join(
        f"{i}. {w} ({s:.2f})" for i, (_, w, s) in enumerate(neighbors[:SAMPLE_N], 1)
    )
    out = _call(
        JUDGE_PROMPT.format(answer=answer, n=SAMPLE_N, neighbors=lines),
        temperature=0.3,
        schema=JUDGE_SCHEMA,
    )
    out["fairness"] = max(0.0, min(1.0, float(out["fairness"])))
    out["semantic_match"] = max(0, min(SAMPLE_N, int(out["semantic_match"])))

    terms = leak_terms(answer)
    for field in ("verdict_line", "fairness_reason", "match_reason"):
        if leaks(out.get(field, ""), terms):
            out[field] = ""
    return out


# ---------------------------------------------------------------- 힌트 호출

HINT_SCHEMA = {
    "type": "object",
    "properties": {
        "pos": {"type": "string"},
        "emoji": {"type": "string"},
        "riddle": {"type": "string"},
        "scenes": {"type": "array", "items": {"type": "string"}},
        "akinator": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
                "required": ["q", "a"],
            },
        },
        "cloze": {"type": "string"},
        "definition": {"type": "string"},
    },
    "required": ["pos", "emoji", "riddle", "scenes", "akinator", "cloze", "definition"],
}

HINT_PROMPT = """한국어 단어 맞히기 게임의 힌트 카드를 만든다.

정답: {answer} ({length}글자)

★ 절대 규칙 — 어느 항목에서도 다음 문자열이 나오면 안 된다: {banned}
  정답을 부분 문자열로 품는 단어(정답이 '위원장'이면 '부위원장' 같은 것)도 금지.
  cloze 의 ○ 자리를 빼면, 정답을 그대로 적은 곳이 하나도 없어야 한다.

항목:

pos        품사 하나. "명사" / "동사" / "형용사" / "부사" 중 하나로만.

emoji      정답을 연상시키는 이모지 정확히 3개. 설명 없이 이모지만.

riddle     수수께끼 한 문장.
           · 사전적·설명적 문장 금지. "무엇을 하는 행위" 같은 정의문은 실패다.
           · 은유 / 역설 / 의인화 / 시점 전환 중 하나를 반드시 써라.
           · 한 번 읽고 바로 알면 실패다. 두세 번 곱씹어야 "아" 하는 수준으로.
           · 톤 예시 (정답이 '속이다'였다면):
             "나는 진실의 옷을 입고 나타나지만, 벗겨지는 순간 아무것도 아니게 된다"

scenes     이 말이 등장할 법한 장면 3개. 각각 한 줄(20자 내외).

akinator   스무고개 질문·답 3쌍. q 는 예/아니오로 답할 수 있는 질문,
           a 는 "예" 또는 "아니오" 로만. 넓은 것에서 좁은 것 순서로.

cloze      정답 자리를 ○ 로 가린 예문 한 문장. ○ 를 정확히 {length}개 이어 쓴다.
           예: "남을 ○○○ 이익만 챙기면 신뢰를 잃는다"
           ○ 는 정답 자리에만 쓰고, 조사·어미는 자연스럽게 이어 붙여라.

definition 사전식 정의 한 문장. 정답 표기 없이 뜻만.
"""

RETRY_NOTE = """
※ 직전 응답이 금지 문자열을 노출해 거부되었다. 다음 문자열을 어디에도 쓰지 말고 다시 만들어라: {banned}
"""


def clean(raw: dict, answer: str):
    """가드를 통과한 필드만 남긴다. 통과 못 한 필드 이름 목록도 함께 돌려준다."""
    terms = leak_terms(answer)
    out = {}
    bad = []

    for field in ("pos", "emoji", "riddle", "definition"):
        value = str(raw.get(field, "")).strip()
        if value and not leaks(value, terms):
            out[field] = value
        else:
            bad.append(field)

    scenes = [str(s).strip() for s in raw.get("scenes", []) if str(s).strip()]
    if len(scenes) >= 3 and not any(leaks(s, terms) for s in scenes):
        out["scenes"] = scenes[:3]
    else:
        bad.append("scenes")

    aki = [
        q for q in raw.get("akinator", [])
        if isinstance(q, dict) and q.get("q") and q.get("a")
    ]
    if len(aki) >= 3 and not any(leaks(q["q"], terms) or leaks(q["a"], terms) for q in aki):
        out["akinator"] = [
            {"q": str(q["q"]).strip(), "a": str(q["a"]).strip()} for q in aki[:3]
        ]
    else:
        bad.append("akinator")

    cloze = str(raw.get("cloze", "")).strip()
    if cloze and cloze_ok(cloze, answer) and not leaks(cloze.replace("○", ""), terms):
        out["cloze"] = cloze
    else:
        bad.append("cloze")

    return out, bad


def hint_cards(answer: str):
    """가드 위반 시 1회만 재요청하고, 그래도 실패한 카드는 비활성으로 넘긴다."""
    banned = ", ".join(leak_terms(answer))
    prompt = HINT_PROMPT.format(answer=answer, length=len(answer), banned=banned)

    raw = _call(prompt, temperature=0.9, schema=HINT_SCHEMA)
    cards, bad = clean(raw, answer)
    if not bad:
        return cards, []

    retry = _call(prompt + RETRY_NOTE.format(banned=banned), temperature=0.9, schema=HINT_SCHEMA)
    fixed, _ = clean(retry, answer)
    for field in bad:
        if field in fixed:
            cards[field] = fixed[field]
    return cards, [f for f in bad if f not in cards]
