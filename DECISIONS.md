# 콘텐츠 요약 → Notion 봇 — 결정 기록

이 문서는 "왜 이렇게 정했는가"만 담는다. 무엇을 어떻게 만드는지는 [SPEC.md](SPEC.md).

## 왜 직접 만드는가 (기성품 검토 결과)

- **Save to Notion** — 브라우저 확장. 인스타·링크드인 앱에서 공유 불가.
- **Recall** — AI 요약은 우수하나 Notion 통합 없음(공식 피드백 보드 확인). 내보내기는 zip 마크다운뿐.
- **Readwise Reader** — Notion 동기화는 되지만 하이라이트 기준이고 Readwise가 정한 구조.
- **Notion Web Clipper** — 본문과 URL만 저장. Summary·Tags·Author 프로퍼티를 채우지 않는다. 그래서 이 프로젝트는 클리퍼를 **캡처 계층으로 활용하고 가공만 담당**한다.
- **Notion AI Autofill** — Business·Enterprise 플랜 + AI 애드온 필요. 현재 워크스페이스는 해당 없음.
- **Obsidian Web Clipper + Interpreter** — 캡처 성능은 가장 우수(자막·이미지·LLM 분류). 단 목적지가 볼트라 서버가 못 읽고, 승격이 아니라 역방향 이관이 생긴다. 채택하지 않음.

## 제약과 결정 (이미 검증됨 — 다시 논의하지 말 것)

