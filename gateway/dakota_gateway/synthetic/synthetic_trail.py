"""Materializa trilha auditável de replay a partir de captura real + dados
sintéticos (fluxo "replay sintético em 1 clique" da UI).

Dada uma captura real (``audit-*.jsonl``) e uma lista de substituições
``(original → sintético)`` na ordem em que aparecem na captura, gera uma
trilha derivada que:

- descarta o ruído de banner pré-sessão (eventos antes do primeiro
  ``deterministic_input`` com tela real — ex.: registro TeraTerm
  ``NOME = .../WINDOWS = ...`` que só aparece na 1ª conexão do terminal e
  poluiria o prompt do menu no replay);
- opcionalmente corta o preâmbulo de login/shell (``start_seq``): quando a
  captura começou fora do sistema (ex.: profile quebrado derrubou o usuário
  no shell e ele navegou manualmente até o ERP), o replay no ambiente são
  começa em outro estado (menu wrapper auto-iniciado) e a trilha inteira
  desalinha. ``detect_session_entry`` reconhece esse padrão, aponta o evento
  em que o sistema inicia (runtime Recital: ``ESC[?7l``) e deriva os passos
  de entrada (menu → shell → ERP) das próprias teclas gravadas;
- substitui os inputs mapeados pelos valores sintéticos — campos com
  máscara digitados dígito a dígito (ex.: CPF ``@R 999.999.999-99``) são
  trocados dígito a dígito, preservando 1 evento por tecla;
- renumera ``seq_global`` (o verifier rejeita gaps) e re-assina a cadeia
  (hash-chain + HMAC), então a trilha passa no ``verify`` e pode ser
  executada por um run real em modo determinístico.
"""
from __future__ import annotations

import base64
import json
import os
import re
from collections import Counter
from pathlib import Path

from ..audit_writer import b64
from ..canonical import payload_for_event
from ..crypto import hmac_sha256_hex, sha256_hex
from ..schema import AuditEvent
from .screen_layout import extract_layout, layout_labels

# Assinatura de tela "vazia" — marca o banner pré-sessão (registro de
# terminal) que não faz parte do fluxo da aplicação.
_EMPTY_SIGS = ("", "L=0;W=0")

# Início do runtime Recital: o ERP desliga o autowrap (ESC[?7l) e limpa a
# tela ao carregar — marca o fim do preâmbulo de login/shell.
_ERP_INIT_MARKER = "\x1b[?7l"
# Prompt do menu wrapper de login do ambiente Dakota ("0 - Fim" sai p/ shell).
_WRAPPER_PROMPT = "Digite a sua opcao"
# Erro típico de profile quebrado (derruba o login no shell em vez de abrir
# o menu/sistema — capturas 13 e 62).
_PROFILE_ERROR = "/etc/profile"
# Prompt de shell ksh do ambiente: "(ferblo)MIG24:/dakota1/u/ferblo >".
_SHELL_PROMPT_RE = re.compile(r"\(\w+\)[\w.\-]+:[/\w.\-~ ]*>")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-Z]|\x1b.")
# Registro de identificação do terminal (TeraTerm responde ao ESC[5i com
# "NOME = .../WINDOWS = .../TERATERM = ...") — não é tecla de menu/shell.
_TERMINAL_ID_RE = re.compile(r"^(NOME|WINDOWS|TERATERM|MACs)\s*=", re.M)
# ERP logo no início da sessão (até este seq) não é preâmbulo — é o fluxo
# normal de quem já cai dentro do sistema.
_MIN_ERP_SEQ = 15
# Código de menu Recital no cabeçalho das telas (ex.: "3.6.1") — os dígitos
# apontam o fonte ``<modulo><digitos>.prg`` (ex.: est361.prg).
_MENU_CODE_RE = re.compile(r"\b\d{1,2}(?:\.\d{1,2}){1,2}\b")
# Declaração do código de menu dentro do fonte (``numrot = "3.3"`` ou lista)
# — desempata stems com o mesmo sufixo numérico (330 existe em todo módulo).
_NUMROT_RE = re.compile(r'numrot\s*=\s*(?:\[([^\]]+)\]|"([^"]+)")', re.I)


