# PEN-SDG 정책 시뮬레이터

**Policy Effect Network(PEN) 기반 SDG 평가 시뮬레이터**
울산(ULS) · 서산(SES) · 순천(SCH) 3개 지역 탄소중립 정책의 SDG 파급 효과를 연도별로 분석합니다.

---

## 폴더 구조

```
AR6-SDGs/
├── simulator_app.py              ← Streamlit 앱 (메인)
├── pen_builder.py                ← PEN 캐시 빌드 (Excel → sim_cache)
├── pen_path_generator.py         ← PEN 경로 자동 생성 (Claude AI 활용)
├── pen_schema.json               ← PEN 데이터 모델 정의
├── PATHALGORITHM.md              ← 경로 생성 알고리즘 상세 문서
├── requirements_simulator.txt    ← Python 의존성
├── .env                          ← API 키 설정 (pen_path_generator 사용 시)
│
├── pen_input/                    ← 입력 데이터 (직접 편집 또는 앱에서 업로드)
│   ├── PEN_nodes_links.xlsx      ← PEN 구조 (NODES + LINKS 시트)
│   └── policy_db.xlsx            ← 정책 투입량 DB (POLICY_MASTER 시트)
│
├── sim_cache/                    ← 자동 생성 캐시
│   ├── {region}_W.npy            ← 가중치 행렬
│   ├── {region}_meta.json        ← 노드 목록, 정책 메타, 링크 타입
│   ├── {region}_{year}_Pt_diag.npy ← 연도별 정책 투입량 벡터
│   └── build_info.json           ← 마지막 빌드 정보
│
├── data/
│   ├── pen/                      ← PEN 원본 Excel (통합본, 지역별, 백업)
│   ├── policy/                   ← 정책 DB, 지역특성 파라미터
│   └── matrix_exports/           ← W·R·Pt·Et 행렬 Excel 내보내기 (참고용)
│
├── docs/                         ← 연구 문서, 방법론, 과업 리스트
└── reference/                    ← 울산·서산·순천 탄소중립 기본계획 PDF
```

---

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements_simulator.txt

# 2. 앱 실행
streamlit run simulator_app.py
```

앱 시작 시 `pen_input/` 파일이 `sim_cache/`보다 최신이면 PEN을 **자동 재구성**합니다.
수동으로 빌드하려면:
```bash
python pen_builder.py
```

---

## 앱 탭 기능

| 탭 | 주요 기능 |
|---|---|
| 📋 베이스라인 시나리오 | 부문·개별 배율 설정, 연도별 정책 투입량 확인, 시뮬레이션 실행 |
| 🚀 시뮬레이션 결과 | SDG×연도 델타 히트맵, 부문별 SDG 수치 테이블, 정책별 효과경로 다이어그램 |
| 📈 SD 투입 지표 | 부문별 SD 기준선/증감량 테이블, 전 연도 CSV 다운로드 |
| ⚙️ PEN 관리 | PEN 구조·정책DB 파일 업로드, PEN 재구성 실행, 입력 파일 다운로드 |

---

## PEN 구조 및 SD 모델 연계

### 두 모델의 역할 분담

이 시스템은 두 개의 모델이 연계하여 탄소중립 정책의 단기·장기 효과를 분석합니다.

**PEN 모델의 본래 목적**은 정책 노드가 어떤 효과 경로를 통해 SDG 지표에 영향을 미치는지 추적하는 것입니다. 이 관점에서 SD 노드는 원칙적으로 필요 없습니다. SD 노드를 모두 제거해도 PEN은 정상 작동합니다.

```
[기본 PEN 경로]  POLICY → DIRECT → INDIRECT ──→ SDG

[SD 추출 추가]   POLICY → DIRECT → INDIRECT ──→ SDG  (기본 경로 유지)
                                         └──→ SD 노드  (SD 모델용 정보 추출기)
                                                  ↓
                                            SD 모델 (장기 10년 추정)
```

- **PEN 모델**: 정책의 단기 효과를 SDG 지표로 직접 평가합니다.
- **SD 노드(SD01~SD05)**: PEN 경로에 병렬로 붙어 INDIRECT 값을 집계하는 **정보 추출기**입니다. SD→SDG 연결은 없으며, SD 모델이 이 노드의 값을 읽어 장기 시뮬레이션에 활용합니다.
- **SD 모델**: PEN이 추출한 SD 지표값을 투입받아 10년 이상의 장기 에너지·경제·생태 변화를 시뮬레이션합니다.

### 노드 레이어

```
POLICY (L1) → DIRECT (L2) → INDIRECT (L3) ──────────→ SDG (L4)
                                       └──→ SD (L3)      ↑ 병렬 추출기
