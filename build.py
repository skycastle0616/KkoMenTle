"""꼬맨틀 판별·힌트 페이지 빌더.

  python build.py                              # 오늘 회차 → docs/
  python build.py --puzzle-id 1611 --out preview
  python build.py --no-llm --out preview       # 키 없이도 페이지는 완성된다
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import glob
import json
import os
import shutil
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape

import hints
import komantle
import verdict

ROOT = os.path.dirname(os.path.abspath(__file__))
SPOILER_LABEL = {"green": "약", "yellow": "중", "orange": "강", "red": "매우 강", "skull": "정답"}

# 유사어 카드가 집어올 필터 후 순위 (1-base)
NEIGHBOR_SLOTS = {"soft": (50, 62), "hard": (14, 22)}


def load_env() -> None:
    """로컬 .env 를 읽는다. Actions 에서는 Secret 이 이미 환경변수로 들어와 있다."""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _pick(neighbors: list, slots: tuple) -> list:
    out = []
    for pos in slots:
        if pos <= len(neighbors):
            _, word, score = neighbors[pos - 1]
            out.append({"word": word, "score": score})
    return out


def build_cards(answer: str, neighbors: list, llm: dict, dead: list) -> list:
    """11장 + 정답. LLM 이 죽어도 규칙 카드 4장은 항상 살아 있다."""

    def llm_card(n, field, spoiler, title, kind="text"):
        return {
            "n": n,
            "id": field,
            "spoiler": spoiler,
            "title": title,
            "kind": kind,
            "value": llm.get(field),
            "active": field in llm and field not in dead,
        }

    soft = _pick(neighbors, NEIGHBOR_SLOTS["soft"])
    hard = _pick(neighbors, NEIGHBOR_SLOTS["hard"])

    cards = [
        {"n": 1, "id": "length", "spoiler": "green", "title": "글자 수",
         "kind": "text", "value": f"{len(answer)}글자", "active": True},
        llm_card(2, "pos", "green", "품사"),
        llm_card(3, "emoji", "green", "이모지 셋", kind="emoji"),
        llm_card(4, "riddle", "green", "수수께끼"),
        llm_card(5, "scenes", "yellow", "상황 3컷", kind="list"),
        llm_card(6, "akinator", "yellow", "스무고개 3문답", kind="qa"),
        {"n": 7, "id": "soft", "spoiler": "yellow", "title": "중간 순위 유사어 ①",
         "kind": "words", "value": soft, "active": bool(soft)},
        {"n": 8, "id": "hard", "spoiler": "orange", "title": "중간 순위 유사어 ②",
         "kind": "words", "value": hard, "active": bool(hard)},
        llm_card(9, "cloze", "orange", "빈칸 예문"),
        {"n": 10, "id": "chosung", "spoiler": "red", "title": "초성",
         "kind": "big", "value": komantle.chosung(answer), "active": True},
        llm_card(11, "definition", "red", "사전식 정의"),
    ]
    for c in cards:
        c["spoiler_label"] = SPOILER_LABEL[c["spoiler"]]
    return cards


def read_archive(out_dir: str, current_id: int, today: dt.date) -> list:
    """지난 회차만. 오늘·미래 회차는 정답이 걸려 있으므로 뺀다."""
    rows = []
    for path in glob.glob(os.path.join(out_dir, "data", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("id") == current_id or dt.date.fromisoformat(d["date"]) >= today:
            continue
        d["answer"] = base64.b64decode(d["answer_b64"]).decode("utf-8")
        rows.append(d)
    rows.sort(key=lambda d: d["id"], reverse=True)
    return rows


def render(out_dir: str, ctx: dict, archive_rows: list) -> None:
    env = Environment(
        loader=FileSystemLoader(os.path.join(ROOT, "templates")),
        autoescape=select_autoescape(["html"]),
    )
    os.makedirs(out_dir, exist_ok=True)
    for name, extra in (("index.html", ctx), ("archive.html", {**ctx, "rows": archive_rows})):
        html = env.get_template(name).render(**extra)
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
    for asset in ("style.css", "app.js"):
        shutil.copyfile(os.path.join(ROOT, "static", asset), os.path.join(out_dir, asset))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--puzzle-id", type=int, default=None, help="특정 회차 (신선도 검증 생략)")
    ap.add_argument("--no-llm", action="store_true", help="Gemini 없이 규칙 카드만")
    ap.add_argument("--out", default="preview", help="출력 디렉터리 (기본 preview)")
    args = ap.parse_args()
    load_env()

    out_dir = os.path.join(ROOT, args.out)
    today = komantle.today_kst()

    meta = komantle.fetch_today()
    puzzle_id = args.puzzle_id or meta["answer_id"]
    puzzle = komantle.fetch_puzzle(puzzle_id)
    answer = puzzle["key"]
    puz_date = komantle.puzzle_date(puzzle["timestamp"])

    # 신선도 검증 — 어제 정답을 오늘 배포하는 사고를 막는다.
    if args.puzzle_id is None and puz_date != today:
        print(f"[중단] {puzzle_id}회차는 {puz_date} 문제인데 오늘은 {today} 다", file=sys.stderr)
        return 1

    judge_sample = komantle.scaled(puzzle["top_scores"])            # 판정 — 원본 상위권
    neighbors = komantle.filter_neighbors(answer, puzzle["top_scores"])  # 힌트 — 정답 조각 제거
    first_score = judge_sample[0][2]

    try:
        previous = komantle.fetch_puzzle(puzzle_id - 1)["key"]
    except Exception:
        previous = None

    signals, llm_cards, dead = {}, {}, []
    if args.no_llm or not hints.api_key():
        note = "--no-llm" if args.no_llm else "GEMINI_API_KEY 없음"
        print(f"[알림] {note} → 판정 축소, LLM 카드 비활성")
    else:
        try:
            signals = hints.judge_signals(answer, judge_sample)
            print(f"[판정] fairness={signals['fairness']} "
                  f"semantic_match={signals['semantic_match']}/{hints.SAMPLE_N}")
        except hints.GeminiError as exc:
            print(f"[경고] 판정 호출 실패 → 축소 판정으로 진행: {exc}", file=sys.stderr)
        try:
            llm_cards, dead = hints.hint_cards(answer)
            if dead:
                print(f"[경고] 가드에 걸려 비활성된 카드: {', '.join(dead)}", file=sys.stderr)
        except hints.GeminiError as exc:
            print(f"[경고] 힌트 호출 실패 → LLM 카드 전부 비활성: {exc}", file=sys.stderr)

    v = verdict.judge(
        first_score,
        signals.get("fairness"),
        signals.get("semantic_match"),
        signals.get("verdict_line") or None,
    )
    print(f"[결과] {v['playable']}점 {v['badge']} {v['headline']} / {v['subline']}")

    cards = build_cards(answer, neighbors, llm_cards, dead)
    active = [c for c in cards if c["active"]]

    ctx = {
        "puzzle_id": puzzle_id,
        "date": puz_date.isoformat(),
        "date_label": f"{puz_date.month}월 {puz_date.day}일",
        "verdict": v,
        "reasons": verdict.reasons(v),
        "cards": cards,
        "total": len(active) + 1,
        "answer_b64": base64.b64encode(answer.encode()).decode(),
        "answer_len": len(answer),
        "previous": previous,
        "game_url": komantle.GAME_URL,
        "built_at": komantle.now_kst().strftime("%Y-%m-%d %H:%M"),
    }

    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    record = {
        "id": puzzle_id,
        "date": puz_date.isoformat(),
        "answer_b64": ctx["answer_b64"],
        "first_score": first_score,
        "fairness": v["fairness"],
        "semantic_match": v["semantic_match"],
        "q_match": v["q_match"],
        "q_word": v["q_word"],
        "playable": v["playable"],
        "grade": v["grade"],
        "weakest": v["weakest"],
        "reduced": v["reduced"],
        "dead_cards": dead,
        "fairness_reason": signals.get("fairness_reason", ""),
        "match_reason": signals.get("match_reason", ""),
        "built_at": komantle.now_kst().isoformat(timespec="seconds"),
    }
    with open(os.path.join(out_dir, "data", f"{puzzle_id}.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)

    render(out_dir, ctx, read_archive(out_dir, puzzle_id, today))
    print(f"[완료] {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