def derive_module_entry(
    events: list[dict],
    start_seq: int,
    source_dir: str | Path | None,
    *,
    anchor: str = "",
    shell_prompt: str = "",
) -> dict | None:
    """Entrada alternativa pelo módulo Recital, derivada dos fontes.

    O comando shell gravado na captura pode depender de artefato volátil do
    home do usuário — na captura 62, ``k`` rodava ``dbrt ferblo`` e o
    ``ferblo.dbo`` não existia mais no destino na hora do replay (FATAL ERROR
    "Cannot open file ferblo.dbo"). A entrada estável do ambiente Dakota é
    pelo módulo: os códigos de menu das telas (``3.6.1``) localizam os fontes
    (``est361.prg``) sob ``source_dir``; o diretório de módulo mais frequente
    vence e o passo é ``cd <dados>/<mod>; dbrt <prg>/<mod>/<mod>`` (layout
    com config.<mod> no diretório de dados — ex.: est) ou
    ``cd <prg>/<mod>; dbrt <mod>`` (config.<mod> junto aos fontes — ex.: loj).

    Só retorna um passo quando ``<mod>.dbo`` e ``config.<mod>`` existem de
    fato no destino — caso contrário não há entrada alternativa confiável.
    """
    if not source_dir:
        return None
    root = Path(str(source_dir))
    if not root.is_dir():
        return None
    digits: list[str] = []
    for ev in events:
        seq = int(ev.get("seq_global") or 0)
        if seq < start_seq or ev.get("type") != "bytes" or ev.get("dir") == "in":
            continue
        for code in _MENU_CODE_RE.findall(_decode_b64(ev)):
            d = code.replace(".", "")
            # Menus de 2 níveis ('3.3' → est330.prg) usam o candidato
            # zero-padded, mesmo critério do capture_knowledge_integrator.
            for cand in ([d, d + "0"] if len(d) == 2 else [d]):
                if len(cand) >= 3:
                    digits.append(cand)
    if not digits:
        return None
    # Índice único dos fontes: stem do .prg → diretório do módulo.
    stems: dict[str, str] = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(".prg"):
                stems.setdefault(name[:-4].lower(), base)
    counts: Counter[str] = Counter()
    for d in set(digits):
        for stem, base in stems.items():
            if stem.endswith(d) and stem[: -len(d)].isalpha():
                counts[base] += 1
    if not counts:
        return None
    # Desempate em 2 níveis: (1) ``numrot`` declarado no fonte — códigos
    # curtos (ex.: 330 do menu "3.3") existem em todo módulo (ace330,
    # cad330, est330...) e a contagem de sufixos empata; (2) quando o
    # próprio numrot empata (todo módulo tem uma ponte <mod>330 com o
    # mesmo numrot), vence o fonte com mais labels posicionados visíveis
    # nas telas da captura (mínimo 2, mesmo critério do integrator) —
    # sem isso o módulo arbitrário podia não ter config e o fallback
    # vinha null (run 50 da captura 73).
    menu_digits = set(digits)
    numrot_hits: list[tuple[int, str, str]] = []  # (votos, base, stem)
    for stem, base in stems.items():
        if base not in counts:
            continue
        try:
            text = (Path(base) / f"{stem}.prg").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _NUMROT_RE.search(text)
        if not m:
            continue
        raw = (m.group(1) or m.group(2) or "").strip().strip("[]")
        for piece in re.split(r"[,\s]+", raw):
            nd = piece.strip().strip('"').replace(".", "")
            cands = [nd, nd + "0"] if len(nd) == 2 else [nd]
            if any(c in menu_digits for c in cands):
                numrot_hits.append((counts[base], base, stem))
                break
    mod_dir = ""
    hit_bases = {base for _v, base, _s in numrot_hits}
    if len(hit_bases) == 1:
        mod_dir = numrot_hits[0][1]
    elif numrot_hits:
        screen_text = _ANSI_RE.sub("", " ".join(
            _decode_b64(ev) for ev in events
            if int(ev.get("seq_global") or 0) >= start_seq
            and ev.get("type") == "bytes" and ev.get("dir") != "in"))
        best: tuple[int, int, str] | None = None  # (labels, votos, base)
        for votos, base, stem in numrot_hits:
            labels = layout_labels(extract_layout(Path(base) / f"{stem}.prg"))
            score = sum(1 for lb in labels if _word_in(lb, screen_text))
            if score >= 2 and (best is None or (score, votos) > (best[0], best[1])):
                best = (score, votos, base)
        if best:
            mod_dir = best[2]
    if not mod_dir:
        mod_dir = counts.most_common(1)[0][0]
    mod = Path(mod_dir).name
    if not (Path(mod_dir) / f"{mod}.dbo").is_file():
        return None
    data_cfg = root.parent / mod / f"config.{mod}"
    prg_cfg = Path(mod_dir) / f"config.{mod}"
    if data_cfg.is_file():
        send = f"cd {root.parent / mod}; dbrt {mod_dir}/{mod}\r"
    elif prg_cfg.is_file():
        send = f"cd {mod_dir}; dbrt {mod}\r"
    else:
        return None
    step: dict = {"send": send, "timeout_s": 60, "label": f"entrada do módulo {mod}"}
    if anchor:
        step["wait_text"] = anchor
    if shell_prompt:
        step["prompt"] = shell_prompt
    return step


