# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PEN-SDG 정책 시뮬레이터** — AR6 기반 기후변화 적응사회를 위한 탄소중립 정책이 K-SDGs 지표에 미치는 파급 효과를 분석하는 Streamlit 시뮬레이터. 전국(KOR) + 세 지역(울산·서산·순천)의 탄소중립 녹색성장 기본계획 정책을 대상으로 한다.

전체 프로젝트 구조는 `README.md` 참조. UI 디자인 원칙은 `.impeccable.md` 참조. 경로 생성 알고리즘은 `PATHALGORITHM.md` 참조.

## 역할 경계 — 이 저장소는 "검토" 영역이다

**PEN 본체는 이 컴퓨터에 없다. 개발자가 다른 컴퓨터에서 관리한다.**

| | 이 저장소 (검토) | 개발자 (다른 컴퓨터) |
|---|---|---|
| 하는 일 | 경로·계수 심사, 판정, 수정안·문안 작성, 반영목록 산출 | NODES/LINKS 등재, `base_weight` 수정, 사이클 검사, 재빌드 |
| 산출물 | 심사결과 xlsx/json, `개발자_반영목록_*.xlsx` | 갱신된 PEN 본체 |

`project-outcome/pen/PEN_nodes_links.xlsx`와 `sim_cache/`는 **개발자 본체의 과거 스냅샷**이다. 앱 구동·구조 참조용이며 심사 결과를 여기에 병합하지 않는다.

### 따르는 규칙

- 심사 결과 반영은 **심사요청 파일의 교정본 + `개발자_반영목록_*.xlsx`** 생성까지가 이쪽 범위다. 거기서 끝낸다.
- `PEN_nodes_links.xlsx`의 `base_weight`·NODES·LINKS를 심사 결과로 수정하지 않는다. `sim_cache/` 재빌드도 하지 않는다.
- 심사 결과를 보고할 때 **"PEN 본체 미반영"을 미결 사항이나 남은 작업으로 올리지 않는다.** 개발자 영역이며 정상 상태다.
- 로컬 PEN 스냅샷에 신규 노드·링크가 없는 것은 결함이 아니다. "노드 미등재 → 등재 선행 필요" 같은 지적을 심사 산출물의 조치 항목에 넣지 않는다.
- 개발자에게 넘길 사항은 반영목록의 작업 지시와 회신 요청 자료(예: 원표 CSV)로만 적는다.

## 문서 관계도 — 무엇이 바뀌면 어디를 수정해야 하는가

변경이 발생하면 아래 표를 기준으로 연쇄 수정 범위를 파악한다.

| 변경 내용 | 수정해야 할 문서/파일 |
|-----------|----------------------|
| **지역 추가/삭제/명칭 변경** | `simulator_app.py:REGION_NAMES` · `pen_schema.json:region_codes` · `CLAUDE.md` 지역 코드 · `README.md` 폴더구조·노드레이어 표 · `project-outcome/policy/policy_{NEW}.xlsx` 신규 생성 |
| **project-outcome/ 폴더 구조 변경** | `project_paths.py` (경로 상수) · `pen_builder.py` (파일 로딩) · `simulator_app.py` (업로드/다운로드 경로) · `README.md` 폴더구조 · `CLAUDE.md` File Layout |
| **PEN 노드 레이어 추가/변경** | `pen_schema.json:node_layers` · `pen_builder.py:LAYER_ORDER` · `simulator_app.py` (SDG_LABELS 등) · `README.md` 노드레이어 표 · `CLAUDE.md` 노드계층구조 · `PATHALGORITHM.md` |
| **SDG 지표 목록 변경 (G01~G13)** | `simulator_app.py:SDG_LABELS·SDG_DISPLAY_SIGN` · `pen_schema.json:sink_nodes` · `README.md` SDG 지표 표 · `PATHALGORITHM.md` |
| **SD 지표 목록 변경 (SD01~SD05)** | `pen_schema.json:sink_nodes_derived` · `README.md` SD 지표 표 · `PATHALGORITHM.md` |
| **`build_pen_cache()` 시그니처 변경** | `pen_builder.py` 함수 정의 · `simulator_app.py:_pen_rebuild_dialog()·_check_auto_rebuild()` |
| **앱 탭 추가/변경** | `simulator_app.py` 탭 정의 · `README.md` 앱 탭 기능 표 |
| **`pen_path_generator.py` 옵션 변경** | `PATHALGORITHM.md` CLI 옵션 표 · `README.md` PEN 확장 섹션 |
| **UI 디자인 변경** | `.impeccable.md` 디자인 원칙 · `simulator_app.py` CSS 블록 |
| **빌드 트리거/자동화 변경** | `simulator_app.py:_check_auto_rebuild()` · `CLAUDE.md` 자동재구성 섹션 · `README.md` 빠른시작 |

## Commands

```bash
# 앱 실행
streamlit run simulator_app.py

# PEN 경로 자동 생성 (Claude API 필요 — ANTHROPIC_API_KEY 환경변수 또는 .env)
python pen_path_generator.py --policy "농촌 태양광 임대 사업"
python pen_path_generator.py --policy "태양광 설비 지원" --unit kW --sector 에너지
python pen_path_generator.py --policy "농촌 태양광 임대 사업" --apply   # Excel에 직접 적용

# sim_cache 재빌드는 앱의 "PEN 관리" 탭에서 실행하거나 직접 호출:
python3 pen_builder.py
```

## Architecture

### Core Model (PEN — Policy Effect Network)

PEN은 방향 비순환 그래프(DAG)로 정책 → SDG 효과 전파를 모델링한다.