- **YouTube 자막을 서버에서 직접 못 가져온다.** AWS/GCP/Azure IP 대역이 차단된다. `youtube-transcript-api`, `yt-dlp` 계열은 Lambda에서 RequestBlocked. 프록시로 우회하지 않는다.
- **공식 Captions API도 안 된다.** 채널 소유자의 OAuth를 요구한다.
- **해결책은 Gemini API의 YouTube URL 입력.** 공개 영상 URL을 비디오 파트로 그대로 넘긴다. Shorts 포함. 자막 추출이 아니라 영상 이해다. 현재 프리뷰·무과금이나 가격과 한도는 변경 예정.
- **Instagram은 URL로 특정할 수 없다.** robots.txt 자동화 차단, 로그인 월, 검색엔진 색인 없음. 브라우저 클리퍼로도 안 된다 — 릴스 뷰어가 무한 스크롤 SPA라 DOM에 스크롤 이력 전체가 남아 한 건을 지정할 수 없다(3회 실측: 6건 → 9건 → 20건). **범위 밖. 추후 확장 대상.**
- **LinkedIn은 미검증.** 게시물마다 고정 URL이 있어 가능성은 있으나 확인 전.
- **Claude 구독으로는 API 호출 불가.** Pro/Max는 챗 전용, API는 별도 Console 결제. ~~프로바이더는 Gemini 하나로 통일한다.~~ → 2026-10-30 분리(아래).
- **영상은 Gemini, 텍스트·아티클·링크 검색은 Claude.** (2026-10-30) Claude API는 영상·유튜브 URL 입력이 없어 영상은 Gemini만 가능. 반대로 Gemini 2.5는 도구(`url_context`·`google_search`)와 JSON 스키마를 한 호출에 못 쓴다(`Tool use with a response mime type: 'application/json' is unsupported`, 실측) — 아티클 경로가 이 때문에 깨져 있었다. Claude는 서버 도구(`web_fetch`·`web_search`) + strict 클라이언트 도구로 구조화 출력이 된다. 모델은 `claude-haiku-5-5`(`ClaudeModel` 파라미터), 호출은 기존 urllib `request()` 재사용(의존성 0 유지).
- **링크는 실제로 가져온 내용에 나온 URL만 남긴다.** Gemini에 영상만 주고 링크를 물으면 검색 없이 URL을 지어냈다(근거 0건, 실측). 영상 경로: Gemini가 도구 이름(`tools`)만 뽑고 → Claude `web_search`가 링크를 찾고 → 입력·검색·fetch 결과 문자열에 없는 URL은 버린다. 링크 검색 실패는 요약을 버리지 않고 본문에 `검색 실패`로 남긴다.
- **Gemini는 무료 등급 유지.** 무료 한도 하루 20회(`generate_content_free_tier_requests`, 실측). 같은 프로젝트의 키는 한도를 공유하고, 프로젝트를 여러 개 돌리는 건 약관상 한도 우회라 하지 않는다. Gemini를 영상 전용으로 줄여 하루 영상 ~20개면 충분. 모자라면 결제 연결(Tier 1).
- **Gemini 한도는 모델별이다 — 모델을 순서대로 쓴다.** (2026-10-31 실측) 429 본문에 `limit: 20, model: gemini-2.5-flash`가 찍힌다. `GEMINI_MODEL`을 쉼표 목록(`gemini-3.8-flash,gemini-2.5-flash,gemini-3.5-flash-lite`)으로 받아 앞 모델이 실패하면 다음 모델로 넘긴다. 같은 프로젝트·같은 키 안이라 한도 우회가 아니다. 3.8 Flash와 3.5 Flash-Lite 둘 다 유튜브 `file_data` + `responseSchema`를 받는 것을 확인했다. 전부 실패하면 기존 Claude 설명 기반 대체.
- **Notion `multi_select`는 없는 옵션을 거절한다.** 새 태그는 데이터 소스 스키마 수정이 선행돼야 한다. (실측 확인)
- **아티클은 LLM이 직접 읽는다** (2026-10-30부터 Claude `web_fetch`, 이전 Gemini `url_context`). Lambda가 HTML을 받아 파싱하는 안은 기각 — 파싱 코드가 늘고 봇 차단 사이트는 어차피 실패한다. 못 읽는 사이트도 우회하지 않고 `실패`로 남긴다.
- **사진 입력은 받지 않는다.** 사진이 오면 "URL이나 영상을 보내줘"로 답한다. (2026-09-04 결정)
- **인스타 릴스: 링크는 저장만, 영상 파일을 보내면 분석.** (2026-10-30 실측) Gemini `url_context`는 `URL_RETRIEVAL_STATUS_ERROR`, 영상 URL 입력은 유튜브 전용이라 `Unsupported MIME type: text/html`. 비로그인 공개·임베드 페이지도 JS 껍데기만 와서 캡션·영상 주소가 없다. 링크만으로 요약시키면 환각이므로 Gemini를 부르지 않는다. 대신 사용자가 릴스를 다운로드해 텔레그램으로 보내면 Gemini Files API로 영상+음성을 분석한다(파일은 48시간 후 자동 삭제, 보관 안 함).
- **유료 스크래핑 API(ScrapeCreators·Apify)는 붙이지 않는다.** 링크만으로 자동화되지만 서드파티 키·약관 회색지대·예고 없는 차단 위험. 영상 파일 보내기로 충분하다고 판단. (2026-10-30)
- **LLM 호출은 일시 오류에 재시도한다.** 2.5 Flash가 `503 high demand`를 간헐적으로 반환한다(실측). 500·503·529·타임아웃만 5초·20초 간격으로 최대 3회. Claude도 같은 함수. 429는 재시도하지 않는다 — Gemini 무료 등급은 일일 한도에도 429를 주므로 기다려도 소용없다.
- **Gemini가 막히면 유튜브는 Claude로 넘긴다.** (2026-10-30 사용자 요청) Claude는 영상을 못 보므로 `web_fetch`로 페이지 제목·설명을 읽고 `web_search`로 보충한다(실측: 설명란 포함 24k자 수신). 영상을 안 본 요약이므로 `one_liner`에 `[설명 기반]`, 요약 앞에 사유를 붙인다. 업로드 영상 파일은 Claude가 못 읽고 파일도 보관하지 않으므로 실패로 알리고 다시 보내 달라고 답한다.
- **원문을 못 읽으면 실패가 아니라 저장만.** 인스타와 같은 규칙을 모든 출처에 적용. Claude가 `save_result` 대신 이유를 답하면(`Unreadable`) `one_liner = 내용 미확인 · 이유`. 실측: 남의 Notion 공개 페이지는 JS 렌더라 `web_fetch`에 랜딩 메타데이터만 오고, Notion API는 워크스페이스 밖이라 404.
- **JS 렌더 공개 페이지는 Jina Reader로 한 번 더 읽는다.** (2026-10-31 실측, 사용자 허용) 아카이브의 `Mods 3` 공개 페이지에 Claude `web_fetch`는 랜딩 메타데이터만, Gemini `url_context`는 `URL_RETRIEVAL_STATUS_SUCCESS`인데 본문 없음, `https://r.jina.ai/<url>`은 본문 전체를 돌려줬다. Claude `web_fetch`에 jina URL을 주면 `invalid_tool_input`이라 Lambda가 직접 받아 `{"text"}`로 넘긴다. Claude가 `Unreadable`을 낸 `{"url"}` 경로에서만 한 번 더 시도하고, 그래도 못 읽으면 저장만. 키 없이 20회/분. 로그인·봇 차단 페이지는 Jina도 못 읽으므로(인스타·링크드인) 우회가 아니다. 429가 보이면 무료 키(10M 토큰)를 SSM `jina-api-key`로 추가한다.
- **공유 추적 파라미터는 받을 때 뗀다.** `fbclid` 하나로 URL이 길어져 `web_fetch`가 `url_too_long`으로 거부했다(실측). `fbclid`·`gclid`·`igsh`·`si`·`utm_*` 등. `web_fetch`는 재시도 1회 허용(`max_uses: 2`).
- **실패 행은 자동 재시도하지 않는다.** 배치는 `status ≠ 실패`만 집는다. 그대로 두면 깨진 URL이 매일 Gemini에 간다. 재처리는 사람이 `status`를 `미확인`으로 되돌린다.
- **리소스 이름은 `event-scraper`로 통일.** 스택·함수·SSM 접두사. 텔레그램 봇 이름(`*_event_scraper_bot`)과 맞춘다. (2026-10-30 `agent-web`에서 변경. 스택은 이름을 못 바꿔 새로 배포하고 구 스택 삭제)

