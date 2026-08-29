"""Integrador entre capturas reais e a base de conhecimento sintetica.

Faz parte da entrega P2.4 — Jornada sintetica real.

Fluxo:
1. Recebe um CaptureTemplate (de uma captura .jsonl real)
2. Associa cada tela da captura a uma entidade (via ScreenEntityBinding)
3. Mapeia cada input da captura a um campo da entidade
4. Substitui valores reais por placeholders {{entidade.campo}}
5. Gera datasets sinteticos com os providers adequados
6. Renderiza sessoes parametrizadas com dados ficticios validos
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..source_analyzer.entity_catalog import EntityDefinition, FieldDefinition
from ..source_analyzer.screen_entity_linker import ScreenEntityBinding
from .capture_parametrizer import CaptureTemplate, ParametrizedSession
from .schema import FieldSchema, ScreenSchema, SyntheticSchema
from .providers import ProviderRegistry, default_registry
from .dataset_builder import DatasetBuilder
from .template_engine import TemplateEngine
from .screen_layout import extract_layout, layout_labels, field_at
from .validation_rules import valid_lookup_table


def _lookup_of(field_or_target: Any, valid_expr: str = "") -> str:
    """lookup_table do campo: KB primeiro; senão, a tabela do fValida da
    VALID do fonte (ex.: frete → arqfrete, situacao → est281)."""
    kb = str(getattr(field_or_target, "lookup_table", "") or "").strip()
    return kb or valid_lookup_table(valid_expr)


@dataclass
class MappedInput:
    """Input da captura mapeado para entidade.campo."""
    input_index: int = 0
    original_value: str = ""
    original_type: str = ""           # cpf, email, phone, text, number, date...
    entity_name: str = ""
    field_name: str = ""
    field_datatype: str = "text"
    semantic_type: str = ""           # cpf, cnpj, email, phone, person_name...
    placeholder: str = ""             # {{entidade.campo}}
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    method: str = ""  # by_semantic_type, by_screen_order, by_matched_fields, by_field_name, unmapped
    picture: str = ""  # PICTURE literal do GET (quando mapeado por posição)
    # Expressão VALID do GET (quando mapeado por posição) — vira constraint
    # de geração via synthetic.validation_rules (ex.: "valor > 0").
    valid_expr: str = ""
    # lookup_table da entidade da KB (FK) quando o campo referencia outra
    # entidade — usada para sortear valores reais quando há lookup_values.
    lookup_table: str = ""
    # Campo do layout do fonte encontrado pela posição do cursor mesmo quando
    # a entidade da KB não tem esse campo (valor mantido, não substituído).
    layout_field: str = ""
    # Origem do campo mapeado por posição: grade dbedit (is_grid) e tabela
    # real que alimenta a grade (grid_source, ex.: est366) quando conhecida.
    is_grid: bool = False
    grid_source: str = ""


@dataclass
class ScreenKnowledgeMapping:
    """Mapeamento de uma tela da captura para a base de conhecimento."""
    screen_signature: str = ""
    entity_name: str = ""
    operation: str = ""
    binding_confidence: float = 0.0
    mapped_inputs: list[MappedInput] = field(default_factory=list)
    field_schemas: list[FieldSchema] = field(default_factory=list)
    unmatched_inputs: list[str] = field(default_factory=list)
    matched_fields: list[str] = field(default_factory=list)
    # Contadores por tela (v0.2.2)
    total_inputs: int = 0
    mapped_count: int = 0
    unmapped_count: int = 0
    command_count: int = 0


@dataclass
class KnowledgeEnrichedTemplate:
    """CaptureTemplate enriquecido com a base de conhecimento."""
    capture_source: str = ""
    session_id: str = ""
    screen_mappings: list[ScreenKnowledgeMapping] = field(default_factory=list)
    total_inputs: int = 0
    mapped_inputs: int = 0
    unmapped_inputs: int = 0
    command_inputs: int = 0
    entities_involved: list[str] = field(default_factory=list)


class CaptureKnowledgeIntegrator:
    """Integra capturas reais com a base de conhecimento P2-A.

    Transforma uma captura bruta em:
    1. Templates semanticos (cada input mapeado a entidade.campo)
    2. Schemas sinteticos (FieldSchema com provider adequado)
    3. Sessoes parametrizadas com dados ficticios validos
    """

    def __init__(self, registry: Optional[ProviderRegistry] = None,
                 source_root: Optional[str] = None):
        self.registry = registry or default_registry()
        self.dataset_builder = DatasetBuilder(self.registry)
        self.template_engine = TemplateEngine()
        # Raiz dos fontes (.prg) para o mapeamento posicional cursor↔GET.
        # Sem ela o passo by_cursor_position fica desligado.
        self.source_root = source_root
        self._layout_cache: dict[str, list] = {}

    # ── API principal ──

    def enrich_template(
        self,
        template: CaptureTemplate,
        entities: list[EntityDefinition],
        bindings: list[ScreenEntityBinding],
    ) -> KnowledgeEnrichedTemplate:
        """Enriquece um CaptureTemplate — per-screen, multi-entidade."""
        entity_index = {e.name.upper(): e for e in entities}

        # Indexa bindings por screen_title/program_name
        bindings_by_key: dict[str, ScreenEntityBinding] = {}
        for b in bindings:
            key = (b.screen_title or b.program_name).upper()
            if key:
                bindings_by_key[key] = b

        screen_mappings: list[ScreenKnowledgeMapping] = []
        entities_involved: set[str] = set()
        total_mapped = 0
        total_unmapped = 0
        total_commands = 0
        total_inputs_count = 0

        for ctx in template.screen_contexts:
            screen_inputs = ctx.get("inputs", [])
            screen_positions = ctx.get("input_positions") or []
            screen_sig = ctx.get("screen_sig", "")
            screen_sample = ctx.get("screen_sample", "")

            # ── Encontra o melhor binding para esta tela ──
            best_binding = self._find_binding_for_screen(
                screen_sample, screen_sig, bindings_by_key, entity_index
            )

            # ── Vínculo posicional (código de menu + labels do fonte) ──
            # Telas cujo binding não é encontrado por título/programa (ex.:
            # bindings com screen_title vazio e program_name interno) ainda
            # podem ser vinculadas pelo layout posicional do fonte. O
            # vínculo posicional também SUBSTITUI o fallback fraco ("nome
            # da entidade aparece no texto da tela", conf 0.4): código de
            # menu + labels posicionados são evidência mais forte.
            screen_layout: list | None = None
            is_weak_fallback = bool(
                best_binding
                and any("encontrada no screen_sample" in ev
                        for ev in (best_binding.evidence or [])))
            if best_binding is None or is_weak_fallback:
                positional = self._find_positional_binding(
                    screen_sample, bindings)
                if positional:
                    best_binding, screen_layout = positional
            if screen_layout is None and self.source_root \
                    and best_binding is not None \
                    and getattr(best_binding, "source_file", ""):
                screen_layout = self._load_layout(best_binding.source_file)

            entity_name = best_binding.entity_name if best_binding else ""
            binding_conf = best_binding.confidence if best_binding else 0.0
            entity = entity_index.get(entity_name.upper()) if entity_name else None

            # ── Mapeia inputs desta tela ──
            mapped: list[MappedInput] = []
            field_schemas: list[FieldSchema] = []
            screen_mapped = 0
            screen_unmapped = 0
            screen_commands = 0

            if entity:
                entity_fields = {f.name.upper(): f for f in entity.fields}
                field_schemas = self._entity_to_field_schemas(entity)
                if screen_layout:
                    self._apply_picture_constraints(field_schemas,
                                                    screen_layout)
                # Labels visíveis no screen_sample ("Cliente:", "Produto:") são
                # a evidência mais forte da ordem dos campos NAQUELA tela —
                # essencial para entidades multi-tela, onde o matched_fields
                # global do binding desalinha os índices por tela.
                labels = self._screen_labels(screen_sample, entity_fields)
                matched_fields_list = (
                    labels
                    or (best_binding.matched_fields if best_binding else [])
                )
                # Binding fraco (só o nome da entidade no texto da tela,
                # conf 0.4) ou posicional (layout do fonte, sem vínculo de
                # campos por tela): o matched_fields global do binding
                # desalinha por tela e produzia substituições erradas em
                # capturas reais ('g2511' → campo 'total'). Nesse modo só
                # mapeia por tipo semântico forte, labels visíveis ou
                # posição do cursor (evidência própria, independente do
                # matched_fields); o resto permanece com o valor original.
                fallback_only = bool(
                    best_binding
                    and any(("encontrada no screen_sample" in ev
                             or "layout posicional:" in ev)
                            for ev in (best_binding.evidence or [])))
                used_fields: set[str] = set()
                data_field_index = 0  # contador separado: so campos de dados

                for idx, inp_value in enumerate(screen_inputs):
                    # Comandos: preservar, nao consomem posicao de campo
                    if self._is_command(str(inp_value)):
                        mapped.append(MappedInput(
                            input_index=idx, original_value=str(inp_value),
                            original_type="command", method="command",
                            confidence=1.0,
                            evidence=["comando preservado; nao consome campo"],
                        ))
                        screen_commands += 1
                        continue

                    mi = self._map_input_to_field(
                        input_index=idx,
                        data_index=data_field_index,
                        value=str(inp_value),
                        entity=entity,
                        entity_fields=entity_fields,
                        matched_fields=matched_fields_list,
                        used_fields=used_fields,
                        label_only=fallback_only,
                        position=(screen_positions[idx]
                                  if idx < len(screen_positions) else None),
                        layout=screen_layout,
                    )
                    mapped.append(mi)
                    if mi.field_name and mi.confidence >= 0.5:
                        used_fields.add(mi.field_name.upper())
                        screen_mapped += 1
                    if not mi.field_name:
                        screen_unmapped += 1
                    data_field_index += 1  # todo campo de dados avanca posicao
            else:
                # Sem entidade: todos os inputs ficam como originais
                for idx, inp_value in enumerate(screen_inputs):
                    mapped.append(MappedInput(
                        input_index=idx, original_value=str(inp_value),
                        original_type=self._classify_value(str(inp_value)),
                        method="unmapped",
                    ))
                screen_unmapped = len(screen_inputs)

            total_mapped += screen_mapped
            total_unmapped += screen_unmapped
            total_commands += screen_commands
            total_inputs_count += len(screen_inputs)
            if entity_name:
                entities_involved.add(entity_name)
            # Entidades por input (grade dbedit → tabela real da grade, ex.:
            # est361/est366) entram na geração do dataset junto da entidade
            # da tela — senão o placeholder {{est361.modelo}} fica sem valor.
            for mi in mapped:
                if mi.entity_name:
                    entities_involved.add(mi.entity_name)

            screen_mappings.append(ScreenKnowledgeMapping(
                screen_signature=screen_sig,
                entity_name=entity_name,
                operation=best_binding.operation if best_binding else "",
                binding_confidence=binding_conf,
                mapped_inputs=mapped,
                field_schemas=field_schemas,
                matched_fields=best_binding.matched_fields if best_binding else [],
                unmatched_inputs=[
                    mi.original_value for mi in mapped if not mi.field_name
                ],
                total_inputs=len(screen_inputs),
                mapped_count=screen_mapped,
                unmapped_count=screen_unmapped,
                command_count=screen_commands,
            ))

        return KnowledgeEnrichedTemplate(
            capture_source=template.capture_source,
            session_id=template.session_id,
            screen_mappings=screen_mappings,
            total_inputs=total_inputs_count,
            mapped_inputs=total_mapped,
            unmapped_inputs=total_unmapped,
            command_inputs=total_commands,
            entities_involved=sorted(entities_involved),
        )

    @staticmethod
    def _word_in(key: str, text: str) -> bool:
        """Match por palavra inteira (case-insensitive).

        Substring simples gerava falsos positivos em capturas reais: o
        binding do programa "arq" casava com qualquer tela contendo
        "ARQ" de "ARQUIVO" no cabeçalho (captura 13).
        """
        if not key or not text:
            return False
        return re.search(r"\b" + re.escape(key) + r"\b", text,
                         re.IGNORECASE) is not None

    # ── Mapeamento posicional (cursor ↔ layout do fonte) ──

    def _load_layout(self, source_file: str) -> list:
        """Layout posicional de um fonte .prg, com cache por arquivo.

        O ``source_file`` gravado no binding é o caminho absoluto da
        máquina que rodou o analyze-source; se não existir localmente,
        tenta reconstruir a partir de ``source_root`` (trecho final do
        caminho, depois só o basename).
        """
        from pathlib import Path

        if source_file in self._layout_cache:
            return self._layout_cache[source_file]

        candidates = [Path(source_file)]
        if self.source_root:
            root = Path(self.source_root)
            parts = Path(source_file).parts
            if len(parts) >= 2:
                candidates.append(root.joinpath(*parts[-2:]))
            candidates.append(root / Path(source_file).name)

        layout: list = []
        for cand in candidates:
            if cand.is_file():
                layout = extract_layout(cand)
                if layout:
                    break
        self._layout_cache[source_file] = layout
        return layout

    def _find_positional_binding(
        self,
        screen_sample: str,
        bindings: list[ScreenEntityBinding],
    ) -> tuple[ScreenEntityBinding, list] | None:
        """Vincula uma tela ao fonte pelo código de menu + labels posicionados.

        Telas do Recital exibem o código do menu no cabeçalho
        (``3.6.1 PEDIDO E-COMMERCE``) e os programas seguem a convenção
        ``<modulo><codigo>.prg`` (``est361.prg``). Entre os arquivos
        candidatos, vence o que tiver mais labels posicionados (SAYs)
        presentes no texto da tela — mínimo de 2 para evitar coincidências.

        Retorna (binding, layout) ou None.
        """
        if not screen_sample or not self.source_root:
            return None

        codes = re.findall(r"\b\d{1,2}(?:\.\d{1,2}){1,2}\b", screen_sample)
        digits = {c.replace(".", "") for c in codes if len(c.replace(".", "")) >= 3}
        if not digits:
            return None

        # Arquivos candidatos: stem termina com os dígitos do código de menu
        files: dict[str, list[ScreenEntityBinding]] = {}
        for b in bindings:
            src = getattr(b, "source_file", "") or ""
            if not src:
                continue
            stem = src.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if any(stem.endswith(d) for d in digits):
                files.setdefault(src, []).append(b)

        best = None  # (score, source_file, bindings, layout)
        for src, bs in files.items():
            layout = self._load_layout(src)
            labels = layout_labels(layout)
            score = sum(1 for lb in labels
                        if self._word_in(lb, screen_sample))
            if score >= 2 and (best is None or score > best[0]):
                best = (score, src, bs, layout)

        if best is None:
            return None
        score, src, bs, layout = best
        binding = max(bs, key=lambda b: (len(b.matched_fields or []),
                                         b.confidence or 0.0))
        # Cópia rasa com evidência do vínculo posicional (não muta o binding
        # compartilhado da KB). matched_fields vai VAZIO de propósito: a
        # lista global do binding desalinha por tela — no modo posicional só
        # valem labels visíveis da tela, tipo semântico e cursor.
        binding = ScreenEntityBinding(
            screen_title=binding.screen_title,
            program_name=binding.program_name,
            source_file=src,
            source_lines=binding.source_lines,
            entity_name=binding.entity_name,
            operation=binding.operation,
            matched_fields=[],
            unmatched_screen_fields=list(binding.unmatched_screen_fields or []),
            confidence=max(binding.confidence or 0.0, 0.7),
            evidence=[f"layout posicional: menu {sorted(digits)} → "
                      f"{src} ({score} labels)"],
        )
        return binding, layout

    def _find_binding_for_screen(
        self,
        screen_sample: str,
        screen_sig: str,
        bindings_by_key: dict[str, ScreenEntityBinding],
        entity_index: dict[str, EntityDefinition],
    ) -> ScreenEntityBinding | None:
        """Encontra o melhor ScreenEntityBinding para uma tela da captura."""
        sample_upper = screen_sample.upper() if screen_sample else ""

        # Match por screen_sample contra screen_title
        for key, binding in bindings_by_key.items():
            if self._word_in(key, sample_upper):
                return binding

        # Match por screen_sig
        sig_upper = screen_sig.upper()
        for key, binding in bindings_by_key.items():
            if key and (self._word_in(key, sig_upper)
                        or screen_sig.upper() in key):
                return binding

        # Fallback: infere entidade por nome no screen_sample
        for ename in entity_index:
            if self._word_in(ename, sample_upper):
                return ScreenEntityBinding(
                    entity_name=ename, confidence=0.4,
                    evidence=[f"entidade '{ename}' encontrada no screen_sample"],
                )

        return None

    def generate_parametrized_sessions(
        self,
        enriched: KnowledgeEnrichedTemplate,
        session_count: int = 10,
        seed: int = 0,
    ) -> list[ParametrizedSession]:
        """Gera sessoes parametrizadas com dados sinteticos validos.

        Usa hash estavel (hashlib) em vez de hash() do Python.
        Preserva ordem original de todos os inputs.
        """
        import hashlib

        sessions: list[ParametrizedSession] = []

        entity_mappings: dict[str, ScreenKnowledgeMapping] = {}
        for mapping in enriched.screen_mappings:
            if mapping.entity_name and mapping.entity_name not in entity_mappings:
                entity_mappings[mapping.entity_name] = mapping

        # Coleta TODOS os inputs de TODAS as telas na ordem
        all_inputs: list[MappedInput] = []
        for mapping in enriched.screen_mappings:
            all_inputs.extend(mapping.mapped_inputs)

        for sess_idx in range(session_count):
            session_data: dict[str, Any] = {}

            for entity_name, mapping in entity_mappings.items():
                if not mapping.field_schemas:
                    continue

                stable_seed = int(hashlib.sha256(
                    f"{entity_name}:{seed}:{sess_idx}".encode()
                ).hexdigest()[:8], 16)

                screen_schema = ScreenSchema(
                    screen_signature=mapping.screen_signature,
                    title=entity_name,
                    program_name=entity_name,
                    fields=mapping.field_schemas,
                )
                synth_schema = SyntheticSchema(
                    screen=screen_schema,
                    entity_name=entity_name,
                    quantity=1,
                    seed=stable_seed,
                )
                dataset = self.dataset_builder.build(synth_schema)
                if dataset.records:
                    record_data = dataset.records[0].data
                    for key, val in record_data.items():
                        session_data[f"{entity_name}.{key}"] = val
                        session_data[key] = val

            # Renderiza TODOS os inputs — placeholder se mapeado, original se nao
            session_inputs: list[str] = []
            for mi in all_inputs:
                if mi.placeholder:
                    session_inputs.append(
                        self.template_engine.render(mi.placeholder, session_data)
                    )
                else:
                    session_inputs.append(mi.original_value)

            sessions.append(ParametrizedSession(
                session_index=sess_idx,
                inputs=session_inputs,
                data=session_data,
            ))

        return sessions

    def to_screen_schemas(
        self,
        enriched: KnowledgeEnrichedTemplate,
    ) -> list[ScreenSchema]:
        """Extrai ScreenSchemas do template enriquecido para geracao sintetica."""
        schemas: list[ScreenSchema] = []
        for mapping in enriched.screen_mappings:
            if mapping.field_schemas:
                schemas.append(ScreenSchema(
                    screen_signature=mapping.screen_signature,
                    title=mapping.entity_name,
                    program_name=mapping.entity_name,
                    fields=mapping.field_schemas,
                ))
        return schemas

    # ── Mapeamento interno ──

    def _map_screen(
        self,
        screen_sig: str,
        screen_context: dict,
        input_index_offset: int,
        inputs: list[str],
        entity_index: dict[str, EntityDefinition],
        bindings_by_sig: dict[str, ScreenEntityBinding],
    ) -> ScreenKnowledgeMapping:
        """Mapeia uma tela da captura para entidade e campos."""

        # Busca binding pelo screen_context (screen_sample pode ter titulo)
        screen_sample = screen_context.get("screen_sample", "")
        entity_name = ""
        binding_confidence = 0.0

        # Tenta match por screen_sig no bindings
        for key, binding in bindings_by_sig.items():
            if key in screen_sig.upper() or screen_sig.upper() in key:
                entity_name = binding.entity_name
                binding_confidence = binding.confidence
                break

        # Tenta match por texto da tela (screen_sample)
        if not entity_name and screen_sample:
            for key, binding in bindings_by_sig.items():
                if key in screen_sample.upper():
                    entity_name = binding.entity_name
                    binding_confidence = binding.confidence
                    break

        # Se nao encontrou binding, tenta inferir pelo nome da screen_sig
        if not entity_name:
            for ename in entity_index:
                if ename in screen_sig.upper():
                    entity_name = ename
                    binding_confidence = 0.5
                    break

        entity = entity_index.get(entity_name.upper()) if entity_name else None

        # Extrai inputs desta tela (aproximacao: screen_context pode ter indices)
        screen_inputs = screen_context.get("_inputs", [])
        if not screen_inputs:
            # Fallback: usa inputs globais na ordem
            screen_inputs = []

        mapped_inputs: list[MappedInput] = []
        unmatched: list[str] = []
        field_schemas: list[FieldSchema] = []

        if entity:
            # Mapeia inputs para campos da entidade
            entity_fields = {f.name.upper(): f for f in entity.fields}
            field_schemas = self._entity_to_field_schemas(entity)

            for idx, inp_value in enumerate(screen_inputs):
                mapped = self._map_input_to_field(
                    input_index=input_index_offset + idx,
                    value=str(inp_value),
                    entity=entity,
                    entity_fields=entity_fields,
                )
                if mapped.field_name:
                    mapped_inputs.append(mapped)
                else:
                    unmatched.append(str(inp_value))

        return ScreenKnowledgeMapping(
            screen_signature=screen_sig,
            entity_name=entity_name,
            operation="",
            binding_confidence=binding_confidence,
            mapped_inputs=mapped_inputs,
            field_schemas=field_schemas,
            unmatched_inputs=unmatched,
        )

    def _grid_source_mapping(
        self,
        input_index: int,
        value: str,
        val_type: str,
        pf,
        position: tuple[int, int],
    ) -> MappedInput:
        """Mapeia célula de grade dbedit para a entidade da tabela real que
        alimenta a grade (``grid_source`` do layout: est361=itens,
        est366=pagamento no est361.prg).

        A tabela da grade é evidência mais forte que o binding tela↔entidade
        da KB (que pode ser espúrio — entidade "arq" da captura 13): a célula
        editada pertence ao alias temporário preenchido com
        ``replace ... with <tabela>->campo``. Não depende do campo existir na
        KB — a geração do dado usa nome/PICTURE/original.
        """
        src = (getattr(pf, "grid_source", "") or "").strip().lower()
        pic = (getattr(pf, "picture", "") or "").strip()
        dtype = "number" if re.fullmatch(r"[9.,]+", pic) else "text"
        return MappedInput(
            input_index=input_index, original_value=value,
            original_type=val_type, entity_name=src,
            field_name=pf.field, field_datatype=dtype,
            semantic_type=self._infer_semantic_type(pf.field),
            method="by_grid_source",
            placeholder=f"{{{{{src}.{pf.field}}}}}",
            confidence=0.8,
            evidence=[f"by_grid_source: ({position[0]},{position[1]})→"
                      f"{pf.var}→{src}.{pf.field}"],
            picture=pic,
            is_grid=True,
            grid_source=src)

    def _map_input_to_field(
        self,
        input_index: int,
        value_or_data_index=None,
        value: str | None = None,
        entity: EntityDefinition | None = None,
        entity_fields: dict[str, FieldDefinition] | None = None,
        matched_fields: list[str] | None = None,
        used_fields: set[str] | None = None,
        data_index: int | None = None,
        label_only: bool = False,
        position: tuple[int, int] | None = None,
        layout: list | None = None,
    ) -> MappedInput:
        """Mapeia input→campo.

        ``label_only=True`` (binding de fallback): só aceita match semântico
        forte (cpf, email, ...) ou pelas labels visíveis da tela
        (matched_fields); ordem/nome de campo ficam desligados para não
        inventar substituições sem vínculo tela↔entidade real.

        ``position`` + ``layout`` (opcionais): posição do cursor no instante
        da digitação e layout posicional do fonte (@ row,col GET). Ativa o
        método ``by_cursor_position`` — inclusive para reclassificar valores
        de 1-2 dígitos (cara de opção de menu) que foram digitados
        exatamente na posição de um campo.

        Assinatura compativel com chamadas antigas e novas:
        - Chamada antiga: _map_input_to_field(input_index, value, entity, entity_fields, ...)
        - Chamada nova:  _map_input_to_field(input_index=input_index, data_index=..., value=..., ...)

        Quando value_or_data_index é string → modo antigo (value veio como 2o arg posicional).
        Quando data_index é passado como keyword → modo novo.
        """
        # ── Detecta modo de chamada ──
        if data_index is not None:
            # Modo novo: data_index passado como keyword
            actual_data_index = data_index
            actual_value = value_or_data_index if isinstance(value_or_data_index, str) else (value if isinstance(value, str) else "")
            actual_entity = entity
            actual_entity_fields = entity_fields
        elif isinstance(value_or_data_index, str):
            # Modo antigo: _map_input_to_field(input_index, value, entity, entity_fields, ...)
            actual_data_index = input_index
            actual_value = value_or_data_index
            actual_entity = value  # type: ignore[assignment]
            actual_entity_fields = entity  # type: ignore[assignment]
        elif value is not None and isinstance(value, str):
            # Modo hibrido: data_index como 2o arg posicional, value como 3o
            actual_data_index = value_or_data_index if isinstance(value_or_data_index, int) else input_index
            actual_value = value
            actual_entity = entity
            actual_entity_fields = entity_fields
        else:
            # Fallback
            actual_data_index = value_or_data_index if isinstance(value_or_data_index, int) else input_index
            actual_value = value if isinstance(value, str) else ""
            actual_entity = entity
            actual_entity_fields = entity_fields

        if actual_entity is None or actual_entity_fields is None:
            return MappedInput(input_index=input_index, original_value=str(actual_value),
                               method="unmapped", evidence=["sem entidade para mapear"])

        # ── Reatribui para o resto do codigo usar os nomes originais ──
        value = actual_value
        entity = actual_entity
        entity_fields = actual_entity_fields
        data_index = actual_data_index

        stripped = value.strip()
        matched_fields = matched_fields or []
        used_fields = used_fields or set()

        # Teclas de controle e marcadores: nunca mapear
        if stripped.startswith("{KEY:"):
            return MappedInput(input_index=input_index, original_value=value,
                               original_type="key_marker")
        if stripped in ("\r", "\n", "\r\n", "\t", "\x1b", ""):
            return MappedInput(input_index=input_index, original_value=value,
                               original_type="key_control")
        if re.match(r"^F\d{1,2}$", stripped, re.IGNORECASE):
            return MappedInput(input_index=input_index, original_value=value,
                               original_type="key_function")
        if re.match(r"^\x1b\[", stripped):
            return MappedInput(input_index=input_index, original_value=value,
                               original_type="key_sequence")
        # Confirmacao S/N
        if re.match(r"^[SsNn]$", stripped) and len(stripped) == 1:
            return MappedInput(input_index=input_index, original_value=value,
                               original_type="confirm")

        if not stripped:
            return MappedInput(input_index=input_index, original_value=value)

        # Classifica o tipo do valor
        val_type = self._classify_value(stripped)

        # Menu options: manter como estao — exceto quando o cursor estava
        # EXATAMENTE na posição de um GET do layout do fonte: aí o valor de
        # 1-2 dígitos é dado de campo (ex.: Frete=1, Situacao=4 na captura
        # 13), não opção de menu. O layout só existe com fonte vinculado,
        # então o hook independe de label_only.
        if val_type == "menu_option":
            if layout and position:
                pf = field_at(layout, position[0], position[1],
                              exact=True, value=stripped)
                if pf is not None:
                    # Célula de grade com tabela de origem conhecida: a
                    # tabela da grade vence o binding tela↔entidade da KB.
                    if pf.is_grid_cell and getattr(pf, "grid_source", "") \
                            and pf.field.upper() not in used_fields:
                        return self._grid_source_mapping(
                            input_index, value, val_type, pf, position)
                    target = entity_fields.get(pf.field.upper())
                    if target is not None \
                            and target.name.upper() not in used_fields:
                        method = "by_grid_column" if getattr(pf, "is_grid_cell", False) \
                            else "by_cursor_position"
                        return MappedInput(
                            input_index=input_index, original_value=value,
                            original_type=val_type, entity_name=entity.name,
                            field_name=target.name,
                            field_datatype=target.datatype,
                            semantic_type=self._infer_semantic_type(target.name),
                            method=method,
                            placeholder=f"{{{{{entity.name}.{target.name}}}}}",
                            confidence=0.85,
                            evidence=[f"{method}: "
                                      f"({position[0]},{position[1]})→"
                                      f"{pf.var}→{target.name}"],
                            picture=pf.picture,
                            valid_expr=getattr(pf, "valid_expr", "") or "",
                            lookup_table=_lookup_of(
                                target, getattr(pf, "valid_expr", "") or ""),
                            is_grid=pf.is_grid_cell,
                            grid_source=getattr(pf, "grid_source", ""))
                    # O cursor estava num GET do fonte, mas o campo não existe
                    # na entidade da KB (ou já foi usado) — manter o original
                    # com evidência explícita em vez de devolver method="".
                    reason = ("campo ausente na entidade "
                              f"'{entity.name}' da KB") if target is None \
                        else "campo já mapeado para outro input"
                    return MappedInput(
                        input_index=input_index, original_value=value,
                        original_type=val_type, entity_name=entity.name,
                        method="kept_layout_field",
                        layout_field=pf.field,
                        picture=pf.picture,
                        valid_expr=getattr(pf, "valid_expr", "") or "",
                        lookup_table=valid_lookup_table(
                            getattr(pf, "valid_expr", "") or ""),
                        is_grid=pf.is_grid_cell,
                        grid_source=getattr(pf, "grid_source", ""),
                        evidence=[f"cursor ({position[0]},{position[1]})→"
                                  f"{pf.var}→{pf.field}: {reason}; "
                                  "valor original mantido"])
                return MappedInput(
                    input_index=input_index, original_value=value,
                    original_type=val_type, method="menu_option_kept",
                    evidence=["valor curto com cara de opção de menu; cursor "
                              "fora de GET conhecido do fonte — valor original "
                              "mantido"])
            return MappedInput(input_index=input_index, original_value=value,
                               original_type=val_type, method="menu_option_kept",
                               evidence=["opção de menu (1-2 dígitos) — valor "
                                         "original mantido"])

        # Procura campo: semantico > matched_fields > screen_order > field_name
        entity_field_list = list(entity.fields)

        # 1: Match semantico forte — apenas tipos especificos (cpf, email, phone...)
        #    Tipos genericos (number, decimal, text) devem usar matched_fields/screen_order
        _SPECIFIC_SEMANTIC = {"cpf", "cnpj", "email", "phone", "cep", "date", "datetime"}
        for _fname, field in entity_fields.items():
            fs = self._infer_semantic_type(field.name)
            if fs in _SPECIFIC_SEMANTIC and fs == val_type:
                return MappedInput(input_index=input_index, original_value=value,
                    original_type=val_type, entity_name=entity.name,
                    field_name=field.name, field_datatype=field.datatype,
                    semantic_type=fs, method="by_semantic_type",
                    placeholder=f"{{{{{entity.name}.{field.name}}}}}", confidence=0.95,
                    lookup_table=str(getattr(field, "lookup_table", "") or ""),
                    evidence=[f"by_semantic_type: {val_type}→{field.name}"])

        # 1.5: posição do cursor × layout posicional do fonte (@ row,col GET).
        # Antes do matched_fields: a posição exata do cursor é evidência mais
        # precisa que a ordem global de campos do binding. Não depende de
        # label_only — o layout só existe quando há fonte vinculado à tela.
        if layout and position:
            pf = field_at(layout, position[0], position[1], value=stripped)
            if pf is not None:
                # Célula de grade com tabela de origem conhecida: a tabela
                # da grade vence o binding tela↔entidade da KB.
                if pf.is_grid_cell and getattr(pf, "grid_source", "") \
                        and pf.field.upper() not in used_fields:
                    return self._grid_source_mapping(
                        input_index, value, val_type, pf, position)
                target = entity_fields.get(pf.field.upper())
                if target is not None and target.name.upper() not in used_fields:
                    method = "by_grid_column" if getattr(pf, "is_grid_cell", False) \
                        else "by_cursor_position"
                    return MappedInput(
                        input_index=input_index, original_value=value,
                        original_type=val_type, entity_name=entity.name,
                        field_name=target.name, field_datatype=target.datatype,
                        semantic_type=self._infer_semantic_type(target.name),
                        method=method,
                        placeholder=f"{{{{{entity.name}.{target.name}}}}}",
                        confidence=0.85,
                        evidence=[f"{method}: "
                                  f"({position[0]},{position[1]})→"
                                  f"{pf.var}→{target.name}"],
                        picture=pf.picture,
                        valid_expr=getattr(pf, "valid_expr", "") or "",
                        lookup_table=_lookup_of(
                            target, getattr(pf, "valid_expr", "") or ""),
                        is_grid=pf.is_grid_cell,
                        grid_source=getattr(pf, "grid_source", ""))

        # 2: matched_fields por posicao de dados (data_index)
        # Com binding fraco (fallback/posicional), a ordem dos labels só é
        # usada quando NÃO há posição de cursor (trilhas legadas): se a
        # posição existe e o passo de cursor não mapeou, a ordem dos labels
        # vira especulação (captura 13: 'i' de Incluir → campo 'total').
        if matched_fields and data_index < len(matched_fields) \
                and not (label_only and position is not None):
            mf_name = matched_fields[data_index]
            mf_upper = mf_name.upper()
            if mf_upper not in used_fields:
                for fname, field in entity_fields.items():
                    if fname.upper() == mf_upper:
                        return MappedInput(input_index=input_index, original_value=value,
                            original_type=val_type, entity_name=entity.name,
                            field_name=field.name, field_datatype=field.datatype,
                            semantic_type=self._infer_semantic_type(field.name),
                            method="by_matched_fields",
                            placeholder=f"{{{{{entity.name}.{field.name}}}}}", confidence=0.85,
                            lookup_table=str(getattr(field, "lookup_table", "") or ""),
                            evidence=[f"by_matched_fields[{data_index}]: {val_type}→{field.name}"])

        # 2.5: (passo de cursor movido para 1.5, antes do matched_fields)

        # 3: Ordem da tela (by data_index, bloqueia ID/CODIGO, pula used)
        _TECH = {"ID", "CODIGO", "COD", "SEQ", "SEQUENCIA", "NUMERO", "NR", "STATUS", "TIPO", "FLAG"}
        if not label_only and data_index < len(entity_field_list):
            pos_field = entity_field_list[data_index]
            pos_upper = pos_field.name.upper()
            if pos_upper not in used_fields:
                if val_type in ("text", "text_long") and pos_upper in _TECH:
                    pass
                elif self._is_text_compatible(val_type, self._infer_semantic_type(pos_field.name), pos_field.datatype):
                    return MappedInput(input_index=input_index, original_value=value,
                        original_type=val_type, entity_name=entity.name,
                        field_name=pos_field.name, field_datatype=pos_field.datatype,
                        semantic_type=self._infer_semantic_type(pos_field.name),
                        method="by_screen_order",
                        placeholder=f"{{{{{entity.name}.{pos_field.name}}}}}", confidence=0.75,
                        evidence=[f"by_screen_order[{data_index}]: {val_type}→{pos_field.name}"])

        # 4: Texto → primeiro campo textual nao-tecnico nao usado
        if not label_only and val_type in ("text", "text_long"):
            for fname, field in entity_fields.items():
                if fname.upper() in _TECH or fname.upper() in used_fields:
                    continue
                fs = self._infer_semantic_type(fname)
                if fs in ("person_name", "company_name", "description",
                          "address", "city", "name", "nome", "descricao",
                          "razao_social", "fantasia", "observacao", "text"):
                    return MappedInput(input_index=input_index, original_value=value,
                        original_type=val_type, entity_name=entity.name,
                        field_name=field.name, field_datatype=field.datatype,
                        semantic_type=fs, method="by_field_name",
                        placeholder=f"{{{{{entity.name}.{field.name}}}}}", confidence=0.65,
                        evidence=[f"by_field_name(text): {val_type}→{field.name}"])

        # 4: Heuristica por nome (usa field.name, nao a chave uppercase)
        if not label_only:
            for _fname, field in entity_fields.items():
                fl = field.name.lower()
                if val_type == "cpf" and "cpf" in fl:
                    return MappedInput(input_index=input_index, original_value=value,
                        original_type=val_type, entity_name=entity.name,
                        field_name=field.name, field_datatype=field.datatype,
                        semantic_type="cpf", method="by_field_name",
                        placeholder=f"{{{{{entity.name}.{field.name}}}}}", confidence=0.80,
                        evidence=[f"by_field_name: cpf→{field.name}"])
                if val_type == "email" and ("email" in fl or "mail" in fl):
                    return MappedInput(input_index=input_index, original_value=value,
                        original_type=val_type, entity_name=entity.name,
                        field_name=field.name, field_datatype=field.datatype,
                        semantic_type="email", method="by_field_name",
                        placeholder=f"{{{{{entity.name}.{field.name}}}}}", confidence=0.80,
                        evidence=[f"by_field_name: email→{field.name}"])

        return MappedInput(input_index=input_index, original_value=value,
            original_type=val_type, method="unmapped",
            evidence=[f"tipo: {val_type}, sem match confiavel"])

    @staticmethod
    def _screen_labels(screen_sample: str, entity_fields: dict) -> list[str]:
        """Extrai campos da entidade visíveis como labels ("Nome:") na tela.

        Retorna os nomes de campo na ordem em que aparecem no screen_sample.
        Nomes curtos (<3 chars, ex.: ID) são ignorados para evitar ruído.
        """
        if not screen_sample:
            return []
        found: list[tuple[int, str]] = []
        for fname in entity_fields:
            if len(fname) < 3:
                continue
            m = re.search(r"\b" + re.escape(fname) + r"\s*:", screen_sample,
                          re.IGNORECASE)
            if m:
                found.append((m.start(), entity_fields[fname].name))
        return [name for _, name in sorted(found)]

    @staticmethod
    def _picture_to_constraints(fs: FieldSchema, pic: str) -> None:
        """Aplica uma PICTURE literal como constraint de um FieldSchema."""
        if not pic or pic.startswith("@"):
            return
        if fs.format or fs.min_value is not None or fs.max_value is not None:
            return
        if re.fullmatch(r"9+", pic):
            fs.datatype = "number"
            fs.min_value = 0
            fs.max_value = 10 ** len(pic) - 1
        elif re.fullmatch(r"[9.,]+", pic) and "9" in pic:
            # formato BR: "999.999,99" — a vírgula separa os decimais
            int_part = pic.split(",")[0]
            fs.datatype = "decimal"
            fs.min_value = 0
            fs.max_value = float(10 ** int_part.count("9") - 1)

    @staticmethod
    def _apply_picture_constraints(field_schemas: list[FieldSchema],
                                   layout: list) -> None:
        """PICTURE literal do GET vira constraint de geração do campo.

        Sem isso, campos de código curto (ex.: situacao com ``pict "99"``)
        recebiam texto lorem de dezenas de caracteres — inválido num replay
        real. Só anota campos sem formato semântico (cpf etc. têm prioridade)
        e sem min/max já definidos. PICTURES com função (``@!``) ou variável
        (``p_mascdoc``) não carregam largura e são ignoradas.
        """
        picts: dict[str, str] = {}
        for pf in layout:
            pic = getattr(pf, "picture", "") or ""
            if pic:
                picts.setdefault(pf.field.upper(), pic)

        for fs in field_schemas:
            CaptureKnowledgeIntegrator._picture_to_constraints(
                fs, picts.get(fs.name.upper(), ""))

    @staticmethod
    def _is_command(value: str) -> bool:
        """Verifica se o valor eh um comando/tecla, nao um dado de campo."""
        v = value.strip()
        if not v:
            # empty apos strip pode ser \r, \n puro
            if value in ("\r", "\n", "\r\n"):
                return True
            return False
        if v.startswith("{KEY:"):
            return True
        if v in ("\t", "\x1b"):
            return True
        # Tecla de ação de grade/browser xbase ('+' confirma o registro no
        # dbedit — gmens22 "<+> Confirma"); nunca é dado de campo.
        if v == "+":
            return True
        if re.match(r"^F\d{1,2}$", v, re.IGNORECASE):
            return True
        if re.match(r"^\x1b\[", v):
            return True
        return False

    @staticmethod
    def _is_text_compatible(val_type: str, field_semantic: str, field_datatype: str) -> bool:
        """Verifica compatibilidade entre tipo de valor e campo."""
        if val_type == field_semantic:
            return True
        if val_type in ("text", "text_long") and field_semantic in (
            "person_name", "company_name", "text", "description",
            "address", "city", "name", "nome", "descricao",
            "razao_social", "fantasia", "observacao",
        ):
            return True
        if val_type == "number" and field_semantic in ("number", "integer", "decimal", "money", "quantity"):
            return True
        if val_type == "number" and field_datatype in ("integer", "decimal", "number", "money"):
            return True
        return False

    @staticmethod
    def _classify_value(value: str) -> str:
        """Classifica o tipo de um valor de input."""
        v = value.strip()

        # Marcadores de tecla (do CaptureParametrizer)
        if v.startswith("{KEY:"):
            return "key_marker"
        if v in ("\r", "\n", "\r\n"):
            return "key_enter"
        if v == "\t":
            return "key_tab"
        if v == "\x1b":
            return "key_esc"
        if re.match(r"^F\d{1,2}$", v, re.IGNORECASE):
            return "key_function"
        if re.match(r"^\x1b\[", v):
            return "key_sequence"
        if re.match(r"^[SsNn]$", v) and len(v) == 1:
            return "confirm"
        if re.match(r"^\d{1,2}$", v):
            return "menu_option"
        if re.match(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$", v):
            return "cpf"
        if re.match(r"^\d{2}\.?\d{3}\.?\d{3}/\d{4}-?\d{2}$", v):
            return "cnpj"
        if re.match(r"^\(?\d{2}\)?\s?\d{4,5}-?\d{4}$", v):
            return "phone"
        if re.match(r"^\d{5}-?\d{3}$", v):
            return "cep"
        if re.match(r"^\d{4}-\d{2}-\d{2}$", v) or re.match(r"^\d{2}/\d{2}/\d{4}$", v):
            return "date"
        if "@" in v and "." in v.split("@")[-1]:
            return "email"
        if re.match(r"^[\d.,]+$", v):
            return "number"
        if len(v) > 30:
            return "text_long"
        return "text"

    @staticmethod
    def _infer_semantic_type(field_name: str) -> str:
        """Infere tipo semantico de um campo pelo nome."""
        low = field_name.lower()
        if "cpf" in low:
            return "cpf"
        if "cnpj" in low:
            return "cnpj"
        if any(t in low for t in ("telefone", "fone", "tel", "celular", "cel")):
            return "phone"
        if "email" in low or "mail" in low:
            return "email"
        if "cep" in low:
            return "cep"
        if "data" in low or "dt_" in low or low.startswith("dt"):
            return "date"
        if any(t in low for t in ("nome", "name", "razao", "fantasia")):
            return "person_name"
        if any(t in low for t in ("endereco", "end", "logradouro", "rua")):
            return "address"
        if any(t in low for t in ("valor", "preco", "total", "vlr")):
            return "number"
        if any(t in low for t in ("qtd", "qtde", "quantidade")):
            return "number"
        return "text"

    @staticmethod
    def _entity_to_field_schemas(entity: EntityDefinition) -> list[FieldSchema]:
        """Converte campos da entidade para FieldSchema com provider adequado."""
        schemas: list[FieldSchema] = []
        for f in entity.fields:
            semantic = CaptureKnowledgeIntegrator._infer_semantic_type(f.name)
            fs = FieldSchema(
                name=f.name,
                datatype=semantic if semantic != "text" else f.datatype,
                required=f.required,
                unique=f.unique_flag,
                prompt=f.prompt or f.name,
                lookup=f.lookup_table,
                min_length=f.min_length,
                max_length=f.max_length,
                format=semantic if semantic in ("cpf", "cnpj", "cep", "email", "phone", "date") else None,
            )
            schemas.append(fs)
        return schemas
