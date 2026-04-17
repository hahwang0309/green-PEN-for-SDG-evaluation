"""
pen_path_generator.py
=====================
PEN 전체 경로 자동 생성기 (1단계: GoT + 싱크 도달 보장)

입력: 정책 설명 (--policy)
출력: DIRECT 노드 + INDIRECT 노드 + 링크 (SD/SDG 싱크 도달 보장)

알고리즘:
  Phase 1: 정책 → DIRECT 노드 결정
           (싱크 연결 불가능한 D 후보는 제외)
  Phase 2: DIRECT → INDIRECT 체인 생성 (최대 3~5홉)
           (3~5홉 내 SD/SDG 미도달 브랜치는 가지치기)
  Phase 3: 최종 검증 및 JSON 출력

사용법:
  python pen_path_generator.py --policy "농촌 태양광 임대 사업"
  python pen_path_generator.py --policy "농촌 태양광 임대 사업" --unit kW --sector 에너지
  python pen_path_generator.py --policy "태양광 설비 지원" --direct D01   # D 강제 지정
  python pen_path_generator.py --policy "농촌 태양광 임대 사업" --apply   # Excel 적용

구현 단계:
  1단계 (현재): GoT 기반 Claude API, 싱크 도달 가지치기
  2단계 (예정): CausalRAG - 탄소중립·SDG 문헌 인덱스 연동
  3단계 (예정): 온톨로지(SDGIO+ENVO) 기반 포괄성 검사
  4단계 (예정): Multi-Agent 검증 파이프라인
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import anthropic
try:
    from dotenv import load_dotenv
    load_dotenv()  # 현재 디렉터리 또는 상위 디렉터리의 .env 자동 탐색
except ImportError:
    pass
import pandas as pd

BASE_DIR  = Path(__file__).parent
PEN_EXCEL = BASE_DIR / "pen_input" / "PEN_nodes_links.xlsx"
SCHEMA_PATH = BASE_DIR / "pen_schema.json"


# ──────────────────────────────────────────────────────────────
# 컨텍스트 로딩
# ──────────────────────────────────────────────────────────────

def load_context() -> dict:
    nodes_df = pd.read_excel(PEN_EXCEL, sheet_name="NODES")
    links_df = pd.read_excel(PEN_EXCEL, sheet_name="LINKS")

    def ntype(t): return nodes_df[nodes_df["node_type"] == t]

    direct   = ntype("DIRECT")[["node_id","label_ko","unit"]].to_dict("records")
    indirect = ntype("INDIRECT")[["node_id","label_ko","unit","notes"]].to_dict("records")
    sd_nodes = ntype("SD_SINK")[["node_id","label_ko","unit"]].to_dict("records")
    sdg_nodes= ntype("SDG_SINK")[["node_id","label_ko","unit"]].to_dict("records")

    # 다음 가용 ID
    next_d = f"D{len(direct)+1:02d}"
    next_i = f"I{len(indirect)+1:02d}"

    # 기존 D→I, I→SD/SDG 연결 패턴
    d_ids  = {nd["node_id"] for nd in direct}
    i_ids  = {nd["node_id"] for nd in indirect}
    sd_ids = {nd["node_id"] for nd in sd_nodes}
    sdg_ids= {nd["node_id"] for nd in sdg_nodes}
    sink_ids = sd_ids | sdg_ids

    d_to_i, i_to_sink = [], []
    for _, row in links_df.iterrows():
        src, tgt = str(row["source"]), str(row["target"])
        w = row.get("base_weight"); w = float(w) if pd.notna(w) else None
        entry = {"source": src, "target": tgt,
                 "link_type": row.get("link_type",""), "base_weight": w}
        if src in d_ids and tgt in i_ids:
            d_to_i.append(entry)
        elif src in i_ids and tgt in sink_ids:
            i_to_sink.append(entry)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    return {
        "direct": direct, "indirect": indirect,
        "sd_nodes": sd_nodes, "sdg_nodes": sdg_nodes,
        "next_d": next_d, "next_i": next_i,
        "d_to_i": d_to_i, "i_to_sink": i_to_sink,
        "schema": schema,
    }


# ──────────────────────────────────────────────────────────────
# 시스템 프롬프트
# ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 정책 효과 네트워크(PEN) 전문가입니다.

## PEN의 설계 원칙

PEN의 본래 목적은 **정책이 SDG 지표에 어떤 경로로 영향을 미치는지 추적**하는 것입니다.
따라서 기본 경로는 POLICY → DIRECT → INDIRECT → SDG 이며,
SD 노드는 이 경로에 관여하지 않습니다.

SD 노드(SD01~SD05)는 **정보 추출기(extractor)**입니다.
INDIRECT 노드들의 값을 집계하여 장기 시스템다이내믹스(SD) 모델에 제공하는 역할을 합니다.
- SD 노드는 INDIRECT에서 값을 받지만 SDG로는 연결되지 않습니다.
- SD 노드를 제거해도 PEN은 정상 작동합니다.

## 경로 생성 3단계

**1단계**: 정책 → DIRECT 노드 결정
**2단계**: INDIRECT 체인 → SDG 직접 연결 (SD 없이 순수 PEN 경로)
**3단계**: INDIRECT 노드 중 SD 노드로 연결 가능한 것 매칭 (SD 모델용 추출 경로)

## SDG 직접 연결 가이드 (2단계용)
- tCO₂eq 단위 INDIRECT → G01(온실가스), G08(CO₂집약도) 직접
- TOE 단위 에너지 절감 → G02(재생에너지비중), G03(에너지집약도) 직접
  (변환 계수: TOE→% 는 에너지믹스 비중으로, TOE→TOE/억원 는 집약도로)
- ha(면적) 단위 → G12(산림면적비율), G13(황무지비율) 직접
- 억원(GDP) 단위 → G04(GDP성장률), G05(노동생산성), G06(제조업부가가치) 직접
- 명(고용) 단위 → G05, G07(제조업고용비중) 직접
- 수생태계 관련 → G09(수생태계면적) 직접

## SD 매칭 기준 (3단계용)
- TOE 단위 에너지 절감 INDIRECT → SD03(기존에너지사용량 변화량)
- TOE 단위 재생에너지 INDIRECT → SD02(신재생에너지사용량 변화량)
- tCO₂eq·tCH₄ 단위 감축 INDIRECT → SD01(온실가스배출량 변화량)
- 억원(GDP) 단위 경제효과 INDIRECT → SD04(GDP 변화량)
- 명(고용) 단위 INDIRECT → SD05(취업자수 변화량)

## SD 연결 중복 방지 규칙 (3단계 핵심)
하나의 인과 체인(D→I→I→...→SDG) 상에서:
- **동일 SD 노드**: 체인 내 여러 I 노드가 같은 SD에 연결될 수 있더라도, **가장 하류(마지막) I 노드에만** 연결한다.
  - 예: D→I01→I02→SDG 에서 I01도 SD01에, I02도 SD01에 연결 가능하다면 → I02→SD01 만 연결
  - 이유: I02는 I01의 효과를 이미 포함하므로 I01에도 연결하면 SD01이 중복 집계됨
- **서로 다른 SD 노드**: 같은 체인 내 여러 I 노드가 각각 다른 SD에 연결되는 것은 허용
  - 예: I01→SD02, I02→SD03 (다른 SD 노드이므로 모두 연결 가능)

## 링크 타입
- CAUSES: 물리·과학적 인과
- TRANSFORMS_TO: 단위 변환 동치
- CONTRIBUTES_TO: 기여 관계
- FORMULA: 수식 변환

## 재사용 원칙
기존 DIRECT/INDIRECT 노드와 동일한 물리 현상이면 반드시 재사용합니다.

## 출력
반드시 JSON만 출력. 다른 텍스트 금지."""