def _decode_b64(ev: dict) -> str:
    try:
        return base64.b64decode(str(ev.get("data_b64") or "")).decode("utf-8", "replace")
    except Exception:
        return ""


def _word_in(key: str, text: str) -> bool:
    """Match por palavra inteira (case-insensitive) — mesmo critério do
    capture_knowledge_integrator (substring casava 'ARQ' de 'ARQUIVO')."""
    if not key or not text:
        return False
    return re.search(r"\b" + re.escape(key) + r"\b", text, re.IGNORECASE) is not None


def _first_printable_anchor(text: str) -> str:
    """Primeira âncora de texto visível de um draw ANSI (ex.: 'DAKOTA S/A')."""
    clean = _ANSI_RE.sub("", text)
    for line in clean.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 4:
            continue
        anchor = " ".join(line.split(" ")[:2])[:24].strip()
        if len(anchor) >= 4:
            return anchor
    return ""


def detect_session_entry(events: list[dict], source_dir: str | Path | None = None) -> dict | None:
    """Detecta captura que começa fora do sistema (preâmbulo shell/wrapper).

    Reconhece o padrão das capturas 13/62: erros de /etc/profile ou prompt de
    shell + menu wrapper antes do runtime Recital subir (``ESC[?7l``). Nesse
    cenário o replay no ambiente são começa em outro estado (o wrapper
    auto-inicia no login) e a trilha desalinha desde a primeira tecla.

    Retorna ``{"start_seq", "preamble", "dropped", "summary"}`` — o corte da
    trilha (``start_seq``) e os passos de entrada derivados das próprias
    teclas gravadas: o que o usuário digitou para sair do wrapper (ex.:
    ``0\\r``) e o comando shell que abriu o sistema (ex.: ``k\\r``), com
    âncoras de espera extraídas da gravação. Com ``source_dir``, inclui
    ``fallback`` — a entrada pelo módulo (``derive_module_entry``) para quando
    o comando gravado depende de artefato que já não existe no destino.
    ``None`` quando não há evidência de preâmbulo (nunca cortar às cegas).
    """
    outs: list[tuple[int, str]] = []
    ins: list[tuple[int, str]] = []
    det_seqs: list[int] = []
    for ev in events:
        seq = int(ev.get("seq_global") or 0)
        typ = ev.get("type")
        if typ == "bytes":
            text = _decode_b64(ev)
            (ins if ev.get("dir") == "in" else outs).append((seq, text))
        elif typ == "deterministic_input":
            det_seqs.append(seq)

    erp_seq = next((seq for seq, text in outs if _ERP_INIT_MARKER in text), None)
    if erp_seq is None or erp_seq <= _MIN_ERP_SEQ:
        return None
    pre_text = "".join(text for seq, text in outs if seq < erp_seq)
    has_wrapper = _WRAPPER_PROMPT in pre_text
    has_shell = bool(_SHELL_PROMPT_RE.search(pre_text)) or _PROFILE_ERROR in pre_text
    if not (has_wrapper and has_shell):
        return None

    # Teclas digitadas após o ÚLTIMO prompt do wrapper antes do ERP, até o
    # shell aparecer — no ambiente são o wrapper auto-inicia no login e as
    # mesmas teclas (ex.: "0\r") o atravessam. O registro de identificação
    # do terminal (TeraTerm, resposta ao ESC[5i) nunca é tecla de menu —
    # na captura 13 a sessão reconectou e o bloco caiu entre dois wrappers.
    last_wrap_seq = max(
        (seq for seq, text in outs if seq < erp_seq and _WRAPPER_PROMPT in text),
        default=None,
    )
    wrapper_keys: list[str] = []
    if last_wrap_seq is not None:
        for ev in events:
            seq = int(ev.get("seq_global") or 0)
            if seq <= last_wrap_seq or seq >= erp_seq or ev.get("type") != "bytes":
                continue
            text = _decode_b64(ev)
            if ev.get("dir") == "out":
                if wrapper_keys and _SHELL_PROMPT_RE.search(text):
                    break
            elif not _TERMINAL_ID_RE.search(text):
                wrapper_keys.append(text)
    wrapper_send = "".join(wrapper_keys)

    # Último comando shell antes do ERP subir — é o que abre o sistema
    # (ex.: "k\r"). Registro de terminal filtrado pelo mesmo motivo.
    shell_prompts = [(seq, text) for seq, text in outs if seq < erp_seq and _SHELL_PROMPT_RE.search(text)]
    shell_send = ""
    shell_wait = ""
    if shell_prompts:
        last_prompt_seq, last_prompt_text = shell_prompts[-1]
        shell_send = "".join(
            text for seq, text in ins
            if last_prompt_seq < seq < erp_seq and not _TERMINAL_ID_RE.search(text)
        )
        m = _SHELL_PROMPT_RE.search(last_prompt_text)
        if m:
            # Só a parte estável '<user>)<host>:' — o cwd gravado no prompt
            # (ex.: /dakota11/est) não se repete no replay (cai no HOME) e um
            # wait literal estouraria antes do send, despejando as teclas do
            # sistema no shell (run 49 da captura 73).
            head = re.match(r"\(\w+\)[\w.\-]+:", m.group(0))
            shell_wait = head.group(0) if head else m.group(0).strip()

    # Âncora da primeira tela do sistema: primeiro texto visível do draw pós-init.
    first_det_after = next((s for s in det_seqs if s > erp_seq), erp_seq + 30)
    anchor = ""
    for seq, text in outs:
        if seq < erp_seq or seq > first_det_after:
            continue
        anchor = _first_printable_anchor(text)
        if anchor:
            break
    if not anchor:
        return None

    steps: list[dict] = []
    if wrapper_send:
        steps.append({
            "wait_text": _WRAPPER_PROMPT,
            "send": wrapper_send,
            "timeout_s": 20,
            "optional": True,
            "label": "menu inicial do login",
        })
    if shell_send:
        step: dict = {"send": shell_send, "timeout_s": 20, "label": "shell do servidor"}
        if shell_wait:
            step["wait_text"] = shell_wait
        else:
            step["wait_stable_ms"] = 1000
        steps.append(step)
    steps.append({"wait_text": anchor, "timeout_s": 30, "label": "primeira tela do sistema"})
    steps.append({"wait_stable_ms": 800, "timeout_s": 10})

    dropped = sum(
        1 for ev in events
        if int(ev.get("seq_global") or 0) < erp_seq and ev.get("type") != "session_start"
    )
    caminho = " → ".join(str(s.get("label") or "estabilizar") for s in steps)
    summary = (
        f"preâmbulo de login/shell detectado ({dropped} eventos antes do sistema); "
        f"o replay entra sozinho: {caminho}"
    )
    result: dict = {"start_seq": erp_seq, "preamble": steps, "dropped": dropped, "summary": summary}
    if source_dir:
        fallback = derive_module_entry(
            events, erp_seq, source_dir, anchor=anchor, shell_prompt=shell_wait
        )
        if fallback:
            result["fallback"] = fallback
    return result


