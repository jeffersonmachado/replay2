#!/usr/bin/env python3
"""Demo do P2-A: Synthetic Knowledge Base — pipeline completo.

Gera um sistema de lojas fake, executa o pipeline P2-A e produz
o relatório de evidências.

Uso:
    python3 demo_p2_knowledge_base.py [--output report.json]
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.parser import SourceParser
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityLinker
from dakota_gateway.source_analyzer.program_catalog import ProgramCatalog
from dakota_gateway.synthetic.business_dataset_planner import BusinessDatasetPlanner
from dakota_gateway.synthetic.synthetic_evidence_report import SyntheticEvidenceReportBuilder
from dakota_gateway.synthetic.data_synthesizer import DataSynthesizer
from dakota_gateway.synthetic.journey_mix import JourneyMixBuilder


def create_sample_source(base_dir: Path) -> None:
    """Cria código-fonte fake de um sistema de lojas (Recital/xBase style)."""

    def _w(path: str, content: str) -> None:
        p = base_dir / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # Schema SQL
    _w("schema.sql", """
CREATE TABLE clientes (
    id INTEGER PRIMARY KEY,
    nome VARCHAR(100) NOT NULL,
    cpf CHAR(14) UNIQUE,
    email VARCHAR(100),
    telefone VARCHAR(20),
    data_cadastro DATE
);
CREATE TABLE produtos (
    id INTEGER PRIMARY KEY,
    descricao VARCHAR(200) NOT NULL,
    preco DECIMAL(10,2) NOT NULL,
    estoque INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'ATIVO'
);
CREATE TABLE pedidos (
    id INTEGER PRIMARY KEY,
    id_cliente INTEGER REFERENCES clientes(id),
    data_pedido DATE NOT NULL,
    valor_total DECIMAL(10,2),
    status VARCHAR(20) DEFAULT 'PENDENTE'
);
CREATE TABLE itens_pedido (
    id INTEGER PRIMARY KEY,
    id_pedido INTEGER REFERENCES pedidos(id),
    id_produto INTEGER REFERENCES produtos(id),
    quantidade INTEGER NOT NULL,
    preco_unitario DECIMAL(10,2)
);
CREATE TABLE financeiro (
    id INTEGER PRIMARY KEY,
    id_pedido INTEGER REFERENCES pedidos(id),
    valor DECIMAL(10,2),
    vencimento DATE,
    status VARCHAR(20) DEFAULT 'PENDENTE'
);
""")

    # Cadastro de Clientes
    _w("cad/cadcli.prg", """
PROCEDURE cadcli
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome........:"
@ 1,20 GET nome
@ 2,1 SAY "CPF.........:"
@ 2,20 GET cpf
@ 3,1 SAY "Email.......:"
@ 3,20 GET email
@ 4,1 SAY "Telefone....:"
@ 4,20 GET telefone
INSERT INTO CLIENTES (NOME, CPF, EMAIL, TELEFONE) VALUES (nome, cpf, email, telefone)
RETURN
""")

    # Consulta de Clientes
    _w("con/concli.prg", """
PROCEDURE concli
TITLE "Consulta de Clientes"
@ 1,1 SAY "CPF.........:"
@ 1,20 GET cpf
SELECT NOME, CPF, EMAIL, TELEFONE FROM CLIENTES WHERE CPF = cpf
RETURN
""")

    # Alteração de Clientes
    _w("alt/altcli.prg", """
PROCEDURE altcli
TITLE "Alteracao de Clientes"
SELECT * FROM CLIENTES WHERE CPF = cpf
@ 1,1 SAY "Nome........:"
@ 1,20 GET nome
@ 2,1 SAY "Email.......:"
@ 2,20 GET email
@ 3,1 SAY "Telefone....:"
@ 3,20 GET telefone
UPDATE CLIENTES SET NOME=nome, EMAIL=email, TELEFONE=telefone WHERE CPF=cpf
RETURN
""")

    # Cadastro de Produtos
    _w("cad/cadprod.prg", """
PROCEDURE cadprod
TITLE "Cadastro de Produtos"
@ 1,1 SAY "Descricao...:"
@ 1,20 GET descricao
@ 2,1 SAY "Preco.......:"
@ 2,20 GET preco
@ 3,1 SAY "Estoque.....:"
@ 3,20 GET estoque
INSERT INTO PRODUTOS (DESCRICAO, PRECO, ESTOQUE) VALUES (descricao, preco, estoque)
RETURN
""")

    # Pedido de Venda
    _w("ven/pedido.prg", """
