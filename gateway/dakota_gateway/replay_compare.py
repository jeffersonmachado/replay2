from __future__ import annotations

import base64
import re
import time
from difflib import SequenceMatcher

from .screen import TerminalScreenState
from dakota_terminal.volatile import mask_volatile_screen_text

# Teto de caracteres por tela gravada na evidência da falha (tela 58x80 ~4,6k).
MAX_SCREEN_EVIDENCE_CHARS = 12000


def expected_snapshot_from_event(ev: dict, *, legacy_sig: str = "") -> dict:
    """Extrai as assinaturas esperadas (canônicas + legada) de um evento de auditoria."""
    return {
        "text_sig": str(ev.get("expected_text_sig") or ev.get("text_sig") or ""),
        "visual_sig": str(ev.get("expected_visual_sig") or ev.get("visual_sig") or ""),
        "semantic_sig": str(ev.get("expected_semantic_sig") or ev.get("semantic_sig") or ""),
        "screen_sig": str(legacy_sig or ev.get("screen_sig") or ev.get("sig") or ""),
    }


def event_requires_comparison(ev: dict, *, mode: str) -> bool:
    """Indica se o evento possui assinatura esperada para o modo de comparação."""
    expected = expected_snapshot_from_event(ev)
    # Check mode-specific signature first
    if mode == "visual":
        has_canonical = bool(expected.get("visual_sig"))
    elif mode == "text":
        has_canonical = bool(expected.get("text_sig"))
    elif mode == "semantic":
        has_canonical = bool(expected.get("semantic_sig"))
    else:
        has_canonical = any(bool(expected.get(key)) for key in ("visual_sig", "text_sig", "semantic_sig"))
    # Legacy screen_sig also triggers comparison (backward compat)
    return has_canonical or bool(expected.get("screen_sig"))


def normalize_deterministic_mismatch_mode(value: str) -> str:
    """Normaliza o modo de tratamento de divergência determinística."""
    mode = str(value or "fail-fast").strip().lower()
    return mode if mode in {"fail-fast", "skip", "send-anyway"} else "fail-fast"


def observed_snapshot_from_session(session) -> dict:
    """Retorna as assinaturas canônicas do estado atual da sessão (ou vazio)."""
    if hasattr(session, "canonical_snapshot_now"):
        return session.canonical_snapshot_now()
    return {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}


def observed_screen_text_from_session(session, *, max_chars: int = MAX_SCREEN_EVIDENCE_CHARS) -> str:
    """Texto atual da tela da sessão de replay (ou "" quando indisponível)."""
    screen = getattr(session, "screen_state", None)
    text_fn = getattr(screen, "text", None)
    if not callable(text_fn):
        return ""
    try:
        value = str(text_fn() or "")
    except Exception:
        return ""
    return value[:max_chars]


def expected_screen_text_from_event(
    ev: dict,
    config=None,
    *,
    max_chars: int = MAX_SCREEN_EVIDENCE_CHARS,
) -> str:
    """Tela esperada a partir do evento da trilha, para evidência de falha.

    Renderiza ``screen_raw_b64`` (bytes brutos da tela estável da captura) na
    geometria/encoding da configuração quando presente; cai para
    ``screen_sample`` (preview das linhas não-vazias) quando não há bytes.
    """
    raw_b64 = str(ev.get("screen_raw_b64") or "")
    if raw_b64:
        rows = int(getattr(config, "rows", 25) or 25)
        cols = int(getattr(config, "cols", 80) or 80)
        encoding = str(getattr(config, "encoding", "utf-8") or "utf-8")
        try:
            raw = base64.b64decode(raw_b64.encode("ascii"), validate=False)
            state = TerminalScreenState(rows=rows, cols=cols, encoding=encoding)
            state.feed_bytes(raw)
            return str(state.text() or "")[:max_chars]
        except Exception:
            pass
    return str(ev.get("screen_sample") or "")[:max_chars]