def _trim_before_seq(events: list[dict], start_seq: int) -> tuple[list[dict], int]:
    """Corta eventos anteriores a ``start_seq`` (mantém ``session_start``)."""
    keep: list[dict] = []
    dropped = 0
    for ev in events:
        seq = int(ev.get("seq_global") or 0)
        if seq < start_seq and ev.get("type") != "session_start":
            dropped += 1
            continue
        keep.append(ev)
    return keep, dropped


def det_key(ev: dict) -> str:
    """Conteúdo digitado de um evento ``deterministic_input`` (key_b64 canônico)."""
    if ev.get("key_b64"):
        try:
            return base64.b64decode(str(ev["key_b64"])).decode("utf-8", "replace")
        except Exception:
            return ""
    return str(ev.get("key_text") or "")


def _drop_pre_session_banner(events: list[dict]) -> tuple[list[dict], int]:
    """Remove eventos anteriores ao primeiro input com tela real.

    Mantém sempre ``session_start`` (configuração da sessão). Retorna
    ``(eventos, removidos)``.
    """
    first_real = None
    for ev in events:
        if ev.get("type") == "deterministic_input" and str(ev.get("screen_sig") or "") not in _EMPTY_SIGS:
            first_real = int(ev.get("seq_global") or 0)
            break
    if first_real is None or first_real <= 1:
        return events, 0
    keep: list[dict] = []
    dropped = 0
    for ev in events:
        seq = int(ev.get("seq_global") or 0)
        if seq < first_real and ev.get("type") != "session_start":
            dropped += 1
            continue
        keep.append(ev)
    return keep, dropped


