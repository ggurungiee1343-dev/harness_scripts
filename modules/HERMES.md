# 🤖 HERMES.md — modules/ 디렉토리 아키텍처 규칙 (v3.9.1)

이 파일은 `hermes_context_builder.py`에 의해 헤르메스 에이전트가 이 폴더를 탐색하거나 파일을 읽을 때 **자동으로 로드되는 가이드라인**입니다.  
이 파일의 규칙은 헤르메스의 **구조적 장기기억(Architectural Long-term Memory)**으로 기능합니다.

---

## 📌 이 디렉토리의 역할

`modules/`는 `harness_agent.py`가 에이전틱 루프를 실행할 때 호출하는 **전문 기능 모듈들의 모음**입니다.  
모든 모듈은 독립적인 클래스(Class) 단위로 설계되어 있으며, `harness_agent.py`에서 인스턴스화하여 사용합니다.

---

## 📋 현재 등록된 모듈 목록 (2026-05-21 기준)

| 모듈 파일 | 역할 |
|:---|:---|
| `wiki_manager.py` | 위키 컨텍스트 로딩 및 LLM 제공 |
| `history_manager.py` | 대화 이력 (`harness_memory.json`) 관리 |
| `file_manager.py` | 파일 생성/읽기/이동/삭제 |
| `bio_memory_engine.py` | L1/L2/L3 인지 메모리 엔진 (에빙하우스 망각+연상망) |
| `ingest_engine.py` | Clippings → 위키 자동 분류 이관 |
| `system_monitor.py` | 하드웨어 감시 및 자가 치유 |
| `news_engine.py` | 뉴스 수집 |
| `git_manager.py` | **[★ v3.9 복구 완료]** Git 저장소 관리 (Add/Commit/Push/Pull) |
| `executor.py` | Bash 실행 + 자율 에러 복구 + Skill 캐싱 |
| `cognitive_engine.py` | 고수준 추론 보조 |
| `web_reader.py` | URL 본문 추출 및 요약 |
| `audit_engine.py` | 작업 감사 로그 |
| `optimistic_response.py` | 🆕 낙관적 응답 엔진 (Linear 패턴: 즉시 피드백 → 백그라운드 처리 → 결과 편집) |
| `model_scanner.py` | LLM 모델 상태 스캔 |
| `hermes_context_builder.py` | **코드베이스 컨텍스트 하네스** (v3.9 비대화 방지 알고리즘 탑재) |

---

## 🏛️ 코딩 규칙 (Coding Rules)

### 규칙 1: 반드시 Class 기반으로 작성할 것
```python
# ✅ 올바른 방식
class NewModule:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

# ❌ 금지: 전역 함수만으로 구성된 모듈
def some_function():
    pass
```

### 규칙 2: `__init__`에는 반드시 `workspace_root` 인자를 받을 것
- 헤르메스의 작업 공간 경로를 모듈에 주입하여, 모듈이 스스로 올바른 경로를 찾을 수 있어야 합니다.

### 규칙 3: 신규 모듈 추가 시 `harness_agent.py` 임포트 구역에 등록할 것
```python
from modules import (
    ...,
    new_module_name   # ← 여기에 추가
)
```

### 규칙 4: 파일명은 `snake_case`로, 확장자는 `.py`로 고정
- 예: `web_agent_module.py`, `hermes_context_builder.py`

### 규칙 5: HERMES.md 비대화 방지 및 슬림화 가이드
- 헤르메스 규칙이 추가됨에 따라 HERMES.md의 용량이 커지더라도, `hermes_context_builder.py`가 자동으로 `_slim_context()` 필터를 돌려 핵심 리스트 및 헤더만 4000자 이내로 파싱하여 LLM에 전달합니다. 따라서 설명 작성 시 규칙은 반드시 `-` 이나 `*` 리스트 기호로 시작하도록 정형화하십시오.

### 규칙 6: 버전 관리는 `git_manager.py`에 위임할 것
- 위키 또는 코드베이스의 변경 사항을 커밋하거나 동기화(Pull/Push)할 때는 하드코딩된 subprocess 호출 대신 반드시 `git_manager.py` 모듈의 `GitManager`를 호출하여 표준화된 방식으로 이력을 관리하십시오.

### 규칙 7: 에이전틱 루프 설계 및 파일 쓰기(SAVE) 가이드
- 여러 도구 태그를 연쇄적으로 사용하는 에이전틱 루프는 최대 3회로 통제하며, `[SAVE]` 태그를 처리할 때는 경로가 절대 경로인지(Scripts 등 백엔드 작업) 혹은 보관소 내의 상대 경로인지(inbox, wiki 등)를 명확히 판별하여 예외를 방지하십시오.

