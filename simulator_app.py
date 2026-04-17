"""
PEN-SDG 정책 시뮬레이터 v2
============================
실행: streamlit run simulator_app.py

탭 구성:
  Tab 0 — 베이스라인 시나리오  (투입량 테이블 + 배율 설정 + 시뮬레이션 실행)
  Tab 1 — 시뮬레이션 결과      (SDG 연도별 + 부문/정책 트리 + 경로 상세)
  Tab 2 — SD 투입 지표         (SD01~SD05 연도별 부문별 기여)
"""

from __future__ import annotations
import json, time
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import streamlit.components.v1 as components
from pathlib import Path

# ══════════════════════════════════════════════════════════════
# 상수
# ══════════════════════════════════════════════════════════════

CACHE_DIR     = Path(__file__).parent / "sim_cache"
PEN_INPUT_DIR = Path(__file__).parent / "pen_input"

REGION_NAMES = {"ULS": "울산", "SES": "서산", "SCH": "순천"}

SDG_LABELS = {
    "G01": "13.2.2 연간 온실가스 총배출량 (천tCO₂eq)",
    "G02": "7.2.1 재생에너지 비중 (%)",
    "G03": "7.3.1 에너지 집약도 (TOE/억원)",
    "G04": "8.1.1 1인당 실질GDP 성장률 (%)",
    "G05": "8.2.1 취업자당 GDP 성장률 (%)",
    "G06": "9.2.1 제조업 부가가치 비중 (%)",
    "G07": "9.2.2 제조업 고용 비중 (%)",
    "G08": "9.4.1 CO₂ 배출집약도 (tCO₂eq/억원)",
    "G09": "6.6.1 수생태계 면적 변화율 (%)",
    "G10": "11.3.1 토지이용/인구 증가율 비율",
    "G11": "11.7.1 도시 공개공간 비율 (%)",
    "G12": "15.1.1 산림면적 비율 (%)",
    "G13": "15.3.1 황무지 비율 (%)",
}
SD_LABELS = {
    "SD01": "온실가스 (tCO₂eq)",
    "SD02": "신재생에너지 (TOE)",
    "SD03": "기존에너지 (TOE)",
    "SD04": "GDP (억원)",
    "SD05": "취업자 (명)",
}
# 양수 = 좋음(+1), 음수 = 좋음(-1)
SD_GOOD_DIRECTION = {"SD01": -1, "SD02": 1, "SD03": -1, "SD04": 1, "SD05": 1}
# SDG 노드 표시 부호: 모델 내부는 이중 음수로 인해 "감소 지표"가 양수로 나옴 → 표시 시 반전
# -1: 낮을수록 좋은 지표 (온실가스, 집약도 등) → 모델 양수를 음수로 표시 (실제 감소를 반영)
# +1: 높을수록 좋은 지표 (재생에너지, GDP 등) → 모델 양수 그대로 표시
SDG_DISPLAY_SIGN = {
    "G01": -1,  # 연간 온실가스 총배출량 (낮을수록 좋음)
    "G02": +1,  # 재생에너지 비중 (높을수록 좋음)
    "G03": -1,  # 에너지 집약도 (낮을수록 좋음)
    "G04": +1,  # GDP 성장률 (높을수록 좋음)
    "G05": +1,  # 노동생산성 (높을수록 좋음)
    "G06": +1,  # 제조업 부가가치 (높을수록 좋음)
    "G07": +1,  # 제조업 고용 (높을수록 좋음)
    "G08": -1,  # CO₂ 배출집약도 (낮을수록 좋음)
    "G09": +1,  # 수생태계 면적 (높을수록 좋음)
    "G10": -1,  # 토지이용 효율성 비율 (낮을수록 좋음)
    "G11": +1,  # 공개공간 비율 (높을수록 좋음)
    "G12": +1,  # 산림면적 비율 (높을수록 좋음)
    "G13": -1,  # 황무지 비율 (낮을수록 좋음)
}

SECTOR_ORDER = ["건물", "수송", "산업", "에너지", "농축산", "폐기물", "흡수원", "자원순환", "기타"]

# ══════════════════════════════════════════════════════════════
# 캐시 로딩
# ══════════════════════════════════════════════════════════════

