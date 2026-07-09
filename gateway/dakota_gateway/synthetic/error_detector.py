from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DetectedError:
    """Erro detectado na saída de tela durante execução de jornada."""
    error_type: str  # validation, not_found, fatal, permission, lock, timeout, data_error
    severity: str  # low, medium, high, critical
    pattern_matched: str  # qual padrão foi detectado
    line_text: str  # linha onde foi detectado
    screen_context: str = ""  # contexto da tela (screen_signature)
    step_order: int = 0
    journey_id: str = ""
    session_index: int = 0
    field_name: str = ""  # campo relacionado, se identificável
    suggestion: str = ""  # sugestão de correção


# ---------------------------------------------------------------------------
# Padrões de erro de sistemas legados (Recital, Clipper, FoxPro, Lianja, Cobol)
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[str, str, str, str]] = [
    # (regex, error_type, severity, description)
    
    # Erros fatais
    (r"erro fatal", "fatal", "critical", "Erro fatal do sistema"),
    (r"ocorreu um erro fatal", "fatal", "critical", "Erro fatal reportado pelo sistema"),
    (r"PANIC", "fatal", "critical", "Pânico do sistema"),
    (r"ABORT", "fatal", "critical", "Abortado pelo sistema"),
    (r"fatal error", "fatal", "critical", "Fatal error"),
    
    # Erros de validação
    (r"(?:inválido|invalido|INVALIDO|INV[ÁA]LIDO)", "validation", "medium", "Dado inválido"),
    (r"ERROR\s+\[(.+?)\]", "validation", "medium", "Erro de validação: \\1"),
    (r"(?:deve ser|must be)\s+(.+)$", "validation", "medium", "Restrição: \\1"),
    (r"VALOR\s+(?:INV[ÁA]LIDO|INCORRETO|N[ÃA]O PERMITIDO)", "validation", "medium", "Valor rejeitado"),
    (r"PICTURE\s+.*ERROR", "validation", "medium", "Erro de formato/PICTURE"),
    (r"RANGE\s+.*ERROR", "validation", "medium", "Valor fora da faixa permitida"),
    
    # Registro não encontrado
    (r"(?:não encontrad[oa]|n[ãa]o encontrad[oa]|not found)", "not_found", "low", "Registro não encontrado"),
    (r"(?:sem resultado|no hay|nenhum registro)", "not_found", "low", "Nenhum registro localizado"),
    (r"(?:n[ãa]o existe|does not exist)", "not_found", "low", "Entidade não existe"),
    (r"(?:n[ãa]o cadastrad[oa]|n[ãa]o localizad[oa])", "not_found", "low", "Registro não cadastrado"),
    
    # Erros de permissão / acesso
    (r"(?:acesso negado|permiss[ãa]o negada|access denied|permission denied)", "permission", "high", "Acesso negado"),
    (r"(?:sem permiss[ãa]o|not authorized|n[ãa]o autorizado)", "permission", "high", "Sem permissão"),
    (r"(?:usu[áa]rio sem acesso|user without access)", "permission", "high", "Usuário sem acesso"),
    
    # Erros de lock / concorrência
    (r"(?:registro bloqueado|record locked|arquivo em uso|file in use)", "lock", "medium", "Registro/arquivo bloqueado"),
    (r"(?:deadlock|bloqueio|LOCK)", "lock", "high", "Conflito de bloqueio"),
    (r"(?:exclusivo|exclusive)", "lock", "medium", "Arquivo requer acesso exclusivo"),
    
    # Erros de timeout / conexão
    (r"(?:timeout|tempo esgotado|time out|TIMEOUT)", "timeout", "high", "Timeout"),
    (r"(?:conex[ãa]o perdida|connection lost|link down)", "timeout", "critical", "Conexão perdida"),
    (r"(?:sem resposta|no response|n[ãa]o responde)", "timeout", "high", "Sem resposta"),
    
    # Erros de dados / integridade
    (r"(?:duplicad[oa]|duplicate|j[áa] existe|already exists)", "data_error", "medium", "Registro duplicado"),
    (r"(?:integridade|integrity|viola[çc][ãa]o)", "data_error", "high", "Violação de integridade"),
    (r"(?:CHAVE\s+DUPLICADA|DUPLICATE\s+KEY)", "data_error", "high", "Chave duplicada"),
    (r"(?:FOREIGN\s+KEY|CHAVE\s+ESTRANGEIRA)", "data_error", "high", "Violação de chave estrangeira"),
    
    # Erros de arquivo / I/O
    (r"(?:arquivo n[ãa]o encontrado|file not found|n[ãa]o foi possível abrir)", "data_error", "high", "Arquivo não encontrado"),
    (r"(?:erro de leitura|erro de grava[çc][ãa]o|read error|write error)", "data_error", "high", "Erro de I/O"),
    (r"(?:disco cheio|disk full|espa[çc]o insuficiente)", "data_error", "critical", "Disco cheio"),
    
    # Erros de sintaxe/programa
    (r"(?:erro de sintaxe|syntax error|erro de compila[çc][ãa]o)", "fatal", "critical", "Erro de sintaxe"),
    (r"(?:vari[áa]vel n[ãa]o encontrada|variable not found)", "fatal", "critical", "Variável não encontrada"),
    (r"(?:ALIAS n[ãa]o encontrado|ALIAS not found)", "fatal", "critical", "Alias não encontrado"),
    
    # Erros específicos Lianja/Recital
    (r"SUBSCRIPT OUT OF RANGE", "fatal", "critical", "Índice fora de faixa"),
    (r"DATATYPE MISMATCH", "data_error", "high", "Tipo de dado incompatível"),
    (r"END OF FILE ENCOUNTERED", "data_error", "medium", "Fim de arquivo inesperado"),
    (r"BEGINNING OF FILE ENCOUNTERED", "data_error", "medium", "Início de arquivo inesperado"),
    (r"RECORD OUT OF RANGE", "data_error", "medium", "Registro fora de faixa"),
    (r"WORKAREA NOT IN USE", "fatal", "critical", "Área de trabalho não está em uso"),
    (r"UNRECOGNIZED COMMAND", "fatal", "critical", "Comando não reconhecido"),
    (r"INSUFFICIENT MEMORY", "fatal", "critical", "Memória insuficiente"),
]


