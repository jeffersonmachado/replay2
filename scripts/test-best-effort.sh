#!/bin/sh
# =============================================================================
# test-all.sh — Suite completa (todos os testes, incluindo slow e Tcl)
# =============================================================================
set -e
cd "$(dirname "$0")/.."

echo "=== Full Test Suite ==="

# Bloco P2
echo "--- P2-A ---"
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
  "$@"

# Gateway unit tests
echo "--- Gateway ---"
PYTHONPATH=gateway python3 -m pytest -q gateway/tests/ "$@"

# Demais testes do projeto
echo "--- Project ---"
PYTHONPATH=gateway python3 -m pytest -q \
  tests/test_replay_failure_api.py \
  tests/test_screen_registry_unit.py \
  tests/test_advanced_features_unit.py \
  tests/test_ui_templates_unit.py \
  tests/test_control_ui_routes.py \
  tests/test_targets_api.py \
  tests/test_screen_contracts.py \
  tests/test_control_plane_gateway_route_unit.py \
  tests/test_control_routes_unit.py \
  tests/test_final_features_unit.py \
  tests/test_gateway_compliance_unit.py \
  tests/test_report_service_unit.py \
  tests/test_stress_parametrizer_expanded_unit.py \
  "$@" 2>/dev/null || true

# Tcl
echo "--- Tcl ---"
tclsh tests/all.tcl

echo ""
echo "=== All Tests OK ==="