@st.cache_resource
def load_region(region: str):
    with open(CACHE_DIR / f"{region}_meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    W = np.load(CACHE_DIR / f"{region}_W.npy")
    # R = I + W + W² + ... W^k까지 동적 계산 (DAG이므로 W^k→0으로 수렴)
    n = W.shape[0]
    R = np.eye(n, dtype=float)
    W_pow = W.copy().astype(float)
    while np.any(np.abs(W_pow) > 1e-12):
        R = R + W_pow
        W_pow = W_pow @ W
    # 링크 타입 딕셔너리: {"source_id:target_id": link_type}
    link_types = meta.get("link_types", {})
    return meta, W, R, link_types


@st.cache_resource
def load_all_years(region: str, years: list[int]) -> dict:
    data = {}
    for yr in years:
        data[yr] = {
            "Pt": np.load(CACHE_DIR / f"{region}_{yr}_Pt_diag.npy"),
        }
    return data


# ══════════════════════════════════════════════════════════════
# 시뮬레이션 핵심 로직
# ══════════════════════════════════════════════════════════════

def apply_multipliers(Pt_diag: np.ndarray, pol_idx: list,
                      pol_ids: list, eff_mults: dict[str, float]) -> np.ndarray:
    """eff_mults {policy_id: multiplier} 적용 → 새 Pt 대각벡터"""
    Pt = Pt_diag.copy()
    for k, pid in enumerate(pol_ids):
        ni = pol_idx[k]
        Pt[ni] = Pt_diag[ni] * eff_mults.get(pid, 1.0)
    return Pt


def run_simulation(region: str, eff_mults: dict[str, float]) -> dict:
    """모든 연도에 대해 baseline/scenario Et 계산"""
    meta, W, R, link_types = load_region(region)
    years = meta["years"]
    year_data = load_all_years(region, years)

    pol_idx  = meta["pol_idx"]
    sdg_idx  = meta["sdg_idx"]
    sd_idx   = meta["sd_idx"]
    dir_idx  = meta["dir_idx"]
    ind_idx  = meta["ind_idx"]
    node_list = meta["node_list"]
    pol_ids  = [node_list[i]["node_id"] for i in pol_idx]
    n = meta["n"]

    results = {}
    for yr in years:
        Pt_base = year_data[yr]["Pt"]
        Pt_scen = apply_multipliers(Pt_base, pol_idx, pol_ids, eff_mults)

        P_base = np.diag(Pt_base)
        P_scen = np.diag(Pt_scen)
        Et_base = P_base @ R
        Et_scen = P_scen @ R

        # 정책 × 전체노드 서브행렬
        Et_base_pols = Et_base[pol_idx, :]   # (n_pol, n)
        Et_scen_pols = Et_scen[pol_idx, :]

        results[yr] = {
            "base_sdg": Et_base_pols[:, sdg_idx].sum(axis=0),   # (n_sdg,)
            "scen_sdg": Et_scen_pols[:, sdg_idx].sum(axis=0),
            "base_sd":  Et_base_pols[:, sd_idx].sum(axis=0),    # (n_sd,)
            "scen_sd":  Et_scen_pols[:, sd_idx].sum(axis=0),
            "base_pol_sdg": Et_base_pols[:, sdg_idx],            # (n_pol, n_sdg)
            "scen_pol_sdg": Et_scen_pols[:, sdg_idx],
            "base_pol_all": Et_base_pols,                         # (n_pol, n) 경로용
            "scen_pol_all": Et_scen_pols,
            "Pt_base": Pt_base,
            "Pt_scen": Pt_scen,
            # SD: 부문별 기여
            "base_sec_sd": _sector_sd(Et_base_pols, sd_idx, pol_ids, meta),
            "scen_sec_sd": _sector_sd(Et_scen_pols, sd_idx, pol_ids, meta),
        }
    return results


def _sector_sd(Et_pols: np.ndarray, sd_idx: list,
               pol_ids: list, meta: dict) -> dict:
    """부문별 SD 기여 {sector: np.array(n_sd,)}"""
    pol_meta = meta["policy_meta"]
    sec_sd = {}
    for k, pid in enumerate(pol_ids):
        sec = pol_meta.get(pid, {}).get("sector", "기타")
        if sec not in sec_sd:
            sec_sd[sec] = np.zeros(len(sd_idx))
        sec_sd[sec] += Et_pols[k, sd_idx]
    return sec_sd


def _sync_sector_mult(sec, pol_ids, pol_meta):
    """부문 배율 변경 시 해당 부문 모든 정책 개별 배율에 동기화"""
    new_val = st.session_state[f"sec_mult_{sec}"]
    for pid in pol_ids:
        if pol_meta.get(pid, {}).get("sector", "기타") == sec:
            st.session_state.policy_mults[pid] = new_val
    if "policy_table" in st.session_state:
        del st.session_state["policy_table"]


# ══════════════════════════════════════════════════════════════
# 세션 초기화
# ══════════════════════════════════════════════════════════════

def init_state():
    defaults = {
        "tab_idx": 0,
        "sim_done": False,
        "sim_results": None,
        "show_dialog": False,
        "sector_mults": {},
        "policy_mults": {},
        "region": "ULS",
        "expand_sector": None,
        "expand_policy": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ══════════════════════════════════════════════════════════════
# 공통 UI 헬퍼
# ══════════════════════════════════════════════════════════════

def tab_bar():
    """커스텀 탭 바 (버튼 기반, 프로그래매틱 전환 지원)"""
    labels = ["📋 베이스라인 시나리오", "🚀 시뮬레이션 결과", "📈 SD 투입 지표", "⚙️ PEN 관리"]
    cols = st.columns(len(labels))
    for i, (col, label) in enumerate(zip(cols, labels)):
        with col:
            is_active = (st.session_state.tab_idx == i)
            btn_type = "primary" if is_active else "secondary"
            if st.button(label, key=f"tab_btn_{i}",
                         use_container_width=True, type=btn_type):
                st.session_state.tab_idx = i
                st.rerun()
    st.markdown("<hr style='margin:0 0 16px 0'>", unsafe_allow_html=True)


def delta_color(val: float, max_abs: float) -> str:
    """델타값에 따른 그라디언트 배경색 (CSS)"""
    if max_abs < 1e-9:
        return "#f5f5f5"
    ratio = min(abs(val) / max_abs, 1.0)
    if val > 0:
        g = int(200 + 55 * ratio)
        r = int(240 - 60 * ratio)
        b = int(240 - 60 * ratio)
    else:
        r = int(200 + 55 * ratio)
        g = int(240 - 60 * ratio)
        b = int(240 - 60 * ratio)
    return f"rgb({r},{g},{b})"


def styled_delta(val: float, fmt: str = ".2f") -> str:
    sign = "+" if val >= 0 else ""
    color = "#1e8449" if val > 0 else ("#c0392b" if val < 0 else "#888")
    return f'<span style="color:{color};font-weight:bold">{sign}{val:{fmt}}</span>'


# ══════════════════════════════════════════════════════════════
# TAB 1: 베이스라인 시나리오
# ══════════════════════════════════════════════════════════════

def tab_baseline(region: str):
    meta, W, R, link_types = load_region(region)
    years = meta["years"]
    year_data = load_all_years(region, years)
    pol_idx   = meta["pol_idx"]
    node_list = meta["node_list"]
    pol_meta  = meta["policy_meta"]
    pol_ids   = [node_list[i]["node_id"] for i in pol_idx]

    st.subheader(f"📋 {REGION_NAMES[region]} — 베이스라인 시나리오 & 배율 설정")

    # ── 1) 부문별 일괄 배율 ────────────────────────────────
    st.markdown("#### 부문별 일괄 배율 설정")
    st.caption("부문 배율을 설정하면 해당 부문 전체 정책에 반영됩니다. 개별 정책 배율이 있으면 우선 적용됩니다.")

    sectors = sorted(set(pol_meta.get(p, {}).get("sector", "기타") for p in pol_ids),
                     key=lambda s: SECTOR_ORDER.index(s) if s in SECTOR_ORDER else 99)
    n_sec = len(sectors)
    sec_cols = st.columns(min(n_sec, 6))
    for i, sec in enumerate(sectors):
        with sec_cols[i % 6]:
            prev = st.session_state.sector_mults.get(sec, 1.0)
            val = st.number_input(
                sec, min_value=0.0, max_value=5.0,
                value=prev, step=0.1, format="%.1f",
                key=f"sec_mult_{sec}",
                label_visibility="visible",
                on_change=_sync_sector_mult,
                args=(sec, pol_ids, pol_meta),
            )
            st.session_state.sector_mults[sec] = val

    st.markdown("---")

    # ── 2) 정책별 연도별 투입량 테이블 + 개별 배율 ────────
    st.markdown("#### 정책별 연도별 투입량 (단위량) 및 개별 배율")
    st.caption("부문 배율 변경 시 해당 부문 정책 개별 배율이 일괄 갱신됩니다. 개별 배율을 1.0으로 설정하면 부문 배율이 적용됩니다.")

    rows = []
    for pid in pol_ids:
        pm = pol_meta.get(pid, {})
        sec = pm.get("sector", "기타")
        row = {
            "policy_id": pid,
            "부문": sec,
            "사업명": pm.get("name", pid),
            "단위": pm.get("unit", ""),
        }
        for yr in years:
            Pt = year_data[yr]["Pt"]
            ni = pol_idx[pol_ids.index(pid)]
            row[str(yr)] = round(float(Pt[ni]), 2)
        rows.append(row)

    df_base = pd.DataFrame(rows)

    # 현재 개별 배율 컬럼 추가
    df_base["개별배율"] = df_base["policy_id"].apply(
        lambda p: st.session_state.policy_mults.get(p, 1.0)
    )

    # 테이블 표시 (스타일: 배율 ≠ 1이면 하이라이트)
    year_cols = [str(y) for y in years]
    display_cols = ["부문", "사업명", "단위"] + year_cols + ["개별배율"]

    edited = st.data_editor(
        df_base[["policy_id"] + display_cols],
        column_config={
            "policy_id": st.column_config.TextColumn("ID", width=120, disabled=True),
            "부문":       st.column_config.TextColumn("부문", width=70, disabled=True),
            "사업명":     st.column_config.TextColumn("사업명", width=200, disabled=True),
            "단위":       st.column_config.TextColumn("단위", width=60, disabled=True),
            **{str(y): st.column_config.NumberColumn(str(y), width=65,
                                                      format="%.2f", disabled=True)
               for y in years},
            "개별배율": st.column_config.NumberColumn(
                "개별배율 ▲▼", width=130, min_value=0.0, max_value=5.0,
                step=0.1, format="%.1f",
                help="셀 클릭 후 ▲▼ 버튼 또는 키보드 ↑↓로 조절. 1.0=기준, 2.0=2배, 0.5=절반",
            ),
        },
        hide_index=True,
        use_container_width=True,
        key="policy_table",
        height=min(60 + len(pol_ids) * 36, 700),
    )

    # 개별 배율 저장
    for _, row in edited.iterrows():
        pid = row["policy_id"]
        st.session_state.policy_mults[pid] = float(row["개별배율"])

    # ── 3) 유효 배율 미리보기 ─────────────────────────────
    with st.expander("📊 유효 배율 미리보기 (배율≠1인 정책만)"):
        eff_rows = []
        for pid in pol_ids:
            sec = pol_meta.get(pid, {}).get("sector", "기타")
            ind = st.session_state.policy_mults.get(pid, 1.0)
            sec_m = st.session_state.sector_mults.get(sec, 1.0)
            eff = ind if ind != 1.0 else sec_m
            if abs(eff - 1.0) > 0.01:
                eff_rows.append({
                    "정책": pol_meta.get(pid, {}).get("name", pid),
                    "부문": sec,
                    "부문배율": sec_m,
                    "개별배율": ind,
                    "적용배율": eff,
                })
        if eff_rows:
            st.dataframe(pd.DataFrame(eff_rows), use_container_width=True, hide_index=True)
        else:
            st.info("배율이 변경된 정책이 없습니다.")

    st.markdown("---")

    # ── 4) Simulation Execution 버튼 ─────────────────────
    col_btn, col_msg = st.columns([2, 5])
    with col_btn:
        run_clicked = st.button("▶ Simulation Execution",
                                type="primary", use_container_width=True)

    if run_clicked:
        # 유효 배율 계산
        eff_mults = {}
        for pid in pol_ids:
            sec = pol_meta.get(pid, {}).get("sector", "기타")
            ind = st.session_state.policy_mults.get(pid, 1.0)
            sec_m = st.session_state.sector_mults.get(sec, 1.0)
            eff_mults[pid] = ind if ind != 1.0 else sec_m

        with st.spinner("⏳ 시뮬레이션 계산 중..."):
            time.sleep(0.5)
            results = run_simulation(region, eff_mults)
            st.session_state.sim_results = results
            st.session_state.sim_done = True
            st.session_state.sim_region = region

        st.session_state.tab_idx = 1   # 완료 즉시 결과 탭으로 이동
        st.rerun()


# ══════════════════════════════════════════════════════════════
# TAB 2: 시뮬레이션 결과
# ══════════════════════════════════════════════════════════════

def tab_simulation(region: str):
    if not st.session_state.sim_done or st.session_state.sim_results is None:
        st.info("📋 '베이스라인 시나리오' 탭에서 배율을 설정하고 시뮬레이션을 실행하세요.")
        return

    results  = st.session_state.sim_results
    sim_reg  = st.session_state.get("sim_region", region)
    meta, W, R, link_types = load_region(sim_reg)
    years    = meta["years"]
    pol_idx  = meta["pol_idx"]
    sdg_idx  = meta["sdg_idx"]
    sd_idx   = meta["sd_idx"]
    node_list = meta["node_list"]
    pol_meta  = meta["policy_meta"]
    pol_ids   = [node_list[i]["node_id"] for i in pol_idx]
    sdg_ids   = [node_list[i]["node_id"] for i in sdg_idx]
    all_ids   = [nd["node_id"] for nd in node_list]

    st.subheader(f"🚀 {REGION_NAMES[sim_reg]} — 시뮬레이션 결과")

    # ── A) SDG 연도별 효과 히트맵 ────────────────────────
    st.markdown("### 📊 SDG 목표별 연도별 효과 (기준선 대비 Δ)")

    # delta 행렬: (n_sdg, n_year)
    delta_mat = np.array([
        [results[yr]["scen_sdg"][si] - results[yr]["base_sdg"][si]
         for yr in years]
        for si in range(len(sdg_ids))
    ])
    # SDG를 최대 abs(delta) 내림차순 정렬
    sort_order = np.argsort(-np.abs(delta_mat).max(axis=1))
    sdg_sorted = [sdg_ids[i] for i in sort_order]
    delta_sorted = delta_mat[sort_order, :]
    sdg_labels_sorted = [SDG_LABELS.get(s, s) for s in sdg_sorted]

    # 행별 정규화: 각 SDG 지표의 최대 abs 값으로 나눠 [-1, 1] 범위로 변환
    # → 단위가 다른 지표끼리 비교 없이 각 행 내에서만 그라데이션 적용
    row_max = np.abs(delta_sorted).max(axis=1, keepdims=True)
    row_max[row_max == 0] = 1  # 모든 값이 0인 행 방지
    delta_normalized = delta_sorted / row_max

    # 표시용 텍스트: % 지표는 값이 소수 비율로 저장된 경우 ×100 변환
    # SDG_PCT_SCALE에 해당하는 지표는 ×100 적용 (0~1 비율 → %)
    SDG_PCT_SCALE = {"G09", "G11", "G12", "G13"}  # 소수 비율(0~1)로 저장된 % 지표
    scale_vec = np.array([
        100.0 if sdg_sorted[i] in SDG_PCT_SCALE else 1.0
        for i in range(len(sdg_sorted))
    ]).reshape(-1, 1)
    delta_display = delta_sorted * scale_vec
    text_formatted = np.vectorize(lambda v: f"{v:.2f}")(delta_display)

    fig_heat = go.Figure(go.Heatmap(
        z=delta_normalized,
        x=[str(y) for y in years],
        y=sdg_labels_sorted,
        colorscale=[
            [0.0, "#c0392b"], [0.45, "#f5b7b1"],
            [0.5, "#f5f5f5"],
            [0.55, "#a9dfbf"], [1.0, "#1e8449"],
        ],
        zmin=-1, zmax=1, zmid=0,
        text=text_formatted,
        texttemplate="%{text}",
        textfont={"size": 10},
        colorbar=dict(title="행별 정규화\n(±1=행최대)"),
        hovertemplate="연도: %{x}<br>지표: %{y}<br>Δ값: %{text}<extra></extra>",
    ))
    fig_heat.update_layout(
        height=420, margin=dict(l=260, t=30, b=30, r=60),
        xaxis=dict(side="top"), font=dict(size=11),
        title="",
    )
    st.plotly_chart(fig_heat, use_container_width=True)
    st.caption("※ 양수(녹색) = 해당 SDG 지표 개선 방향. 온실가스·에너지집약도(G01·G03·G08) 등 낮을수록 좋은 지표는 양수 = 감소(개선)를 의미.")

    # 연도 선택 슬라이더 (상세 분석용)
    sel_year = st.select_slider("🔍 상세 분석 연도 선택", options=years,
                                 value=years[len(years)//2], key="sim_year_sel")
    yr_res = results[sel_year]

    # ── B) 부문별 → 정책별 트리 ──────────────────────────
    st.markdown(f"### 🏭 부문별 정책 효과 ({sel_year}년) — 클릭하여 펼치기")

    # 부문 목록 (정책이 존재하는)
    sectors_in = sorted(set(pol_meta.get(p, {}).get("sector", "기타") for p in pol_ids),
                        key=lambda s: SECTOR_ORDER.index(s) if s in SECTOR_ORDER else 99)

    for sec in sectors_in:
        sec_pol_ids = [p for p in pol_ids
                       if pol_meta.get(p, {}).get("sector", "기타") == sec]
        sec_pol_local = [pol_ids.index(p) for p in sec_pol_ids]

        # 부문 SDG 합계
        base_sec = yr_res["base_pol_sdg"][sec_pol_local, :].sum(axis=0)
        scen_sec = yr_res["scen_pol_sdg"][sec_pol_local, :].sum(axis=0)
        delta_sec = scen_sec - base_sec
        total_delta = delta_sec.sum()
        delta_str = f"+{total_delta:.1f}" if total_delta >= 0 else f"{total_delta:.1f}"
        delta_color_css = "#1e8449" if total_delta > 0 else ("#c0392b" if total_delta < 0 else "#888")

        with st.expander(
            f"**{sec}** — {len(sec_pol_ids)}개 정책  "
            f"| 총 Δ: "
            f"{'↑' if total_delta > 0 else '↓' if total_delta < 0 else '—'} "
            f"{delta_str}",
            expanded=(st.session_state.expand_sector == sec),
        ):
            if st.button(f"▲ 접기", key=f"close_sec_{sec}", help="부문 접기"):
                st.session_state.expand_sector = None
                st.rerun()

            # 부문 SDG 수치 테이블 (SDG지표별 기준선 / 시나리오 / Δ)
            sec_tbl_rows = []
            for gi, gid in enumerate(sdg_ids):
                dsign   = SDG_DISPLAY_SIGN.get(gid, 1)
                raw_d_v = round(float(delta_sec[gi]), 2)
                b_v = round(float(base_sec[gi]) * dsign, 2)
                s_v = round(float(scen_sec[gi]) * dsign, 2)
                d_v = round(float(delta_sec[gi]) * dsign, 2)
                if abs(b_v) < 1e-6 and abs(s_v) < 1e-6:
                    continue   # 이 부문과 무관한 SDG 제외
                sec_tbl_rows.append({
                    "SDG 지표": f"{gid}  {SDG_LABELS.get(gid, '')}",
                    "기준선":   b_v,
                    "시나리오": s_v,
                    "Δ":        d_v,
                    "_raw_d":   raw_d_v,
                })

            if sec_tbl_rows:
                st.caption("※ 수치는 정책 단위량에 RAf 계수를 적용한 SDG 기여값이며, 지표 단위(괄호)로 환산된 결과입니다.")
                df_sec = pd.DataFrame(sec_tbl_rows)

                def _style_sec_rows(df):
                    styles = pd.DataFrame("", index=df.index, columns=df.columns)
                    for i in df.index:
                        raw = df.at[i, "_raw_d"]
                        color = ("color:#1e8449;font-weight:bold" if raw > 0.001
                                 else "color:#c0392b;font-weight:bold" if raw < -0.001
                                 else "color:#888")
                        styles.at[i, "Δ"] = color
                    return styles

                display_cols = ["SDG 지표", "기준선", "시나리오", "Δ"]
                styled_sec = (
                    df_sec.style
                    .apply(_style_sec_rows, axis=None)
                    .format({"기준선": "{:+.2f}", "시나리오": "{:+.2f}", "Δ": "{:+.2f}"})
                    .hide(["_raw_d"], axis="columns")
                )
                st.dataframe(styled_sec, use_container_width=True, hide_index=True,
                             height=min(420, 60 + len(sec_tbl_rows) * 36))
            else:
                st.caption("이 부문에서 SDG 효과 없음")

            # 정책 목록
            for pid in sec_pol_ids:
                pname = pol_meta.get(pid, {}).get("name", pid)
                ki = pol_ids.index(pid)
                base_v  = yr_res["base_pol_sdg"][ki].sum()
                scen_v  = yr_res["scen_pol_sdg"][ki].sum()
                delta_v = scen_v - base_v
                Pt_base_v = float(yr_res["Pt_base"][pol_idx[ki]])
                Pt_scen_v = float(yr_res["Pt_scen"][pol_idx[ki]])
                mult_v = Pt_scen_v / (Pt_base_v + 1e-12)

                d_icon = "↑" if delta_v > 1e-6 else ("↓" if delta_v < -1e-6 else "—")
                d_color = "#1e8449" if delta_v > 1e-6 else ("#c0392b" if delta_v < -1e-6 else "#888")
                mult_str = f"{mult_v:.1f}×" if abs(mult_v - 1.0) > 0.05 else "기준"

                with st.expander(
                    f"　📌 **{pname}** [{mult_str}]  "
                    f"Δ: <span style='color:{d_color}'>{d_icon}{delta_v:+.2f}</span>",
                    expanded=False,
                ):
                    _render_policy_path(
                        pid, ki, yr_res, node_list,
                        pol_idx, sdg_idx, sd_idx, all_ids, W, link_types
                    )

    if st.button("⬆ 모두 접기", key="collapse_all"):
        st.session_state.expand_sector = None
        st.rerun()


def _render_policy_path(pid, ki, yr_res, node_list, pol_idx, sdg_idx, sd_idx, all_ids, W, link_types):
    """개별 정책의 경로 효과를 Mermaid 다이어그램으로 표시"""
    n = len(node_list)
    pol_node_idx = pol_idx[ki]

    base_row  = yr_res["base_pol_all"][ki]   # (n,)
    scen_row  = yr_res["scen_pol_all"][ki]   # (n,)
    delta_row = scen_row - base_row

    # 영향 노드 집합 (정책 노드 자신 포함)
    threshold = 1e-8
    affected_set = {pol_node_idx}
    for j in range(n):
        if abs(base_row[j]) > threshold or abs(scen_row[j]) > threshold:
            affected_set.add(j)

    if len(affected_set) <= 1:
        st.caption("연결된 경로 없음")
        return

    # 레이어 순서 정렬
    layer_order = {"POLICY": 0, "DIRECT": 1, "INDIRECT": 2, "SD_SINK": 2, "SDG_SINK": 4}
    affected_list = sorted(affected_set,
                           key=lambda j: (layer_order.get(node_list[j]["node_type"], 9),
                                          node_list[j]["node_id"]))

    # ── Mermaid 노드 형상 / 클래스 ────────────────────────────
    # POLICY=subroutine[[]], DIRECT=rounded(), INDIRECT=diamond{},
    # SD_SINK=stadium([]), SDG_SINK=cylinder[(])
    OPEN  = {"POLICY": "[[", "DIRECT": "(",  "INDIRECT": "{",  "SD_SINK": "([",  "SDG_SINK": "[("}
    CLOSE = {"POLICY": "]]", "DIRECT": ")",  "INDIRECT": "}",  "SD_SINK": "])",  "SDG_SINK": ")]"}
    CLS   = {"POLICY": "policy", "DIRECT": "direct", "INDIRECT": "indirect",
             "SD_SINK": "sd", "SDG_SINK": "sdg"}

    def mid(j):
        return node_list[j]["node_id"].replace("-", "_").replace(".", "_")

    def fv(v):
        if abs(v) >= 10000:
            return f"{v/1000:.1f}k"
        if abs(v) >= 1000:
            return f"{v:.0f}"
        return f"{v:.2f}"

    lines = ["flowchart LR"]
    lines.append("  classDef policy   fill:#4472C4,color:#fff,stroke:#2952a3,stroke-width:2px")
    lines.append("  classDef direct   fill:#ED7D31,color:#fff,stroke:#b05a1e,stroke-width:1px")
    lines.append("  classDef indirect fill:#FFC000,color:#333,stroke:#cc9900,stroke-width:1px")
    lines.append("  classDef sd       fill:#1ABC9C,color:#fff,stroke:#148f77,stroke-width:1px")
    lines.append("  classDef sdg      fill:#9E0FA6,color:#fff,stroke:#6b0a72,stroke-width:2px")

    # 노드 정의
    Pt_base_v = float(yr_res["Pt_base"][pol_node_idx])
    Pt_scen_v = float(yr_res["Pt_scen"][pol_node_idx])

    for j in affected_list:
        nd    = node_list[j]
        ntype = nd["node_type"]
        nid   = nd["node_id"]
        m     = mid(j)
        op    = OPEN.get(ntype, "[")
        cl    = CLOSE.get(ntype, "]")
        cls   = CLS.get(ntype, "")

        unit     = nd.get("unit", "")
        unit_str = f" ({unit})" if unit else ""
        lbl_name = nd.get("label", nid) + unit_str
        if ntype == "POLICY":
            lbl = lbl_name + "\\n기준:" + fv(Pt_base_v) + " → " + fv(Pt_scen_v)
        else:
            # SDG 노드: 이중 음수 보정 (낮을수록 좋은 지표는 부호 반전하여 직관적으로 표시)
            dsign = SDG_DISPLAY_SIGN.get(nid, 1) if ntype == "SDG_SINK" else 1
            bv = base_row[j] * dsign
            dv = delta_row[j] * dsign
            sign = "+" if dv >= 0 else ""
            lbl = lbl_name + "\\n기준:" + fv(bv) + "  Δ:" + sign + fv(dv)

        # 라벨 내 특수문자 이스케이프 (")  → 단일따옴표 대체
        lbl = lbl.replace('"', "'")
        lines.append(f'  {m}{op}"{lbl}"{cl}:::{cls}')

    # 엣지 (W 행렬 기반)
    for i in affected_list:
        for j in affected_list:
            if i == j:
                continue
            w = float(W[i][j])
            if abs(w) > 1e-9:
                src_id = node_list[i]["node_id"]
                tgt_id = node_list[j]["node_id"]
                lt = link_types.get(f"{src_id}:{tgt_id}", "")
                # 링크 타입 약어 표시
                lt_abbr = {"CAUSES": "→원인", "TRANSFORMS_TO": "→변환",
                           "CONTRIBUTES_TO": "→기여", "FORMULA": "→산식"}.get(lt, lt)
                lines.append(f'  {mid(i)}-->|"{lt_abbr}"|{mid(j)}')

    mermaid_code = "\n".join(lines)

    # 완전한 HTML 문서로 Mermaid 렌더링 (startOnLoad + pre.mermaid 방식)
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({{
    startOnLoad: true,
    theme: 'base',
    themeVariables: {{
      fontSize: '13px',
      primaryColor: '#4472C4',
      primaryTextColor: '#fff',
      primaryBorderColor: '#2952a3',
      lineColor: '#555',
      background: '#ffffff'
    }}
  }});
</script>
<style>
  body {{ margin: 0; padding: 8px; background: #f9f9f9; font-family: sans-serif; }}
  .mermaid {{ background: white; padding: 12px; border-radius: 8px;
              box-shadow: 0 1px 4px rgba(0,0,0,0.12); }}
</style>
</head>
<body>
<pre class="mermaid">
{mermaid_code}
</pre>
</body>
</html>"""
    # 노드 수에 따라 높이 동적 조절
    n_nodes = len(affected_list)
    height = max(320, min(600, 160 + n_nodes * 60))
    components.html(html, height=height, scrolling=True)


# ══════════════════════════════════════════════════════════════
# TAB 3: SD 투입 지표
# ══════════════════════════════════════════════════════════════

def tab_sd_indicators(region: str):
    meta, W, R, link_types = load_region(region)
    years      = meta["years"]
    sd_idx     = meta["sd_idx"]
    pol_idx    = meta["pol_idx"]
    node_list  = meta["node_list"]
    pol_meta   = meta["policy_meta"]
    sd_ids     = [node_list[i]["node_id"] for i in sd_idx]
    pol_ids    = [node_list[i]["node_id"] for i in pol_idx]

    st.subheader(f"📈 {REGION_NAMES[region]} — SD 투입 지표")

    sim_reg  = st.session_state.get("sim_region", region)
    sim_done = st.session_state.sim_done and st.session_state.sim_results and sim_reg == region

    year_data = load_all_years(region, years)

    # 연도 선택 (표시용)
    sel_yr = st.select_slider(
        "연도 선택", options=years,
        value=years[len(years) // 2], key="sd_year_sel"
    )

    # ── 공통 헬퍼 ────────────────────────────────────────────
    # 전 연도 베이스 부문별 SD 사전 계산
    base_all = {}
    for yr in years:
        Pt = year_data[yr]["Pt"]
        Et_pols = (np.diag(Pt) @ R)[pol_idx, :]
        base_all[yr] = _sector_sd(Et_pols, sd_idx, pol_ids, meta)

    all_secs = sorted(
        base_all[sel_yr].keys(),
        key=lambda s: SECTOR_ORDER.index(s) if s in SECTOR_ORDER else 99
    )

    good_dirs = {
        f"{sd_id}  {SD_LABELS.get(sd_id, sd_id)}": SD_GOOD_DIRECTION.get(sd_id, 1)
        for sd_id in sd_ids
    }

    def _build_display_df(sec_sd_dict):
        """선택 연도 기준 가로형 표 (SD지표 × 부문)"""
        rows = {}
        for si, sd_id in enumerate(sd_ids):
            lbl = f"{sd_id}  {SD_LABELS.get(sd_id, sd_id)}"
            row = {sec: round(float(sec_sd_dict.get(sec, np.zeros(len(sd_idx)))[si]), 2)
                   for sec in all_secs}
            row["합계"] = round(sum(row[s] for s in all_secs), 2)
            rows[lbl] = row
        df = pd.DataFrame(rows).T
        df.index.name = "SD 지표"
        return df

    def _make_styled(df_in, fmt_str):
        def style_row(row):
            gd = good_dirs.get(row.name, 1)
            return [
                ("background-color:#abebc6;color:#1e5c2e;font-weight:bold" if v * gd > 0.001
                 else "background-color:#f1948a;color:#7b241c;font-weight:bold" if v * gd < -0.001
                 else "color:#888")
                if isinstance(v, (int, float)) else ""
                for v in row
            ]
        return df_in.style.apply(style_row, axis=1).format({c: fmt_str for c in df_in.columns})

    def _build_long_df(all_years_dict, label):
        """전 연도 DB형 롱 포맷 (SD지표, 부문, 연도, 값, 단위)"""
        rows = []
        for yr, sec_sd in all_years_dict.items():
            for sec in all_secs:
                for si, sd_id in enumerate(sd_ids):
                    arr = sec_sd.get(sec, np.zeros(len(sd_idx)))
                    rows.append({
                        "SD지표코드": sd_id,
                        "SD지표명": SD_LABELS.get(sd_id, sd_id),
                        "부문": sec,
                        "연도": yr,
                        label: round(float(arr[si]), 2),
                        "단위": node_list[sd_idx[si]].get("unit", ""),
                    })
        return pd.DataFrame(rows)

    def _to_csv_bytes(df):
        return df.to_csv(index=False).encode("utf-8-sig")

    legend = "🟢 목표 방향 개선  🔴 목표 방향 악화  (SD01·SD03: 감소=개선 / SD02·SD04·SD05: 증가=개선)"

    # ── 표 1: 베이스 시나리오 결과 (부문별) ──────────────────
    st.markdown("### 📋 베이스 시나리오 결과 (부문별)")
    df_base_disp = _build_display_df(base_all[sel_yr])
    st.dataframe(_make_styled(df_base_disp, "{:.2f}"), use_container_width=True,
                 height=min(280, 60 + len(sd_ids) * 38))
    st.caption(legend)

    df_base_long = _build_long_df(base_all, "베이스값")
    st.download_button(
        "⬇ 베이스 시나리오 전 연도 CSV (DB형)",
        data=_to_csv_bytes(df_base_long),
        file_name=f"{region}_SD_baseline_all.csv",
        mime="text/csv",
        key="dl_sd_base",
    )

    # ── 표 2: 시뮬레이션 증감 결과 (부문별) ─────────────────
    st.markdown("### 📊 시뮬레이션 증감 결과 (부문별, Δ = 시나리오 − 기준선)")

    if sim_done:
        results = st.session_state.sim_results

        delta_sel = {}
        for sec in all_secs:
            b = results[sel_yr]["base_sec_sd"].get(sec, np.zeros(len(sd_idx)))
            s = results[sel_yr]["scen_sec_sd"].get(sec, np.zeros(len(sd_idx)))
            delta_sel[sec] = s - b

        df_delta_disp = _build_display_df(delta_sel)
        st.dataframe(_make_styled(df_delta_disp, "{:+.2f}"), use_container_width=True,
                     height=min(280, 60 + len(sd_ids) * 38))
        st.caption(legend)

        delta_all = {}
        for yr in years:
            delta_all[yr] = {
                sec: results[yr]["scen_sec_sd"].get(sec, np.zeros(len(sd_idx)))
                     - results[yr]["base_sec_sd"].get(sec, np.zeros(len(sd_idx)))
                for sec in all_secs
            }
        df_delta_long = _build_long_df(delta_all, "증감량(Δ)")
        st.download_button(
            "⬇ 시뮬레이션 증감 전 연도 CSV (DB형)",
            data=_to_csv_bytes(df_delta_long),
            file_name=f"{region}_SD_delta_all.csv",
            mime="text/csv",
            key="dl_sd_delta",
        )
    else:
        st.info("📋 '베이스라인 시나리오' 탭에서 시뮬레이션을 실행하면 증감량이 표시됩니다.")


# ══════════════════════════════════════════════════════════════
# TAB 4: PEN 관리
# ══════════════════════════════════════════════════════════════

def tab_pen_manager():
    from pen_builder import build_pen_cache

    st.subheader("⚙️ PEN 데이터 관리")
    st.caption("PEN 구조(NODES/LINKS) 또는 정책DB를 업데이트하고 시스템을 재구성합니다.")

    # ── 현재 상태 ────────────────────────────────────────────
    build_info_path = CACHE_DIR / "build_info.json"
    if build_info_path.exists():
        bi = json.loads(build_info_path.read_text(encoding="utf-8"))
        st.success(f"마지막 빌드: {bi.get('built_at','?')[:19].replace('T',' ')}")
        cols = st.columns(len(bi.get("regions", {})))
        for col, (reg, info) in zip(cols, bi.get("regions", {}).items()):
            with col:
                rname = REGION_NAMES.get(reg, reg)
                st.metric(f"{reg} ({rname})",
                          f"{info['nodes']}노드",
                          f"{info['policies']}정책")
    else:
        st.info("빌드 정보 없음. 아래에서 PEN을 재구성하세요.")

    st.markdown("---")

    # ── 파일 업로드 & 다운로드 ──────────────────────────────
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 📁 PEN 구조 파일 (NODES + LINKS)")
        pen_file = st.file_uploader(
            "NODES·LINKS 시트가 있는 Excel",
            type=["xlsx"], key="upload_pen"
        )
        if pen_file:
            dest = PEN_INPUT_DIR / "PEN_nodes_links.xlsx"
            PEN_INPUT_DIR.mkdir(exist_ok=True)
            dest.write_bytes(pen_file.read())
            st.success(f"저장 완료: {dest.name}")

        cur_pen = PEN_INPUT_DIR / "PEN_nodes_links.xlsx"
        if cur_pen.exists():
            st.download_button(
                "⬇ 현재 PEN 구조 파일 다운로드",
                data=cur_pen.read_bytes(),
                file_name="PEN_nodes_links.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_pen",
            )
            st.caption(f"파일 크기: {cur_pen.stat().st_size // 1024} KB")

    with col2:
        st.markdown("#### 📁 정책DB 파일 (POLICY_MASTER)")
        db_file = st.file_uploader(
            "POLICY_MASTER 시트가 있는 Excel",
            type=["xlsx"], key="upload_db"
        )
        if db_file:
            dest = PEN_INPUT_DIR / "policy_db.xlsx"
            PEN_INPUT_DIR.mkdir(exist_ok=True)
            dest.write_bytes(db_file.read())
            st.success(f"저장 완료: {dest.name}")

        cur_db = PEN_INPUT_DIR / "policy_db.xlsx"
        if cur_db.exists():
            st.download_button(
                "⬇ 현재 정책DB 파일 다운로드",
                data=cur_db.read_bytes(),
                file_name="policy_db.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_db",
            )
            st.caption(f"파일 크기: {cur_db.stat().st_size // 1024} KB")

    st.markdown("---")

    # ── PEN 재구성 버튼 ──────────────────────────────────────
    pen_excel = PEN_INPUT_DIR / "PEN_nodes_links.xlsx"
    policy_db = PEN_INPUT_DIR / "policy_db.xlsx"
    ready = pen_excel.exists() and policy_db.exists()

    if not ready:
        st.warning("PEN 구조 파일과 정책DB 파일이 모두 있어야 재구성할 수 있습니다.")
    else:
        if st.button("🔨 PEN 재구성 실행", type="primary", use_container_width=True):
            msgs = []
            with st.status("PEN 재구성 중...", expanded=True) as status:
                def cb(msg):
                    msgs.append(msg)
                    st.write(msg)
                try:
                    result = build_pen_cache(pen_excel, policy_db, CACHE_DIR, progress_cb=cb)
                    status.update(label="✅ 재구성 완료!", state="complete")
                    st.cache_resource.clear()
                    st.rerun()
                except Exception as e:
                    status.update(label=f"❌ 오류: {e}", state="error")
                    st.error(str(e))

    st.markdown("---")
    st.markdown("#### 📋 입력 파일 형식 안내")
    with st.expander("PEN 구조 파일 (PEN_nodes_links.xlsx) 필수 컬럼"):
        st.markdown("""
**NODES 시트:**
| 컬럼 | 설명 |
|------|------|
| `node_id` | 노드 ID (지역prefix-부문-번호, 예: ULS-건물-001) |
| `node_type` | POLICY / DIRECT / INDIRECT / SD_SINK / SDG_SINK |
| `layer` | 1=정책, 2=직접, 3=간접, 4=SD, 5=SDG |
| `label_ko` | 한국어 노드명 |
| `unit` | 단위 |
| `active_regions` | 활성 지역 (예: ULS,SES,SCH) |

**LINKS 시트:**
| 컬럼 | 설명 |
|------|------|
| `source` | 출발 노드 ID |
| `target` | 도착 노드 ID |
| `link_type` | CAUSES / TRANSFORMS_TO / CONTRIBUTES_TO / FORMULA |
| `base_weight` | 기본 가중치 (eff_* 없으면 이 값 사용) |
| `eff_{지역코드}` | 지역별 유효 가중치 (예: eff_ULS) |
| `active_regions` | 활성 지역 |
        """)
    with st.expander("정책DB 파일 (policy_db.xlsx) 필수 컬럼"):
        st.markdown("""
**POLICY_MASTER 시트:**
| 컬럼 | 설명 |
|------|------|
| `policy_id` | 정책 ID (지역prefix-부문-번호) |
| `부문` | 정책 부문 |
| `사업명` | 정책명 |
| `투입단위` | 물리 단위 |
| `정량_정성` | 정량 / 정성 |
| `시작연도` | 정책 시작 연도 |
| `종료연도` | 정책 종료 연도 |
| `감축원단위(tCO2/단위)` | tCO2/물리단위 (없으면 ghg 직접 사용) |
| `ghg_2024` ~ `ghg_2034` | 연도별 GHG 감축 목표량 |
        """)


def _check_auto_rebuild():
    """앱 시작 시 pen_input/ 파일이 sim_cache보다 최신이면 자동 재구성"""
    from pen_builder import build_pen_cache
    pen_excel     = PEN_INPUT_DIR / "PEN_nodes_links.xlsx"
    policy_db     = PEN_INPUT_DIR / "policy_db.xlsx"
    build_marker  = CACHE_DIR / "build_info.json"

    if not (pen_excel.exists() and policy_db.exists()):
        return  # 입력 파일 없으면 스킵

    input_mtime = max(pen_excel.stat().st_mtime, policy_db.stat().st_mtime)
    if build_marker.exists() and build_marker.stat().st_mtime >= input_mtime:
        return  # 이미 최신 상태

    # 자동 재구성 (UI 없이 백그라운드)
    try:
        build_pen_cache(pen_excel, policy_db, CACHE_DIR)
        st.cache_resource.clear()
    except Exception as e:
        st.warning(f"자동 PEN 재구성 실패: {e}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    st.set_page_config(
        page_title="PEN-SDG 정책 시뮬레이터",
        page_icon="🌿",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    init_state()
    _check_auto_rebuild()  # pen_input/ 파일이 최신이면 자동 재구성

    # ── 사이드바: 지역 선택 ────────────────────────────────
    def _on_region_change():
        st.session_state.sim_done    = False
        st.session_state.sector_mults = {}
        st.session_state.policy_mults = {}
        st.session_state.sim_results  = None

    with st.sidebar:
        st.markdown("## 🌿 PEN-SDG 시뮬레이터")
        region = st.selectbox(
            "📍 지역 선택",
            options=["ULS", "SES", "SCH"],
            format_func=lambda r: f"{r} — {REGION_NAMES[r]}",
            key="region",          # session_state.region 에 직접 바인딩
            on_change=_on_region_change,
        )

        st.markdown("---")
        if st.button("🔄 캐시 초기화", use_container_width=True,
                     help="R 행렬 재계산. 모델 데이터 변경 후 실행하세요."):
            st.cache_resource.clear()
            st.rerun()
        st.markdown("---")
        if st.session_state.sim_done:
            st.success("✅ 시뮬레이션 완료")
            if st.button("📊 결과 탭으로", use_container_width=True):
                st.session_state.tab_idx = 1
                st.rerun()
        else:
            st.info("배율 설정 후\n▶ Simulation Execution")

        st.markdown("---")
        st.caption("**사용 방법**\n1. 지역 선택\n2. 베이스라인 탭에서 배율 설정\n3. Simulation Execution 실행\n4. 결과 탭에서 확인")

    # ── 상단 타이틀 ───────────────────────────────────────
    st.title(f"🌿 PEN-SDG 정책 시뮬레이터  |  {REGION_NAMES[region]}")

    # ── 커스텀 탭 바 ──────────────────────────────────────
    tab_bar()

    # ── 탭 내용 렌더링 ────────────────────────────────────
    idx = st.session_state.tab_idx
    if idx == 0:
        tab_baseline(region)
    elif idx == 1:
        tab_simulation(region)
    elif idx == 2:
        tab_sd_indicators(region)
    elif idx == 3:
        tab_pen_manager()


if __name__ == "__main__":
    main()