# ──────────────────────────────────────────────────────────────
# Phase 1 프롬프트: 정책 → DIRECT 노드
# ──────────────────────────────────────────────────────────────

def build_phase1_prompt(
    policy: str,
    unit: str | None,
    sector: str | None,
    forced_direct: str | None,
    ctx: dict,
) -> str:
    forced_hint = ""
    if forced_direct:
        forced_hint = f"\n**주의**: DIRECT 노드를 {forced_direct}(으)로 강제 지정합니다. 재사용 여부를 검토하세요."

    return f"""## Phase 1: 정책 → DIRECT 노드 결정

### 입력 정책
- 정책명: {policy}
- 단위: {unit or "(추론 필요)"}
- 부문: {sector or "(추론 필요, 건물/수송/산업/에너지/농축산/폐기물/흡수원/자원순환 중)"}
{forced_hint}

### 기존 DIRECT 노드 (재사용 우선)
{json.dumps(ctx["direct"], ensure_ascii=False, indent=2)}

### SD 싱크 노드
{json.dumps(ctx["sd_nodes"], ensure_ascii=False, indent=2)}

### SDG 싱크 노드
{json.dumps(ctx["sdg_nodes"], ensure_ascii=False, indent=2)}

### 기존 D→I 연결 패턴 (참고)
{json.dumps(ctx["d_to_i"][:15], ensure_ascii=False, indent=2)}

### 다음 가용 DIRECT 노드 ID: {ctx["next_d"]}

---

## 지시

Graph of Thoughts 방식으로 분석하세요:

1. **Breadth**: 이 정책이 직접적으로 만들어내는 물리적 측정값을 모두 나열
2. **싱크 연결성 검토**: 각 측정값이 최종적으로 어떤 SD/SDG에 도달할 수 있는지
   - 직접 연결 또는 I 노드를 3~5번 거쳐 도달 가능한가?
   - 불가능한 측정값은 제외하고 이유 명시
3. **재사용 판단**: 기존 D 노드와 동일한 물리 현상이면 재사용
4. **단위 추론**: 단위가 명시되지 않았으면 정책 특성으로 추론

다음 JSON을 출력하세요:
```json
{{
  "inferred_unit": "추론된 단위 (또는 입력값)",
  "inferred_sector": "추론된 부문 (또는 입력값)",
  "breadth": ["측정값1", "측정값2", ...],
  "pruned_candidates": [
    {{"candidate": "측정값", "reason": "SD/SDG 연결 불가 이유"}}
  ],
  "selected_direct": {{
    "node_id": "D01 또는 {ctx['next_d']}",
    "label_ko": "노드명",
    "unit": "단위",
    "reuse": true,
    "sink_path_preview": "D→I→SD→G 예상 경로 요약",
    "notes": "결정 근거"
  }}
}}
```"""


