"""오늘 문제가 풀 만한지 판별한다.

축이 셋이다.
  q_reach — 탐색하다 이 단어를 치게 되는가       (LLM probe)
  q_match — 유사도가 정답의 뜻을 따라가는가      (`속이다` 유형: 임베딩 붕괴)
  q_word  — 정답으로 내걸기에 공정한 단어인가    (방언·고어·비표준 표기)

실제 시도 횟수 11회차를 받아 맞춰본 결과, q_match 중심이던 옛 식은 시도 횟수와
스피어만 +0.47 이었다. 부호가 반대다 — 점수가 높을수록 오래 걸렸다. q_reach 단독은
-0.80 (힌트 없이 본인이 푼 6개만 보면 -0.89). 그래서 무게를 q_reach 로 옮겼다.

  선생님 22회 / 주인공 36회 / 피곤 57회  ...  모니터 400회 / 이후 440회

`속이다` 같은 임베딩 붕괴는 난이도와 무관하게 걸러야 하므로 q_match 를 별도 게이트로
남겨뒀다. 점수 배합에서는 0.2 만 쓰고, BROKEN_GATE 밑으로 떨어지면 점수와 상관없이
🔴 로 못 박는다.
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

# 배합. 실측 11회차로 격자 탐색해 고른 값이다. q_match 를 더 실으면 상관이 급격히
# 나빠진다 (0.2 → -0.71, 0.5 → -0.39, 1.0 → +0.47).
MATCH_W, REACH_W = 0.20, 0.80
# 이 밑이면 임베딩이 정답의 뜻을 아예 안 따라간다. 난이도와 무관하게 🔴.
BROKEN_GATE = 0.15

# 임계값도 실측에 맞췄다. 배합이 q_reach 중심이 되면서 점수 분포가 통째로 내려왔다.
GRADES = [
    (60, "green", "🟢", "오늘은 할 만합니다"),
    (42, "yellow", "🟡", "조금 까다롭습니다"),
    (28, "orange", "🟠", "각오하고 들어가세요"),
    (0, "red", "🔴", "오늘은 건너뛰세요"),
]

# 판정 헤드라인 옆 문구는 전부 여기서 나온다. LLM 자유 문장을 쓰면 안 된다 —
# "상위권이 어떤 성격으로 쏠렸는가"를 말하는 순간 정답의 분야가 그대로 새어나간다.
SUBLINE = {
    "broken": "유사도가 정답의 뜻을 배신하는 날입니다",
    "word": "정답으로 내걸기엔 공정하지 않은 단어입니다",
    "reduced": "유사도 점수만으로 매긴 임시 판정입니다",
    # 대부분의 날은 이쪽이다. 고장이 아니라 '떠올릴 계기가 있느냐' 로 갈린다.
    "reach": {
        "green": "탐색하다 보면 자연스럽게 닿는 단어입니다",
        "yellow": "떠올리기까지 좀 헤맬 단어입니다",
        "orange": "탐색 경로에서 한참 벗어나 있는 단어입니다",
        "red": "탐색하다 떠올릴 계기가 거의 없는 단어입니다",
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
    probe: float | None = None,
) -> dict:
    """first_score 는 0~100. LLM 신호가 없으면 축소 판정."""
    first_norm = norm(first_score, FIRST_LO, FIRST_HI)
    echo_ranks = list(echo_ranks or [])
    kept = None if matched_ranks is None else drop_echo(sorted(matched_ranks), echo_ranks)
    broken = False

    if fairness is None or matched_ranks is None or probe is None:
        q_match = first_norm
        q_word = q_reach = None
        playable = 100 * q_match
        weakest = "reduced"
    else:
        q_match = 0.80 * weighted_ratio(kept) + 0.20 * first_norm
        q_word = float(fairness)
        q_reach = float(probe)
        base = MATCH_W * q_match + REACH_W * q_reach
        # 공정하지 않은 단어는 아무리 닿기 쉬워도 그 위로 못 올라간다.
        playable = 100 * min(base, q_word)
        broken = q_match < BROKEN_GATE
        weakest = "broken" if broken else ("word" if q_word < base else "reach")

    # 화면에 나가는 값으로 등급을 매긴다. 반올림 전 값으로 매기면 77.8 이 🟡 인데
    # 표시는 78 이라, 78+ 는 🟢 이라고 적어둔 기준과 어긋나 보인다.
    playable = round(playable)
    name, badge, headline = grade_of(playable)
    if broken:
        # 임베딩이 통째로 딴 데로 튄 날. 닿기 쉬운 단어라도 도달할 수가 없다.
        name, badge, headline = "red", "🔴", "오늘은 건너뛰세요"
    subline = SUBLINE["reach"][name] if weakest == "reach" else SUBLINE[weakest]
    return {
        "playable": playable,
        "grade": name,
        "badge": badge,
        "headline": headline,
        "subline": subline,
        "weakest": weakest,
        "q_match": round(q_match, 3),
        "q_word": None if q_word is None else round(q_word, 3),
        "q_reach": None if q_reach is None else round(q_reach, 3),
        "probe": probe,
        "broken": broken,
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

    # 점수를 실제로 움직이는 축부터 말한다. 부제와 겹치지 않게 '왜 그런지' 쪽으로 쓴다.
    reach = v["q_reach"]
    if reach >= 0.7:
        out.append("흔히 쓰는 말이라 탐색 초반에 걸릴 가능성이 높습니다")
    elif reach >= 0.4:
        out.append("쓰임이 좁아 특정 주제로 좁힌 뒤에야 닿습니다")
    else:
        out.append("일상에서 이 말을 굳이 떠올릴 일이 없습니다")

    # 자기반향을 뺀 유효 개수를 말한다. 몇 개를 왜 뺐는지는 말하지 않는다 —
    # "정답 어간이 든 말이 상위권에 셋 있다" 는 그 자체로 스포일러다.
    n = v["semantic_match_eff"]
    if v["broken"]:
        out.append(f"유사도 상위 {SAMPLE_N}개 중 정답 쪽을 가리키는 단어 {n}개 — "
                   "상위권이 통째로 엉뚱한 곳에 쏠려 근접해도 도달이 안 됩니다")
    else:
        out.append(f"유사도 상위 {SAMPLE_N}개 중 정답 쪽을 가리키는 단어 {n}개")

    fair = v["fairness"]
    if fair is not None and fair < 0.5:
        out.append("정답 단어가 오늘날의 표준어라고 보기 어렵습니다")
    elif fair is not None and fair < 0.8:
        out.append("정답 단어가 표준어이긴 하나 지금은 다른 말에 거의 밀려났습니다")

    if first < 45 and not v["broken"]:
        out.append(f"정답 바로 옆 단어조차 {first:.2f}점 — 근접해도 점수가 더디게 오릅니다")

    return out