def apply_volatile_mask_fallback(
    match: dict,
    *,
    expected_event: dict | None,
    observed_snapshot: dict,
    session_config=None,
) -> dict:
    """Segunda chance contra ruído ambiental na comparação de checkpoints.

    Quando as assinaturas divergem, re-compara o TEXTO das duas telas com os
    trechos voláteis mascarados (``mask_volatile_screen_text`` — ex.: memória
    livre da linha de status do Recital). Se só isso diferia, o checkpoint é
    considerado coincidente e o resultado é marcado com
    ``volatile_mask_applied=True``. A comparação é por texto (não reavalia
    atributos de cor) e não altera as assinaturas gravadas na trilha.
    """
    if match.get("matched") or not expected_event:
        return match
    observed_text = str(observed_snapshot.get("screen_text") or "")
    if not observed_text:
        return match
    expected_text = expected_screen_text_from_event(expected_event, session_config)
    if not expected_text:
        return match
    if mask_volatile_screen_text(expected_text) != mask_volatile_screen_text(observed_text):
        return match
    masked = dict(match)
    masked["matched"] = True
    masked["volatile_mask_applied"] = True
    return masked


def _normalized_pairs(substitutions: list | tuple | None) -> list[tuple[str, str]]:
    """Pares (original → sintético) válidos, ignorando identidade (campos mantidos)."""
    pairs: list[tuple[str, str]] = []
    for item in substitutions or []:
        try:
            orig, synth = str(item[0] or ""), str(item[1] or "")
        except (TypeError, IndexError):
            continue
        if orig and synth and orig != synth:
            pairs.append((orig, synth))
    return pairs


def _split_pairs(pairs: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], set]:
    """Separa pares longos (≥3 chars, distintivos — substituição direta segura)
    dos curtos (ex.: opção "1"→"2" — conferidos só nos trechos divergentes)."""
    long_pairs = [(o, s) for o, s in pairs if len(o) >= 3 and len(s) >= 3]
    short_pairs = {(o, s) for o, s in pairs if (o, s) not in long_pairs}
    return long_pairs, short_pairs


# Placeholder próprio da troca (distinto do volátil) para não confundir a
# máscara de Kb livres com o eco de uma substituição longa.
SUBSTITUTION_PLACEHOLDER = "<troca>"


# Identificador gerado pela aplicação (ex.: número do pedido D00011073) — não é
# dado digitado (não entra no de→para), mas muda a cada run sintética como
# consequência da troca. Mesmo shape na mesma posição = eco da troca.
_GENERATED_ID_RE = re.compile(r"[A-Za-z]?\d{5,}")


def _numeric_identity(value: str) -> str:
    """Normaliza número ignorando zeros à esquerda ('01' e '1' são o mesmo valor)."""
    return str(int(value)) if value.isdigit() else value


