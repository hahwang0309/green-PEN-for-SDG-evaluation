"""
pen_builder.py
==============
PEN 동적 빌드 파이프라인

입력:
  pen_excel   : NODES + LINKS 시트가 있는 Excel (PEN_nodes_links.xlsx)
  policy_db   : POLICY_MASTER 시트가 있는 Excel (policy_db.xlsx)
  output_dir  : sim_cache/ 경로

출력 (지역별):
  {region}_W.npy          — 가중치 행렬
  {region}_{year}_Pt_diag.npy — 정책 투입량 벡터
  {region}_meta.json      — 노드 메타데이터
  build_info.json         — 빌드 타임스탬프 및 요약
"""

from __future__ import annotations
import json
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path


# ──────────────────────────────────────────────────────────────
# 노드 타입 → layer_order 매핑
# ──────────────────────────────────────────────────────────────
LAYER_ORDER = {
    "POLICY":   0,
    "DIRECT":   1,
    "INDIRECT": 2,
    "SD_SINK":  2,
    "SDG_SINK": 4,
}

NODE_TYPE_BY_LAYER = {1: "POLICY", 2: "DIRECT", 3: "INDIRECT",
                      4: "SD_SINK", 5: "SDG_SINK"}


def _log(msg: str, cb=None):
    if cb:
        cb(msg)
    else:
        import sys
        print(msg, file=sys.stdout if sys.stdout.encoding and
              sys.stdout.encoding.lower() in ("utf-8","utf-8-sig") else sys.stderr)
        try:
            sys.stdout.flush()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────
# 메인 빌드 함수
# ──────────────────────────────────────────────────────────────

