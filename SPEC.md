# 콘텐츠 요약 → Notion 봇 — 스펙

개인용 도구. 모바일 공유 시트 또는 데스크톱 웹 클리퍼로 넘긴 콘텐츠를 요약해 Notion 데이터베이스에 쌓는다.

배경과 기술 선택 근거는 [DECISIONS.md](DECISIONS.md) 참고.

## 아키텍처

진입점이 둘, 워커는 하나다.

```
텔레그램 봇 ──웹훅──┐
                     ├──> Lambda 워커 ──> Gemini ──> 콘텐츠 아카이브
Notion 클리퍼 ─빈 행─┘        ↑
                             크론(하루 1회)
```

- **텔레그램 경로** — 봇에게 보낸 메시지를 텔레그램이 Function URL로 밀어준다. 한 건을 즉시 처리해 완성된 행을 만들고, 봇이 결과를 답장으로 보낸다.
- **클리퍼 경로** — Lambda를 거치지 않는다. 본문과 URL만 담긴 행이 노션에 생기고 `one_liner`는 빈 상태로 남는다.
- **크론** — 하루 1회 Lambda를 깨워 빈 행을 배치로 채운다.

두 경로가 만나는 지점은 Notion DB 자체다. 워커 로직은 "빈 행을 받아 채운다" 하나뿐이고 진입점만 다르다.

### 이 구조에서 공짜로 얻는 것

- **Notion DB가 대기열이다.** `one_liner`가 빈 행을 필터로 조회하면 그게 처리 대기 목록. SQS가 필요 없다.
- **멱등성이 공짜다.** 채워지면 다음 배치에서 안 잡힌다. 중복 방지용 DynamoDB가 필요 없다.

## 구성 요소

| 항목 | 선택 |
|---|---|
| 런타임 | Python 3.12, Lambda 단일 함수 `event-scraper` (스택·SSM 접두사 동일) |
| 진입점 1 | 텔레그램 봇 웹훅 → Lambda Function URL (auth NONE) |
| 진입점 2 | EventBridge 규칙, 하루 1회 |
| 모델 | 영상: Gemini API (`GeminiModel` 쉼표 목록, 앞 모델이 한도에 걸리면 다음 모델) / 텍스트·아티클·링크 검색: Claude API (`claude-haiku-5-5`, `web_fetch`·`web_search`). 둘 다 키 직접 호출 |
| 시크릿 | SSM Parameter Store SecureString |
| 배포 | AWS SAM |
| 관측 | CloudWatch Logs |

LLM 호출은 `summarize(payload) -> dict` 한 함수에 격리한다. 프로바이더를 갈아끼울 수 있어야 한다.

## 진입점: 텔레그램 봇

### 셋업

1. BotFather에게 `/newbot` → 토큰 발급
2. 웹훅 등록:

```
POST https://api.telegram.org/bot<TOKEN>/setWebhook
{
  "url": "https://xxx.lambda-url.ap-northeast-2.on.aws/",
  "secret_token": "<랜덤 시크릿>"
}
```

텔레그램이 매 요청에 `X-Telegram-Bot-Api-Secret-Token` 헤더를 붙여 보낸다.

### 인증 (2단계)

1. `X-Telegram-Bot-Api-Secret-Token`을 `hmac.compare_digest`로 검증
2. `message.chat.id`가 본인 chat_id와 일치하는지 확인

두 번째가 실질적인 방어선이다. 봇 주소가 노출돼도 다른 사람의 메시지는 무시된다.

### 수신 페이로드

텔레그램 Update 객체에서 다음만 추출한다.

| 입력 | 추출 |
|---|---|
| URL 텍스트 | `message.text` → URL 파싱 |
| URL + 설명 | `message.text` → URL은 `url`, 나머지 줄은 `memo` |
| 영상 파일 (+ 캡션) | `message.video` 또는 `video/*` 문서 → `getFile` 다운로드(20MB 한도) → Gemini Files API. 캡션의 URL은 `url`, 나머지는 `memo` |
| 사진 | 무시. "URL이나 영상을 보내줘" 답장 |

**유튜브인지 아티클인지는 판별하지 않는다** — URL 호스트를 보고 Lambda가 정한다.

