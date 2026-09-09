# -*- coding: utf-8 -*-
"""전체 코드 구조 시각화 Playground 생성기 (독립 HTML).

실제 import 의존성을 파싱해 모듈을 카드 노드로, 의존을 화살표로 그린다.
- 각 노드에 한 줄 역할 설명, 파스텔 카테고리 색, 코어 모듈 ★
- 실행(런타임) 단계 기준 왼->오 계층 정렬
- 좌측 사이드바: 프리셋 보기 / 그룹 표시(토글) / 줌 / 코멘트
- 노드 클릭 = 연결만 강조(focus) + 메모(브라우저 localStorage)
- 그래프 엔진: vis-network (CDN)

사용법: python scripts/build_playground.py  ->  docs/code_playground.html
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "code_playground.html"

GROUP_PREFIX = [
    ("service.pipeline", "pipeline"),
    ("service.backtest", "backtest"),
    ("service.download", "download"),
    ("service.report", "report"),
]


def group_of(mod: str) -> str:
    for pre, g in GROUP_PREFIX:
        if mod.startswith(pre):
            return g
    return "entry"


# 실행(런타임) 단계 = 왼->오 컬럼.
STAGE_NAMES = ["진입", "다운로드", "팩터분석", "수익률·상관", "팩터선정",
               "가중치", "MP 조립", "백테스트", "리포트·시각화"]
LEVELS = {
    "main": 0, "config": 0,
    "download_factors": 1, "download_validation": 1, "parquet_io": 1,
    "factor_analysis": 2,
    "weight_construction": 3,
    "factor_selection": 4,
    "optimization": 5, "weight_history": 5,
    "model_portfolio": 6,
    "walk_forward_engine": 7, "data_slicer": 7, "result_stitcher": 7, "overfit_diagnostics": 7,
    "report_generator": 8, "dashboard": 8, "dashboard_data": 8, "dashboard_charts": 8,
}

# 모듈별 한 줄 역할 설명
DESC = {
    "main": "CLI 진입점 · download/mp/backtest/viz 라우팅",
    "config": "환경설정 · PIPELINE_PARAMS · .env 로드",
    "download_factors": "SQL Server → factor parquet 다운로드",
    "download_validation": "다운로드 데이터 무결성 검증",
    "parquet_io": "연도분할 parquet 저장 / 병합 로드",
    "factor_analysis": "5분위 · 섹터필터 · L/N/S 라벨링",
    "weight_construction": "롱-숏 종목 수익률(net-of-cost) 계산",
    "factor_selection": "rank_score 랭킹 · 클러스터 dedup · 히스테리시스",
    "optimization": "style_cap 제약 동일가중(CEW) 재분배",
    "weight_history": "월별 가중치 이력 저장 (전월대비 delta용)",
    "model_portfolio": "MP 오케스트레이터 · 종목별 비중 · CSV 산출",
    "walk_forward_engine": "Walk-Forward(OOS) 백테스트 엔진",
    "data_slicer": "IS / OOS 날짜 슬라이싱",
    "result_stitcher": "OOS 결과 접합 · 성과 지표",
    "overfit_diagnostics": "과적합 진단 (Funnel · Jaccard 등)",
    "report_generator": "팩터/섹터/분위 PDF 리포트",
    "dashboard": "대시보드 HTML 조립 (viz)",
    "dashboard_data": "대시보드 데이터 레이어 (read-only)",
    "dashboard_charts": "대시보드 plotly 차트",
}
CORE = {"model_portfolio"}  # ★ 표시할 코어 모듈

# 모듈별 자세한 역할 설명 (사이드바 메모 칸 위에 표시)
DETAIL = {
    "main": "CLI 진입점. argparse로 download/mp/backtest/viz 서브커맨드를 파싱해 각 파이프라인으로 라우팅한다. backtest는 _run_backtest()에서 엔진 실행 + 결과/진단 CSV + weight_history 직렬화까지 담당.",
    "config": "전역 설정. .env(python-dotenv)에서 DB 접속정보를 로드하고, PIPELINE_PARAMS에 style_cap·거래비용·top_factor_count·랭킹기준·선정 히스테리시스·backtest_cost_multiplier 등 비즈니스 파라미터를 중앙 관리한다. 거의 모든 모듈이 참조.",
    "download_factors": "SQL Server에서 200+ 팩터 원천 데이터를 받아 pipeline-ready 연도분할 parquet로 저장(download 커맨드). 증분 모드 지원. parquet_io·download_validation 사용.",
    "download_validation": "다운로드 factor 데이터 무결성 검증(필수컬럼·월수·팩터/종목 수·NaN비율·월 gap·중복). ERROR 발견 시 적재 중단.",
    "parquet_io": "연도별 분할 parquet 저장/로드 유틸. load_factor_parquet()가 연도별 파일을 투명하게 병합(단일파일 하위호환). 모든 데이터 진입의 IO 계층.",
    "factor_analysis": "5분위 포트폴리오 구성 + 섹터 필터링 + L/N/S 라벨링([2][3]). Q1-Q5 스프레드 음수 섹터 제거, 분위 평균수익으로 롱/숏/중립 결정. 횡단면 정렬이라 시계열 과적합 아님. 백테스트 OOS에선 IS 규칙(rule_bundle)을 직접 적용(재학습 금지 = look-ahead 방지).",
    "weight_construction": "팩터별 롱-숏 종목 수익률을 벡터연산으로 산출(net-of-cost). 종목 내부 회전 turnover에 거래비용(bps) 차감. 롱/숏 leg 합산이 팩터 순수익률.",
    "factor_selection": "rank_score(기본 tstat) 기준 Top-N 팩터 선정. 클러스터 dedup(상관 기반 중복 제거) + 선정 히스테리시스(챌린저가 margin 이상 이겨야 교체)로 turnover 억제. mp·백테스트 공통.",
    "optimization": "style_cap(기본 25%) 제약 하 동일가중(CEW) 재분배. 1/N에서 시작해 캡 초과 스타일을 반복 축소·정규화. 학습 가중치 없는 결정론적 제약.",
    "weight_history": "운영 mp의 월별 팩터/스타일 가중치 이력 저장(output/mp_weight_history/). 전월대비 delta 분석용. test 모드 skip.",
    "model_portfolio": "MP 파이프라인 오케스트레이터(코어). 데이터로딩→5분위→필터/라벨→롱숏수익→Top-N 선정→style_cap 가중치→종목별 mp_ls_weight 집계→CSV(Bloomberg 입력)까지 run()에서 순차 실행. 백테스트도 이 모듈을 래핑. OUTPUT_DIR/DATA_DIR 상수 보유.",
    "walk_forward_engine": "Walk-Forward(Expanding) 백테스트 엔진. 파이프라인을 한 줄도 수정 않고 감싸며 Tier1(6M 규칙학습)·Tier2(3M 선정·가중치)·Tier3(월 OOS 조회) 계층 리밸런싱. OOS look-ahead 방지 위해 IS 전용 규칙 매핑. 비용 = 종목비용 × backtest_cost_multiplier(기본 60bp).",
    "data_slicer": "백테스트 IS/OOS 날짜 슬라이싱 유틸. 누적창(expanding) IS 범위를 날짜 필터로 제어.",
    "result_stitcher": "OOS 월별 결과를 접합해 WalkForwardResult 구성(누적수익·가중치이력·팩터수익이력·리밸로그). CAGR/MDD/Sharpe/Calmar 등 성과지표 계산.",
    "overfit_diagnostics": "과적합 진단. Funnel Value-Add(A<B<C)·OOS 백분위·Strict Jaccard·IS-OOS 랭크상관·Deflation Ratio. 패턴: NORMAL / CONSTRAINT_DRAG / FILTER_OVERFIT.",
    "report_generator": "팩터수익/섹터/분위 스프레드를 matplotlib PDF 멀티페이지 리포트로 생성(mp 실행 시). 스타일→색 매핑.",
    "dashboard": "viz 대시보드 조립 레이어. 기존 output CSV만 읽어 plotly 단일 HTML 생성(read-only). build_dashboard()가 백테스트+현재포트 섹션 조립.",
    "dashboard_data": "대시보드 데이터 레이어(순수함수). CSV→정돈 DataFrame + 낙폭/KPI/스타일집계/상위 롱숏/회전율/churn/섹터 등 파생. plotly 의존 없어 테스트 용이.",
    "dashboard_charts": "대시보드 plotly Figure 생성 레이어(누적수익·낙폭·분포·스타일배분·섹터·회전율 등). STYLE_COLORS 매핑.",
}


def extract_graph():
    files = [ROOT / "main.py", ROOT / "config.py"]
    files += [p for p in (ROOT / "service").rglob("*.py") if p.name != "__init__.py"]

    def modid(p: Path) -> str:
        return p.relative_to(ROOT).as_posix()[:-3].replace("/", ".")

    ids = {modid(p) for p in files}
    edges = set()

    def resolve(frommod: str, name: str):
        cand = f"{frommod}.{name}"
        if cand in ids:
            return cand
        if frommod in ids:
            return frommod
        return None

    for p in files:
        src = modid(p)
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            mfrom = re.match(r"\s*from\s+(service\.[\w.]+|config)\s+import\s+(.+)", line)
            if mfrom:
                frommod = mfrom.group(1)
                for nm in re.split(r"[,\(]", mfrom.group(2)):
                    nm = nm.strip().strip("()").split(" as ")[0].strip()
                    if nm and nm != "*":
                        tgt = resolve(frommod, nm)
                        if tgt and tgt != src:
                            edges.add((src, tgt))
                continue
            mimp = re.match(r"\s*import\s+(service\.[\w.]+|config)\b", line)
            if mimp and mimp.group(1) in ids and mimp.group(1) != src:
                edges.add((src, mimp.group(1)))

    indeg = {i: 0 for i in ids}
    outdeg = {i: 0 for i in ids}
    for a, b in edges:
        indeg[b] += 1
        outdeg[a] += 1

    nodes = []
    for mid in sorted(ids):
        label = mid.split(".")[-1]
        nodes.append({
            "id": mid, "label": label, "group": group_of(mid),
            "indeg": indeg[mid], "outdeg": outdeg[mid],
            "level": LEVELS.get(label, 5),
            "desc": DESC.get(label, ""), "core": label in CORE,
            "detail": DETAIL.get(label, DESC.get(label, "")),
        })

    # 실행 단계(level)별 x,y 좌표를 직접 계산 (LR 컬럼). vis 계층 엔진 미사용 -> 자유 드래그.
    # 컬럼 내부 세로 순서는 barycenter 휴리스틱(Sugiyama)으로 정렬해 엣지 교차/길이를 줄인다:
    # 각 노드를 '연결된 이웃들의 평균 세로위치'로 반복 정렬 -> 연결된 노드끼리 가까이 모임.
    COL, ROW = 230, 140
    adj: dict[str, set] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    by_lvl: dict[int, list] = defaultdict(list)
    for n in nodes:
        by_lvl[n["level"]].append(n)
    order = {}
    for lvl in by_lvl:
        for i, n in enumerate(by_lvl[lvl]):
            order[n["id"]] = i

    def _bary(n):
        nb = [order[m] for m in adj[n["id"]] if m in order]
        return sum(nb) / len(nb) if nb else order[n["id"]]

    for _ in range(12):  # down/up 스윕 반복 수렴
        for lvl in sorted(by_lvl):
            ns = by_lvl[lvl]
            ns.sort(key=_bary)
            for i, n in enumerate(ns):
                order[n["id"]] = i

    for lvl, ns in by_lvl.items():
        for i, n in enumerate(ns):
            n["x"] = lvl * COL
            n["y"] = int((i - (len(ns) - 1) / 2) * ROW)

    return {"nodes": nodes, "edges": [{"from": a, "to": b} for a, b in sorted(edges)]}


def build():
    graph = extract_graph()
    html = (
        TEMPLATE
        .replace("%%GRAPH%%", json.dumps(graph, ensure_ascii=False))
        .replace("%%STAGES%%", json.dumps(STAGE_NAMES, ensure_ascii=False))
        .replace("%%STAGESLINE%%", " &rarr; ".join(STAGE_NAMES))
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"written: {OUT}")
    print(f"  nodes={len(graph['nodes'])} edges={len(graph['edges'])}")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QMind · Module Dependency Map</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
 --canvas:#0b0e11;--card:#1e2329;--elev:#2b3139;--elev-hi:#323a44;--hair:#2b3139;
 --primary:#fcd535;--primary-active:#f0b90b;--on-primary:#181a20;
 --body:#eaecef;--on-dark:#ffffff;--muted:#707a8a;--muted-strong:#929aa5;
 --up:#0ecb81;--down:#f6465d;--info:#3b82f6;
 --sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Malgun Gothic',sans-serif;
 --mono:'JetBrains Mono','Consolas',ui-monospace,monospace;
 color-scheme:dark}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--canvas);color:var(--body);height:100vh;overflow:hidden;
 font-family:var(--sans)}
.win{width:100%;height:100vh;background:var(--canvas);overflow:hidden;border:0;display:flex;flex-direction:column}
.subbar{display:flex;align-items:center;gap:10px;padding:13px 16px;border-bottom:1px solid var(--hair);font-size:13px;color:var(--on-dark)}
.subbar b{font-size:13.5px;color:var(--on-dark)}
.pill{font-size:11px;color:var(--muted-strong);background:var(--elev);border:1px solid var(--hair);border-radius:9999px;padding:2px 9px}
.pill.p{color:var(--info);background:rgba(59,130,246,.12);border-color:rgba(59,130,246,.35)}
.pill.g{color:var(--up);background:rgba(14,203,129,.12);border-color:rgba(14,203,129,.35)}
.body{display:flex;flex:1;min-height:0}
.side{width:236px;flex:0 0 236px;border-right:1px solid var(--hair);padding:14px 14px 16px;overflow:auto;background:var(--card)}
.side h3{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin:16px 0 8px;font-weight:700}
.side h3:first-child{margin-top:0}
.preset{display:block;width:100%;text-align:left;border:1px solid var(--hair);background:var(--elev);border-radius:8px;
 padding:8px 11px;font-size:13px;margin-bottom:6px;cursor:pointer;color:var(--body)}
.preset:hover{background:var(--elev-hi);border-color:#3a434e}
.preset.on{border-color:var(--primary);background:rgba(252,213,53,.10);color:var(--primary);font-weight:600}
.grp{display:flex;align-items:center;gap:8px;font-size:13px;margin:5px 0;cursor:pointer;color:var(--body)}
.grp input{accent-color:var(--primary)}
.grp i{width:13px;height:13px;border-radius:4px;display:inline-block;border:1px solid rgba(255,255,255,.14)}
.zoom{display:flex;gap:6px}
.zoom button,.searchwrap input{height:32px}
.zoom button{flex:1;border:1px solid var(--hair);background:var(--elev);border-radius:8px;cursor:pointer;font-size:15px;color:var(--body)}
.zoom button:hover{background:var(--elev-hi)}
.searchwrap input{width:100%;border:1px solid var(--hair);border-radius:8px;padding:0 10px;font-size:13px;background:var(--elev);color:var(--on-dark)}
.searchwrap input::placeholder{color:var(--muted)}
.cmtbox{font-size:13px}
.cmt-empty{color:var(--muted);font-size:12.5px;line-height:1.6}
.cmt-item{border:1px solid var(--hair);border-radius:8px;padding:7px 9px;margin-bottom:6px;cursor:pointer;background:var(--elev)}
.cmt-item:hover{background:var(--elev-hi)}
.cmt-item b{font-size:12.5px;color:var(--body)}.cmt-item div{font-size:12px;color:var(--muted);margin-top:2px;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.editor .ename{font-size:14px;font-weight:600;color:var(--on-dark)}
.editor .epath{font-size:11px;color:var(--muted);font-family:var(--mono);margin:2px 0 4px;word-break:break-all}
.editor .edesc{font-size:12.5px;color:var(--muted-strong);margin-bottom:8px}
.editor .edetail{font-size:12.5px;color:var(--body);line-height:1.62;background:var(--elev);border:1px solid var(--hair);
 border-left:3px solid var(--primary);border-radius:8px;padding:9px 11px;margin:3px 0 11px}
.editor .elabel{font-size:11px;color:var(--muted);font-weight:700;letter-spacing:.04em;margin:0 0 4px}
.editor textarea{width:100%;height:120px;border:1px solid var(--hair);border-radius:8px;padding:9px;font-size:13px;
 font-family:inherit;line-height:1.55;resize:vertical;background:var(--canvas);color:var(--on-dark)}
.editor .ebtn{display:flex;gap:6px;margin-top:8px}
.editor .ebtn button{flex:1;height:34px;border:1px solid var(--hair);border-radius:8px;background:var(--elev);cursor:pointer;font-size:13px;color:var(--body)}
.editor .ebtn .save{background:var(--primary);border-color:var(--primary);color:var(--on-primary);font-weight:600}
.editor .ebtn .save:hover{background:var(--primary-active);border-color:var(--primary-active)}
.editor .deps{margin-top:10px}
.editor .deps h4{font-size:10.5px;color:var(--muted);margin:8px 0 3px;font-weight:700;letter-spacing:.04em}
.editor .deps a{display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border:1px solid var(--hair);border-radius:14px;
 font-size:11.5px;color:var(--primary);background:var(--elev);cursor:pointer;text-decoration:none}
.editor .deps a:hover{background:var(--elev-hi)}
.main{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0}
.stages{font-size:12px;color:var(--muted-strong);background:var(--card);border-bottom:1px solid var(--hair);padding:7px 16px}
.stages b{color:var(--primary)}
#net{flex:1;min-height:0}
.foot{display:flex;align-items:center;gap:18px;padding:8px 16px;border-top:1px solid var(--hair);font-size:12px;color:var(--muted)}
.foot .ek{display:flex;align-items:center;gap:6px}
.foot .ek span{width:22px;height:0;border-top:2px solid var(--muted)}
.foot .note{margin-left:auto;color:var(--muted)}
</style></head>
<body>
<div class="win">
  <div class="subbar"><b>QMind 팩터 파이프라인 — 모듈 의존성 맵</b>
    <span class="pill">22 모듈</span><span class="pill p">Python</span>
    <span class="pill g">Factor Pipeline</span></div>
  <div class="body">
    <aside class="side">
      <h3>프리셋 보기</h3><div id="presets"></div>
      <h3>그룹 표시</h3><div id="groups"></div>
      <h3>보기</h3>
      <div class="searchwrap" style="margin-bottom:7px"><input id="search" placeholder="모듈 검색" oninput="onSearch(this.value)"></div>
      <div class="zoom"><button onclick="zoom(1.25)">+</button><button onclick="fitAll()" title="전체 보기">&#9678;</button><button onclick="zoom(0.8)">&minus;</button></div>
      <h3 id="cmt-head">코멘트 (0)</h3>
      <div class="cmtbox" id="cmtbox"></div>
    </aside>
    <div class="main">
      <div class="stages"><b>실행 순서 (왼→오):</b> %%STAGESLINE%%</div>
      <div id="net"></div>
      <div class="foot">
        <div class="ek"><span></span>import 의존성 (A→B = A가 B를 import)</div>
        <div class="ek" style="color:var(--primary)"><span style="border-color:var(--primary)"></span>선택 시 연결 강조</div>
        <div class="note">클릭=강조·메모 · Ctrl/Shift+클릭=여러 개 선택 후 함께 이동 · 빈 곳 드래그=화면 이동</div>
      </div>
    </div>
  </div>
</div>
<script>
var GRAPH=%%GRAPH%%, STAGES=%%STAGES%%;
// Binance 다크: 노드는 카드 표면(#1e2329), 카테고리는 테두리/제목 색으로 구분.
// 코어(pipeline)는 브랜드 옐로, 나머지는 절제된 콘텐츠 색(차트 범례용).
var PAL={
 entry:{bg:'#1e2329',bd:'#3b82f6',ti:'#6ea8ff'},
 download:{bg:'#1e2329',bd:'#2dbdb6',ti:'#48d6cf'},
 pipeline:{bg:'#262a1c',bd:'#fcd535',ti:'#fcd535'},
 backtest:{bg:'#1e2329',bd:'#8a7cff',ti:'#a99bff'},
 report:{bg:'#1e2329',bd:'#f6699a',ti:'#ff85b0'}};
var GLABEL={entry:'진입 (main/config)',download:'다운로드',pipeline:'파이프라인 (코어)',backtest:'백테스트',report:'리포트·시각화'};
var PRESETS={'전체 시스템':['entry','download','pipeline','backtest','report'],
 'MP 파이프라인':['entry','download','pipeline','report'],
 '백테스트':['entry','pipeline','backtest'],
 '시각화':['entry','download','report'],
 '다운로드 데이터':['entry','download']};
var LSP='bok-codemap:', cur=null, groupOn={};
Object.keys(PAL).forEach(function(g){groupOn[g]=true;});
function ck(id){return LSP+id;} function hasC(id){return !!localStorage.getItem(ck(id));}
var META={}; GRAPH.nodes.forEach(function(n){META[n.id]=n;});

function nodeColor(n){var p=PAL[n.group]; var bd=hasC(n.id)?'#f6465d':(n.core?'#fcd535':p.bd);
 return {background:p.bg,border:bd,highlight:{background:p.bg,border:'#fcd535'}};}
function nodeObj(n){var p=PAL[n.group];
 return {id:n.id, x:n.x, y:n.y, value:n.indeg,
  label:'<b>'+(n.core?'★ ':'')+n.label+'</b>\n'+n.desc,
  title:n.id+'  ·  '+STAGES[n.level],
  color:nodeColor(n), borderWidth:(hasC(n.id)||n.core)?2.6:1.4,
  font:{multi:'html',size:12.5,color:'#929aa5',face:"Inter,-apple-system,Segoe UI,Malgun Gothic",
        bold:{size:15,color:p.ti}}};}
var nodes=new vis.DataSet(GRAPH.nodes.map(nodeObj));
var edges=new vis.DataSet(GRAPH.edges.map(function(e,i){return {id:'e'+i,from:e.from,to:e.to};}));

var net=new vis.Network(document.getElementById('net'),{nodes:nodes,edges:edges},{
 layout:{hierarchical:false,improvedLayout:false},
 nodes:{shape:'box',shapeProperties:{borderRadius:11},margin:{top:9,bottom:9,left:13,right:13},
   widthConstraint:{minimum:165,maximum:235},scaling:{min:1,max:1.5}},
 edges:{arrows:{to:{enabled:true,scaleFactor:0.5}},color:{color:'#3a434e',highlight:'#fcd535',hover:'#fcd535'},
   smooth:{type:'cubicBezier',forceDirection:'horizontal',roundness:0.35},width:1,selectionWidth:1.6},
 physics:false,
 interaction:{hover:true,tooltipDelay:140,dragNodes:true,dragView:true,zoomView:true,navigationButtons:false,keyboard:false,multiselect:true}});
// 노드는 Python에서 계산한 고정 x,y 로 LR 배치됨 (계층 엔진 미사용) -> 자유 드래그 가능
// 시작 줌: 전체 fit 시 너무 작으면 가독성 위해 최소 배율로 키우고 왼쪽(실행 시작)부터 보여준다.
var MINSCALE=0.95;
function initView(){net.fit({animation:false});
 if(net.getScale()<MINSCALE){
  var xs=GRAPH.nodes.map(function(n){return n.x;}), ys=GRAPH.nodes.map(function(n){return n.y;});
  var minx=Math.min.apply(null,xs), cy=(Math.min.apply(null,ys)+Math.max.apply(null,ys))/2;
  net.moveTo({scale:MINSCALE, position:{x:minx+360, y:cy}});}}
net.once('afterDrawing',initView);
var _rt; window.addEventListener('resize',function(){clearTimeout(_rt);_rt=setTimeout(function(){net.fit();},150);});

// focus
function focusNode(id){var conn=net.getConnectedNodes(id);var keep={};keep[id]=1;conn.forEach(function(c){keep[c]=1;});
 nodes.update(nodes.get().map(function(n){return {id:n.id,opacity:keep[n.id]?1:0.16};}));
 edges.update(edges.get().map(function(e){var on=(e.from===id||e.to===id);
  return {id:e.id,color:{color:on?'#fcd535':'#23262d'},width:on?2.3:0.6};}));}
function clearFocus(){nodes.update(nodes.get().map(function(n){return {id:n.id,opacity:1};}));
 edges.update(edges.get().map(function(e){return {id:e.id,color:{color:'#3a434e'},width:1};}));}
net.on('selectNode',function(p){
 if(p.nodes.length===1){focusNode(p.nodes[0]);openEditor(p.nodes[0]);}
 else{clearFocus();document.getElementById('cmt-head').textContent='선택 '+p.nodes.length+'개 · 드래그로 함께 이동';
  document.getElementById('cmtbox').innerHTML='<div class="cmt-empty">Ctrl(또는 Shift)+클릭으로 여러 노드를 선택했습니다. 아무 노드나 드래그하면 함께 움직입니다.</div>';}});
net.on('deselectNode',function(){clearFocus();renderCmtList();});
net.on('click',function(p){if(!p.nodes.length){clearFocus();renderCmtList();}});

// sidebar: presets + groups
(function(){var pe=document.getElementById('presets');
 Object.keys(PRESETS).forEach(function(name){var b=document.createElement('button');
  b.className='preset'+(name==='전체 시스템'?' on':'');b.textContent=name;
  b.onclick=function(){applyPreset(name);};pe.appendChild(b);});
 var ge=document.getElementById('groups');
 Object.keys(GLABEL).forEach(function(g){var l=document.createElement('label');l.className='grp';
  l.innerHTML='<input type="checkbox" checked onchange="toggleGroup(\''+g+'\',this.checked)">'+
   '<i style="background:'+PAL[g].bd+';border-color:'+PAL[g].bd+'"></i>'+GLABEL[g];
  ge.appendChild(l);});})();
function applyPreset(name){var on=PRESETS[name];Object.keys(groupOn).forEach(function(g){groupOn[g]=on.indexOf(g)>=0;});
 document.querySelectorAll('#groups input').forEach(function(cb,i){cb.checked=groupOn[Object.keys(GLABEL)[i]];});
 document.querySelectorAll('.preset').forEach(function(b){b.classList.toggle('on',b.textContent===name);});
 applyVis();setTimeout(fitAll,40);}
function toggleGroup(g,on){groupOn[g]=on;document.querySelectorAll('.preset').forEach(function(b){b.classList.remove('on');});applyVis();}
function applyVis(){nodes.update(GRAPH.nodes.map(function(n){return {id:n.id,hidden:!groupOn[n.group]};}));
 edges.update(GRAPH.edges.map(function(e,i){return {id:'e'+i,hidden:!(groupOn[META[e.from].group]&&groupOn[META[e.to].group])};}));}

// zoom / search
function zoom(f){net.moveTo({scale:net.getScale()*f});}
function fitAll(){clearFocus();net.unselectAll();net.fit({animation:true});renderCmtList();}
function onSearch(q){q=q.trim().toLowerCase();if(!q){clearFocus();return;}
 var h=GRAPH.nodes.find(function(n){return n.id.toLowerCase().indexOf(q)>=0;});if(h)go(h.id);}
function go(id){net.selectNodes([id]);net.focus(id,{scale:1.05,animation:true});focusNode(id);openEditor(id);}

// comments (sidebar)
function openEditor(id){cur=id;var m=META[id];var outs=[],ins=[];
 GRAPH.edges.forEach(function(e){if(e.from===id)outs.push(e.to);if(e.to===id)ins.push(e.from);});
 function chips(a){return a.length?a.map(function(x){return '<a onclick="go(\''+x+'\')">'+META[x].label+'</a>';}).join(''):'<span style="color:#707a8a">없음</span>';}
 document.getElementById('cmt-head').textContent='코멘트 · '+m.label;
 document.getElementById('cmtbox').innerHTML=
  '<div class="editor"><div class="ename">'+(m.core?'★ ':'')+m.label+'</div>'+
  '<div class="epath">'+id+'</div>'+
  '<div class="edetail">'+m.detail+'</div>'+
  '<div class="elabel">내 메모 (피드백)</div>'+
  '<textarea id="cmt-text" placeholder="이 모듈에 대한 메모...">'+(localStorage.getItem(ck(id))||'')+'</textarea>'+
  '<div class="ebtn"><button class="save" onclick="saveC()">저장</button><button onclick="delC()">삭제</button></div>'+
  '<div class="deps"><h4>IMPORT (→)</h4>'+chips(outs)+'<h4>IMPORTED BY (←)</h4>'+chips(ins)+'</div></div>';}
function saveC(){if(!cur)return;var v=document.getElementById('cmt-text').value.trim();
 if(v)localStorage.setItem(ck(cur),v);else localStorage.removeItem(ck(cur));mark(cur);}
function delC(){if(!cur)return;localStorage.removeItem(ck(cur));var t=document.getElementById('cmt-text');if(t)t.value='';mark(cur);}
function mark(id){nodes.update({id:id,color:nodeColor(META[id]),borderWidth:(hasC(id)||META[id].core)?2.4:1.3});updateHead();}
function updateHead(){var n=GRAPH.nodes.filter(function(x){return hasC(x.id);}).length;
 if(!cur)document.getElementById('cmt-head').textContent='코멘트 ('+n+')';}
function renderCmtList(){cur=null;var ks=GRAPH.nodes.filter(function(n){return hasC(n.id);});
 document.getElementById('cmt-head').textContent='코멘트 ('+ks.length+')';
 var box=document.getElementById('cmtbox');
 if(!ks.length){box.innerHTML='<div class="cmt-empty">노드를 클릭하면 여기서 메모를 추가할 수 있습니다. 저장한 메모는 이 목록에 모입니다.</div>';return;}
 box.innerHTML=ks.map(function(n){return '<div class="cmt-item" onclick="go(\''+n.id+'\')"><b>'+n.label+'</b><div>'+
   (localStorage.getItem(ck(n.id))||'').replace(/</g,'&lt;')+'</div></div>';}).join('');}
renderCmtList();
</script>
</body></html>
"""


if __name__ == "__main__":
    build()