# ──────────────────────────────────────────────────────────────
# Phase 2 프롬프트: DIRECT → INDIRECT 체인 (가지치기 포함)
# ──────────────────────────────────────────────────────────────

def build_phase2_prompt(
    policy: str,
    phase1_result: dict,
    ctx: dict,
) -> str:
    d_node = phase1_result["selected_direct"]
    next_i = ctx["next_i"]
    next_i2 = f"I{int(next_i[1:])+1:02d}"
    next_i3 = f"I{int(next_i[1:])+2:02d}"

    return f"""## Phase 2: DIRECT → INDIRECT → SDG (기본 PEN 경로, SD 없음)

이 단계에서는 SD 노드를 완전히 무시하고, 순수한 PEN 경로만 생성합니다.
INDIRECT 노드에서 SDG 노드로의 **직접 연결**이 목표입니다.

### 결정된 DIRECT 노드
{json.dumps(d_node, ensure_ascii=False, indent=2)}

### 정책 정보
- 정책명: {policy}
- 단위: {phase1_result.get("inferred_unit", "")}
- 부문: {phase1_result.get("inferred_sector", "")}

### 기존 INDIRECT 노드 (재사용 우선)
{json.dumps(ctx["indirect"], ensure_ascii=False, indent=2)}

### SDG 노드 (경로 최종 목적지)
{json.dumps(ctx["sdg_nodes"], ensure_ascii=False, indent=2)}

### 다음 가용 INDIRECT 노드 ID: {next_i}, {next_i2}, {next_i3} ...

---

## 지시

Graph of Thoughts 방식으로 순수 PEN 경로를 탐색하세요.
**SD 노드는 이 단계에서 고려하지 않습니다.**

1. **Breadth**: DIRECT 노드로부터 발생하는 모든 가능한 물리·경제·생태 중간 효과 나열
2. **Depth**: 각 중간 효과에서 SDG까지 직접 경로 추론 (최대 3~5홉, I→I→I 다단계 가능)
3. **Pruning**: 3~5홉 내 어떤 SDG도 도달 불가한 브랜치는 제거
4. **재사용 판단**: 기존 I 노드와 동일한 물리 현상이면 재사용
5. **I→SDG 직접 연결 가중치**: 단위변환 계수 포함하여 계산, 근거 명시
   - 지역별 차이가 있으면 eff_ULS/SES/SCH 다르게 설정

다음 JSON을 출력하세요:
```json
{{
  "got_phase2": {{
    "breadth": ["중간효과1", "중간효과2", ...],
    "chains": {{
      "체인설명1": "D→I→G 직접 경로 요약 (SDG 명시)",
      "체인설명2": "D→I→I→G 경로 요약"
    }},
    "pruned": [
      {{"branch": "브랜치 설명", "reason": "제거 이유 (SDG 연결 불가)"}}
    ]
  }},
  "reuse_indirect": ["I01", "I04"],
  "new_indirect_nodes": [
    {{
      "node_id": "{next_i}",
      "node_type": "INDIRECT",
      "label_ko": "노드명",
      "unit": "단위",
      "layer": 3,
      "active_regions": "SCH,SES,ULS",
      "llm_generated": true,
      "notes": "생성 근거"
    }}
  ],
  "new_links": [
    {{
      "source": "D01",
      "target": "I01",
      "link_type": "CAUSES",
      "pen_type": "BOTH",
      "base_weight": 0.0,
      "eff_ULS": 0.0,
      "eff_SES": 0.0,
      "eff_SCH": 0.0,
      "active_regions": "SCH,SES,ULS",
      "llm_generated": true,
      "notes": "근거 및 계수 출처 (I→SDG 직접 연결 포함)"
    }}
  ]
}}
```"""


