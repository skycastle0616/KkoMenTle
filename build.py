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


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


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


ARCHIVE_DIR = os.path.join(ROOT, "archive")
OUTCOMES = os.path.join(ROOT, "outcomes.json")


def read_outcome(puzzle_id: int) -> dict | None:
    """사람이 적어 넣은 실측 결과. 없으면 None.

    보정은 하루 체감이 아니라 이 숫자로 한다. 파일이 없어도 빌드는 그대로 돈다.
    """
    try:
        with open(OUTCOMES, encoding="utf-8") as f:
            return json.load(f).get(str(puzzle_id))
    except FileNotFoundError:
        return None


def write_archive(puzzle_id: int, puz_date, answer: str, judge_sample: list) -> None:
    """상위 1000개를 통째로 남긴다. 표본 크기나 공식을 바꿀 때 되돌아가려면 필요하다.

    꼬맨틀 API 는 어제~내일 3일치만 열어서, 그날 받아두지 않으면 영영 못 받는다.
    docs/ 바깥이라 페이지로 배포되지 않는다. 정답은 어차피 docs/data 에 answer_b64 로
    들어가므로 여기서 새로 새는 것은 없다 — 같은 취급으로 감싸두기만 한다.
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    payload = {
        "id": puzzle_id,
        "date": puz_date.isoformat(),
        "answer_b64": _b64(answer),
        "top_scores_b64": _b64(json.dumps(judge_sample, ensure_ascii=False)),
    }
    with open(os.path.join(ARCHIVE_DIR, f"{puzzle_id}.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


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

    signals, llm_cards, dead, probe_words = {}, {}, [], []
    if args.no_llm or not hints.api_key():
        note = "--no-llm" if args.no_llm else "GEMINI_API_KEY 없음"
        print(f"[알림] {note} → 판정 축소, LLM 카드 비활성")
    else:
        try:
            signals = hints.judge_signals(answer, judge_sample)
            print(f"[판정] fairness={signals['fairness']} "
                  f"probe={signals['probe']} 통하는 순위={signals['matched_ranks']} "
                  f"직접={signals['direct_ranks']}")
        except hints.GeminiError as exc:
            print(f"[경고] 판정 호출 실패 → 축소 판정으로 진행: {exc}", file=sys.stderr)
        # 탐색어는 상위 목록을 안 보여준 채 따로 받는다. 같이 받으면 이미 목록에
        # 있는 말 쪽으로 끌려서, 바깥에서 들어오는 길을 재는 의미가 없어진다.
        try:
            probe_words = hints.search_words(answer)
        except hints.GeminiError as exc:
            print(f"[경고] 탐색어 호출 실패 → 경로 미측정: {exc}", file=sys.stderr)
        try:
            llm_cards, dead = hints.hint_cards(answer)
            if dead:
                print(f"[경고] 가드에 걸려 비활성된 카드: {', '.join(dead)}", file=sys.stderr)
        except hints.GeminiError as exc:
            print(f"[경고] 힌트 호출 실패 → LLM 카드 전부 비활성: {exc}", file=sys.stderr)

    # 판정 표본 안에서 정답 어간을 그대로 품은 이웃 수. 어간만 본다 — 누출 가드가
    # 쓰는 접미사까지 넣으면 `가득히`의 `득히`에 `그득히` 같은 남남이 걸린다.
    echo_terms = komantle.echo_terms(answer)
    echo_ranks = [
        i for i, (_, w, _) in enumerate(judge_sample[:hints.SAMPLE_N], 1)
        if any(t in w for t in echo_terms)
    ]
    if len(echo_ranks) > 1:
        print(f"[할인] 정답 어간이 든 이웃 {echo_ranks} → 가장 높은 순위 하나만 남긴다")

    # 역방향 신호. LLM 이 고른 탐색어들이 실제 1000개 목록 몇 위에 걸리는지 조회한다.
    # 순위 조회는 결정론적이다 — LLM 은 '무엇을 쳐볼까'만 고른다.
    rank_of = {w: r for r, w, _ in judge_sample}
    # 어간 필터와 중복 제거로 깎여서 SEARCH_MIN 을 못 채우면 빈 목록을 넘긴다.
    # judge 는 그걸 '막힌 경로 0점'이 아니라 '미측정'으로 읽어 캡을 아예 안 건다.
    if len(probe_words) < hints.SEARCH_MIN:
        if probe_words:
            print(f"[경로] 탐색어 {len(probe_words)}개뿐 "
                  f"(최소 {hints.SEARCH_MIN}) → 경로 미측정", file=sys.stderr)
        probe_words = []
    probe_ranks = [rank_of.get(w) for w in probe_words]
    if probe_words:
        hit = [f"{w}({r})" if r else f"{w}(밖)" for w, r in zip(probe_words, probe_ranks)]
        print(f"[경로] {' '.join(hit)}")

    v = verdict.judge(
        first_score,
        signals.get("fairness"),
        signals.get("matched_ranks"),
        echo_ranks,
        signals.get("probe"),
        probe_ranks,
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
        # 어느 자로 잰 기록인지. 이게 없으면 옛 공식과 새 공식의 점수가 한 폴더에
        # 섞인 채 구분이 안 된다. 모델은 MODELS 를 순서대로 도느라 날마다 다를 수 있다.
        "formula_version": verdict.FORMULA_VERSION,
        "prompt_version": hints.PROMPT_VERSION,
        "model": signals.get("model"),
        "answer_b64": ctx["answer_b64"],
        "first_score": first_score,
        "fairness": v["fairness"],
        "probe": v["probe"],
        "q_reach": v["q_reach"],
        "q_path": v["q_path"],
        "probe_ranks": v["probe_ranks"],
        # 몇 개로 잰 q_path 인지. 적을수록 한 단어의 무게가 커져 분산이 커진다.
        "probe_n": len(probe_words),
        # 탐색어는 정답을 좁히는 목록이라 평문으로 두면 스포일러다.
        "probe_words_b64": _b64(",".join(probe_words)),
        "broken": v["broken"],
        "semantic_match": v["semantic_match"],
        "semantic_match_eff": v["semantic_match_eff"],
        # 순위까지 남긴다. 다음 보정은 하루 체감이 아니라 이 기록으로 한다.
        # 정답을 설명하는 한국어가 아니라 숫자라 base64 로 감싸지 않는다.
        "matched_ranks": v["matched_ranks"],
        # 통하는 단어 중 그것만 보고도 정답을 좁힐 수 있는 것. 채점에 안 쓴다 —
        # 느슨한 연관과 같은 무게로 세는 게 맞는지 나중에 이 기록으로 검증한다.
        "direct_ranks": signals.get("direct_ranks"),
        "kept_ranks": v["kept_ranks"],
        "echo_ranks": echo_ranks,
        "self_echo": v["self_echo"],
        # 품사는 보정용 기록. "부사 날이 체계적으로 후하게 나오는가" 를 나중에 보려면
        # 쌓여 있어야 한다. 힌트 카드 1장이라 정답과 같이 base64 로 넣는다.
        "pos_b64": _b64(llm_cards.get("pos", "")),
        "q_match": v["q_match"],
        "weighted_ratio": v["weighted_ratio"],
        "q_word": v["q_word"],
        "playable": v["playable"],
        "grade": v["grade"],
        "weakest": v["weakest"],
        "reduced": v["reduced"],
        "dead_cards": dead,
        # 실측 결과. outcomes.json 에 사람이 적어 넣으면 여기로 딸려 들어온다.
        "outcome": read_outcome(puzzle_id),
        # 채점 근거는 보정용으로만 남긴다. 정답의 분야를 그대로 말하는 문장이라
        # 공개 파일에 평문으로 두면 그 자체가 스포일러다. 정답과 같은 취급을 한다.
        "fairness_reason_b64": _b64(signals.get("fairness_reason", "")),
        "probe_reason_b64": _b64(signals.get("probe_reason", "")),
        "match_reason_b64": _b64(signals.get("match_reason", "")),
        "built_at": komantle.now_kst().isoformat(timespec="seconds"),
    }
    with open(os.path.join(out_dir, "data", f"{puzzle_id}.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    write_archive(puzzle_id, puz_date, answer, judge_sample)

    render(out_dir, ctx, read_archive(out_dir, puzzle_id, today))
    print(f"[완료] {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
