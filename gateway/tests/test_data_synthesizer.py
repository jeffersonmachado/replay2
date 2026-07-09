from __future__ import annotations

import sys
from pathlib import Path

import pytest

GATEWAY_DIR = str(Path(__file__).resolve().parents[1])
if GATEWAY_DIR not in sys.path:
    sys.path.insert(0, GATEWAY_DIR)

from dakota_gateway.synthetic.data_synthesizer import (
    BulkGenerationResult,
    DataSynthesizer,
    InferredDataPlan,
)
from dakota_gateway.synthetic.constraints import ConstraintRule
from dakota_gateway.synthetic.schema import FieldSchema, ScreenSchema


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    content = """
TITLE "Cadastro de Clientes"
@ 01,01 SAY "CPF"
@ 01,20 GET cpf
@ 02,01 SAY "Email"
@ 02,20 GET email
@ 03,01 SAY "Telefone"
@ 03,20 GET telefone
@ 04,01 SAY "Quantidade"
@ 04,20 GET qtd
"""
    path = tmp_path / "cadcli.prg"
    path.write_text(content, encoding="utf-8")
    return tmp_path


class TestDataSynthesizer:
    def test_infer_plans_from_source(self, source_dir: Path):
        synth = DataSynthesizer()
        plans = synth.infer_plans(str(source_dir))

        assert plans
        plan = plans[0]
        names = {field.name.lower(): field for field in plan.screen.fields}
        assert "cpf" in names
        assert names["cpf"].format == "cpf"
        assert "email" in names
        assert names["email"].format == "email"
        assert "qtd" in names
        assert names["qtd"].min_value == 1

    def test_preflight_validates_preview_before_bulk(self, source_dir: Path):
        synth = DataSynthesizer()
        plan = synth.infer_plans(str(source_dir))[0]

        preview = synth.generate_preflight(plan, sample_size=3, seed=7)

        assert preview.sample_size == 3
        assert preview.ok
        assert preview.total_violations == 0

    def test_bulk_generation_runs_after_successful_preflight(self, source_dir: Path):
        synth = DataSynthesizer()
        plan = synth.infer_plans(str(source_dir))[0]

        result = synth.generate_bulk(plan, quantity=10, sample_size=3, seed=11)

        assert isinstance(result, BulkGenerationResult)
        assert not result.blocked
        assert result.preflight is not None
        assert result.preflight.ok
        assert result.generated_count == 10

    def test_bulk_generation_blocks_when_preflight_fails(self):
        synth = DataSynthesizer()
        plan = InferredDataPlan(
            plan_id="plan-invalid",
            source_dir="/tmp",
            entity_name="INVALID",
            screen=ScreenSchema(
                screen_id="invalid",
                title="Invalid",
                program_name="invalid",
                fields=[
                    FieldSchema(
                        name="email",
                        datatype="email",
                        format="email",
                        required=True,
                        max_length=3,
                    )
                ],
            ),
            field_rules=[],
        )
        plan.field_rules = [ConstraintRule.from_field_schema(plan.screen.fields[0])]
        result = synth.generate_bulk(plan, quantity=10, sample_size=2, seed=5)

        assert result.blocked
        assert result.preflight is not None
        assert not result.preflight.ok
        assert result.generated_count == 0
