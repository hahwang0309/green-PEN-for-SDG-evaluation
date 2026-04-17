# /pen-path-gen — PEN INDIRECT 노드 경로 생성

PEN(Policy Effect Network)에서 새로운 DIRECT 노드에 대한 INDIRECT 노드와 링크를 GoT(Graph of Thoughts) 방식으로 자동 생성합니다.

## 사용법

```
/pen-path-gen [DIRECT_ID] [레이블] [단위] [부문] [옵션]
```

**예시:**
```
/pen-path-gen D21 "농촌 태양광 임대 사업" kW 에너지
/pen-path-gen D21 "농촌 태양광 임대 사업" kW 에너지 --apply
/pen-path-gen D21 "농촌 태양광 임대 사업" kW 에너지 --regions ULS,SES
```

## 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--apply` | Excel에 실제 적용 | 없으면 미리보기만 |
| `--regions` | 활성 지역 | ULS,SES,SCH |
| `--output` | 결과 JSON 저장 경로 | - |
| `--model` | Claude 모델 | claude-opus-4-6 |

## 실행

다음 명령을 실행합니다:

```bash
cd "C:/Users/ghkdg/Dropbox/2026_(외부)AR6 기반 기후변화 적응사회를 위한 통합 시나리오 및 SDGs 평가기술/AR6-SDGs"
python pen_path_generator.py --direct $ARGUMENTS
```

$ARGUMENTS 자리에 사용자가 입력한 인자를 그대로 전달합니다.

---

이 커맨드는 `pen_path_generator.py`의 1단계(GoT 기반) 구현을 호출합니다.

생성 후 `--apply` 없이 실행하면 미리보기만 보여줍니다.
결과를 확인한 후 `--apply`를 추가하여 `pen_input/PEN_nodes_links.xlsx`에 적용하고,
앱의 **⚙️ PEN 관리** 탭 또는 `python pen_builder.py`로 재구성하세요.

## 구현 단계 현황

| 단계 | 상태 | 내용 |
|------|------|------|
| **1단계** | ✅ 구현됨 | GoT 프롬프트 + Claude API |
| **2단계** | 예정 | CausalRAG 문헌 인덱스 연동 |
| **3단계** | 예정 | SDGIO 온톨로지 포괄성 검사 |
| **4단계** | 예정 | Multi-Agent 검증 파이프라인 |

자세한 내용은 `PATHALGORITHM.md` §10 참조.