## 왜 텔레그램인가

- Saved Messages(나에게 보내기)가 기본 제공 — 서버도 채널도 만들 필요 없음
- BotFather로 봇 생성 3분, 승인 절차 없음
- 웹훅 등록이 API 호출 한 번
- 모바일 공유 시트에 이미 존재 (인스타·유튜브에서 바로 공유 가능)
- 이미지가 그대로 전달됨 — base64 인코딩을 클라이언트에서 할 필요 없음
- 처리 결과를 답장으로 돌려줄 수 있음 (성공/실패 모두)

OpenClaw 가이드에서도 채널 연결 중 텔레그램이 초보자 추천으로 꼽힌다.

## 하지 말 것

현 단계에서 명시적으로 범위 밖. 제안하지도, 추가하지도 말 것.

- Step Functions — 스텝이 하나뿐
- SQS — Notion DB가 대기열 역할을 한다
- DynamoDB — 멱등성이 구조적으로 보장된다
- API Gateway — Function URL로 대체
- VPC, ECS/Fargate, RDS, S3
- Secrets Manager — SSM Parameter Store SecureString을 쓴다
- 프록시, 스크래퍼, 헤드리스 브라우저 (예외: Jina Reader 2차 경로 — 위 「JS 렌더 공개 페이지」 항목)
- Instagram 처리 (추후 확장)
- 원본 영상 다운로드·보관
- 테스트 커버리지 목표, 다중 환경 분리

EventBridge 규칙 1개는 예외적으로 사용한다. 클리퍼 경로에 필요하다.

## 확장 경로 (지금은 범위 밖)

**OpenClaw** — 셀프 호스팅 게이트웨이. 텔레그램·디스코드·왓츠앱 등을 AI 에이전트에 연결하고, 하나의 게이트웨이 프로세스에서 목적별로 격리된 멀티 에이전트를 운영한다. 페르소나는 `SOUL.md`·`AGENTS.md`로 정의하고 `memory/`로 기억을 관리한다.

지금 도입하지 않는 이유:

- 게이트웨이 프로세스가 24/7 상주해야 한다. Lambda의 무상주 이점과 정반대
- 에이전트가 파일·터미널·브라우저 제어 권한을 갖는다. 인스타 캡션의 프롬프트 인젝션이 실제로 위험해지는 환경
- 목표가 콘텐츠 아카이브 하나면 과하다

**이 선택이 나중을 막지 않는다.** 텔레그램 봇 토큰은 그대로 쓰고 채널만 다시 연결하면 된다. 오픈클로가 맞는 시점은 시킬 일이 여러 개가 됐을 때다.