# Teclas que nunca são dado de campo digitado tecla a tecla (ENTER, ESC,
# TAB, backspace) — quebram a sequência de teclas de um campo.
_NON_DATA_KEYS = {"\r", "\n", "\x1b", "\t", "\x00", "\x7f", "\x08"}


def _is_data_key(key: str) -> bool:
    """Tecla de 1 caractere que compõe o valor digitado num campo."""
    return len(key) == 1 and key not in _NON_DATA_KEYS


def _apply_substitutions(
    events: list[dict],
    substitutions: list[tuple[str, str]],
) -> tuple[list[str], list[str]]:
    """Aplica substituições em ordem; retorna (avisos, log de aplicações)."""
    warnings: list[str] = []
    applied: list[str] = []
    det_idx = [i for i, ev in enumerate(events) if ev.get("type") == "deterministic_input"]
    cursor = -1  # posição (em `events`) da última substituição aplicada

    for original, value in substitutions:
        if not original:
            continue
        # Campo digitado tecla a tecla: sequência de eventos de 1 caractere
        # cuja concatenação é o valor original (dígitos de máscara como CPF,
        # mas também alfanuméricos/decimais de grade — 'g2511', '229,9' da
        # captura 13). O valor novo é distribuído pelos eventos do run: 1
        # caractere por evento; se for mais longo que o run, o último evento
        # carrega o restante (input multi-caractere é válido no replay); se
        # mais curto, os excedentes ficam vazios (nada é enviado).
        if len(original) > 1:
            run: list[int] = []
            found: list[int] | None = None
            for i in det_idx:
                if i <= cursor:
                    continue
                key = det_key(events[i])
                if _is_data_key(key):
                    run.append(i)
                    typed = "".join(det_key(events[j]) for j in run)
                    if typed == original:
                        found = list(run)
                        break
                    if not original.startswith(typed):
                        run = []
                else:
                    run = []
            if found:
                last = len(found) - 1
                for pos_i, pos in enumerate(found):
                    if pos_i < last:
                        chunk = value[pos_i] if pos_i < len(value) else ""
                    else:
                        chunk = value[pos_i:] if pos_i < len(value) else ""
                    events[pos]["key_b64"] = b64(chunk.encode("utf-8"))
                    events[pos]["key_text"] = chunk
                cursor = found[-1]
                label = ("dígitos" if original.isdigit() and value.isdigit()
                         and len(value) == len(original) else "teclas")
                applied.append(f"{original}->{value} ({label}, seq {events[found[0]].get('seq_global')}..{events[found[-1]].get('seq_global')})")
                continue
        # Substituição simples: primeiro evento igual ao original após o cursor.
        pos = next(
            (i for i in det_idx if i > cursor and det_key(events[i]) == original),
            None,
        )
        if pos is None:
            warnings.append(f"input {original!r} não encontrado após seq do cursor; substituição pulada")
            continue
        events[pos]["key_b64"] = b64(value.encode("utf-8"))
        events[pos]["key_text"] = value
        cursor = pos
        applied.append(f"{original}->{value} (seq {events[pos].get('seq_global')})")

    return warnings, applied


