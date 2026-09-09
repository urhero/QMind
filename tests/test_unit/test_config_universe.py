# -*- coding: utf-8 -*-
"""BENCHMARK 하나로 PARAM/PIPELINE_PARAMS 가 유니버스별로 조립되는지 (2026-09-02)."""
import importlib

import pytest


def _load(monkeypatch, benchmark):
    monkeypatch.setenv("BENCHMARK", benchmark)
    # 빈 문자열 = 미설정 취급(config 의 `or`). delenv 로 지우면 load_dotenv 가 상위 폴더의
    # 실제 .env(다른 유니버스 활성)를 다시 읽어 넣으므로 키를 남겨 두고 비운다.
    for k in ("UNIVERSE", "SERVER_NAME", "DB_NAME"):
        monkeypatch.setenv(k, "")
    import config
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    """테스트 후 config 를 현재 환경 기준으로 되돌린다 (다른 테스트가 모듈 상태에 의존)."""
    yield
    import config
    importlib.reload(config)


@pytest.mark.parametrize("bm,cost,dedup,win", [("MXCN1A", 20.0, True, None), ("MXWO", 10.0, False, 48)])
def test_universe_params(monkeypatch, bm, cost, dedup, win):
    cfg = _load(monkeypatch, bm)
    assert cfg.PARAM["benchmark"] == bm
    assert cfg.PARAM["universe"] == cfg.UNIVERSES[bm]["universe"]
    assert cfg.PARAM["db_name"] == cfg.UNIVERSES[bm]["db_name"]
    assert cfg.PIPELINE_PARAMS["transaction_cost_bps"] == cost
    assert cfg.PIPELINE_PARAMS["use_cluster_dedup"] is dedup
    assert cfg.PIPELINE_PARAMS["is_window_months"] == win
    assert cfg.PIPELINE_PARAMS["apply_country_tax"] is (bm == "MXWO")  # 거래세는 MXWO 전용
    assert cfg.PIPELINE_PARAMS["style_cap"] == 0.25  # 공통


def test_env_overrides_db_identity(monkeypatch):
    cfg = _load(monkeypatch, "MXCN1A")
    assert cfg.PARAM["server_name"] == cfg.UNIVERSES["MXCN1A"]["server_name"]
    monkeypatch.setenv("SERVER_NAME", "my-host")
    cfg = importlib.reload(cfg)
    assert cfg.PARAM["server_name"] == "my-host"


def test_default_benchmark_is_mxwo(monkeypatch):
    """BENCHMARK 미설정(.env 없는 CI 경로) 폴백 = MXWO (2026-09-09, 구 MXCN1A)."""
    cfg = _load(monkeypatch, "")  # 빈 값 = 미설정; load_dotenv 는 이미 있는 키를 덮지 않는다
    assert cfg.PARAM["benchmark"] == "MXWO"
    assert cfg.PIPELINE_PARAMS["transaction_cost_bps"] == 10.0


def test_unknown_benchmark_fails_fast(monkeypatch):
    monkeypatch.setenv("BENCHMARK", "NOPE")
    import config
    with pytest.raises(KeyError):
        importlib.reload(config)
