"""꼬맨틀 API 클라이언트와 규칙 기반 유틸."""
from __future__ import annotations

import datetime as dt

import requests

BASE = "https://semantle-ko.newsjel.ly"
GAME_URL = "https://semantle-ko.newsjel.ly/"
KST = dt.timezone(dt.timedelta(hours=9))
TIMEOUT = 20

CHOSUNG = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")


def _get(path: str):
    r = requests.get(BASE + path, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_today() -> dict:
    """오늘 회차 메타. answer_id / 1st_score / previous.key 를 담고 있다."""
    return _get("/today")


def fetch_puzzle(puzzle_id: int) -> dict:
    """정답(key)과 유사어 1000개. 어제~내일 3일치만 열려 있다."""
    data = _get(f"/top_scores/{puzzle_id}")
    if not data:
        raise LookupError(f"{puzzle_id}회차는 열려 있지 않다 (어제~내일 3일치만 조회 가능)")
    return data


def puzzle_date(timestamp: int) -> dt.date:
    """회차 timestamp(UTC 15:00 = KST 자정)를 그 회차가 걸린 KST 날짜로."""
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).astimezone(KST).date()


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def scaled(top_scores: list) -> list[tuple[int, str, float]]:
    """[(순위, 단어, 0~100 점수)]. 거르지 않은 원본 — 판정은 이걸 봐야 한다."""
    return [(rank, word, round(score * 100, 2)) for rank, word, score in top_scores]


def filter_neighbors(answer: str, top_scores: list) -> list[tuple[int, str, float]]:
    """정답을 품거나 정답에 품히는 단어는 노출 즉시 정답 공개라 걷어낸다.

    힌트 카드 전용이다. 판정에는 쓰지 마라 — `부위원장`처럼 뜻이 제대로 통하는
    형태적 이웃까지 사라져서 semantic_match 가 왜곡된다.

    반환은 [(원래 순위, 단어, 0~100 점수)] 이며 원래 순위를 유지한다.
    """
    out = []
    for rank, word, score in top_scores:
        if answer in word or word in answer:
            continue
        out.append((rank, word, round(score * 100, 2)))
    return out


def chosung(word: str) -> str:
    out = []
    for ch in word:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            out.append(CHOSUNG[(code - 0xAC00) // 588])
        else:
            out.append(ch)
    return "".join(out)