PROCEDURE pedido
TITLE "Pedido de Venda"
@ 1,1 SAY "Cliente.....:"
@ 1,20 GET id_cliente
@ 2,1 SAY "Data........:"
@ 2,20 GET data_pedido
INSERT INTO PEDIDOS (ID_CLIENTE, DATA_PEDIDO, VALOR_TOTAL, STATUS)
VALUES (id_cliente, data_pedido, 0, 'PENDENTE')
@ 4,1 SAY "Produto.....:"
@ 4,20 GET id_produto
@ 5,1 SAY "Quantidade..:"
@ 5,20 GET quantidade
INSERT INTO ITENS_PEDIDO (ID_PEDIDO, ID_PRODUTO, QUANTIDADE, PRECO_UNITARIO)
VALUES (LAST_INSERT_ID(), id_produto, quantidade, 0)
RETURN
""")

    # Financeiro
    _w("fin/financeiro.prg", """
PROCEDURE financeiro
TITLE "Lancamento Financeiro"
@ 1,1 SAY "Pedido......:"
@ 1,20 GET id_pedido
@ 2,1 SAY "Valor.......:"
@ 2,20 GET valor
@ 3,1 SAY "Vencimento..:"
@ 3,20 GET vencimento
INSERT INTO FINANCEIRO (ID_PEDIDO, VALOR, VENCIMENTO, STATUS)
VALUES (id_pedido, valor, vencimento, 'PENDENTE')
RETURN
""")

    # Relatório de Vendas
    _w("rel/relvendas.prg", """
PROCEDURE relvendas
TITLE "Relatorio de Vendas"
@ 1,1 SAY "Data Inicial:"
@ 1,20 GET data_ini
@ 2,1 SAY "Data Final..:"
@ 2,20 GET data_fim
SELECT P.ID, C.NOME, P.DATA_PEDIDO, P.VALOR_TOTAL, P.STATUS
FROM PEDIDOS P JOIN CLIENTES C ON P.ID_CLIENTE = C.ID
WHERE P.DATA_PEDIDO BETWEEN data_ini AND data_fim
RETURN
""")

    # Menu Principal
    _w("menu_principal.prg", """