```

| 레이어 | 노드 수 (ULS/SES/SCH) | 설명 |
|--------|----------------------|------|
| L1 POLICY | 47 / 31 / 83 | 지역별 탄소중립 정책 (지역-부문-번호) |
| L2 DIRECT | 20 (공통) | 정책의 직접 물리 산출물 D01~D20 |
| L3 INDIRECT | 18 (공통) | 물리·경제·생태 중간 효과 I01~I18 |
| L3 SD | 5 (공통) | SD 모델 정보 추출기 SD01~SD05. SDG로 연결 없음 |
| L4 SDG | 13 (공통) | K-SDGs 최종 지표 G01~G13 |

**현재 SDG 도달 현황 (ULS 기준):**

| 도달 가능 (11개) | 미연결 (2개) |
|----------------|-------------|
| G01 G02 G03 G04 G05 G06 G07 G08 G09 G12 G13 | G10 G11 (도시계획 관련 INDIRECT 노드 없음) |

---

## PEN 확장: 경로 자동 생성

`pen_path_generator.py`는 **정책 이름을 입력하면 Claude AI가 GoT(Graph of Thoughts) 방식으로 전체 효과 경로를 자동 설계**하는 도구입니다.

PEN의 설계 철학(기본 경로 + SD 추출기 분리)을 반영하여 **3단계**로 생성합니다:

| 단계 | 목표 | SD 관여 |
|------|------|---------|
| **Phase 1** | 정책 → DIRECT 노드 결정 (기존 D01~D20 재사용 우선) | 없음 |
| **Phase 2** | DIRECT → INDIRECT → SDG 경로 생성 (순수 PEN, SD 완전 배제) | 없음 |
| **Phase 3** | INDIRECT → SD 매칭 (SD 추출기 연결, 중복 방지) | I→SD만 생성 |

**SD 중복 방지 원칙**: 동일한 SD 노드에 여러 I 노드가 연결 가능할 때, 인과 체인에서 가장 하류(마지막) I 노드에만 연결합니다. 상류 I 노드에 중복 연결하면 SD 모델이 동일 효과를 이중으로 집계하기 때문입니다.

### 사전 준비: API 키 설정

`.env` 파일을 프로젝트 루트에 생성합니다:
```
ANTHROPIC_API_KEY=sk-ant-여기에키입력
```
키 발급: https://console.anthropic.com → API Keys

```bash
pip install python-dotenv   # 최초 1회
```

### 단일 정책 경로 생성

```bash
# 정책명만으로 전체 경로 자동 생성 (미리보기, 파일 변경 없음)
python pen_path_generator.py --policy "농촌 태양광 임대 사업"

# 단위·부문 직접 지정 (생략 시 Claude 추론)
python pen_path_generator.py --policy "농촌 태양광 임대 사업" --unit kW --sector 에너지

# 결과 확인 후 Excel에 적용
python pen_path_generator.py --policy "농촌 태양광 임대 사업" --apply
```

### 기존 PEN 전체 검증·확장 (배치)

기존 DIRECT 노드(D01~D20) 전체를 대상으로 누락 경로를 검토합니다.

```bash
# 미리보기 (파일 변경 없음, 예상 비용 ~$0.26)
python pen_path_generator.py --validate-all

# 결과를 JSON으로 저장하면서 실행
python pen_path_generator.py --validate-all --output pen_validate_results/

# 검토 후 적용
python pen_path_generator.py --validate-all --apply
```

### 전체 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--policy` | 정책 이름/설명 | 필수 (단일 모드) |
| `--validate-all` | D 노드 전체 배치 검증 | - |
| `--direct` | DIRECT 노드 ID 강제 지정 (기존 D01~D20 지정 시 검토 모드) | 자동 배정 |
| `--unit` | 물리 단위 | Claude 추론 |
| `--sector` | 정책 부문 | Claude 추론 |
| `--regions` | 활성 지역 | ULS,SES,SCH |
| `--apply` | Excel에 실제 저장 | 미리보기만 |
| `--output` | 결과 JSON 저장 경로 | - |
| `--model` | Claude 모델 | claude-sonnet-4-6 |