# ──────────────────────────────────────────────────────────────
# Phase 3 프롬프트: INDIRECT → SD 매칭 (SD 추출기 연결)
# ──────────────────────────────────────────────────────────────

def build_phase3_prompt(
    policy: str,
    phase1_result: dict,
    phase2_result: dict,
    indirect_node_ids: list[str],
    ctx: dict,
) -> str:
    # Phase 2에서 생성된 INDIRECT 노드 정보 수집
    all_i_nodes = {nd["node_id"]: nd for nd in ctx["indirect"]}
    for nd in phase2_result.get("new_indirect_nodes", []):
        all_i_nodes[nd["node_id"]] = nd

    selected_i = [all_i_nodes[nid] for nid in indirect_node_ids if nid in all_i_nodes]

    return f"""## Phase 3: INDIRECT → SD 매칭 (SD 모델 추출기 연결)

이 단계는 Phase 2에서 생성된 INDIRECT 노드들 중
**어떤 노드가 어떤 SD 지표에 실질적인 변화를 일으키는지** 매칭합니다.

SD 노드는 SDG와 연결되지 않으며, INDIRECT 노드의 값을 수집하여
SD(시스템다이내믹스) 모델에 제공하는 **정보 추출기**입니다.

### 정책 정보
- 정책명: {policy}
- 단위: {phase1_result.get("inferred_unit", "")}

### Phase 2에서 결정된 INDIRECT 노드들
{json.dumps(selected_i, ensure_ascii=False, indent=2)}

### SD 노드 목록 (추출 대상)
{json.dumps(ctx["sd_nodes"], ensure_ascii=False, indent=2)}

---

## 지시

**Step 1**: 각 INDIRECT 노드와 SD 노드의 연결 가능성 파악
- 각 I 노드의 물리·경제적 변화가 어떤 SD 지표에 실질적 영향을 주는가?
- 단위 비교로 연결 타당성 판단

**Step 2**: 중복 방지 처리 (핵심)
- 동일한 SD 노드와 연결 가능한 I 노드가 둘 이상이면, 체인에서 **가장 하류(마지막) I 노드** 하나에만 연결
- 인과 체인 순서: D→I→I 에서 더 오른쪽(하류)이 마지막
- 이유: 하류 I 노드는 상류 I 노드의 효과를 이미 포함하므로, 상류에도 연결하면 중복 집계 발생
- 단, 서로 다른 SD 노드끼리는 각각 별도 연결 가능

**Step 3**: 연결 가중치 계산
- 물리적 변환 계수 사용, 근거 명시

**중요**: SD→SDG 연결은 생성하지 않습니다. I→SD 연결만 생성합니다.

다음 JSON을 출력하세요:
```json
{{
  "sd_matching": {{
    "matches": [
      {{"indirect": "I04", "sd": "SD03", "reason": "에너지 절감(TOE) → 기존에너지사용량 변화(TOE)", "is_last_in_chain": true}},
      {{"indirect": "I04", "sd": "SD01", "reason": "에너지 절감 → GHG 감축 (환산계수 적용)", "is_last_in_chain": true}}
    ],
    "dedup_skipped": [
      {{"indirect": "I01", "sd": "SD03", "reason": "I01→I04 체인에서 I04가 더 하류이므로 I01 연결 생략 (중복 방지)"}}
    ],
    "no_match": [
      {{"indirect": "I노드ID", "reason": "어떤 SD 지표에도 실질적 영향 없음"}}
    ]
  }},
  "sd_links": [
    {{
      "source": "I04",
      "target": "SD03",
      "link_type": "CAUSES",
      "pen_type": "BOTH",
      "base_weight": -1.0,
      "eff_ULS": -1.174,
      "eff_SES": -1.0,
      "eff_SCH": -1.0,
      "active_regions": "SCH,SES,ULS",
      "llm_generated": true,
      "notes": "에너지절감(TOE) → SD 기존에너지 절감 집계용 (체인 내 마지막 I 노드)"
    }}
  ]
}}
```"""


