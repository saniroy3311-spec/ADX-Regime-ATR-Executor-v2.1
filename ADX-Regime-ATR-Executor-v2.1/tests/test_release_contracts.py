from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_delta_bracket_uses_documented_endpoints():
    src = (ROOT / "orders" / "manager.py").read_text(encoding="utf-8")
    assert '"POST", "/v2/orders/bracket"' in src
    assert '"GET", path' in src
    assert 'product_ids=' in src
    assert '"DELETE", "/v2/orders"' in src
    assert '"DELETE", "/v2/orders/all"' in src
    assert '"DELETE", "/v2/orders/bracket"' not in src


def test_pine_release_uses_custom_atr_trail_state_machine():
    src = (ROOT / "pine" / "ADX_Regime_ATR_Execution_Engine_v2_1.pine").read_text(encoding="utf-8")
    assert '//@version=6' in src
    assert 'calc_on_every_tick=true' in src
    assert 'calc_on_every_history_tick=true' in src
    assert 'calc_on_order_fills=true' in src
    assert 'varip float entryATR' in src
    assert 'strategy.exit("Risk Exit"' in src
    assert 'trail_points=' not in src
    assert 'trail_offset=' not in src