TITLE "Menu Principal - Sistema de Lojas"
@ 1,1 SAY "1 - Cadastros"
@ 2,1 SAY "2 - Vendas"
@ 3,1 SAY "3 - Financeiro"
@ 4,1 SAY "4 - Relatorios"
@ 5,1 SAY "0 - Sair"
DO cadcli
DO cadprod
DO pedido
DO financeiro
DO relvendas
RETURN
""")


def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else ""

    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp) / "lojas"
        base_dir.mkdir()
        create_sample_source(base_dir)

        print("=" * 60, file=sys.stderr)
        print("  P2-A Synthetic Knowledge Base — Demo", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"  Fonte: {base_dir}", file=sys.stderr)

        # ── Fase 1: Discovery ──
        print("\n── Fase 1: Discovery ──", file=sys.stderr)
        parser = SourceParser(str(base_dir))
        entities, screens = parser.parse_all()
        print(f"  Entidades: {len(entities)}", file=sys.stderr)
        for e in entities:
            print(f"    {e.name} ({e.storage_type}): {len(e.fields)} campos, "
                  f"{len(e.operations)} operações", file=sys.stderr)
        print(f"  Telas: {len(screens)}", file=sys.stderr)
        for s in screens:
            print(f"    {s.title or s.program_name}: {len(s.fields)} campos", file=sys.stderr)

        # ── Fase 2: Screen-Entity Bindings ──
        print("\n── Fase 2: Screen-Entity Bindings ──", file=sys.stderr)
        bindings = parser.screen_entity_bindings()
        for b in bindings:
            label = f"{b.confidence:.0%}"
            print(f"  [{label}] {b.screen_title or b.program_name} → {b.entity_name} "
                  f"({b.operation}) matched={b.matched_fields}", file=sys.stderr)

        # ── Fase 3: Program Catalog ──
        print("\n── Fase 3: Program Catalog ──", file=sys.stderr)
        catalog = parser.program_catalog()
        catalog_report = catalog.to_report()
        print(f"  Programas: {catalog_report['total_programs']}", file=sys.stderr)
        print(f"  Módulos: {catalog_report['total_modules']}", file=sys.stderr)
        for mod_name, mod_data in sorted(catalog_report["modules"].items()):
            print(f"    {mod_data['friendly_name']}: {mod_data['program_count']} programas", file=sys.stderr)

        # ── Fase 4: Dependency Graph ──
        print("\n── Fase 4: Dependency Graph ──", file=sys.stderr)
        rels = parser.relationships()
        graph = parser.business_dependency_graph()
        planner = BusinessDatasetPlanner()
        summary = planner.plan_summary(graph)
        print(f"  Relacionamentos: {len(rels.relationships)}", file=sys.stderr)
        print(f"  Ordem de geração: {' → '.join(summary['generation_order'])}", file=sys.stderr)
        print(f"  Raízes: {summary['roots']}", file=sys.stderr)
        print(f"  Folhas: {summary['leaves']}", file=sys.stderr)
        print(f"  Profundidade máxima: {summary['max_depth']}", file=sys.stderr)
        if summary.get("cycles_detected"):
            print(f"  ⚠ Ciclos: {summary['cycles']}", file=sys.stderr)

        # ── Fase 5: Synthetic Samples ──
        print("\n── Fase 5: Synthetic Samples ──", file=sys.stderr)
        synthesizer = DataSynthesizer()
        samples = []
        for plan in graph.plans[:5]:
            entity = next((e for e in entities if e.name.upper() == plan.entity_name.upper()), None)
            if not entity:
                continue
            try:
                plans = synthesizer.infer_plans(str(base_dir), entity_filter=plan.entity_name)
                if plans:
                    result = synthesizer.generate_bulk(
                        plans[0], quantity=3, seed=42, sample_size=2, strict_preflight=False,
                    )
                    if result.dataset:
                        print(f"  {plan.entity_name}: {result.generated_count} registros", file=sys.stderr)
                        for rec in result.dataset.records[:2]:
                            print(f"    {rec.data}", file=sys.stderr)
                        samples.append({
                            "entity": plan.entity_name,
                            "count": result.generated_count,
                            "records": [r.data for r in result.dataset.records],
                        })
            except Exception as e:
                print(f"  {plan.entity_name}: erro - {e}", file=sys.stderr)

        # ── Fase 6: Journey Mix ──
        print("\n── Fase 6: Journey Mix ──", file=sys.stderr)
        mix_builder = JourneyMixBuilder()
        config = JourneyMixBuilder.lojas_basico()
        schedule = mix_builder.build_schedule(config, total_sessions=100)
        print(f"  Cenário: {config.name}", file=sys.stderr)
        print(f"  Sessões: {schedule.total_sessions}", file=sys.stderr)
        for jid, count in sorted(schedule.journey_distribution.items()):
            pct = count / max(1, schedule.total_sessions) * 100
            print(f"    {jid}: {count} ({pct:.0f}%)", file=sys.stderr)

        # ── Fase 7: Evidence Report ──
        print("\n── Fase 7: Evidence Report ──", file=sys.stderr)
        builder = SyntheticEvidenceReportBuilder()
        evidence = builder.build(
            entities=entities,
            screens_count=len(screens),
            bindings=bindings,
            relationships=rels,
            dependency_graph=graph,
            program_catalog=catalog,
            source_files_count=len(list(base_dir.rglob("*.prg"))),
        )
        print(f"  Warnings: {len(evidence.warnings)}", file=sys.stderr)
        for w in evidence.warnings[:5]:
            print(f"    ⚠ {w}", file=sys.stderr)
        print(f"  Recomendações: {len(evidence.recommendations)}", file=sys.stderr)
        for r in evidence.recommendations[:5]:
            print(f"    ➤ {r}", file=sys.stderr)

        # ── Output ──
        report = {
            "pipeline": "P2-A Synthetic Knowledge Base",
            "source_dir": str(base_dir),
            "discovery": {
                "entities": len(entities),
                "screens": len(screens),
                "bindings": len(bindings),
                "relationships": len(rels.relationships),
            },
            "dependency_graph": summary,
            "program_catalog": catalog_report,
            "synthetic_samples": samples,
            "journey_mix": {
                "config": config.name,
                "distribution": schedule.journey_distribution,
            },
            "evidence_report": json.loads(builder.to_json(evidence)),
        }

        if output_file:
            Path(output_file).write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"\nRelatório salvo: {output_file}", file=sys.stderr)
        else:
            print("\n" + json.dumps(report, ensure_ascii=False, indent=2, default=str))

        print("\n✅ Demo P2-A concluído com sucesso!", file=sys.stderr)


if __name__ == "__main__":
    main()