# ──────────────────────────────────────────────────────────────
# Claude API 호출
# ──────────────────────────────────────────────────────────────

def _call_claude(client: anthropic.Anthropic, model: str, prompt: str) -> dict:
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    return json.loads(raw)


def generate_full_path(
    policy: str,
    regions: list[str],
    policy_unit: str | None = None,
    policy_sector: str | None = None,
    forced_direct: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """정책 → DIRECT → INDIRECT → SD/SDG 전체 경로 생성"""
    ctx = load_context()
    client = anthropic.Anthropic()

    # Phase 1
    p1_prompt = build_phase1_prompt(policy, policy_unit, policy_sector, forced_direct, ctx)
    phase1 = _call_claude(client, model, p1_prompt)

    # forced_direct 재사용인 경우 override
    if forced_direct:
        existing = next((nd for nd in ctx["direct"] if nd["node_id"] == forced_direct), None)
        if existing:
            phase1["selected_direct"] = {
                "node_id": forced_direct,
                "label_ko": existing["label_ko"],
                "unit": existing["unit"],
                "reuse": True,
                "sink_path_preview": "기존 노드 강제 지정",
                "notes": "사용자가 --direct 옵션으로 강제 지정",
            }

    # Phase 2: 기본 PEN 경로 (I→SDG, SD 없음)
    p2_prompt = build_phase2_prompt(policy, phase1, ctx)
    phase2 = _call_claude(client, model, p2_prompt)

    # Phase 3: SD 추출기 매칭 (I→SD, SDG 미연결)
    all_indirect = (
        [nd["node_id"] for nd in ctx["indirect"] if nd["node_id"] in phase2.get("reuse_indirect", [])]
        + [nd["node_id"] for nd in phase2.get("new_indirect_nodes", [])]
    )
    p3_prompt = build_phase3_prompt(policy, phase1, phase2, all_indirect, ctx)
    phase3 = _call_claude(client, model, p3_prompt)

    # 최종 결과 조합
    d_node = phase1["selected_direct"]
    new_direct = [] if d_node.get("reuse") else [{
        "node_id": d_node["node_id"],
        "node_type": "DIRECT",
        "label_ko": d_node["label_ko"],
        "unit": d_node["unit"],
        "layer": 2,
        "active_regions": ",".join(regions),
        "llm_generated": True,
        "notes": d_node.get("notes", ""),
    }]

    # Phase 2 링크(I→SDG) + Phase 3 링크(I→SD) 합산
    all_links = phase2.get("new_links", []) + phase3.get("sd_links", [])

    return {
        "policy": policy,
        "policy_meta": {
            "inferred_unit":   phase1.get("inferred_unit"),
            "inferred_sector": phase1.get("inferred_sector"),
        },
        "got_reasoning": {
            "phase1_breadth": phase1.get("breadth", []),
            "phase1_pruned":  phase1.get("pruned_candidates", []),
            "phase2":         phase2.get("got_phase2", {}),
            "phase3_sd":      phase3.get("sd_matching", {}),
        },
        "reuse_direct":       [d_node["node_id"]] if d_node.get("reuse") else [],
        "new_direct_nodes":   new_direct,
        "reuse_indirect":     phase2.get("reuse_indirect", []),
        "new_indirect_nodes": phase2.get("new_indirect_nodes", []),
        "new_links":          all_links,
    }


# ──────────────────────────────────────────────────────────────
# Excel 적용
# ──────────────────────────────────────────────────────────────

def apply_to_excel(result: dict, dry_run: bool = False) -> None:
    nodes_df = pd.read_excel(PEN_EXCEL, sheet_name="NODES")
    links_df = pd.read_excel(PEN_EXCEL, sheet_name="LINKS")

    new_nodes = result.get("new_direct_nodes", []) + result.get("new_indirect_nodes", [])
    new_links = result.get("new_links", [])

    print(f"\n적용 예정: 노드 {len(new_nodes)}개, 링크 {len(new_links)}개")

    if dry_run:
        print("[DRY RUN] 파일을 수정하지 않습니다.")
        return

    for nd in new_nodes:
        row = {col: None for col in nodes_df.columns}
        row.update(nd)
        nodes_df = pd.concat([nodes_df, pd.DataFrame([row])], ignore_index=True)

    for lk in new_links:
        row = {col: None for col in links_df.columns}
        row.update(lk)
        links_df = pd.concat([links_df, pd.DataFrame([row])], ignore_index=True)

    with pd.ExcelWriter(PEN_EXCEL, engine="openpyxl") as w:
        nodes_df.to_excel(w, sheet_name="NODES", index=False)
        links_df.to_excel(w, sheet_name="LINKS", index=False)

    print(f"저장 완료: {PEN_EXCEL}")
    print("※ pen_builder.py 또는 앱 ⚙️ PEN 관리 탭에서 재구성을 실행하세요.")


# ──────────────────────────────────────────────────────────────
# 결과 출력
# ──────────────────────────────────────────────────────────────

def print_result(result: dict) -> None:
    got = result.get("got_reasoning", {})

    print("\n=== Phase 1: DIRECT 노드 결정 ===")
    pm = result.get("policy_meta", {})
    print(f"추론 단위: {pm.get('inferred_unit')}  부문: {pm.get('inferred_sector')}")
    print(f"검토된 직접효과: {got.get('phase1_breadth', [])}")
    if got.get("phase1_pruned"):
        print("제외된 후보:")
        for p in got["phase1_pruned"]:
            print(f"  ✗ {p.get('candidate','')}: {p.get('reason','')}")
    if result.get("reuse_direct"):
        print(f"재사용 D 노드: {result['reuse_direct']}")
    for nd in result.get("new_direct_nodes", []):
        print(f"신규 D 노드: {nd['node_id']} {nd['label_ko']} ({nd['unit']})")

    print("\n=== Phase 2: INDIRECT → SDG (기본 PEN 경로) ===")
    p2 = got.get("phase2", {})
    if p2.get("chains"):
        for chain, desc in p2["chains"].items():
            print(f"  ✓ {chain}: {desc}")
    if p2.get("pruned"):
        print("가지치기된 브랜치:")
        for p in p2["pruned"]:
            print(f"  ✗ {p.get('branch','')}: {p.get('reason','')}")

    if result.get("reuse_indirect"):
        print(f"재사용 I 노드: {result['reuse_indirect']}")
    for nd in result.get("new_indirect_nodes", []):
        print(f"신규 I 노드: {nd['node_id']} {nd['label_ko']} ({nd['unit']})")
        print(f"  근거: {nd.get('notes','')}")

    print("\n=== Phase 3: SD 추출기 매칭 ===")
    p3 = got.get("phase3_sd", {})
    if p3.get("matches"):
        for m in p3["matches"]:
            print(f"  ✓ {m.get('indirect','')} → {m.get('sd','')}: {m.get('reason','')}")
    if p3.get("dedup_skipped"):
        print("  중복 방지로 생략된 연결:")
        for m in p3["dedup_skipped"]:
            print(f"  ⊘ {m.get('indirect','')} → {m.get('sd','')}: {m.get('reason','')}")
    if p3.get("no_match"):
        for m in p3["no_match"]:
            print(f"  - {m.get('indirect','')}: {m.get('reason','')}")

    print("\n=== 신규 링크 ===")
    links = result.get("new_links", [])
    i_to_sdg = [lk for lk in links if not str(lk.get("target","")).startswith("SD")]
    i_to_sd  = [lk for lk in links if str(lk.get("target","")).startswith("SD")]
    print(f"  [기본 PEN] I→SDG 직접 연결: {len(i_to_sdg)}개")
    for lk in i_to_sdg:
        print(f"    {lk['source']} → {lk['target']}  w={lk.get('base_weight')}  {lk.get('notes','')}")
    print(f"  [SD 추출기] I→SD 연결: {len(i_to_sd)}개")
    for lk in i_to_sd:
        print(f"    {lk['source']} → {lk['target']}  w={lk.get('base_weight')}  {lk.get('notes','')}")


# ──────────────────────────────────────────────────────────────
# 배치: 기존 D 노드 전체 검증/확장
# ──────────────────────────────────────────────────────────────

def apply_batch_to_excel(all_results: list[dict]) -> None:
    """배치 결과를 중복 제거 후 Excel에 일괄 적용"""
    nodes_df = pd.read_excel(PEN_EXCEL, sheet_name="NODES")
    links_df = pd.read_excel(PEN_EXCEL, sheet_name="LINKS")

    existing_node_ids = set(nodes_df["node_id"].astype(str))
    existing_link_keys = set(
        zip(links_df["source"].astype(str), links_df["target"].astype(str))
    )

    added_nodes, added_links = [], []

    for result in all_results:
        for nd in result.get("new_direct_nodes", []) + result.get("new_indirect_nodes", []):
            if nd["node_id"] not in existing_node_ids:
                row = {col: None for col in nodes_df.columns}
                row.update(nd)
                nodes_df = pd.concat([nodes_df, pd.DataFrame([row])], ignore_index=True)
                existing_node_ids.add(nd["node_id"])
                added_nodes.append(nd["node_id"])

        for lk in result.get("new_links", []):
            key = (str(lk["source"]), str(lk["target"]))
            if key not in existing_link_keys:
                row = {col: None for col in links_df.columns}
                row.update(lk)
                links_df = pd.concat([links_df, pd.DataFrame([row])], ignore_index=True)
                existing_link_keys.add(key)
                added_links.append(f"{lk['source']}→{lk['target']}")

    with pd.ExcelWriter(PEN_EXCEL, engine="openpyxl") as w:
        nodes_df.to_excel(w, sheet_name="NODES", index=False)
        links_df.to_excel(w, sheet_name="LINKS", index=False)

    print(f"\n✅ 저장 완료: 신규 노드 {len(added_nodes)}개, 신규 링크 {len(added_links)}개")
    if added_nodes:
        print(f"   노드: {added_nodes}")
    if added_links:
        print(f"   링크: {added_links}")
    print("※ pen_builder.py 또는 앱 ⚙️ PEN 관리 탭에서 재구성을 실행하세요.")


def run_batch_validate(
    model: str = "claude-sonnet-4-6",
    apply: bool = False,
    output_dir: str | None = None,
) -> list[dict]:
    """기존 D 노드 전체를 순차 실행하여 PEN 검증·확장"""
    ctx = load_context()
    direct_nodes = ctx["direct"]
    total = len(direct_nodes)

    print(f"[PEN Batch Validate] D 노드 {total}개 순차 검증 시작")
    print(f"  모델: {model}  apply: {apply}\n")

    all_results = []
    summary_rows = []

    for i, dnd in enumerate(direct_nodes, 1):
        d_id    = dnd["node_id"]
        d_label = dnd["label_ko"]
        d_unit  = dnd["unit"]

        print(f"[{i:2d}/{total}] {d_id} {d_label} ({d_unit})")

        try:
            result = generate_full_path(
                policy=d_label,
                regions=["ULS", "SES", "SCH"],
                policy_unit=d_unit,
                forced_direct=d_id,
                model=model,
            )

            new_i  = [nd["node_id"] for nd in result.get("new_indirect_nodes", [])]
            new_lk = [f"{lk['source']}→{lk['target']}" for lk in result.get("new_links", [])]
            pruned = result.get("got_reasoning", {}).get("phase2", {}).get("pruned", [])

            print(f"  재사용 I: {result.get('reuse_indirect', [])}")
            if new_i:  print(f"  신규 I:   {new_i}")
            if new_lk: print(f"  신규 링크: {new_lk}")
            if pruned: print(f"  가지치기: {len(pruned)}개 브랜치")
            print()

            all_results.append(result)
            summary_rows.append({
                "D노드": d_id,
                "레이블": d_label,
                "재사용I": ",".join(result.get("reuse_indirect", [])),
                "신규I": ",".join(new_i),
                "신규링크수": len(new_lk),
                "가지치기수": len(pruned),
            })

            if output_dir:
                out = Path(output_dir) / f"{d_id}.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        except Exception as e:
            print(f"  ❌ 오류: {e}\n")
            summary_rows.append({
                "D노드": d_id, "레이블": d_label,
                "재사용I": "", "신규I": "", "신규링크수": 0,
                "가지치기수": 0, "오류": str(e),
            })

    # 요약 출력
    print("\n" + "="*60)
    print("[ 배치 검증 요약 ]")
    df_sum = pd.DataFrame(summary_rows)
    print(df_sum.to_string(index=False))

    total_new_i  = df_sum["신규I"].apply(lambda x: len(x.split(",")) if x else 0).sum()
    total_new_lk = df_sum["신규링크수"].sum()
    total_pruned = df_sum["가지치기수"].sum()
    print(f"\n전체 신규 I 노드: {total_new_i}개  신규 링크: {total_new_lk}개  가지치기: {total_pruned}개")

    if apply and all_results:
        apply_batch_to_excel(all_results)
    elif all_results:
        print("\n[DRY RUN] --apply 옵션 추가 시 Excel에 적용됩니다.")

    return all_results


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PEN 경로 자동 생성기 (정책→DIRECT→INDIRECT→SD/SDG)"
    )
    parser.add_argument("--validate-all", action="store_true",
                        help="기존 D 노드 전체 검증·확장 배치 모드")
    parser.add_argument("--policy",  default=None,
                        help="정책 이름/설명 (예: '농촌 태양광 임대 사업')")
    parser.add_argument("--direct",  default=None,
                        help="DIRECT 노드 ID 강제 지정 (생략 시 자동. 기존 D01~D20 입력 시 검토 모드)")
    parser.add_argument("--unit",    default=None,
                        help="정책 투입 단위 (생략 시 Claude 추론)")
    parser.add_argument("--sector",  default=None,
                        help="정책 부문 (생략 시 Claude 추론)")
    parser.add_argument("--regions", default="ULS,SES,SCH",
                        help="활성 지역 (기본: ULS,SES,SCH)")
    parser.add_argument("--apply",   action="store_true",
                        help="Excel에 실제 적용 (없으면 미리보기만)")
    parser.add_argument("--model",   default="claude-sonnet-4-6",
                        help="사용할 Claude 모델")
    parser.add_argument("--output",  help="결과 JSON 저장 경로 (선택)")
    args = parser.parse_args()

    # ── 배치 모드 ──────────────────────────────────────────────
    if args.validate_all:
        run_batch_validate(
            model=args.model,
            apply=args.apply,
            output_dir=args.output,
        )
        return

    # ── 단일 정책 모드 ─────────────────────────────────────────
    if not args.policy:
        parser.error("--policy 또는 --validate-all 중 하나는 필수입니다.")

    regions = [r.strip() for r in args.regions.split(",")]

    print(f"[PEN Path Generator] '{args.policy}'")
    print(f"  direct: {args.direct or '(자동)'}  unit: {args.unit or '(추론)'}  sector: {args.sector or '(추론)'}")
    print(f"  지역: {regions}  모델: {args.model}")
    print("  Phase 1+2 경로 생성 중...\n")

    result = generate_full_path(
        policy=args.policy,
        regions=regions,
        policy_unit=args.unit,
        policy_sector=args.sector,
        forced_direct=args.direct,
        model=args.model,
    )

    print_result(result)

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON 저장: {args.output}")

    apply_to_excel(result, dry_run=not args.apply)


if __name__ == "__main__":
    main()