> ⚠️ 생성된 가중치는 **추정값**입니다. 감축원단위·용량계수 등 실제 계수를 검토해 수정하세요.
> 결과를 되돌리려면 `pen_input/PEN_nodes_links.xlsx`에서 추가된 행을 삭제 후 `pen_builder.py`를 재실행합니다.

### 향후 고도화

현재 1단계(GoT 기반)에서 향후 4단계(Multi-Agent 검증)까지 단계적으로 발전시킬 계획입니다.
자세한 방법론 및 로드맵은 `PATHALGORITHM.md` §10 참조.

---

## 입력 데이터 업데이트

PEN 구조나 정책 투입량을 직접 수정할 경우:

1. `pen_input/PEN_nodes_links.xlsx` 또는 `pen_input/policy_db.xlsx` 편집
2. 앱 **⚙️ PEN 관리** 탭 → "🔨 PEN 재구성 실행" 또는 `python pen_builder.py`

**새 지역 추가:**
- LINKS 시트에 `eff_{지역코드}` 컬럼 추가
- NODES 시트에 해당 지역 prefix 정책 노드 추가
- policy_db.xlsx POLICY_MASTER에 해당 지역 정책 추가
- `eff_*` 컬럼 패턴으로 지역 자동 감지

---

## 지표 참조

### SDG 지표 (G01~G13)

| ID | SDG 코드 | 지표명 | 단위 | 목표 방향 |
|---|---|---|---|---|
| G01 | 13.2.2 | 연간 온실가스 총배출량 | 천tCO₂eq | 감소 ↓ |
| G02 | 7.2.1 | 재생에너지 비중 | % | 증가 ↑ |
| G03 | 7.3.1 | 에너지 집약도 | TOE/억원 | 감소 ↓ |
| G04 | 8.1.1 | 1인당 실질GDP 성장률 | % | 증가 ↑ |
| G05 | 8.2.1 | 취업자당 GDP 성장률 | % | 증가 ↑ |
| G06 | 9.2.1 | 제조업 부가가치 비중 | % | 증가 ↑ |
| G07 | 9.2.2 | 제조업 고용 비중 | % | 증가 ↑ |
| G08 | 9.4.1 | CO₂ 배출집약도 | tCO₂eq/억원 | 감소 ↓ |
| G09 | 6.6.1 | 수생태계 면적 변화율 | % | 증가 ↑ |
| G10 | 11.3.1 | 토지이용/인구 증가율 비율 | 비율 | 감소 ↓ |
| G11 | 11.7.1 | 도시 공개공간 비율 | % | 증가 ↑ |
| G12 | 15.1.1 | 산림면적 비율 | % | 증가 ↑ |
| G13 | 15.3.1 | 황무지 비율 | % | 감소 ↓ |

### SD 지표 (SD01~SD05)

SD 노드는 PEN의 인과 경로에 **병렬로 붙은 정보 추출기**입니다. SDG와의 직접 연결은 없으며, INDIRECT 노드들의 값을 집계하여 SD 모델에 제공합니다. SD 노드를 제거해도 PEN의 SDG 계산에는 영향이 없습니다.

| ID | 지표명 | 단위 | 집계 대상 INDIRECT | SD 모델 활용 |
|---|---|---|---|---|
| SD01 | 온실가스배출량 변화량 | tCO₂eq | I02~I06, I10, I13 (에너지 절감·전환) | 장기 탄소 감축 궤적 추정 투입 |
| SD02 | 신재생에너지사용량 변화량 | TOE | I01, I02, I13 (재생에너지 투입) | 에너지 믹스 변화 시나리오 투입 |
| SD03 | 기존에너지사용량 변화량 | TOE | I02~I06, I10, I13, I16 (에너지 절감) | 기저 에너지 수요 변화 추정 투입 |
| SD04 | 1·2차산업 GDP 변화량 | 억원 | I17 (친환경차 제조업 수요) | 장기 경제 전환 효과 추정 투입 |
| SD05 | 1·2차산업 취업자수 변화량 | 명 | I18 (건설·설치업 고용) | 장기 고용 효과 추정 투입 |
