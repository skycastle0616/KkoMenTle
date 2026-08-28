# 꼬맨틀 알리미

[꼬맨틀](https://semantle-ko.newsjel.ly)은 날에 따라 문제 자체가 망가진다. 단어가 지나치게
생소하거나, 유사도 점수가 정답의 실제 뜻과 어긋나서 아무리 잘 추측해도 도달할 수 없는 날이 있다.

**이 페이지는 그날 문제가 풀 만한지 판별해서 크게 알려준다.** 힌트는 곁들이는 기능이다.

> 답을 찾아가는 과정이 오래 걸리는 건 난이도이고 그 자체가 게임의 재미다.
> 걸러내야 하는 건 결함이 있는 날이다.

공개 URL — <https://skycastle0616.github.io/KkoMenTle/>

## 판별 로직

축이 둘이다.

| 축 | 잡아내는 실패 | 산출 |
|---|---|---|
| `q_word` | 단어를 후보로 떠올릴 수 없는 날 | LLM `familiarity` |
| `q_match` | 유사도가 뜻을 따라가지 못하는 날 | LLM `semantic_match` + 1위 단어 점수 |

```
q_match  = 0.65 * (semantic_match / 16) + 0.35 * norm(first_score, 35, 70)
q_word   = familiarity
playable = 100 * min(q_match, q_word)
```

**합산이 아니라 `min()`이다.** 예를 들어 `속이다`는 흔한 말이라 `familiarity`가 1.0인데,
FastText가 이 단어를 `속(屬, 생물 분류) + 이다`로 쪼개 읽어서 유사어가 전부 생물 분류 용어다.
합산하면 `q_word`에서 점수를 그냥 먹고 등급이 올라가버린다. 둘 중 하나만 망가져도 추천하면
안 되므로 약한 고리가 점수를 결정해야 한다.

| 점수 | 배지 | 헤드라인 |
|---|---|---|
| 78+ | 🟢 | 오늘은 할 만합니다 |
| 55~77 | 🟡 | 조금 까다롭습니다 |
| 32~54 | 🟠 | 각오하고 들어가세요 |
| ~31 | 🔴 | 오늘은 건너뛰세요 |

부제는 더 약한 축을 그대로 말한다 — "유사도가 정답의 뜻을 배신하는 날입니다" 또는
"단어 자체를 떠올리기 어려운 날입니다".

임계값과 가중치는 초기값이다. `docs/data/*.json`에 원자료가 매일 쌓이므로 2주쯤 뒤 체감과
맞춰 보정한다.

## 구조

```
komantle.py   꼬맨틀 API 클라이언트, 유사어 필터, 초성
verdict.py    판별 로직 (q_match / q_word / min / 등급 / 근거 문장)
hints.py      Gemini 호출 + 프롬프트 + 누출 가드
build.py      파이프라인 + Jinja2 렌더
templates/    index.html, archive.html
static/       style.css, app.js
docs/         빌드 산출물 = GitHub Pages 소스 (커밋됨)
```

## 실행

```bash
pip install -r requirements.txt
cp .env.example .env        # GEMINI_API_KEY 를 채운다

python build.py                              # 오늘 회차 → preview/
python build.py --out docs                   # 배포용
python build.py --puzzle-id 1611 --out preview   # 특정 회차
python build.py --no-llm --out preview           # 키 없이 (규칙 카드 4장만)
```

꼬맨틀 API는 **어제·오늘·내일 3일치만** 열어준다. 과거 회차는 조회할 수 없어서 기록은
오늘부터 하루씩 쌓인다.

`--puzzle-id` 를 주지 않으면 회차 `timestamp`를 KST로 바꿔 오늘과 맞는지 검사하고,
어긋나면 종료 코드 1로 중단한다. 어제 정답을 오늘 배포하는 사고를 막는 장치다.

## LLM이 죽어도 페이지는 나온다

Gemini 호출이 실패하면 판정은 1위 단어 점수만으로 축소하고, LLM 카드 7장은 비활성으로
렌더한다. 규칙 카드 4장(글자 수 · 유사어 ① · 유사어 ② · 초성)은 API 없이 항상 살아 있다.

## 누출 가드

LLM이 만든 모든 문자열을 검사한다.

1. 정답 문자열 포함 → 거부
2. 정답의 길이 2 이상 접두사 포함 → 거부 (`속이다` → `속이` 금지)
   접미사는 정답이 `다`로 끝나지 않을 때만 본다. `이다`까지 막으면 평범한 문장이 전부 걸린다.
3. 빈칸 예문의 `○` 개수 ≠ 정답 글자 수 → 거부

위반하면 한 번 재요청하고, 그래도 실패한 카드만 비활성으로 넘긴다.

유사어 카드는 `정답 in 단어 or 단어 in 정답`인 단어를 걷어낸다. 정답이 `위원장`일 때
상위 80위 안의 `부위원장 · 위원 · 상임위원장 · 원장`이 여기서 걸린다.

## 배포

- `.github/workflows/daily.yml` — cron `5 15 * * *` (UTC) = KST 00:05, `workflow_dispatch` 가능
- GitHub Pages — Settings → Pages → Source: `main` 브랜치 `/docs`
- `GEMINI_API_KEY` — Settings → Secrets and variables → Actions 에 등록.
  **리포가 공개이므로 소스·YAML·README·커밋 메시지 어디에도 문자열로 넣지 않는다.**
  로컬은 `.env`(gitignore)를 쓴다.

카카오톡은 링크 미리보기를 캐시한다. 갱신이 필요하면 공유 URL 뒤에 `?d=회차번호`를 붙인다.

## 안 하는 것

- **카카오톡 자동 발송** — 오픈채팅방에 봇으로 메시지를 보내는 공식 API가 없다
  ([카카오 데브톡 확답](https://devtalk.kakao.com/t/api/149749)). 고정 URL 하나를 공지로 박아두는 방식이다.
- **프로브 방식 판별** — "사람이 흔히 찍는 단어가 몇 위에 걸리는가"는 도달 속도(=난이도)를
  재는 것이지 결함을 재는 게 아니다.
- **과거 회차 백필** — API가 3일치만 열어준다.