def build_pen_cache(
    pen_excel: Path,
    policy_db: Path,
    output_dir: Path,
    progress_cb=None,
) -> dict:
    """
    Excel 2개 → sim_cache 전체 재구성.
    Returns: {region: {"nodes": n, "years": [...], "policies": k}}
    """
    pen_excel  = Path(pen_excel)
    policy_db  = Path(policy_db)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _log("📖 Excel 파일 읽기...", progress_cb)

    # ── Excel 로드 ───────────────────────────────────────────
    nodes_df = pd.read_excel(pen_excel, sheet_name="NODES")
    links_df = pd.read_excel(pen_excel, sheet_name="LINKS")
    pdb_df   = pd.read_excel(policy_db, sheet_name="POLICY_MASTER")

    # ── 지역 자동 감지: LINKS의 eff_* 컬럼 ──────────────────
    region_codes = sorted(
        c.replace("eff_", "")
        for c in links_df.columns
        if c.startswith("eff_")
    )
    _log(f"🌏 감지된 지역: {region_codes}", progress_cb)

    summary = {}

    for region in region_codes:
        _log(f"\n🔨 [{region}] 빌드 시작...", progress_cb)

        # ── 1. 지역별 노드 필터 및 정렬 ─────────────────────
        # POLICY: 해당 지역 prefix(ULS-, SES-, SCH-)의 노드만 포함
        # 비POLICY: 모든 지역 공유 (DIRECT, INDIRECT, SD_SINK, SDG_SINK)
        pol_mask     = (nodes_df["node_type"] == "POLICY") & \
                       (nodes_df["node_id"].str.startswith(f"{region}-"))
        nonpol_mask  = nodes_df["node_type"] != "POLICY"
        region_nodes = pd.concat(
            [nodes_df[pol_mask], nodes_df[nonpol_mask]]
        ).sort_values(by=["layer", "node_id"]).reset_index(drop=True)

        n = len(region_nodes)
        node_idx: dict[str, int] = {
            row["node_id"]: i
            for i, row in region_nodes.iterrows()
        }

        _log(f"  노드 수: {n}", progress_cb)

        # ── 2. 인덱스 그룹 ───────────────────────────────────
        def _idx_list(ntype: str) -> list[int]:
            return [
                node_idx[nid]
                for nid in region_nodes.loc[
                    region_nodes["node_type"] == ntype, "node_id"
                ]
            ]

        pol_idx = _idx_list("POLICY")
        dir_idx = _idx_list("DIRECT")
        ind_idx = _idx_list("INDIRECT")
        sd_idx  = _idx_list("SD_SINK")
        sdg_idx = _idx_list("SDG_SINK")
        all_idx = list(range(n))

        # ── 3. W 행렬 구성 ───────────────────────────────────
        # 규칙:
        #  - POLICY 소스: 해당 region prefix(ULS-, SES-, SCH-)인 경우만
        #  - 비POLICY 소스(D,I,SD): 두 노드 모두 node_idx에 있으면 포함
        #  - 가중치: eff_{region} 우선, NaN이면 base_weight 사용
        W = np.zeros((n, n), dtype=float)
        eff_col = f"eff_{region}"
        # POLICY 노드 ID 집합
        policy_node_ids = set(
            region_nodes.loc[region_nodes["node_type"] == "POLICY", "node_id"]
        )

        for _, row in links_df.iterrows():
            src = str(row["source"])
            tgt = str(row["target"])
            if src not in node_idx or tgt not in node_idx:
                continue
            # POLICY 소스: 이 지역 prefix인지 확인
            if src in policy_node_ids and not src.startswith(f"{region}-"):
                continue  # 다른 지역 정책 링크 제외
            # 가중치: eff_{region} → base_weight 순서
            w_val = row.get(eff_col, np.nan)
            if pd.isna(w_val):
                w_val = row.get("base_weight", np.nan)
            if pd.notna(w_val):
                W[node_idx[src], node_idx[tgt]] = float(w_val)

        np.save(output_dir / f"{region}_W.npy", W)
        _log(f"  W 행렬 저장 ({n}×{n}, 비영 원소: {np.count_nonzero(W)})", progress_cb)

        # ── 4. Pt_diag 구성 ──────────────────────────────────
        region_pol = pdb_df[
            pdb_df["policy_id"].fillna("").str.startswith(region)
        ]
        ghg_cols = sorted(
            c for c in pdb_df.columns if str(c).startswith("ghg_")
        )
        rate_col = "감축원단위(tCO2/단위)"

        years: list[int] = []
        baseline_qty: dict[str, float] = {}

        for yr_col in ghg_cols:
            try:
                yr = int(str(yr_col).replace("ghg_", ""))
            except ValueError:
                continue
            years.append(yr)

            Pt = np.zeros(n, dtype=float)
            for _, pol in region_pol.iterrows():
                pid = pol["policy_id"]
                if pid not in node_idx:
                    continue
                idx = node_idx[pid]

                # 시작/종료 연도 범위 체크
                start_yr = pol.get("시작연도", None)
                end_yr   = pol.get("종료연도", None)
                start_yr = int(start_yr) if pd.notna(start_yr) else None
                end_yr   = int(end_yr)   if pd.notna(end_yr)   else None
                if start_yr is not None and yr < start_yr:
                    continue  # 아직 시작 전
                if end_yr is not None and yr > end_yr:
                    continue  # 이미 종료

                ghg_val = pol.get(yr_col, 0.0)
                ghg_val = float(ghg_val) if pd.notna(ghg_val) else 0.0
                rate_val = pol.get(rate_col, np.nan)
                rate_val = float(rate_val) if pd.notna(rate_val) else np.nan

                if not np.isnan(rate_val) and rate_val > 0:
                    Pt[idx] = ghg_val / rate_val
                elif ghg_val != 0.0:
                    Pt[idx] = ghg_val       # 정성/직접 사용
                else:
                    Pt[idx] = 1.0           # 활성 정책, 수치 미입력 → 이진(1)

            np.save(output_dir / f"{region}_{yr}_Pt_diag.npy", Pt)

        # baseline_qty: 첫 연도 Pt 값
        first_yr = years[0] if years else None
        if first_yr:
            Pt_first = np.load(output_dir / f"{region}_{first_yr}_Pt_diag.npy")
            for _, pol in region_pol.iterrows():
                pid = pol["policy_id"]
                if pid in node_idx:
                    baseline_qty[pid] = float(Pt_first[node_idx[pid]])

        _log(f"  Pt_diag 저장 ({len(years)}개 연도)", progress_cb)

        # ── 5. link_types 딕셔너리 ───────────────────────────
        link_types: dict[str, str] = {}
        for _, row in links_df.iterrows():
            src, tgt = str(row["source"]), str(row["target"])
            if src in node_idx and tgt in node_idx:
                link_types[f"{src}:{tgt}"] = str(row.get("link_type", ""))

        # ── 6. policy_meta ───────────────────────────────────
        policy_meta: dict[str, dict] = {}
        for _, pol in region_pol.iterrows():
            pid = pol["policy_id"]
            policy_meta[pid] = {
                "name":       str(pol.get("사업명", pid)),
                "sector":     str(pol.get("부문", "")),
                "unit":       str(pol.get("투입단위", "")),
                "start_year": int(pol["시작연도"]) if pd.notna(pol.get("시작연도")) else None,
                "end_year":   int(pol["종료연도"]) if pd.notna(pol.get("종료연도")) else None,
            }

        # ── 7. node_list 구성 ────────────────────────────────
        node_list = []
        for i, row in region_nodes.iterrows():
            node_list.append({
                "node_id":    str(row["node_id"]),
                "node_type":  str(row["node_type"]),
                "label":      str(row["label_ko"]),
                "unit":       str(row["unit"]) if pd.notna(row["unit"]) else "",
                "layer_order": LAYER_ORDER.get(str(row["node_type"]), 9),
            })

        # ── 8. meta.json 저장 ────────────────────────────────
        meta = {
            "region":       region,
            "years":        years,
            "n":            n,
            "node_list":    node_list,
            "pol_idx":      pol_idx,
            "dir_idx":      dir_idx,
            "ind_idx":      ind_idx,
            "sd_idx":       sd_idx,
            "sdg_idx":      sdg_idx,
            "idx":          all_idx,
            "policy_meta":  policy_meta,
            "baseline_qty": baseline_qty,
            "link_types":   link_types,
            "sdg_labels":   {},   # 앱에서 SDG_LABELS 상수 사용
        }

        meta_path = output_dir / f"{region}_meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _log(f"  meta.json 저장 (정책 {len(pol_idx)}개)", progress_cb)

        summary[region] = {
            "nodes":    n,
            "years":    years,
            "policies": len(pol_idx),
        }

    # ── 빌드 정보 저장 ───────────────────────────────────────
    build_info = {
        "built_at": datetime.now().isoformat(),
        "pen_excel": pen_excel.name,
        "policy_db": policy_db.name,
        "regions":   summary,
    }
    (output_dir / "build_info.json").write_text(
        json.dumps(build_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _log(f"\n✅ 빌드 완료: {list(summary.keys())}", progress_cb)
    return summary


# ──────────────────────────────────────────────────────────────
# CLI 실행
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    base = Path(__file__).parent
    pen_excel = base / "pen_input" / "PEN_nodes_links.xlsx"
    policy_db = base / "pen_input" / "policy_db.xlsx"
    out_dir   = base / "sim_cache"

    if not pen_excel.exists():
        print(f"파일 없음: {pen_excel}")
        sys.exit(1)
    if not policy_db.exists():
        print(f"파일 없음: {policy_db}")
        sys.exit(1)

    result = build_pen_cache(pen_excel, policy_db, out_dir)
    for region, info in result.items():
        print(f"{region}: 노드={info['nodes']}, 정책={info['policies']}, 연도={info['years']}")