**노드 계층 구조** (`pen_schema.json` 참고):
```
POLICY (Layer 1)  →  DIRECT (Layer 2)  →  INDIRECT (Layer 3)
                                        →  SD_SINK (Layer 4: 온실가스·에너지·GDP·취업자)
                                        →  SDG_SINK (Layer 5: 13개 K-SDG 지표)
```

**지역 코드** — KOR(전국), ULS(울산광역시), SES(충남 서산시), SCH(전남 순천시). POLICY 노드는 지역 prefix로 분리되고, DIRECT/INDIRECT/SD_SINK/SDG_SINK는 공유. 신규 지역 추가 시: `project-outcome/policy/policy_{NEW}.xlsx` 추가 + LINKS에 `eff_{NEW}` 컬럼 + NODES에 `NEW-*` 노드 추가 후 재빌드.

**행렬 계산** (`simulator_app.py:load_region`, `run_simulation`):
- `W`: 가중치 행렬 (n×n). `eff_{region}` 컬럼 우선, 없으면 `base_weight` 사용
- `R = I + W + W² + ...`: 도달 가능성 행렬 (DAG이므로 W^k → 0 으로 수렴)
- `Pt`: 연도별 정책 투입량 대각벡터
- `Et = diag(Pt) @ R`: 전체 파급 효과 행렬

**시뮬레이션 흐름**: 사용자가 부문/정책 배율 설정 → `apply_multipliers()`로 Pt 조정 → `Et_scen - Et_base`로 델타 계산 → 13개 SDG / 5개 SD 지표로 집계.

### File Layout

| 경로 | 설명 |
|------|------|
| `simulator_app.py` | Streamlit 앱 (Tab0: 베이스라인·배율, Tab1: 시뮬 결과, Tab2: SD 투입, Tab3: PEN 관리, Tab4: 경로 검증) |
| `pen_builder.py` | `project-outcome/` → `sim_cache/` 빌드 파이프라인 |
| `pen_path_generator.py` | Claude API 기반 DIRECT/INDIRECT 경로 자동 생성 |
| `project_paths.py` | 경로 상수 및 `discover_policy_files()` 유틸리티 |
| `pen_schema.json` | PEN 노드 타입·링크 타입·지역 코드 스키마 정의 |
| `project-outcome/pen/PEN_nodes_links.xlsx` | NODES + LINKS 시트 (빌드 소스). **개발자 본체의 스냅샷** — 심사 결과를 여기에 병합하지 않는다 (역할 경계 참조) |
| `project-outcome/policy/policy_{REGION}.xlsx` | 지역별 POLICY_MASTER (연도별 정책 투입량) |
| `project-outcome/raf/지역특성_파라미터_DB.xlsx` | RAF 지역조정계수 파라미터 |
| `project-outcome/manual/정책속성_표준화_매뉴얼.xlsx` | 정책 입력 표준화 가이드 |
| `sim_cache/` | 빌드 결과물 (`.npy` 행렬, `_meta.json`) — 재생성 가능, 직접 편집 금지 |
| `project-outcome/reviews/` | 경로 검증 탭 저장 결과 (`existing_*.json`, `new_*.json`). 스키마: `review_type·region·policy_id·links[{source·target·link_type·base_weight·status·comment}]·applied` |
| `data/pen/` | 분석용 Excel 참고 자료 (참고용, 빌드에 사용 안 함) |
| `data/matrix_exports/` | 행렬 수출 파일 |
| `data/policy/` | 구버전 정책 참고 파일 (지역특성_파라미터_DB.xlsx 사본) |

### sim_cache 파일 규약

```
{region}_W.npy           — 가중치 행렬
{region}_{year}_Pt_diag.npy — 연도별 정책 투입량 벡터
{region}_meta.json       — 노드 메타 (node_list, pol_idx, sdg_idx, sd_idx, ...)
build_info.json          — 빌드 타임스탬프 및 요약
```

### 지역별 유효 가중치

`eff_{region}` = `base_weight × RAF_지역`. RAF(지역조정계수) = 지역파라미터값 / 3개 지역 평균값. `pen_builder.py`가 빌드 시 계산하여 W 행렬에 반영.

### SDG 표시 부호

13개 SDG 지표 중 온실가스·에너지집약도·CO₂집약도·토지이용·황무지 비율(G01·G03·G08·G10·G13)은 낮을수록 좋은 지표 → `SDG_DISPLAY_SIGN = -1`. 모델 내부 양수 값을 UI에서 음수로 반전하여 표시.

### 자동 재구성 (Auto-rebuild)

앱 기동 시 `_check_auto_rebuild()`가 호출되어 `project-outcome/` 입력 파일의 mtime이 `sim_cache/build_info.json`보다 최신이면 자동으로 재빌드한다. 정책DB 파일 수정 후 앱을 재시작하거나 인터랙션을 트리거하면 자동 반영된다.

## Key Constraints

- `sim_cache/`를 수정하려면 반드시 `pen_builder.py`를 통해 재빌드한다. `.npy` 파일을 직접 편집하지 않는다.
- 심사·검토 작업의 결과물은 `PEN_nodes_links.xlsx`나 `sim_cache/`에 반영하지 않는다. 본체는 개발자가 다른 컴퓨터에서 관리한다 (역할 경계 참조).
- W 행렬은 DAG 가정에 의존한다 (루프 없음). 새 링크 추가 시 사이클 생성 여부를 확인해야 한다.
- `pen_path_generator.py`는 `ANTHROPIC_API_KEY`가 필요하다. `.env` 파일 또는 환경변수로 제공한다.
- Python 3.9 venv가 `venv/`에 포함되어 있다.
- 신규 지역을 추가해도 `eff_{REGION}` 컬럼이 LINKS에 없으면 빌드되지 않는다 (두 조건 모두 필요).
