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
# 하루 한 번뿐인 빌드가 일시적 5xx 로 축소 판정에 빠지면 손해라 라운드를 다시 돈다.
# 429 에는 절대 재시도하지 않는다 — 무료 티어 할당량은 모델당 하루 20회
# (GenerateRequestsPerDayPerProjectPerModel-FreeTier) 라서 기다려도 그날은 안 풀리고,
# 재시도가 남은 할당량을 더 태운다.
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
        transient = False
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
            except requests.HTTPError as exc:  # 모델 폐기(404)·할당량(429)은 다시 물어도 같다
                errors.append(f"{model}: HTTP {exc.response.status_code}")
                transient = transient or exc.response.status_code >= 500
            except Exception as exc:  # 타임아웃·연결 끊김·응답 파싱 실패
                errors.append(f"{model}: {type(exc).__name__} {exc}")
                transient = True
        if not transient:
            break
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
    },
    "required": [
        "fairness",
        "fairness_reason",
        "semantic_match",
        "match_reason",
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
  위 목록 {n}개 중 '정답 쪽을 가리키는' 단어의 개수.
  ★ 정답과 바꿔 쓸 수 있는가(동의어)를 세는 게 아니다. 그 단어를 본 사람의 생각이
    정답 쪽으로 굴러가는가를 센다. 이 게임은 유사도만 보고 답을 찾아가는 게임이라
    방향만 맞으면 도달한다. 동의어만 세면 멀쩡한 날을 망가진 날로 오판한다.
  센다: 동의어, 상위어·하위어, 같은 과정·같은 장면에 속하는 말, 활용형·사동·피동,
        정답이 가리키는 것의 부분·결과·재료.
        예 (정답 '자라다'): 싹터·성장하다·키우다·여물어는 물론이고,
        태어나·살아가다도 같은 생애 과정이라 센다.
  안 센다: 엉뚱한 분야로 통째로 튄 것, 형태만 닮고 뜻이 전혀 다른 것, 고유명사·지명,
        보고도 정답 쪽으로 전혀 굴러가지 않는 것.
        예 (정답 '자라다'): 입양되어·과년한·영특하여·다스리다.

fairness_reason / match_reason
  각각 한 문장. 채점 근거. 페이지에 노출되지 않고 보정용 기록으로만 남는다.
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
    for field in ("fairness_reason", "match_reason"):
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

★ 카드마다 스포일러 강도가 정해져 있다. 강도를 넘기면 그 카드는 실패다.
  약    pos, emoji, riddle       읽고 나서도 후보가 셋 이상 남아야 한다
  중    scenes, akinator         범위가 좁혀지되 하나로 확정되면 안 된다
  강    cloze                    거의 확정돼도 된다
  매우 강 definition             확정해도 된다

  사람들은 약한 카드부터 연다. 약·중 카드가 정답을 바로 알려주면 이 페이지는 쓸모가 없다.

★ 약·중 카드(riddle, scenes, akinator)에는 '뜻풀이 낱말'을 쓰지 마라.
  definition 에 쓸 말, 정답의 동의어·상위어·핵심 개념어가 전부 여기 해당한다.
  정답이 '속이다' 였다면 거짓·진실·속임·기만·사기·눈을 가리다 — 전부 금지다.
  이 규칙을 어기면 은유를 아무리 씌워도 사전을 옮겨적은 것에 지나지 않는다.

★ 아래 예시는 전부 정답이 '거울' 인 다른 문제에 대한 것이다.
  문장을 베끼지 말고 방식만 가져와라. 예시 문장을 그대로 쓰면 실패다.

항목:

pos        품사 하나. "명사" / "동사" / "형용사" / "부사" 중 하나로만.

emoji      정답을 연상시키는 이모지 정확히 3개. 설명 없이 이모지만.

riddle     수수께끼 한 문장. 스포 강도 '약'.
           · 정답을 설명하지 마라. 그것이 지나간 자리, 그것을 겪은 쪽의 처지,
             그것과 시간의 관계 — 이런 옆면 하나만 골라 말하라.
           · 은유 / 역설 / 의인화 / 시점 전환 중 하나를 반드시 써라.
           · 뜻풀이 낱말 금지 (위 ★ 규칙).
           · 검사: 이 한 문장만 보고 사람이 떠올릴 후보가 셋 이상이어야 한다.
             하나로 좁혀지면 실패다.
           · 나쁜 예 (정답이 '속이다'): "나는 진실의 탈을 쓰고 남의 눈을 가린 채
             달콤한 거짓을 현실로 만든다" — 은유를 걸쳤을 뿐 뜻풀이를 그대로 옮겼다.
           · 좋은 예 (정답이 '거울'): "나는 네가 오면 태어나고 네가 가면 죽지만,
             한 번도 너를 본 적이 없다."

scenes     이 말이 등장할 법한 장면 3개. 각각 한 줄(20자 내외). 스포 강도 '중'.

           ★ 그 행위를 하는 장면을 그리지 마라. 이 카드가 가장 자주 망가지는 지점이다.
             '어떻게 하는지'를 보여주면 낱말을 한 자도 안 써도 답이 하나로 떨어진다.
             나쁜 예 (정답이 '속이다'):
               "밑바닥에 무게추를 붙인 저울 위로 사과를 올릴 때"
               "소매 안쪽에 카드를 숨기고 판돈을 끌어모을 때"
               "서류의 도장을 몰래 바꿔 찍어 상대에게 전달할 때"
               — 금지어는 하나도 안 썼지만 수법을 그대로 실연했다. 보면 바로 안다.

           · 대신 셋 중 하나를 골라 그려라. 행위 자체는 화면 밖에 둔다.
             ① 그 일이 지나간 뒤 — 남은 자국, 뒤늦게 드러난 것
             ② 겪은 쪽의 반응 — 표정, 침묵, 뒤늦은 행동
             ③ 그 말이 입에 오르는 자리 — 누가 그 말을 꺼낼 법한 대화
             좋은 예 (정답이 '거울'): "아침에 본 얼굴이 하루 종일 마음에 걸릴 때"

           · 직업 이름 금지 (사기꾼·마술사·도둑 같은 것).
           · 셋은 서로 다른 영역이어야 한다. 셋 다 비슷한 장면이면 실패다.
           · 검사: 장면만 보고 '어떻게 했는지'가 보이면 실패다.
             '무슨 일이 있었나 보다' 정도로만 남아야 한다.

akinator   스무고개 질문·답 3쌍. q 는 예/아니오로 답할 수 있는 질문,
           a 는 "예" 또는 "아니오" 로만. 스포 강도 '중'.
           · 범주를 좁히는 질문이어야 한다. 정답의 뜻풀이를 그대로 묻지 마라.
             나쁜 예 (정답이 '속이다'): "타인에게 거짓을 진실처럼 믿게 만드는 것인가요?"
                      — 이건 질문이 아니라 정의다. 읽는 순간 끝난다.
             좋은 예 (정답이 '거울'): "이것은 사람 손으로 만든 물건인가요?"
           · 세 질문 모두 정답이 '무엇인지' 묻지 마라. 주변을 물어라.
             1번 — 아주 넓은 갈래 (사람의 일인가, 물건인가, 혼자 되는 일인가)
             2번 — 어디서 벌어지는가, 무엇이 있어야 하는가
             3번 — 그 뒤에 무슨 일이 생기는가, 겪은 쪽은 어떻게 되는가,
                    흔한 일인가 드문 일인가
           · 질문은 짧아야 한다. 35자를 넘으면 정의를 옮겨적고 있다는 뜻이다.
           · 셋을 다 읽었을 때 후보가 여러 개 남아 있어야 한다.
           · 뜻풀이 낱말 금지 (위 ★ 규칙).

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
