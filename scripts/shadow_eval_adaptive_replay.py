#!/usr/bin/env python3
"""Avaliação offline de shadow mode sobre capturas reais (§19–§20).

Percorre as capturas locais (default ``gateway/state/captures``) rodando o
scheduler adaptativo em modo shadow offline — mesmos componentes do caminho
de execução (ShadowTracker/AdaptiveScheduler/extract_typeahead_evidence) —
sem enviar byte nenhum, e grava o relatório de critério de promoção em
``artifacts/adaptive-shadow-evaluation.json``.

Uso:
    python3 scripts/shadow_eval_adaptive_replay.py [--captures-dir DIR]
        [--stable-ms 150] [--speed 1.0] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))

from dakota_gateway.replay_control.shadow_eval import evaluate_captures  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures-dir", default="gateway/state/captures")
    parser.add_argument("--stable-ms", type=int, default=150)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument(
        "--out", default="artifacts/adaptive-shadow-evaluation.json",
    )
    args = parser.parse_args()

    report = evaluate_captures(
        args.captures_dir, stable_ms=args.stable_ms, speed=args.speed,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    totals = report["totals"]
    print(f"captures_ok={totals['captures_ok']} "
          f"captures_error={totals['captures_error']} "
          f"sessions={totals['sessions']}")
    print(f"total_actions={totals['total_actions']} "
          f"batch_candidates={totals['batch_candidates']} "
          f"safe={totals['safe_candidates']} "
          f"rejected={totals['rejected_candidates']}")
    print(f"potential_saved_ms={totals['potential_saved_ms']} "
          f"writes {totals['writes_before']}→{totals['writes_after']}")
    print(f"false_safe_decisions={totals['false_safe_decisions']} "
          f"checkpoint_violations={totals['checkpoint_violations']} "
          f"bytes_equivalent={totals['bytes_equivalent']}")
    print(f"promotion_criteria_met={totals['promotion_criteria_met']}")
    print(f"relatorio: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