class ErrorDetector:
    """Detecta erros em telas de sistemas legados usando padrões conhecidos."""

    def __init__(self):
        self._patterns: list[tuple[re.Pattern, str, str, str]] = [
            (re.compile(pattern, re.IGNORECASE), error_type, severity, desc)
            for pattern, error_type, severity, desc in _ERROR_PATTERNS
        ]

    def detect(self, screen_text: str, context: dict | None = None) -> list[DetectedError]:
        """Analisa texto de tela e retorna erros detectados."""
        context = context or {}
        errors: list[DetectedError] = []

        # Normalizar: remover ANSI, unificar whitespace
        cleaned = self._clean_screen(screen_text)

        for pattern, error_type, severity, desc in self._patterns:
            for m in pattern.finditer(cleaned):
                # Extrair linha de contexto
                line_start = max(0, cleaned.rfind("\n", 0, m.start()) + 1)
                line_end = cleaned.find("\n", m.end())
                if line_end < 0:
                    line_end = len(cleaned)
                line_text = cleaned[line_start:line_end].strip()

                # Tentar extrair nome do campo mencionado
                field_name = ""
                field_m = re.search(r"(?:campo|field|coluna|column)\s+['\"]?(\w+)", line_text, re.IGNORECASE)
                if field_m:
                    field_name = field_m.group(1)

                # Construir descrição com substituição de grupo
                description = desc
                if "\\1" in description and m.groups():
                    description = description.replace("\\1", m.group(1))

                errors.append(DetectedError(
                    error_type=error_type,
                    severity=severity,
                    pattern_matched=pattern.pattern[:80],
                    line_text=line_text[:200],
                    screen_context=context.get("screen_signature", ""),
                    step_order=context.get("step_order", 0),
                    journey_id=context.get("journey_id", ""),
                    session_index=context.get("session_index", 0),
                    field_name=field_name,
                    suggestion=self._suggest_fix(error_type, field_name, line_text),
                ))

        return errors

    def detect_all(self, screens: list[dict]) -> list[DetectedError]:
        """Analisa múltiplas telas e retorna erros consolidados."""
        all_errors: list[DetectedError] = []
        for screen in screens:
            text = screen.get("text", screen.get("norm_text", ""))
            context = {
                "screen_signature": screen.get("screen_sig", screen.get("screen_signature", "")),
                "step_order": screen.get("step_order", 0),
                "journey_id": screen.get("journey_id", ""),
                "session_index": screen.get("session_index", 0),
            }
            all_errors.extend(self.detect(text, context))
        return all_errors

    def classify_screen(
        self,
        screen_text: str,
        expected_signature: str = "",
        journey_step: int = 0,
    ) -> dict:
        """Classifica uma tela: ok, erro, divergente, ou navegação inesperada."""
        errors = self.detect(screen_text)
        
        if errors:
            # Priorizar o erro mais grave
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            worst = min(errors, key=lambda e: severity_order.get(e.severity, 99))
            return {
                "status": "error",
                "error_type": worst.error_type,
                "severity": worst.severity,
                "message": worst.line_text,
                "suggestion": worst.suggestion,
                "all_errors": [
                    {"type": e.error_type, "severity": e.severity, "message": e.line_text}
                    for e in errors
                ],
            }

        # Verificar divergência de tela (se esperava uma assinatura específica)
        if expected_signature:
            cleaned = self._clean_screen(screen_text)
            # Extrair indicadores de navegação
            is_login = bool(re.search(r"(?:login|usu[áa]rio|senha|password|sign\s*on)", cleaned, re.IGNORECASE))
            is_menu = bool(re.search(r"(?:menu|op[çc][ãa]o|escolha|selecione)", cleaned, re.IGNORECASE))
            is_error_page = bool(re.search(r"(?:erro|error|falha|n[ãa]o foi poss[íi]vel)", cleaned, re.IGNORECASE))

            if is_error_page and not errors:
                return {
                    "status": "warning",
                    "error_type": "possible_error",
                    "severity": "low",
                    "message": "Tela contém texto de possível erro não catalogado",
                    "all_errors": [],
                }

            if is_login and "menu" not in expected_signature.lower():
                return {
                    "status": "navigation_divergence",
                    "error_type": "navigation_error",
                    "severity": "high",
                    "message": "Sessão redirecionada para tela de login",
                    "all_errors": [],
                }

        return {"status": "ok", "error_type": "", "severity": "", "message": "", "all_errors": []}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_screen(text: str) -> str:
        """Remove ANSI e normaliza whitespace."""
        # Remove sequências ANSI
        cleaned = re.sub(r"\x1B\[[0-9;?]*[A-Za-z]", "", text)
        cleaned = re.sub(r"\x1B.", "", cleaned)
        # Normaliza quebras de linha
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        # Remove espaços repetidos
        cleaned = re.sub(r" {2,}", " ", cleaned)
        return cleaned

    @staticmethod
    def _suggest_fix(error_type: str, field_name: str, line_text: str) -> str:
        """Sugere correção baseada no tipo de erro."""
        suggestions = {
            "validation": f"Verificar valor do campo '{field_name}'" if field_name else "Verificar formato/tipo do dado informado",
            "not_found": "Verificar se o registro de referência existe antes desta operação",
            "permission": "Verificar permissões do usuário de replay",
            "lock": "Aguardar liberação do registro ou reduzir concorrência",
            "timeout": "Aumentar timeout ou verificar conectividade",
            "data_error": "Verificar unicidade/integridade dos dados gerados",
            "fatal": "Investigar causa raiz no sistema alvo - possível bug ou incompatibilidade",
        }
        return suggestions.get(error_type, "Analisar mensagem de erro completa")
