"""Associacao Tela → Programa → Entidade → Campos → Operacao.

Modulo responsavel por cruzar telas extraidas do codigo-fonte com entidades
detectadas, gerando bindings semanticos com evidencias e confianca.

Faz parte da entrega P2-A — Synthetic Knowledge Base.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .entity_catalog import EntityDefinition, FieldDefinition, ScreenDefinition


# ── Helpers ──

def _strip_accents(value: str) -> str:
    """Remove acentos de string usando NFKD normalization."""
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _canonical(name: str) -> str:
    """Canonicaliza nome de campo: acentos→ASCII, lowercase, strip."""
    return _strip_accents(name).lower().strip()


# ── Dataclass de binding ──

@dataclass
class ScreenEntityBinding:
    """Associacao entre uma tela e uma entidade, com evidencias."""
    screen_title: str = ""
    program_name: str = ""
    source_file: str = ""
    source_lines: tuple[int, int] = (0, 0)
    entity_name: str = ""
    operation: str = ""                         # create, read, update, delete, report, menu
    matched_fields: list[str] = field(default_factory=list)
    unmatched_screen_fields: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)


# ── Constantes de inferencia ──

# Campos genericos FRACOS — quase nenhum peso no matching
_GENERIC_WEAK: set[str] = {
    "ID", "CODIGO", "COD", "DATA", "STATUS", "TIPO",
    "OBS", "OBSERVACAO", "NUMERO", "NUM",
    "SEQUENCIA", "SEQ", "FLAG", "ATIVO", "SITUACAO", "INDICADOR",
}

# Campos SEMANTICOS fortes — peso alto, evidencias fortes
_GENERIC_STRONG: set[str] = {
    "CPF", "CNPJ", "EMAIL", "CEP", "TELEFONE", "CELULAR", "FONE",
    "RG", "IE", "INSCRICAO_ESTADUAL", "PLACA",
}

# Combinado: todos os genericos (fracos + fortes sao "generational" mas fortes tem peso diferente)
_GENERIC_FIELDS = _GENERIC_WEAK | _GENERIC_STRONG

# Campos que NUNCA sao considerados fortes (mesmo que nao estejam em _GENERIC_WEAK)
_ALWAYS_WEAK = {"NOME"}

# Aliases de entidades (programa/titulo → nome canonico da entidade)
_ENTITY_ALIASES: dict[str, list[str]] = {
    "CLIENTES": ["cli", "clie", "client", "cadcli", "altcli", "concli", "exccli"],
    "PRODUTOS": ["prod", "produ", "product", "cadprod", "altprod", "conprod"],
    "FORNECEDORES": ["forn", "fornec", "forne", "cadfor", "altfor", "confor"],
    "PEDIDOS": ["ped", "pedi", "order", "cadped", "altped"],
    "VENDAS": ["ven", "vend", "venda", "sale", "cadven"],
    "VENDEDORES": ["vendedor", "codven", "cdven"],
    "FINANCEIRO": ["fin", "finan", "titulo", "tit", "cadfin", "dupl", "dup"],
    "ESTOQUE": ["est", "estoq", "stock"],
    "NOTAS": ["nfe", "nf", "nota", "fiscal", "nfs", "notafiscal"],
    "ITENS_PEDIDO": ["itens", "item", "itped"],
    "CONTAS": ["cta", "contas", "contab"],
}

# Operacoes detectaveis no codigo-fonte
_CREATE_PATTERNS = [
    r"\bAPPEND\s+BLANK\b", r"\bINSERT\s+INTO\b", r"\bCREATE\b",
    r"\bINCLUI\b", r"\bINCLUSAO\b", r"\bNOVO\b",
]
_READ_PATTERNS = [
    r"\bSEEK\b", r"\bLOCATE\b", r"\bSELECT\b", r"\bSCATTER\b",
    r"\bFIND\b", r"\bCONSULTA\b", r"\bLISTA\b", r"\bEXIBE\b",
]
_UPDATE_PATTERNS = [
    r"\bREPLACE\b", r"\bUPDATE\b", r"\bGATHER\b", r"\bALTERA\b",
    r"\bALTERACAO\b", r"\bEDITA\b", r"\bMODIFICA\b",
]
_DELETE_PATTERNS = [
    r"\bDELETE\b", r"\bPACK\b", r"\bZAP\b", r"\bERASE\b",
    r"\bEXCLUI\b", r"\bEXCLUSAO\b", r"\bCANCELA\b", r"\bCANCELAMENTO\b",
    r"\bREMOVE\b", r"\bAPAGA\b",
]

# Palavras-chave no titulo
_TITLE_CREATE = {"cadastro", "cadastrar", "inclusao", "incluir", "novo", "nova", "criar"}
_TITLE_READ = {"consulta", "consultar", "pesquisa", "pesquisar", "visualizar", "listar", "listagem"}
_TITLE_UPDATE = {"alteracao", "alterar", "edicao", "editar", "modificar", "atualizar"}
_TITLE_DELETE = {"exclusao", "excluir", "cancelamento", "cancelar", "remover", "apagar"}
_TITLE_REPORT = {"relatorio", "relatorios", "impressao", "imprimir", "listagem"}
_TITLE_MENU = {"menu", "principal", "modulos", "sistema"}

# Padroes de relatorio no codigo
_REPORT_PATTERNS = [
    r"\bREPORT\b", r"\bLIST\b", r"\bPRINT\b", r"\bRELATORIO\b",
    r"\bIMPRIME\b", r"\bIMPRESSAO\b",
]


def _tokenize_for_match(name: str) -> set[str]:
    """Tokeniza nome de campo/entidade em tokens normalizados para comparacao."""
    canonical = _canonical(name)
    tokens = set()
    tokens.add(canonical)
    tokens.add(canonical.replace("_", ""))
    for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)", name):
        if part:
            tokens.add(_canonical(part))
    for part in canonical.split("_"):
        if part:
            tokens.add(part)
    return {t for t in tokens if len(t) >= 2}


def _strip_common_prefixes(field_name: str) -> list[str]:
    """Remove prefixos comuns e notacao hungara para matching."""
    name = _canonical(field_name)
    variants = [name]
    for prefix in ("m.", "c_", "n_", "l_", "d_", "a_", "p_", "t_"):
        if name.startswith(prefix):
            variants.append(name[len(prefix):])
    if len(name) >= 2 and name[0] in "cndlamptx" and name[1:2].isalpha():
        variants.append(name[1:])
    return variants


# Abreviacoes comuns Dakota → nome canonico (para comparacao, nao substituicao)
_ABBREVIATION_EXPAND: dict[str, str] = {
    "desc": "descricao", "descr": "descricao", "des": "descricao",
    "descricao": "descricao",
    "vlr": "valor", "val": "valor", "valor": "valor",
    "qtd": "quantidade", "qtde": "quantidade", "quantidade": "quantidade",
    "cod": "codigo", "cd": "codigo", "codigo": "codigo",
    "end": "endereco", "ender": "endereco", "endereco": "endereco",
    "nom": "nome", "nome": "nome",
    "tel": "telefone", "fone": "telefone", "telefone": "telefone",
    "cpf": "cpf", "cnpj": "cnpj",
    "email": "email", "mail": "email",
    "obs": "observacao", "observacao": "observacao",
    "prec": "preco", "prc": "preco", "preco": "preco",
    "cat": "categoria", "categoria": "categoria",
    "est": "estoque", "estoque": "estoque",
    "forn": "fornecedor", "fornecedor": "fornecedor",
    "func": "funcionario", "funcionario": "funcionario",
}

# Sufixos de contexto que podem ser removidos para matching
_CONTEXT_SUFFIXES = ["_prod", "_produto", "prod", "produto",
                     "_cli", "_cliente", "cli", "cliente",
                     "_forn", "_fornecedor", "forn", "fornecedor"]


def _expand_abbreviation(name: str) -> str | None:
    """Expande abreviacao Dakota para nome canonico, ou None se nao reconhecida."""
    low = _canonical(name)
    result = _ABBREVIATION_EXPAND.get(low)
    if result:
        return result
    for suffix in _CONTEXT_SUFFIXES:
        if low.endswith(suffix) and len(low) > len(suffix) + 1:
            base = low[:-len(suffix)]
            expanded = _ABBREVIATION_EXPAND.get(base)
            if expanded:
                return expanded
    return None


def _strip_hungarian_prefix(name: str) -> str:
    """Remove prefixo hungaro: cDesc→desc, nValor→valor."""
    clean = _canonical(name)
    if len(name) >= 2 and name[0].lower() in "cndlamptx" and name[1:2].isalpha():
        stripped = name[1].lower() + name[2:] if len(name) > 2 else name[1].lower()
        return _canonical(stripped)
    return clean


class ScreenEntityLinker:
    """Associa telas a entidades usando evidencias do codigo-fonte."""

    def __init__(self, source_dir: str = ""):
        self.source_dir = Path(source_dir) if source_dir else None
        self._file_cache: dict[str, str] = {}

    # ── API publica ──

    def link(
        self,
        screens: list[ScreenDefinition],
        entities: list[EntityDefinition],
    ) -> list[ScreenEntityBinding]:
        """Gera bindings para todas as telas contra todas as entidades."""
        bindings: list[ScreenEntityBinding] = []

        # Indexa entidades por nome para busca rapida
        entity_index: dict[str, EntityDefinition] = {}
        for ent in entities:
            entity_index[ent.name.upper()] = ent

        for screen in screens:
            binding = self._link_single(screen, entities, entity_index)
            bindings.append(binding)

        return bindings

    # ── Logica de linking por tela ──

    def _link_single(
        self,
        screen: ScreenDefinition,
        entities: list[EntityDefinition],
        entity_index: dict[str, EntityDefinition],
    ) -> ScreenEntityBinding:
        """Associa uma unica tela a melhor entidade candidata."""
        screen_fields = [f.name for f in screen.fields if f.name.strip()]
        screen_field_tokens: dict[str, set[str]] = {}
        for fname in screen_fields:
            screen_field_tokens[fname] = _tokenize_for_match(fname)

        best_entity = ""
        best_score = 0.0
        best_matched: list[str] = []
        best_unmatched: list[str] = []
        best_evidence: list[str] = []

        # Tenta match por nome do programa primeiro (atalho forte)
        program_entity = self._entity_from_program_name(
            screen.program_name, entity_index
        )

        for entity in entities:
            entity_field_set = self._build_entity_field_token_set(entity)
            matched, unmatched = self._match_fields(
                screen_fields, screen_field_tokens, entity
            )

            if not screen_fields:
                score = 0.0
            else:
                score = len(matched) / len(screen_fields)

            evidence: list[str] = []

            # Bonus: nome do programa casa com entidade
            if program_entity and entity.name.upper() == program_entity.upper():
                score = min(1.0, score + 0.25)
                evidence.append(f"programa '{screen.program_name}' referencia entidade '{entity.name}'")

            # Bonus: titulo contem nome da entidade
            if screen.title and entity.name.lower() in screen.title.lower():
                score = min(1.0, score + 0.15)
                evidence.append(f"titulo '{screen.title}' contem nome da entidade '{entity.name}'")

            # Bonus por storage type (sql/isam > unknown)
            if entity.storage_type in ("sql", "isam", "recital", "dbf"):
                score = min(1.0, score + 0.05)

            if score > best_score:
                best_score = score
                best_entity = entity.name
                best_matched = matched
                best_unmatched = unmatched
                best_evidence = evidence

        # ── Inferir operacao ──
        operation, op_evidence = self._infer_operation(screen)
        best_evidence.extend(op_evidence)

        # ── Ajustar confianca com regras mais rigorosas ──
        strong_matches = self._count_strong_matches(best_matched)
        has_entity_ref = any(
            "USE" in e or "INSERT" in e or "APPEND" in e or "REPLACE" in e
            or "SELECT" in e or "SEEK" in e
            for e in best_evidence
        )
        has_alias_match = any("alias" in e.lower() or "programa '" in e for e in best_evidence)
        has_title_match = any("titulo" in e.lower() for e in best_evidence)

        # Conta evidencias fortes
        strong_evidence_count = sum([
            1 if has_entity_ref else 0,
            1 if has_alias_match else 0,
            1 if has_title_match else 0,
            1 if strong_matches >= 2 else 0,
        ])

        if not screen_fields and not screen.title:
            confidence = 0.0
        elif not screen_fields:
            confidence = 0.3 if operation == "menu" else 0.1
        elif best_score >= 0.6 and strong_evidence_count >= 2:
            # Alta confianca requer >= 2 evidencias fortes
            confidence = min(0.95, 0.6 + (best_score - 0.6) * 0.7 + strong_evidence_count * 0.08)
        elif best_score >= 0.6:
            # Score alto mas sem evidencias fortes suficientes → confianca media
            confidence = 0.50 + (best_score - 0.6) * 0.3
        elif best_score >= 0.3:
            confidence = 0.30 + (best_score - 0.3) * 0.5
        else:
            confidence = max(0.05, best_score * 0.5)

        # Se so tem campos genericos fracos e nenhuma outra evidencia forte,
        # limita confidence a <= 0.35
        only_weak_fields = (
            best_matched and strong_matches == 0
            and not has_entity_ref and not has_alias_match and not has_title_match
        )
        if only_weak_fields:
            best_evidence.append("apenas campos genericos fracos, sem evidencias fortes")
            confidence = min(confidence, 0.35)

        # ── Classifica confianca ──
        if confidence >= 0.75:
            best_evidence.append(f"confianca alta ({confidence:.0%})")
        elif confidence >= 0.40:
            best_evidence.append(f"confianca media ({confidence:.0%})")
        else:
            best_evidence.append(f"confianca baixa ({confidence:.0%}) — associacao fraca")

        # ── Evidencia de campos ──
        if best_matched:
            best_evidence.append(
                f"{len(best_matched)}/{len(screen_fields)} campos da tela "
                f"correspondem a campos da entidade '{best_entity}': "
                f"{', '.join(best_matched[:8])}"
                + ("..." if len(best_matched) > 8 else "")
            )
        if best_unmatched:
            best_evidence.append(
                f"campos nao associados: {', '.join(best_unmatched[:5])}"
                + ("..." if len(best_unmatched) > 5 else "")
            )

        if not best_entity and screen_fields:
            best_evidence.append("nenhuma entidade candidata encontrada para esta tela")

        return ScreenEntityBinding(
            screen_title=screen.title,
            program_name=screen.program_name,
            source_file=screen.source_file or "",
            source_lines=screen.source_lines,
            entity_name=best_entity,
            operation=operation,
            matched_fields=best_matched,
            unmatched_screen_fields=sorted(best_unmatched),
            confidence=round(confidence, 4),
            evidence=best_evidence,
        )

    # ── Metodos auxiliares ──

    @staticmethod
    def _entity_from_program_name(
        program_name: str,
        entity_index: dict[str, EntityDefinition],
    ) -> str:
        """Tenta inferir entidade pelo nome do programa usando aliases.

        Ex: cadcli → CLIENTES, altprod → PRODUTOS, pedido → PEDIDOS
        """
        if not program_name:
            return ""
        clean = program_name.lower().replace(".prg", "").replace(".PRG", "")

        # Alias match (prioritario)
        for entity_name, aliases in _ENTITY_ALIASES.items():
            if entity_name.upper() not in entity_index:
                continue
            for alias in aliases:
                if alias in clean:
                    return entity_name

        # Token match (fallback)
        prog_tokens = _tokenize_for_match(clean)
        for ename in entity_index:
            ent_tokens = _tokenize_for_match(ename.lower())
            if prog_tokens & ent_tokens:
                return ename
        return ""

    @staticmethod
    def _build_entity_field_token_set(entity: EntityDefinition) -> set[str]:
        """Constroi conjunto de tokens para todos os campos da entidade."""
        tokens: set[str] = set()
        for f in entity.fields:
            tokens.update(_tokenize_for_match(f.name))
            # Tambem considera variantes sem prefixos comuns
            for variant in _strip_common_prefixes(f.name):
                tokens.update(_tokenize_for_match(variant))
        return tokens

    @staticmethod
    def _match_fields(
        screen_fields: list[str],
        screen_field_tokens: dict[str, set[str]],
        entity: EntityDefinition,
    ) -> tuple[list[str], list[str]]:
        """Retorna (matched, unmatched) comparando campos da tela com entidade.

        Campos genericos (NOME, CODIGO, DATA...) sao matched mas considerados
        evidencias fracas para confidence.
        """
        entity_field_names = {f.name.upper(): f.name for f in entity.fields}
        entity_tokens: dict[str, set[str]] = {}
        for f in entity.fields:
            entity_tokens[f.name] = _tokenize_for_match(f.name)

        matched: list[str] = []
        unmatched: list[str] = []

        for sf in screen_fields:
            sf_upper = sf.upper().strip()
            found = False

            if sf_upper in entity_field_names:
                matched.append(entity_field_names[sf_upper])  # nome original da entidade
                found = True

            if not found:
                # 2b. Hungarian prefix stripping + abbreviation expansion
                stripped = _strip_hungarian_prefix(sf)
                if stripped != sf.lower():
                    # Direct match after stripping
                    for ef_upper, ef_original in entity_field_names.items():
                        if stripped == ef_original.lower().strip():
                            matched.append(ef_original)
                            found = True
                            break
                if not found:
                    # Abbreviation expansion on stripped name
                    expanded = _expand_abbreviation(stripped)
                    if expanded:
                        for ef_upper, ef_original in entity_field_names.items():
                            ef_expanded = _expand_abbreviation(ef_original)
                            ef_cmp = (ef_expanded or ef_original).lower().strip()
                            if expanded == ef_cmp:
                                matched.append(ef_original)
                                found = True
                                break

            if not found:
                # 3. Token match
                sf_tokens = screen_field_tokens.get(sf, set())
                for ef_name, ef_tokens in entity_tokens.items():
                    if sf_tokens and ef_tokens and (sf_tokens & ef_tokens):
                        matched.append(ef_name)
                        found = True
                        break

            if not found:
                # 3. Expansao de abreviacao: desc→descricao, vlr→valor, etc.
                expanded = _expand_abbreviation(sf)
                if expanded:
                    exp_upper = expanded.upper().strip()
                    for ef_upper, ef_original in entity_field_names.items():
                        ef_expanded = _expand_abbreviation(ef_original)
                        ef_cmp = (ef_expanded or ef_original).lower().strip()
                        if expanded == ef_cmp or exp_upper == ef_upper:
                            matched.append(ef_original)
                            found = True
                            break

            if not found:
                # 4. Tentativa com prefixos stripados
                for variant in _strip_common_prefixes(sf):
                    var_tokens = _tokenize_for_match(variant)
                    for ef_name, ef_tokens in entity_tokens.items():
                        if var_tokens and ef_tokens and (var_tokens & ef_tokens):
                            matched.append(ef_name)  # nome original da entidade
                            found = True
                            break
                    if found:
                        break

            if not found:
                unmatched.append(sf)

        return matched, unmatched

    @staticmethod
    def _count_strong_matches(matched_fields: list[str]) -> int:
        """Conta campos matched que NAO sao fracos (exclui genericos fracos e NOME)."""
        count = 0
        for f in matched_fields:
            fu = f.upper().strip()
            if fu in _GENERIC_STRONG:
                count += 1  # CPF, CNPJ, EMAIL etc sao fortes
            elif fu not in _GENERIC_WEAK and fu not in _ALWAYS_WEAK:
                count += 1
        return count

    def _infer_operation(self, screen: ScreenDefinition) -> tuple[str, list[str]]:
        """Infere a operacao (create/read/update/delete/report/menu) da tela.

        Prioriza analise do trecho delimitado por source_lines.
        """
        evidence: list[str] = []
        title_lower = screen.title.lower() if screen.title else ""
        prog_lower = screen.program_name.lower() if screen.program_name else ""

        # ── Checar titulo ──
        title_ops: dict[str, str] = {}
        for kw in _TITLE_CREATE:
            if kw in title_lower:
                title_ops["create"] = kw
        for kw in _TITLE_READ:
            if kw in title_lower:
                title_ops["read"] = kw
        for kw in _TITLE_UPDATE:
            if kw in title_lower:
                title_ops["update"] = kw
        for kw in _TITLE_DELETE:
            if kw in title_lower:
                title_ops["delete"] = kw
        for kw in _TITLE_REPORT:
            if kw in title_lower:
                title_ops["report"] = kw
        for kw in _TITLE_MENU:
            if kw in title_lower:
                title_ops["menu"] = kw

        # ── Checar codigo no trecho da tela (source_lines) ──
        content = self._get_source_lines(screen)
        source_ops: dict[str, int] = {}

        if content:
            for pattern in _CREATE_PATTERNS:
                matches = len(re.findall(pattern, content, re.IGNORECASE))
                if matches:
                    source_ops["create"] = source_ops.get("create", 0) + matches
            for pattern in _READ_PATTERNS:
                matches = len(re.findall(pattern, content, re.IGNORECASE))
                if matches:
                    source_ops["read"] = source_ops.get("read", 0) + matches
            for pattern in _UPDATE_PATTERNS:
                matches = len(re.findall(pattern, content, re.IGNORECASE))
                if matches:
                    source_ops["update"] = source_ops.get("update", 0) + matches
            for pattern in _DELETE_PATTERNS:
                matches = len(re.findall(pattern, content, re.IGNORECASE))
                if matches:
                    source_ops["delete"] = source_ops.get("delete", 0) + matches
            for pattern in _REPORT_PATTERNS:
                matches = len(re.findall(pattern, content, re.IGNORECASE))
                if matches:
                    source_ops["report"] = source_ops.get("report", 0) + matches

        # ── Telas sem campos GET: classificar como menu ──
        has_get_fields = any(
            f.name.strip() and not f.name.startswith("m.") for f in screen.fields
        )
        if not has_get_fields and screen.title:
            if "menu" in title_ops:
                evidence.append(f"titulo contem 'menu': classificado como menu")
                return "menu", evidence
            if not any(kw in title_lower for kw in (
                "cadastro", "consulta", "alteracao", "exclusao", "relatorio"
            )):
                evidence.append("tela sem campos GET: classificada como menu")
                return "menu", evidence

        # ── Decidir operacao ──
        op_priority = ["create", "update", "delete", "read", "report", "menu"]
        for op in op_priority:
            if op in title_ops:
                evidence.append(f"titulo contem '{title_ops[op]}': operacao inferida como '{op}'")
                return op, evidence

        if source_ops:
            best_op = max(source_ops, key=lambda k: source_ops[k])
            evidence.append(
                f"codigo-fonte (linhas {screen.source_lines[0]}-{screen.source_lines[1]}) "
                f"contem padroes de '{best_op}' ({source_ops[best_op]} ocorrencias)"
            )
            return best_op, evidence

        if not has_get_fields:
            evidence.append("tela sem campos GET: classificada como menu (fallback)")
            return "menu", evidence

        evidence.append("operacao nao identificada")
        return "", evidence

    def _get_source_lines(self, screen: ScreenDefinition) -> str:
        """Obtem conteudo do trecho delimitado por source_lines do arquivo fonte."""
        if not screen.source_file or not self.source_dir:
            return self._get_source_content(screen.source_file or "")
        try:
            path = Path(screen.source_file)
            if not path.is_absolute() and self.source_dir:
                path = self.source_dir / path
            if not path.exists():
                return ""
            all_lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
            start, end = screen.source_lines
            if start > 0 and end >= start:
                # Pega o trecho da tela + N linhas apos para capturar USE/INSERT/APPEND
                end_extended = min(end + 20, len(all_lines))
                return "\n".join(all_lines[start - 1:end_extended])
            return "\n".join(all_lines)
        except Exception:
            return ""

    def _get_source_content(self, source_file: str) -> str:
        """Carrega conteudo do arquivo fonte, com cache."""
        if not source_file or not self.source_dir:
            return ""
        if source_file in self._file_cache:
            return self._file_cache[source_file]

        try:
            path = Path(source_file)
            if not path.is_absolute() and self.source_dir:
                path = self.source_dir / path
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                self._file_cache[source_file] = content
                return content
        except Exception:
            pass
        return ""

