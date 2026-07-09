#!/bin/sh
# =============================================================================
# test-p2.sh — Executa bloco P2-A Synthetic Knowledge Base
# =============================================================================
set -e
cd "$(dirname "$0")/.."

echo "=== P2-A Synthetic Knowledge Base ==="

PYTHONPATH=gateway python3 -m pytest -q \
  tests/test_capture_knowledge_integrator_order.py \
  tests/test_capture_knowledge_report.py \
  tests/test_relationship_report_types.py \
  tests/test_control_knowledge_base_api.py \
  tests/test_screen_entity_linker_confidence.py \
  tests/test_template_engine_case_insensitive.py \
  tests/test_source_parser_sql_ddl_pipeline.py \
  tests/test_screen_extractor_real_patterns.py \
  tests/test_relationship_mapper_aliases.py \
  tests/test_capture_parametrizer_screen_inputs.py \
  tests/test_sql_extractor_create_table.py \
  tests/test_p2_knowledge_base.py \
  tests/test_capture_knowledge_integrator.py \
  tests/test_screen_entity_linker_unit.py \
  tests/test_source_parser_inferencer_unit.py \
  tests/test_integrated_pipeline_e2e.py \
  tests/test_journey_synthesizer.py \
  "$@"

echo ""
echo "=== P2-A OK ==="