def _chunks_have_short_pair_echo(line_exp: str, line_obs: str, short_pairs: set) -> bool:
    """True quando algum trecho divergente casa um par curto (exato ou numérico).

    O casamento numérico ignora zeros à esquerda: o campo 'Frete' exibe '01'
    para a opção '1', então o par ('1' → '2') ecoa como '01' → ' 2'.
    """
    short_numeric = {(_numeric_identity(o), _numeric_identity(s)) for o, s in short_pairs}
    matcher = SequenceMatcher(None, line_exp, line_obs, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        chunk_exp, chunk_obs = line_exp[i1:i2].strip(), line_obs[j1:j2].strip()
        if (chunk_exp, chunk_obs) in short_pairs:
            return True
        if (_numeric_identity(chunk_exp), _numeric_identity(chunk_obs)) in short_numeric:
            return True
    return False


def _numeric_tokens_echo(line_exp: str, line_obs: str, short_pairs: set) -> bool:
    """True quando TODOS os tokens numéricos divergentes casam pares curtos.

    Cobre o caso em que o diff de caracteres funde o número com o texto
    ao lado (o decode muda junto: '01 PAC' → ' 2 SEDEX'): compara a sequência
    de tokens ``\\d+`` das duas linhas, ignorando zeros à esquerda. Se algum
    token divergente não casa um par curto, a linha não é eco (conservador).
    """
    nums_exp = re.findall(r"\d+", line_exp)
    nums_obs = re.findall(r"\d+", line_obs)
    if not nums_exp or len(nums_exp) != len(nums_obs) or nums_exp == nums_obs:
        return False
    short_numeric = {(_numeric_identity(o), _numeric_identity(s)) for o, s in short_pairs}
    for token_exp, token_obs in zip(nums_exp, nums_obs):
        if token_exp == token_obs:
            continue
        if (_numeric_identity(token_exp), _numeric_identity(token_obs)) not in short_numeric:
            return False
    return True


def _chunks_have_generated_id_echo(line_exp: str, line_obs: str) -> bool:
    """True quando a linha difere só em identificadores gerados de mesmo shape."""
    ids_exp = _GENERATED_ID_RE.findall(line_exp)
    ids_obs = _GENERATED_ID_RE.findall(line_obs)
    if not ids_exp or ids_exp == ids_obs or len(ids_exp) != len(ids_obs):
        return False
    shapes = lambda ids: [re.sub(r"\d", "#", token) for token in ids]
    return shapes(ids_exp) == shapes(ids_obs)


def _line_has_substitution_echo(line_exp: str, line_obs: str, short_pairs: set) -> bool:
    """True quando algum trecho divergente da linha é eco do de→para.

    Eco = placeholder de par longo, par curto nos trechos divergentes (exato
    ou numérico sem zeros à esquerda) ou identificador gerado pela aplicação
    com o mesmo shape na mesma posição (consequência da troca, ex.: número
    do pedido recém-criado).
    """
    if SUBSTITUTION_PLACEHOLDER in line_exp or SUBSTITUTION_PLACEHOLDER in line_obs:
        return True
    if _chunks_have_short_pair_echo(line_exp, line_obs, short_pairs):
        return True
    if _numeric_tokens_echo(line_exp, line_obs, short_pairs):
        return True
    return _chunks_have_generated_id_echo(line_exp, line_obs)


def _substitution_masked_lines(
    expected_text: str,
    observed_text: str,
    pairs: list[tuple[str, str]],
) -> tuple[list[str], list[str], set]:
    """Telas mascaradas (volátil + pares longos) prontas para diff por linha."""
    exp = mask_volatile_screen_text(expected_text)
    obs = mask_volatile_screen_text(observed_text)
    long_pairs, short_pairs = _split_pairs(pairs)
    for orig, synth in long_pairs:
        exp = exp.replace(orig, SUBSTITUTION_PLACEHOLDER)
        obs = obs.replace(synth, SUBSTITUTION_PLACEHOLDER)
    return exp.splitlines(), obs.splitlines(), short_pairs


def substitution_echo_line_indices(
    expected_text: str,
    observed_text: str,
    substitutions: list | tuple | None,
) -> list[int]:
    """Índices das linhas divergentes que contêm eco do de→para sintético.

    Uma linha divergente (após a máscara volátil) que fica idêntica após a
    máscara do de→para é troca pura; uma linha ainda divergente que carrega
    o placeholder ou um trecho casando par curto também é eco.
    """
    pairs = _normalized_pairs(substitutions)
    if not pairs:
        return []
    vol_exp_lines = mask_volatile_screen_text(expected_text).splitlines()
    vol_obs_lines = mask_volatile_screen_text(observed_text).splitlines()
    exp_lines, obs_lines, short_pairs = _substitution_masked_lines(expected_text, observed_text, pairs)
    indices: list[int] = []
    for idx in range(max(len(exp_lines), len(obs_lines))):
        line_exp = exp_lines[idx] if idx < len(exp_lines) else ""
        line_obs = obs_lines[idx] if idx < len(obs_lines) else ""
        vol_exp = vol_exp_lines[idx] if idx < len(vol_exp_lines) else ""
        vol_obs = vol_obs_lines[idx] if idx < len(vol_obs_lines) else ""
        if vol_exp == vol_obs:
            continue
        if line_exp == line_obs or _line_has_substitution_echo(line_exp, line_obs, short_pairs):
            indices.append(idx)
    return indices


def substitution_explained_diff(
    expected_text: str,
    observed_text: str,
    substitutions: list | tuple | None,
) -> bool:
    """True quando TODA a diferença entre as telas vem dos pares de→para.

    Caso estrito (troca pura): além do eco, não sobra nenhuma divergência de
    conteúdo — trechos só de espaços (deslocamento de preenchimento) são
    tolerados. Na prática as telas de checkpoint carregam também dados
    gerados pela aplicação (datas, totais, decodes como PAC→SEDEX), então a
    classificação usa o eco por linha (``substitution_echo_line_indices``).
    """
    pairs = _normalized_pairs(substitutions)
    if not pairs:
        return False
    exp_lines, obs_lines, short_pairs = _substitution_masked_lines(expected_text, observed_text, pairs)
    for idx in range(max(len(exp_lines), len(obs_lines))):
        line_exp = exp_lines[idx] if idx < len(exp_lines) else ""
        line_obs = obs_lines[idx] if idx < len(obs_lines) else ""
        if line_exp == line_obs:
            continue
        if not _line_diff_explained(line_exp, line_obs, short_pairs):
            return False
    return True


def _line_diff_explained(line_exp: str, line_obs: str, short_pairs: set) -> bool:
    """True quando os trechos divergentes da linha são pares de→para curtos.

    Trechos só de espaços (deslocamento de preenchimento pela diferença de
    tamanho do valor ecoado) são tolerados — não carregam conteúdo.
    """
    matcher = SequenceMatcher(None, line_exp, line_obs, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        chunk_exp = line_exp[i1:i2]
        chunk_obs = line_obs[j1:j2]
        if not chunk_exp.strip() and not chunk_obs.strip():
            continue
        if (chunk_exp.strip(), chunk_obs.strip()) in short_pairs:
            continue
        if SUBSTITUTION_PLACEHOLDER in chunk_exp or SUBSTITUTION_PLACEHOLDER in chunk_obs:
            # Resto de linha com par longo já mascarado — o deslocamento de
            # preenchimento em volta do placeholder não é conteúdo.
            stripped_exp = chunk_exp.replace(SUBSTITUTION_PLACEHOLDER, "").strip()
            stripped_obs = chunk_obs.replace(SUBSTITUTION_PLACEHOLDER, "").strip()
            if not stripped_exp and not stripped_obs:
                continue
        return False
    return True


def apply_synthetic_substitution_fallback(
    match: dict,
    *,
    expected_event: dict | None,
    observed_snapshot: dict,
    session_config=None,
    substitutions: list | tuple | None = None,
) -> dict:
    """Marca divergência que contém eco da troca de dados sintéticos.

    Critério por linha: basta UMA linha divergente com eco do de→para para a
    falha ser classificada como ``synthetic_data_swap`` — telas de checkpoint
    carregam também dados gerados pela aplicação (datas, totais, decodes),
    então exigir a tela inteira explicada não classificaria nada (análise da
    run 28: 0/226 telas 100% explicadas, 78/226 com eco presente).
    O checkpoint NÃO é dado como coincidente (``matched`` permanece False):
    a troca é apontada na run com classificação própria. O resultado traz
    ``synthetic_echo_lines`` com os índices das linhas de eco para a UI.
    """
    if match.get("matched") or not expected_event:
        return match
    observed_text = str(observed_snapshot.get("screen_text") or "")
    if not observed_text:
        return match
    expected_text = expected_screen_text_from_event(expected_event, session_config)
    if not expected_text:
        return match
    echo_lines = substitution_echo_line_indices(expected_text, observed_text, substitutions)
    if not echo_lines:
        return match
    flagged = dict(match)
    flagged["synthetic_substitution"] = True
    flagged["synthetic_echo_lines"] = echo_lines
    return flagged


def wait_for_signature_match(
    session,
    selector,
    *,
    compare,
    checkpoint_quiet_ms: int,
    checkpoint_timeout_ms: int,
    should_pause_or_cancel=None,
    drain_event=None,
    return_first_result: bool = False,
) -> tuple[bool, dict, dict]:
    """Máquina de espera de checkpoint compartilhada.

    Aguarda a tela estabilizar (quiet >= checkpoint_quiet_ms) e avalia
    compare(observed) -> dict com chave "matched". Por padrão repete até o
    timeout; com return_first_result=True retorna na primeira estabilização
    (semântica do replay não-controlado). should_pause_or_cancel (opcional) é
    chamado a cada iteração. drain_event(key) (opcional) consome eventos
    legíveis do seletor; o default é session.read_out().
    Retorna (matched, match, observed).
    """
    deadline = int(time.time() * 1000) + checkpoint_timeout_ms
    last_observed: dict = {}
    last_match = compare({})
    while int(time.time() * 1000) < deadline:
        if should_pause_or_cancel is not None:
            should_pause_or_cancel()
        for key, _ in selector.select(timeout=0.05):
            try:
                if drain_event is not None:
                    drain_event(key)
                else:
                    session.read_out()
            except Exception:
                pass
        quiet = int(time.time() * 1000) - session.last_out_ms
        if quiet >= checkpoint_quiet_ms:
            observed = observed_snapshot_from_session(session)
            last_observed = observed
            last_match = compare(observed)
            if last_match.get("matched") or return_first_result:
                return bool(last_match.get("matched")), last_match, observed
        time.sleep(0.02)
    observed = last_observed or observed_snapshot_from_session(session)
    return False, compare(observed), observed
