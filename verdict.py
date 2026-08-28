"""오늘 문제가 풀 만한지 판별한다.

축이 둘이다.
  q_word  — 단어를 후보로 떠올릴 수 있는가      (`네거리` 유형: 생소어)
  q_match — 유사도가 정답의 뜻을 따라가는가     (`속이다` 유형: 임베딩 붕괴)

둘을 더하지 않고 min() 을 쓴다. `속이다`는 흔한 말이라 familiarity 가 1.0 이고,
합산하면 점수를 그냥 먹고 등급이 올라가버린다. 하나만 망가져도 추천하면 안 된다.
"""
from __future__ import annotations

SAMPLE_N = 16          # LLM 에게 보여주는 유사도 상위 단어 개수
FIRST_LO, FIRST_HI = 35.0, 70.0   # 1위 단어 점수 정규화 구간

GRADES = [
    (78, "green", "🟢", "오늘은 할 만합니다"),
    (55, "yellow", "🟡", "조금 까다롭습니다"),
    (32, "orange", "🟠", "각오하고 들어가세요"),
    (0, "red", "🔴", "오늘은 건너뛰세요"),
]

SUBLINE = {
    "match": "유사도가 정답의 뜻을 배신하는 날입니다",
    "word": "단어 자체를 떠올리기 어려운 날입니다",
    "reduced": "유사도 점수만으로 매긴 임시 판정입니다",
}


def norm(value: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def grade_of(playable: float) -> tuple[str, str, str]:
    for cutoff, name, badge, headline in GRADES:
        if playable >= cutoff:
            return name, badge, headline
    raise AssertionError


def judge(
    first_score: float,
    familiarity: float | None,
    semantic_match: int | None,
    verdict_line: str | None = None,
) -> dict:
    """first_score 는 0~100. familiarity/semantic_match 가 없으면 축소 판정."""
    first_norm = norm(first_score, FIRST_LO, FIRST_HI)

    if familiarity is None or semantic_match is None:
        q_match = first_norm
        q_word = None
        playable = 100 * q_match
        weakest = "reduced"
    else:
        match_ratio = semantic_match / SAMPLE_N
        q_match = 0.65 * match_ratio + 0.35 * first_norm
        q_word = float(familiarity)
        playable = 100 * min(q_match, q_word)
        weakest = "match" if q_match < q_word else "word"

    name, badge, headline = grade_of(playable)
    # 🟢 인 날은 어느 축이 약한지 따질 게 없다. LLM 한 줄평을 그대로 쓴다.
    subline = verdict_line if (name == "green" and verdict_line) else SUBLINE[weakest]
    return {
        "playable": round(playable),
        "grade": name,
        "badge": badge,
        "headline": headline,
        "subline": subline,
        "weakest": weakest,
        "q_match": round(q_match, 3),
        "q_word": None if q_word is None else round(q_word, 3),
        "first_score": round(first_score, 2),
        "semantic_match": semantic_match,
        "familiarity": familiarity,
        "reduced": q_word is None,
    }


def reasons(v: dict) -> list[str]:
    """근거 문장. 스포일러 금지 — 숫자와 쏠림의 유무까지만 말한다."""
    out = []
    first = v["first_score"]

    if v["reduced"]:
        out.append(f"정답 바로 옆 1위 단어가 {first:.2f}점입니다")
        out.append("힌트 생성이 실패해 유사도 점수만으로 매긴 판정입니다")
        return out

    n = v["semantic_match"]
    out.append(f"유사도 상위 {SAMPLE_N}개 중 정답의 뜻과 통하는 단어 {n}개")

    if first < 45:
        out.append(f"정답 바로 옆 단어조차 {first:.2f}점 — 근접해도 점수가 안 오릅니다")
    elif first < 58:
        out.append(f"정답 바로 옆 단어가 {first:.2f}점 — 근접해도 점수가 더디게 오릅니다")
    else:
        out.append(f"정답 바로 옆 단어가 {first:.2f}점 — 근접하면 점수가 확실히 오릅니다")

    if n <= 3:
        out.append("상위권이 엉뚱한 분야로 통째로 쏠려 있습니다")
    elif n <= 9:
        out.append("상위권에 결이 다른 단어가 꽤 섞여 있습니다")

    fam = v["familiarity"]
    if fam is not None:
        if fam < 0.5:
            out.append("정답 단어 자체를 후보로 떠올리기가 쉽지 않습니다")
        elif fam < 0.75:
            out.append("정답 단어가 아주 흔한 말은 아닙니다")

    return out