### 규칙 8: 카파시(Karpathy)의 4대 AI 개발 가이드라인 준수
- **1. Ask, don't assume (질문하고 확인하기):** 지시가 불명확하거나 아키텍처적 선택이 필요할 땐 자의적으로 가정하여 구현하지 말고, 2~3가지 안을 제시하여 승인을 받은 뒤 진행하십시오.
- **2. Simplest solution first (단순성 최우선):** 요청받지 않은 추상화나 불필요한 설계를 더하지 말고 항상 목적을 달성하는 가장 심플하고 직관적인 코드를 먼저 작성하십시오.
- **3. Don't touch unrelated code (범위 엄수):** 주어진 작업 범위 밖의 파일이나 코드는 리팩토링, 포맷팅, 네이밍 변경 등을 임의로 시도하지 말고 철저히 방관하십시오.
- **4. Flag uncertainty (불확실성 선언):** 기술적 지식, API 규격, 버전에 대해 확신이 없을 경우 가짜 정보를 지어내지 말고 불확실함을 유저에게 명확히 알리십시오.

### 규칙 9: 불필요한 미사여구 배제 (Kill the filler)
- 응답 서두에 "물론입니다!", "좋은 질문입니다!", "확인해 드리겠습니다!" 등 토큰과 인지적 리소스를 낭비하는 웜업용 filler 문구들을 절대 사용하지 마십시오. 인사말 없이 바로 대답(결과)으로 진입하십시오.

---

## 🛠️ 오늘(2026-05-21) 적용된 변경사항 기록

### ① `git_manager.py` 복구 완료
- **기능**: 유실되어 에이전트의 오작동 및 가져오기 누락을 일으키던 git_manager.py 모듈 소스를 신규 구현하여 연동 정상화.

### ② `hermes_context_builder.py` 비대화 방지 고도화
- **기능**: `_slim_context()` 알고리즘 적용으로 `HERMES.md` 파일 크기가 대형화되어도 컨텍스트 주입 용량을 MAX_CONTEXT_CHARS(4000자) 이내로 상시 필터링하여 지연 현상을 원천 방지함.

### ③ `harness_agent.py` 기능 개선
- 연쇄 에이전틱 루프 횟수 증가 (2회 → 3회)로 여러 태그 연동(LIST -> READ -> SAVE) 시 안정성 강화.
- `[SAVE]` 태그의 절대 경로/상대 경로 구분 저장 프로세스 정밀화.

### ④ 카파시(Karpathy) AI 코딩 가이드라인 공식 이식
- **내용**: Karpathy의 4대 AI 개발 강령(Ask don't assume, Simplest solution, Scope 엄수, Uncertainty 선언) 및 미사여구 배제(No Filler) 규칙을 모듈 코딩 규칙(규칙 8, 9)과 에이전트 시스템 프롬프트에 직접 반영하여 행동 방침 최적화 완료.

---

## 🧠 기억 시스템 통합 구조 요약

```
Bio-Memory (bio_memory_engine.py)
  L1: 최근 대화 (harness_memory.json 상위 15개)  → 수 시간
  L2: 연상망 (벡터+그래프)                        → 수 주
  L3: 절차기억 (Bash 스킬 캐시)                  → 반영구

Codebase Harness (hermes_context_builder.py)
  구조기억: HERMES.md                            → 영구 (파일 존재하는 한, v3.9 슬림 요약 필터 적용)
  노이즈차단: .hermesignore                       → 영구
  맵핑: Codebase Map (런타임 자동생성)            → 즉시

→ 두 시스템이 합쳐져야 "경험+원칙"을 동시에 기억하는 완전한 AI
```

---

### ⑤ `optimistic_response.py` 낙관적 응답 엔진 추가 (2026-06-09)
- **기능**: Linear 아키텍처 패턴 도입. 사용자 명령 시 즉시 "⏳ 진행 중" 메시지 발송 → `asyncio.create_task()`로 백그라운드 실행 → 완료 시 메시지 편집("✅ 완료"/"❌ 실패").
- **재시도**: 최대 3회 자동 재시도(2초 간격), 에러 유형별 복구 힌트(권한/네트워크/파일/메모리) 자동 생성.
- **사용법**: `get_engine()` 싱글턴 → `initiate_action(bot, chat_id, user_id, action_name, description, work_fn)`.
- **적용**: `handlers/_file.py`의 `/ingest` 3개 분기 전면 교체. `/retry` 명령어 신규 추가(`_callbacks.py`).
- **하네스 통합**: `harness_agent.py`에 `/retry` 텍스트 인터셉트 추가.

### ⑥ `memory_refinement.py` 세밀한 메모리 엔진 추가 (2026-06-09)
- **기능**: `bio_memory_engine.py`(Lock Stack)를 건드리지 않고 4대 메모리 갭 해결.
- `auto_forget()`: 에빙하우스 보유율 15% 미만 + 7일 경과 L2 에피소드 자동 정리 (dry-run/confirm 2단계).
- `check_conflict()`: `save_important()` 전 기존 L2/L3와 키워드 충돌 검사.
- `should_store()`: "7일 후에도 쓸모있나?" 자동 판단 (중요도/일시적 표현/중복 검사).
- `hybrid_recall()`: `bio_memory.recall()` + `knowledge_indexer` FTS5 결과 병합. `harness_agent.py` 메모리 오버레이에 통합.
- **적용**: `/memory forget`, `/memory health` 서브커맨드 추가. `harness_agent.py` LLM 컨텍스트에 hybrid_recall 자동 주입.

*이 파일 마지막 업데이트: 2026-06-09*  
*다음 업데이트 트리거: 새 모듈 추가 또는 아키텍처 변경 시*
