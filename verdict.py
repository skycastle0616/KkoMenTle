"""오늘 문제가 풀 만한지 판별한다.

축이 둘이다.
  q_word  — 정답으로 내걸기에 공정한 단어인가   (방언·고어·비표준 표기)
  q_match — 유사도가 정답의 뜻을 따라가는가     (`속이다` 유형: 임베딩 붕괴)

q_word 는 '떠올리기 쉬운가'가 아니다. 떠올리는 데 오래 걸리는 건 난이도이고 그게 게임의
재미다. 여기서 재는 건 그 단어를 정답으로 삼는 것 자체가 부당한가다.

둘을 더하지 않고 min() 을 쓴다. `속이다`는 멀쩡한 표준어라 fairness 가 1.0 이고,
합산하면 점수를 그냥 먹고 등급이 올라가버린다. 하나만 망가져도 추천하면 안 된다.
"""
from __future__ import annotations

SAMPLE_N = 16          # LLM 에게 보여주는 유사도 상위 단어 개수
# 순위 가중. 1위 이웃은 16위 이웃보다 압도적으로 정보가 많다. `주인공` 날의 상위 16 에
# 통하는 단어가 7개였는데 그게 1·2·5·6·7·14·16위였다. 개수만 세면 10~16위에 몰린 날과
# 똑같이 0.4375 인데, 실제로는 1위 `여주인공`(58.98) 하나가 사실상 답을 쥐여준다.
RANK_WEIGHT_TOTAL = sum(1.0 / r for r in range(1, SAMPLE_N + 1))
# 1위 단어 점수 정규화 구간. 실측 분포가 39~62 라 35~70 은 너무 넓어서
# 중간값이 부당하게 낮게 깎였다.
FIRST_LO, FIRST_HI = 38.0, 62.0

GRADES = [
    (78, "green", "🟢", "오늘은 할 만합니다"),
    (55, "yellow", "🟡", "조금 까다롭습니다"),
    (32, "orange", "🟠", "각오하고 들어가세요"),
    (0, "red", "🔴", "오늘은 건너뛰세요"),
]

# 판정 헤드라인 옆 문구는 전부 여기서 나온다. LLM 자유 문장을 쓰면 안 된다 —
# "상위권이 어떤 성격으로 쏠렸는가"를 말하는 순간 정답의 분야가 그대로 새어나간다.
SUBLINE = {
    "green": "유사도가 정답의 뜻을 곧게 따라가는 날입니다",
    "word": "정답으로 내걸기엔 공정하지 않은 단어입니다",
    "reduced": "유사도 점수만으로 매긴 임시 판정입니다",
    # match 축은 같은 '어긋남'이어도 정도가 하늘과 땅이다. 10/16 인 날과 0/16 인 날에
    # 같은 문장을 붙이면 🟡 이 실제보다 나쁘게 읽힌다.
    "match": {
        "yellow": "유사도가 정답의 뜻을 군데군데 놓치는 날입니다",
        "orange": "유사도가 정답의 뜻을 자주 놓치는 날입니다",
        "red": "유사도가 정답의 뜻을 배신하는 날입니다",
    },
}


def norm(value: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def grade_of(playable: float) -> tuple[str, str, str]:
    for cutoff, name, badge, headline in GRADES:
        if playable >= cutoff:
            return name, badge, headline
    raise AssertionError


def drop_echo(matched_ranks: list, echo_ranks: list) -> list:
    """정답 어간이 그대로 든 이웃은 가장 높은 순위 하나만 남긴다.

    `가득히` 날의 상위 16 에 `한가득·가득·가득가득` 이 있었다. 셋 다 정답이 자기를
    메아리친 것이라 목록이 넓게 가리키는 것처럼 보이게 만든다.
    """
    drop = set(sorted(echo_ranks)[1:])
    return [r for r in matched_ranks if r not in drop]


def weighted_ratio(matched_ranks: list) -> float:
    """순위 가중 일치도. 1/순위 를 더해 상위 16개 전부일 때 1.0 이 되게 나눈다."""
    return sum(1.0 / r for r in matched_ranks) / RANK_WEIGHT_TOTAL


def judge(
    first_score: float,
    fairness: float | None,
    matched_ranks: list | None,
    echo_ranks: list | None = None,
) -> dict:
    """first_score 는 0~100. fairness/matched_ranks 가 없으면 축소 판정."""
    first_norm = norm(first_score, FIRST_LO, FIRST_HI)
    echo_ranks = list(echo_ranks or [])
    kept = None if matched_ranks is None else drop_echo(sorted(matched_ranks), echo_ranks)

    if fairness is None or matched_ranks is None:
        q_match = first_norm
        q_word = None
        playable = 100 * q_match
        weakest = "reduced"
    else:
        match_ratio = weighted_ratio(kept)
        # 1위 점수 비중이 0.35 였는데, 천장이 낮은 것은 결함이 아니라 난이도다.
        # 목록이 제대로 가리키면 낮은 천장에서도 수렴한다. 결함 신호인 match 에 무게를 준다.
        q_match = 0.80 * match_ratio + 0.20 * first_norm
        q_word = float(fairness)
        playable = 100 * min(q_match, q_word)
        weakest = "match" if q_match < q_word else "word"

    # 화면에 나가는 값으로 등급을 매긴다. 반올림 전 값으로 매기면 77.8 이 🟡 인데
    # 표시는 78 이라, 78+ 는 🟢 이라고 적어둔 기준과 어긋나 보인다.
    playable = round(playable)
    name, badge, headline = grade_of(playable)
    # 🟢 은 두 축 다 0.78 이상이라 약한 고리를 따질 게 없다.
    if name == "green":
        subline = SUBLINE["green"]
    elif weakest == "match":
        subline = SUBLINE["match"][name]
    else:
        subline = SUBLINE[weakest]
    return {
        "playable": playable,
        "grade": name,
        "badge": badge,
        "headline": headline,
        "subline": subline,
        "weakest": weakest,
        "q_match": round(q_match, 3),
        "q_word": None if q_word is None else round(q_word, 3),
        "first_score": round(first_score, 2),
        "semantic_match": None if matched_ranks is None else len(matched_ranks),
        "semantic_match_eff": None if kept is None else len(kept),
        "matched_ranks": None if matched_ranks is None else sorted(matched_ranks),
        "kept_ranks": kept,
        "self_echo": len(echo_ranks),
        "fairness": fairness,
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

    # 자기반향을 뺀 유효 개수를 말한다. 몇 개를 왜 뺐는지는 말하지 않는다 —
    # "정답 어간이 든 말이 상위권에 셋 있다" 는 그 자체로 스포일러다.
    n = v["semantic_match_eff"]
    out.append(f"유사도 상위 {SAMPLE_N}개 중 정답 쪽을 가리키는 단어 {n}개")

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

    fair = v["fairness"]
    if fair is not None:
        if fair < 0.5:
            out.append("정답 단어가 오늘날의 표준어라고 보기 어렵습니다")
        elif fair < 0.8:
            out.append("정답 단어가 표준어이긴 하나 지금은 다른 말에 거의 밀려났습니다")

    return out