def build_synthetic_trail(
    capture_jsonl: str | Path,
    substitutions: list[tuple[str, str]],
    out_dir: str | Path,
    *,
    hmac_key: bytes,
    drop_banner: bool = True,
    start_seq: int | str | None = None,
    source_dir: str | Path | None = None,
) -> dict:
    """Gera trilha auditável derivada da captura com dados sintéticos.

    ``substitutions``: pares ``(valor_original, valor_sintético)`` na ordem
    em que os inputs aparecem na captura. ``start_seq``: corte do preâmbulo
    de login/shell — um ``int`` (seq_global do primeiro evento mantido) ou
    ``"auto`` para detectar (``detect_session_entry``). ``source_dir`` (raiz
    dos fontes Recital) habilita o ``fallback`` de entrada pelo módulo na
    detecção automática. Retorna dict com
    ``out``, ``events``, ``dropped_banner``, ``dropped_entry``, ``entry``
    (detecção de entrada, quando ``auto``), ``applied`` e ``warnings``.
    """
    capture_path = Path(capture_jsonl)
    events = [
        json.loads(line)
        for line in capture_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    entry = None
    if start_seq == "auto":
        entry = detect_session_entry(events, source_dir=source_dir)
        start_seq = int(entry["start_seq"]) if entry else None
    elif start_seq is not None:
        start_seq = int(start_seq)

    dropped = 0
    if drop_banner:
        events, dropped = _drop_pre_session_banner(events)

    # O trim do preâmbulo roda DEPOIS do banner drop: ambos usam os
    # seq_global originais; se o trim viesse antes, o banner drop poderia
    # cortar o draw inicial do sistema (primeiro DET real fica depois dele).
    dropped_entry = 0
    if isinstance(start_seq, int) and start_seq > 1:
        events, dropped_entry = _trim_before_seq(events, start_seq)

    warnings, applied = _apply_substitutions(events, list(substitutions))

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    out_file = out_path / capture_path.name

    prev_hash = ""
    with open(out_file, "w", encoding="utf-8") as f:
        for new_seq, ev in enumerate(events, 1):
            ev["seq_global"] = new_seq
            schema_ev = AuditEvent(**{
                k: v for k, v in ev.items() if k in AuditEvent.__dataclass_fields__
            })
            schema_ev.prev_hash = prev_hash
            payload = payload_for_event(schema_ev).encode("utf-8")
            ev["prev_hash"] = prev_hash
            ev["hash"] = sha256_hex(payload)
            ev["hmac"] = hmac_sha256_hex(hmac_key, payload)
            prev_hash = ev["hash"]
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    return {
        "out": str(out_file),
        "events": len(events),
        "dropped_banner": dropped,
        "dropped_entry": dropped_entry,
        "entry": entry,
        "applied": applied,
        "warnings": warnings,
    }