메모를 별도 입력창 없이 링크 뒤에 이어 쓸 수 있는 게 이 방식의 이점이다.

### 응답

즉시 200을 반환한다(텔레그램은 200이 늦으면 재전송한다). 처리 완료 후 `sendMessage`로 답장한다.

- 성공: 한 줄 요약 + 노션 페이지 링크
- 실패: 실패 단계와 사유

## Lambda 동작

핸들러는 이벤트 소스로 분기한다.

**Function URL 이벤트 (텔레그램 경로)**
1. 시크릿 토큰 헤더 검증 → `chat_id` 검증. 불일치 시 200을 반환하고 무시(텔레그램 재전송 방지)
2. Update를 자기 자신에게 `InvocationType='Event'`로 전달하고 즉시 200 반환
3. 비동기 실행에서 페이지를 만들고 `process_row`
4. 끝나면 `sendMessage`로 답장

**EventBridge 이벤트 (배치 경로)**
1. `one_liner`가 비고 `status ≠ 실패`인 행을 조회 (실패 행은 사람이 `status`를 `미확인`으로 되돌려야 재시도)
2. 각 행에 `process_row`
3. 건당 실패는 그 행만 `status: 실패`로 두고 나머지 계속

**공통 초기화 (콜드 스타트만)**
- SSM에서 파라미터 일괄 조회 후 모듈 전역 캐시
- `GET /v1/databases/{id}`로 `data_sources[0].id`와 기존 `tags` 옵션 캐시. `summary` 프로퍼티가 없으면 이때 추가

**process_row**

```python
def process_row(page):
    url  = page.properties["url"]
    body = fetch_page_body(page.id)     # 클리퍼가 넣은 본문

    if video:                           # 사용자가 보낸 영상 파일 (다운로드한 릴스 등)
        result = gemini(file_data=upload(video)) + claude_links(result.tools)
    elif is_instagram(url) and len(memo) < 30:
        mark_saved_only(page); return   # 링크로는 못 읽음 — 환각 방지
    elif is_youtube(url):
        result = gemini(file_data={"file_uri": watch_url(url)}) + claude_links(result.tools)
    elif body:                          # 클리퍼 경로: 본문이 이미 있음
        result = claude(text=body, tools=[web_search])
    elif is_instagram(url):             # 캡션을 메모로 붙여넣은 경우
        result = claude(text=memo, tools=[web_search])
    elif url:                           # 텔레그램 경로: URL만 옴
        result = claude(url=url, tools=[web_fetch, web_search])   # Claude가 직접 읽음
        # 못 읽으면(JS 껍데기): claude(text=jina(url)) 한 번 더, 그래도 못 읽으면 mark_saved_only
    else:
        mark_failed(page); return

    ensure_tag_options(result["tags"])  # 없는 옵션은 스키마에 먼저 추가
    update_properties(page, result)
```

유튜브(주소는 `watch?v=ID`로 정규화 — `m.youtube.com`은 Gemini가 거부)와 영상 파일은 Gemini, 클리퍼 본문·캡션·URL은 Claude가 읽는다. Lambda는 HTML을 직접 받지 않는다. JS 렌더 페이지(남의 Notion 공개 페이지 등)는 Jina Reader(`r.jina.ai`)가 렌더한 텍스트로 한 번 더 읽는다. 본문 고정 순서: `## 요약 / ## 핵심 / ## 해볼 것 / ## 링크 / ## 원문`. 링크는 검색·fetch 결과에 실제로 나온 URL만.

어떤 예외든 잡아서 `status: 실패`로 두고 에러 메시지를 본문에 넣는다. 조용한 실패 금지.

## 출력 스키마

`responseMimeType: "application/json"` + `responseSchema`로 고정한다.

```json
{
  "title": "string",
  "one_liner": "string",
  "summary": "string",
  "key_points": ["string"],
  "category": "string",
  "type": "string",
  "tags": ["string"],
  "author": "string"
}
```

`category`와 `type`은 아래 고정 옵션 중에서만 고른다. `tags`는 프롬프트에 기존 태그 목록을 주입하고 "여기서 우선 고르고, 없으면 신규 최대 1개, 총 2~4개"로 제약한다.

