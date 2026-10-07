#!/usr/bin/env python3
"""
Patch: TRAIL_TV_ENTRY_CANDLE_LIVE  (ADX Regime ATR Executor v2.1)

With TRAIL_TV_BAR_PATH=true the bot could not exit inside the entry candle.
This patch adds one option:

  TRAIL_TV_ENTRY_CANDLE_LIVE=true
    * entry candle  -> trail runs on LIVE ticks (arm + follow + exit inside
                       the running candle, like Pine's native trail)
    * candle 2 on   -> TradingView candle mode, exactly as before
                       (frozen stop/TP live, trail moved only at candle close)

Default is false, so nothing changes until the .env line is added.

Usage (run from the project root, patches every folder given):
    .venv/bin/python apply_tv_entry_live_patch.py . ADX-Regime-ATR-Executor-v2.1

Safe: makes a .bak copy of each file, stops with an error if the code does
not look as expected, and skips files that are already patched.
"""
import shutil
import sys
import time
from pathlib import Path

MARK = "TRAIL_TV_ENTRY_CANDLE_LIVE"

CONFIG_EDITS = [
    (
        'TRAIL_TV_BAR_PATH = _b("TRAIL_TV_BAR_PATH", False)\n',
        'TRAIL_TV_BAR_PATH = _b("TRAIL_TV_BAR_PATH", False)\n'
        "# Only used with TRAIL_TV_BAR_PATH=true. When true, the ENTRY candle is\n"
        "# managed on live ticks (trail can arm and exit inside the running entry\n"
        "# candle); TV candle mode starts from the first candle close.\n"
        'TRAIL_TV_ENTRY_CANDLE_LIVE = _b("TRAIL_TV_ENTRY_CANDLE_LIVE", False)\n',
    ),
]

TRAIL_EDITS = [
    # 1. import
    (
        "    TRAIL_TV_BAR_PATH,\n",
        "    TRAIL_TV_BAR_PATH,\n    TRAIL_TV_ENTRY_CANDLE_LIVE,\n",
    ),
    # 2. bar-close replay: skip it for the entry candle in hybrid mode
    (
        "        if TRAIL_TV_BAR_PATH:\n"
        "            o = bar_open if bar_open > 0.0 else bar_close\n",
        "        if TRAIL_TV_BAR_PATH and not self._tv_frozen_active():\n"
        "            logger.info(\n"
        "                \"[TRAIL] TV bar-path: entry candle was managed on live ticks \"\n"
        "                \"- no replay for this bar\"\n"
        "            )\n"
        "        if self._tv_frozen_active():\n"
        "            o = bar_open if bar_open > 0.0 else bar_close\n",
    ),
    # 3. live tick: use the normal live evaluator during the entry candle
    (
        "        if TRAIL_TV_BAR_PATH:\n"
        "            await self._evaluate_tick_tv_frozen(price, source, gap_fill)\n",
        "        if self._tv_frozen_active():\n"
        "            await self._evaluate_tick_tv_frozen(price, source, gap_fill)\n",
    ),
    # 4. helper method
    (
        "    async def _evaluate_tick_tv_frozen(self, price: float, source: str, gap_fill: bool) -> None:\n",
        "    def _tv_frozen_active(self) -> bool:\n"
        "        \"\"\"True when TV candle mode applies to the current candle.\n"
        "\n"
        "        With TRAIL_TV_ENTRY_CANDLE_LIVE the entry candle (static orders not\n"
        "        yet active) is handled by the live tick evaluator instead.\n"
        "        \"\"\"\n"
        "        if not TRAIL_TV_BAR_PATH:\n"
        "            return False\n"
        "        if TRAIL_TV_ENTRY_CANDLE_LIVE and not getattr(self, \"_static_orders_active\", True):\n"
        "            return False\n"
        "        return True\n"
        "\n"
        "    async def _evaluate_tick_tv_frozen(self, price: float, source: str, gap_fill: bool) -> None:\n",
    ),
    # 5. start log
    (
        "            f\"tv_bar_path={TRAIL_TV_BAR_PATH}  \"\n",
        "            f\"tv_bar_path={TRAIL_TV_BAR_PATH}  \"\n"
        "            f\"tv_entry_candle_live={TRAIL_TV_ENTRY_CANDLE_LIVE}  \"\n",
    ),
]


def patch_file(path: Path, edits) -> str:
    text = path.read_text()
    if MARK in text:
        return "already patched"
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise SystemExit(
                f"ERROR {path}: expected 1 match, found {n} for:\n{old}\n"
                "Nothing was changed in this file. Send this output for review."
            )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(path.name + f".bak.{stamp}"))
    for old, new in edits:
        text = text.replace(old, new, 1)
    path.write_text(text)
    return "patched"


def main() -> None:
    dirs = sys.argv[1:] or ["."]
    for d in dirs:
        base = Path(d)
        cfg = base / "config.py"
        trl = base / "monitor" / "trail_loop.py"
        if not cfg.exists() or not trl.exists():
            raise SystemExit(f"ERROR: {base} does not contain config.py and monitor/trail_loop.py")
        # check both first so a folder is never half patched
        for p, e in ((cfg, CONFIG_EDITS), (trl, TRAIL_EDITS)):
            t = p.read_text()
            if MARK not in t:
                for old, _ in e:
                    if t.count(old) != 1:
                        raise SystemExit(f"ERROR {p}: code differs from expected, nothing changed.")
        print(f"{cfg}: {patch_file(cfg, CONFIG_EDITS)}")
        print(f"{trl}: {patch_file(trl, TRAIL_EDITS)}")
    print("DONE")


if __name__ == "__main__":
    main()
