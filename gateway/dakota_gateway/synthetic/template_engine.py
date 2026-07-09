from __future__ import annotations

import re
from typing import Any

# Placeholder pattern: {{entidade.campo}} ou {{campo}}
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)(?:\.(\w+))?\}\}")


class TemplateEngine:
    """Transforma capturas em templates com placeholders e gera entradas parametrizadas."""

    @staticmethod
    def detect_placeholders(original_inputs: list[str]) -> list[str]:
        """Analisa entradas originais e sugere placeholders baseados em padroes.

        Exemplo:
            "JOAO SILVA" -> "{{cliente.nome}}"
            "12345678909" -> "{{cliente.cpf}}"
        """
        # Heuristica: se parece CPF, CNPJ, telefone, email, data...
        placeholders: list[str] = []
        for inp in original_inputs:
            stripped = inp.strip()
            if re.match(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$", stripped):
                placeholders.append("{{cliente.cpf}}")
            elif re.match(r"^\d{2}\.?\d{3}\.?\d{3}/\d{4}-?\d{2}$", stripped):
                placeholders.append("{{cliente.cnpj}}")
            elif re.match(r"^\(?\d{2}\)?\s?\d{4,5}-?\d{4}$", stripped):
                placeholders.append("{{cliente.telefone}}")
            elif re.match(r"^\d{5}-?\d{3}$", stripped):
                placeholders.append("{{cliente.cep}}")
            elif re.match(r"^\d{4}-\d{2}-\d{2}$", stripped):
                placeholders.append("{{data}}")
            elif re.match(r"^[\d.,]+$", stripped):
                placeholders.append("{{valor}}")
            elif "@" in stripped and "." in stripped:
                placeholders.append("{{cliente.email}}")
            else:
                placeholders.append("{{texto}}")
        return placeholders

    @staticmethod
    def render(template: str, data: dict[str, Any]) -> str:
        """Substitui placeholders. Case-insensitive para entidade e campo."""

        # Constroi indice case-insensitive: data_normalized[ENTIDADE][CAMPO] = valor
        data_ci: dict[str, dict[str, Any]] = {}
        for key, val in data.items():
            if isinstance(val, dict):
                inner = {k.upper(): v for k, v in val.items()}
                data_ci[key.upper()] = inner
                # Tambem expoe valores diretamente
                for k, v in val.items():
                    data_ci.setdefault("_flat", {})[k.upper()] = v
            else:
                data_ci.setdefault("_flat", {})[key.upper()] = val

        def _replace(m: re.Match) -> str:
            entity = m.group(1)
            field = m.group(2)
            if field:
                # {{entidade.campo}} — case-insensitive
                ent_key = entity.upper()
                field_key = field.upper()
                if ent_key in data_ci and field_key in data_ci[ent_key]:
                    return str(data_ci[ent_key][field_key])
                # Fallback: busca no flat
                flat = data_ci.get("_flat", {})
                compound = f"{ent_key}.{field_key}"
                if compound in flat:
                    return str(flat[compound])
                if field_key in flat:
                    return str(flat[field_key])
            else:
                # {{campo}} — case-insensitive
                flat = data_ci.get("_flat", {})
                if entity.upper() in flat:
                    return str(flat[entity.upper()])
            return m.group(0)

        return _PLACEHOLDER_RE.sub(_replace, template)

    @staticmethod
    def render_batch(
        templates: list[str],
        dataset: list[dict[str, Any]],
    ) -> list[list[str]]:
        """Renderiza um batch de templates com multiplos registros.

        Args:
            templates: Lista de strings template
            dataset: Lista de dicionarios com dados para cada sessao

        Returns:
            Lista de sessoes, cada uma com lista de strings renderizadas.
        """
        results: list[list[str]] = []
        for record in dataset:
            session_output: list[str] = []
            for tmpl in templates:
                session_output.append(TemplateEngine.render(tmpl, record))
            results.append(session_output)
        return results

    @staticmethod
    def extract_entities(templates: list[str]) -> set[str]:
        """Extrai nomes de entidades referenciadas nos templates."""
        entities: set[str] = set()
        for tmpl in templates:
            for m in _PLACEHOLDER_RE.finditer(tmpl):
                entities.add(m.group(1))
        return entities