## Notion

**API 버전은 `2025-09-03` 고정.** 이 버전부터 `database_id`를 쓰던 대부분의 작업이 `data_source_id`를 요구하며 하위 호환되지 않는다.

- 페이지: `[Cloud] 콘텐츠 요약 봇` — `3d1a3e9a-6fbc-81fa-aa5c-ee962e1af1e0`
- 데이터베이스: `콘텐츠 아카이브` — `17bdf5a5-eb8e-4200-8f07-6b0be21b24b5`
- data source: `efe7d056-5c9a-4a4f-9f98-8115c915d973`

data source ID는 하드코딩하지 말고 콜드 스타트에 조회해 캐시한다. 위 값은 검증용 참고치다.

페이지 생성 시 부모는 반드시 `{ "parent": { "type": "data_source_id", "data_source_id": "..." } }`.

### 프로퍼티

| 이름 | 타입 | 옵션 |
|---|---|---|
| `title` | title | |
| `source` | select | youtube / instagram / linkedin / article / other |
| `url` | url | |
| `author` | text | |
| `category` | select | AI·LLM / Cloud·인프라 / 에이전트 / 개발도구 / 크리에이티브 / 비즈니스·전략 / 학습·기타 |
| `type` | select | 뉴스 / Repo / 논문 / 블로그·에세이 / 서비스·툴 |
| `tags` | multi_select | 사용하며 증가 |
| `one_liner` | text | **빈 값 = 미처리. 이게 대기열 조건이다** |
| `summary` | text | 3~5문장 요약. 본문에도 요약·key_points 블록을 붙인다 |
| `memo` | text | 사람만 쓴다. 워커가 덮어쓰지 않는다 |
| `status` | select | 미확인 / 읽음 / 실패 |
| `saved_at` | date | |

`status`는 항상 `미확인`, `saved_at`은 처리 시각.

AI가 쓰는 필드와 사람이 쓰는 필드를 섞지 않는다. 재처리 시 `one_liner`는 덮어써도 되지만 `memo`는 건드리지 않는다.

`category`와 `type` 옵션은 기존 `[Cowork] AI, Cloud News` 데이터베이스와 동일하다. 두 DB는 데이터를 합치지 않되 분류 체계만 공유한다.

### 태그 함정

`multi_select`에 없는 옵션을 넣으면 400이 난다. `ensure_tag_options()`로 페이지 갱신 **전에** 데이터 소스 스키마를 수정해야 한다. 실측으로 확인된 동작이다.

## SSM 파라미터

- `/event-scraper/gemini-api-key`
- `/event-scraper/notion-token`
- `/event-scraper/telegram-bot-token`
- `/event-scraper/telegram-secret-token`
- `/event-scraper/telegram-chat-id`
- `/event-scraper/notion-database-id`
- `/event-scraper/anthropic-api-key`

## 셋업 시 주의

Notion 통합은 notion.so/my-integrations에서 **연결(connection)** 로 생성한다. 개인 액세스 토큰이 아니라 연결이어야 페이지의 연결 메뉴에 나타난다. 토큰을 만든 뒤 위 데이터베이스 페이지에서 해당 통합을 직접 추가해야 한다. 이 단계를 빼먹으면 모든 호출이 404다.

## 구현 순서

1. **Lambda + Function URL + 텔레그램 봇.** 봇에 유튜브 URL 하나 보내서 Gemini 요약 → 노션 행 생성 → 답장까지. 이게 되면 핵심은 끝
2. **클리퍼 경로 확인.** 데스크톱 클리핑 시 빈 행이 생기는지 (확인 완료)
3. **크론 추가.** 빈 행 배치 처리
4. 2주 사용 후 「읽을 것」 뷰에서 `one_liner` 품질 검토

실패 경로를 먼저 테스트한다. 일부러 잘못된 URL을 보내 `status: 실패` 페이지가 생기는지 확인할 것.

## 성공 기준

공유 시트에서 텔레그램 선택 후 전송, 30초 내 노션에 완성된 행과 봇 답장. 클리퍼로 들어온 행은 늦어도 다음 날 채워진다. 실패는 항상 눈에 보인다.
