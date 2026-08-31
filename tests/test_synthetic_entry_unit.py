"""Testes da entrada automática no sistema (trim de preâmbulo + preamble).

Cobre o caso da captura 62: a gravação começou num shell quebrado (erros de
/etc/profile) e o usuário navegou manualmente até o ERP; no replay o login
são auto-inicia o menu wrapper e a trilha inteira desalinha. A correção
detecta o ponto de entrada do sistema (runtime Recital: ESC[?7l), corta o
preâmbulo da trilha sintética e deriva os passos de entrada (menu wrapper →
shell → ERP) a partir das próprias teclas gravadas na captura.
"""
from __future__ import annotations

import base64
import json
import time

from pathlib import Path

from dakota_gateway.synthetic.synthetic_trail import (
    build_synthetic_trail,
    derive_module_entry,
    detect_session_entry,
    det_key,
)
from dakota_gateway.replay_control.executors import _run_entry_preamble
from dakota_gateway.verifier import verify_log

HMAC_KEY = b"test-hmac-key"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _ev(seq, type_, **kw):
    ev = {
        "v": "v1",
        "seq_global": seq,
        "ts_ms": 1000 + seq,
        "type": type_,
        "actor": "ferblo",
        "session_id": "sess-1",
        "seq_session": seq,
    }
    ev.update(kw)
    return ev


def _out(seq, text):
    return _ev(seq, "bytes", dir="out", data_b64=_b64(text), n=len(text))


def _in(seq, text):
    return _ev(seq, "bytes", dir="in", data_b64=_b64(text), n=len(text))


def _det(seq, key, screen_sig="L=24;W=80;LBL=x"):
    return _ev(seq, "deterministic_input", key_b64=_b64(key), key_text=key, screen_sig=screen_sig)


def _capture62_like_events():
    """Preâmbulo no formato da captura 62 (profile quebrado → shell → wrapper
    → shell → k → ERP) seguido do fluxo de negócio."""
    events = [
        _ev(1, "session_start", logname="ferblo", rows=24, cols=80, term="dk100"),
        _out(2, "\x1b[5i\x1b[V\x1b[4i"),
        _det(3, ""),
        _in(4, "0"),
        _out(5, "0"),
        _in(7, "NOME = PCMAT-FERBLON\nWINDOWS = 6.2.9200\n\x04"),
        _out(9, "/etc/profile[137]: /usr/util/controle_de_acessos/pedeID.log: 0403-005 Cannot create\r\n"),
        _det(12, ""),
        _in(13, "\r"),
        _out(15, "Acesso autorizado via VPN.\r\n"),
        _out(17, "(ferblo)MIG24:/dakota1/u/ferblo > "),
        _det(18, ""),
    ]
    # estl\r → cd; wrapper aparece; "0\r" sai; date; k → ERP
    seq = 22
    for c in "estl":
        events += [_det(seq, c), _in(seq + 1, c), _out(seq + 1, c)]
        seq += 2
    events += [_det(seq, "\r"), _in(seq + 1, "\r"), _out(seq + 1, "\r\n(ferblo)MIG24:/dakota11/est > ")]
    seq += 2
    events += [_out(seq, "\x1b[H\x1b[2J"), _out(seq + 1, "Menu de opcoes do usuario ferblo\r\n"),
               _out(seq + 2, "  1 - (REDE LOJAS) Sistema das Lojas\r\n\r\n  0 - Fim\r\n"),
               _out(seq + 3, "\r\nDigite a sua opcao: ")]
    seq += 4
    events += [_det(seq, "0"), _in(seq + 1, "0"), _out(seq + 1, "0"),
               _det(seq + 2, "\r"), _in(seq + 3, "\r"),
               _out(seq + 3, "\r\n(ferblo)MIG24:/dakota1/u/ferblo > ")]
    seq += 4
    for c in "date":
        events += [_det(seq, c), _in(seq + 1, c), _out(seq + 1, c)]
        seq += 2
    events += [_det(seq, "\r"), _in(seq + 1, "\r"),
               _out(seq + 1, "\r\nFri Aug 28 14:52:46 -03 2026\r\n(ferblo)MIG24:/dakota1/u/ferblo > ")]
    seq += 2
    events += [_det(seq, "k"), _in(seq + 1, "k"), _out(seq + 1, "k"),
               _det(seq + 2, "\r"), _in(seq + 3, "\r")]
    seq += 4
    events += [_out(seq, "\x1b[?7l\x1b[0m\x1b[H\x1b[2J\x1b[1;1H")]
    erp_seq = seq
    seq += 1
    events += [_out(seq, "\x1b[?25l\x1b[0m\x1b[H\x1b[2J\x1b[1;1H\x1b[7m DAKOTA S/A   MENU PRINCIPAL\x1b[0m")]
    seq += 1
    events += [_det(seq, "3"), _in(seq + 1, "3")]
    seq += 2
    events += [_det(seq, "\r"), _in(seq + 1, "\r"), _ev(seq + 2, "session_end")]
    return events, erp_seq


def test_detect_entry_reconhece_preambulo_shell():
    events, erp_seq = _capture62_like_events()
    entry = detect_session_entry(events)
    assert entry is not None
    assert entry["start_seq"] == erp_seq
    steps = entry["preamble"]
    # passo 1: menu wrapper → sai com "0\r" (teclas reais da captura)
    assert steps[0]["wait_text"] == "Digite a sua opcao"
    assert steps[0]["send"] == "0\r"
    assert steps[0]["optional"] is True
    # passo 2: shell → comando "k\r" (último comando antes do ERP)
    assert steps[1]["send"] == "k\r"
    assert "MIG24:" in steps[1]["wait_text"]
    # passo 3: âncora da primeira tela do sistema
    assert steps[2]["wait_text"] == "DAKOTA S/A"
    assert "send" not in steps[2]
    # passo final: drenar até estabilizar
    assert steps[3]["wait_stable_ms"] > 0


def test_detect_entry_shell_wait_independente_do_cwd():
    """O wait do passo shell não pode carregar o diretório da captura: o
    replay cai no HOME (ex.: /dakota1/u/ferblo) e um wait com o cwd gravado
    (ex.: /dakota11/est) estoura ANTES do send — a run 49 digitou toda a NF
    no ksh por causa disso. A parte estável é '<user>)<host>:'."""
    events, _ = _capture62_like_events()
    entry = detect_session_entry(events)
    assert entry is not None
    step = entry["preamble"][1]
    assert step["wait_text"] == "(ferblo)MIG24:"
    assert "/" not in step["wait_text"]
    assert ">" not in step["wait_text"]


def test_detect_entry_sem_erp_retorna_none():
    events = [
        _ev(1, "session_start"),
        _out(2, "(ferblo)MIG24:/dakota1/u/ferblo > "),
        _in(3, "date\r"),
        _ev(4, "session_end"),
    ]
    assert detect_session_entry(events) is None


def test_detect_entry_erp_imediato_retorna_none():
    """ERP desde o início da sessão — não há preâmbulo a cortar."""
    events = [
        _ev(1, "session_start"),
        _out(2, "\x1b[?7l\x1b[0m\x1b[H\x1b[2J"),
        _out(3, "DAKOTA S/A  MENU PRINCIPAL"),
        _det(4, "3"),
        _ev(5, "session_end"),
    ]
    assert detect_session_entry(events) is None


def test_detect_entry_sem_evidencia_shell_retorna_none():
    """ERP tardio mas sem prompt/wrapper/erro de profile — não cortar às cegas."""
    events = [_ev(1, "session_start")]
    for i in range(2, 30):
        events.append(_out(i, f"carregando modulo {i}\r\n"))
    events.append(_out(30, "\x1b[?7l\x1b[0m\x1b[H\x1b[2J"))
    events.append(_out(31, "DAKOTA S/A  MENU PRINCIPAL"))
    events.append(_det(32, "3"))
    assert detect_session_entry(events) is None


def test_detect_entry_ignora_registro_de_terminal():
    """Captura 13: a sessão reconectou — há DOIS wrappers e o bloco de
    identificação TeraTerm ('NOME = ...\\nTERATERM = ...') cai entre eles.
    O send do wrapper deve ser só '0\\r' (teclas após o ÚLTIMO prompt do
    wrapper); o registro de terminal nunca é tecla de menu."""
    events = [
        _ev(1, "session_start", logname="ferblo"),
        _out(2, "\x1b[5i\x1b[V\x1b[4i"),
        _in(4, "NOME = PCMAT-FERBLON\nWINDOWS = 6.2.9200\nTERATERM = DK 2.01a\n\x04"),
        _out(6, "/etc/profile[137]: pedeID.log: 0403-005 Cannot create\r\n"),
        _out(11, "\x1b[H\x1b[2J"),
        _out(13, "Menu de opcoes do usuario ferblo\r\n"),
        _out(15, "\r\nDigite a sua opcao: "),
        # reconexão: segundo bloco TeraTerm + profile quebrado de novo
        _out(17, "\x1b[5i\x1b[V\x1b[4i"),
        _in(19, "NOME = PCMAT-FERBLON\nWINDOWS = 6.2.9200\nTERATERM = DK 2.01a\n\x04"),
        _out(21, "/etc/profile[137]: pedeID.log: 0403-005 Cannot create\r\n"),
        # interleaving da reconexão: prompt shell no meio do draw do wrapper
        _in(28, "0"),
        _out(29, "0"),
        _in(32, "\r"),
        _out(35, "Menu de opcoes do usuario ferblo\r\n"),
        _out(36, "(ferblo)MIG24:/dakota1/u/ferblo > "),
        _out(37, "  1 - (REDE LOJAS) Sistema das Lojas\r\n\r\n  0 - Fim\r\n"),
        _out(38, "\r\nDigite a sua opcao: "),
        _in(40, "0"),
        _out(41, "0"),
        _in(43, "\r"),
        _out(45, "\r\n(ferblo)MIG24:/dakota1/u/ferblo > "),
        _in(78, "k"),
        _out(79, "k"),
        _in(81, "\r"),
        _out(82, "\r\n"),
        _out(83, "\x1b[?7l\x1b[0m\x1b[H\x1b[2J\x1b[1;1H"),
        _out(86, "\x1b[?25l\x1b[0m\x1b[H\x1b[2J\x1b[7m DAKOTA S/A   MENU PRINCIPAL\x1b[0m"),
        _det(89, "3"),
        _ev(90, "session_end"),
    ]
    entry = detect_session_entry(events)
    assert entry is not None
    assert entry["start_seq"] == 83
    steps = entry["preamble"]
    assert steps[0]["send"] == "0\r"
    assert "TERATERM" not in steps[0]["send"]
    assert "NOME" not in steps[0]["send"]


def test_build_trail_start_seq_corta_preâmbulo_e_verifica(tmp_path):
    events, erp_seq = _capture62_like_events()
    src = tmp_path / "audit-000001.jsonl"
    with open(src, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    result = build_synthetic_trail(src, [], tmp_path / "out", hmac_key=HMAC_KEY, start_seq=erp_seq)
    out_events = [
        json.loads(l)
        for l in Path(result["out"]).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert result["dropped_entry"] > 0
    assert out_events[0]["type"] == "session_start"
    # primeiro evento de conteúdo é o draw do ERP; nada de shell/wrapper
    text = "".join(
        base64.b64decode(ev.get("data_b64") or "").decode("utf-8", "replace")
        for ev in out_events if ev.get("type") == "bytes"
    )
    assert "DAKOTA S/A" in text
    assert "Digite a sua opcao" not in text
    assert "MIG24:" not in text
    # renumeração sem gaps + cadeia íntegra
    assert [ev["seq_global"] for ev in out_events] == list(range(1, len(out_events) + 1))
    verify_log(str(tmp_path / "out"), HMAC_KEY)


def test_build_trail_start_seq_auto_detecta(tmp_path):
    events, erp_seq = _capture62_like_events()
    src = tmp_path / "audit-000001.jsonl"
    with open(src, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    result = build_synthetic_trail(src, [], tmp_path / "out", hmac_key=HMAC_KEY, start_seq="auto")
    assert result["entry"] is not None
    assert result["entry"]["start_seq"] == erp_seq
    assert result["dropped_entry"] > 0


def test_build_trail_start_seq_nao_quebra_substituicoes(tmp_path):
    """O corte do preâmbulo não pode deslocar os alvos das substituições
    (todas posteriores ao corte)."""
    events, erp_seq = _capture62_like_events()
    # campo digitado tecla a tecla DEPOIS do ERP: CPF
    seq = events[-1]["seq_global"] + 1
    events = events[:-1]  # remove session_end
    for c in "00109829069":
        events += [_det(seq, c), _in(seq + 1, c)]
        seq += 2
    events += [_det(seq, "\r"), _in(seq + 1, "\r"), _ev(seq + 2, "session_end")]
    src = tmp_path / "audit-000001.jsonl"
    with open(src, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    result = build_synthetic_trail(
        src, [("00109829069", "18503257408")], tmp_path / "out",
        hmac_key=HMAC_KEY, start_seq=erp_seq,
    )
    out_events = [
        json.loads(l)
        for l in Path(result["out"]).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    keys = [det_key(ev) for ev in out_events if ev["type"] == "deterministic_input"]
    digits = "".join(k for k in keys if len(k) == 1 and k.isdigit())
    assert "18503257408" in digits
    assert result["applied"] and not result["warnings"]


class _FakeSession:
    """Sessão mínima para testar o preamble: roteiro de saída + inputs."""

    def __init__(self, script: list[tuple[float, str]]):
        self.session_id = "sess-fake"
        self.script = sorted(script)
        self.sent: list[str] = []
        self.last_out_ms = 0
        self._start = time.monotonic()

    def write_in(self, data: bytes):
        self.sent.append(data.decode("utf-8"))

    def read_out(self) -> bytes:
        now = time.monotonic() - self._start
        due = [t for t, _ in self.script if t <= now]
        if not due:
            return b""
        out = "".join(text for t, text in self.script if t <= now)
        self.script = [(t, text) for t, text in self.script if t > now]
        self.last_out_ms = int(time.time() * 1000)
        return out.encode("utf-8")


class _FakeSelector:
    def select(self, timeout: float = 0.0):
        time.sleep(min(timeout, 0.05))
        return [(type("K", (), {"data": "sess-fake"})(), None)]


def test_preamble_executa_passos_em_ordem():
    sess = _FakeSession([
        (0.05, "Menu de opcoes\r\nDigite a sua opcao: "),
        (0.10, "(ferblo)MIG24:/dakota1/u/ferblo > "),
        (0.15, "\x1b[?7l\x1b[H\x1b[2J DAKOTA S/A  MENU PRINCIPAL"),
    ])
    steps = [
        {"wait_text": "Digite a sua opcao", "send": "0\r", "timeout_s": 5, "optional": True},
        {"wait_text": "MIG24:", "send": "k\r", "timeout_s": 5},
        {"wait_text": "DAKOTA S/A", "timeout_s": 5},
        {"wait_stable_ms": 50, "timeout_s": 2},
    ]
    warnings = _run_entry_preamble(sess, _FakeSelector(), steps)
    assert sess.sent == ["0\r", "k\r"]
    assert warnings == []


def test_preamble_wait_estourado_nao_envia_send():
    """Se a âncora não aparece, a tecla do passo NÃO é enviada (cairia no
    contexto errado) e o passo seguinte ainda tenta."""
    sess = _FakeSession([(0.05, "DAKOTA S/A  MENU PRINCIPAL")])
    steps = [
        {"wait_text": "Digite a sua opcao", "send": "0\r", "timeout_s": 0.2, "optional": True},
        {"wait_text": "DAKOTA S/A", "timeout_s": 2},
    ]
    warnings = _run_entry_preamble(sess, _FakeSelector(), steps)
    assert sess.sent == []
    assert len(warnings) == 1
    assert "Digite a sua opcao" in warnings[0]


def _source_tree(tmp_path: Path) -> Path:
    """Árvore mínima do layout Dakota: prg/<mod>/<fontes>.prg + <mod>.dbo e
    <mod>/config.<mod> no diretório de dados (irmão de prg/)."""
    prg = tmp_path / "prg"
    (prg / "est").mkdir(parents=True)
    (prg / "est" / "est361.prg").write_text("&& pedido e-commerce", encoding="utf-8")
    (prg / "est" / "est366.prg").write_text("&& pagamento", encoding="utf-8")
    (prg / "est" / "est.dbo").write_bytes(b"dbo")
    (tmp_path / "est").mkdir()
    (tmp_path / "est" / "config.est").write_text("cfg", encoding="utf-8")
    return prg


def test_derive_module_entry_layout_dados_separados(tmp_path):
    """config.<mod> no diretório de dados → 'cd <dados>; dbrt <prg>/<mod>/<mod>'."""
    prg = _source_tree(tmp_path)
    events = [
        _out(100, "\x1b[?7l\x1b[H\x1b[2J DAKOTA S/A  MENU PRINCIPAL"),
        _out(120, "| 3.6.1 PEDIDO E-COMMERCE"),
        _out(140, "| 3.6.6 PAGAMENTO"),
    ]
    step = derive_module_entry(events, 82, prg, anchor="DAKOTA S/A", shell_prompt="(ferblo)MIG24:/dakota1/u/ferblo >")
    assert step is not None
    assert step["send"] == f"cd {tmp_path}/est; dbrt {prg}/est/est\r"
    assert step["wait_text"] == "DAKOTA S/A"
    assert step["prompt"].startswith("(ferblo)MIG24:")
    assert "est" in step["label"]


def test_derive_module_entry_config_junto_aos_fontes(tmp_path):
    """config.<mod> junto aos fontes (layout do loj) → 'cd <prg>/<mod>; dbrt <mod>'."""
    prg = _source_tree(tmp_path)
    (tmp_path / "est" / "config.est").unlink()
    (prg / "est" / "config.est").write_text("cfg", encoding="utf-8")
    events = [_out(100, "| 3.6.1 PEDIDO E-COMMERCE")]
    step = derive_module_entry(events, 82, prg)
    assert step is not None
    assert step["send"] == f"cd {prg}/est; dbrt est\r"


def test_derive_module_entry_sem_config_retorna_none(tmp_path):
    """Sem config.<mod> em nenhum layout não há entrada alternativa confiável."""
    prg = _source_tree(tmp_path)
    (tmp_path / "est" / "config.est").unlink()
    events = [_out(100, "| 3.6.1 PEDIDO E-COMMERCE")]
    assert derive_module_entry(events, 82, prg) is None


def test_derive_module_entry_sem_codigo_menu_retorna_none(tmp_path):
    prg = _source_tree(tmp_path)
    events = [_out(100, "DAKOTA S/A  MENU PRINCIPAL sem codigo")]
    assert derive_module_entry(events, 82, prg) is None


def test_derive_module_entry_codigo_menu_dois_niveis(tmp_path):
    """Menu de 2 níveis ('3.3 NOTAS FISCAIS EMITIDA') localiza o fonte pelo
    candidato zero-padded ('33' → '330' → est330.prg) — mesmo critério do
    capture_knowledge_integrator; sem isso o fallback da captura 73 vinha null."""
    prg = _source_tree(tmp_path)
    (prg / "est" / "est330.prg").write_text("&& notas fiscais emitidas", encoding="utf-8")
    events = [_out(100, "| 3.3 NOTAS FISCAIS EMITIDA")]
    step = derive_module_entry(events, 82, prg)
    assert step is not None
    assert step["send"] == f"cd {tmp_path}/est; dbrt {prg}/est/est\r"
    assert "est" in step["label"]


def test_detect_entry_com_source_dir_inclui_fallback(tmp_path):
    """Captura 62 + árvore de fontes → entry carrega o fallback do módulo."""
    prg = _source_tree(tmp_path)
    events, erp_seq = _capture62_like_events()
    # tela de negócio com código de menu após a entrada no sistema
    last = events[-1]["seq_global"]
    events.insert(-1, _out(last, "| 3.6.1 PEDIDO E-COMMERCE"))
    entry = detect_session_entry(events, source_dir=prg)
    assert entry is not None
    fb = entry.get("fallback")
    assert fb is not None
    assert fb["send"] == f"cd {tmp_path}/est; dbrt {prg}/est/est\r"
    assert fb["wait_text"] == "DAKOTA S/A"


class _ReactiveSession:
    """Sessão fake reativa: cada tecla enviada produz a saída correspondente
    (wrapper → shell → FATAL ERROR → ENTER → shell → dbrt do módulo → ERP)."""

    def __init__(self):
        self.session_id = "sess-fake"
        self.sent: list[str] = []
        self.last_out_ms = 0
        self._queue: list[str] = ["Menu de opcoes\r\nDigite a sua opcao: "]

    def write_in(self, data: bytes):
        text = data.decode("utf-8")
        self.sent.append(text)
        if text == "0\r":
            self._queue.append("\r\n(ferblo)MIG24:/dakota1/u/ferblo > ")
        elif text == "k\r":
            self._queue.append("\r\nFATAL ERROR Cannot open file ferblo.dbo  Confirm")
        elif text == "\r":
            self._queue.append("\r\n(ferblo)MIG24:/dakota1/u/ferblo > ")
        elif text.startswith("cd "):
            self._queue.append("\r\n\x1b[?7l\x1b[H\x1b[2J DAKOTA S/A  MENU PRINCIPAL")
        self.last_out_ms = int(time.time() * 1000)

    def read_out(self) -> bytes:
        if not self._queue:
            return b""
        out = "".join(self._queue)
        self._queue.clear()
        self.last_out_ms = int(time.time() * 1000)
        return out.encode("utf-8")


def test_preamble_fallback_abre_sistema_quando_caminho_gravado_falha():
    """Caminho gravado ('k') cai no FATAL ERROR; o fallback confirma o diálogo
    com ENTER e entra pelo módulo (cd dados; dbrt prg/mod/mod)."""
    sess = _ReactiveSession()
    steps = [
        {"wait_text": "Digite a sua opcao", "send": "0\r", "timeout_s": 2, "optional": True},
        {"wait_text": "MIG24:", "send": "k\r", "timeout_s": 2},
        {"wait_text": "DAKOTA S/A", "timeout_s": 0.5},
    ]
    fallback = {
        "send": "cd /dakota11/est; dbrt /dakota11/prg/est/est\r",
        "wait_text": "DAKOTA S/A",
        "prompt": "(ferblo)MIG24:",
        "timeout_s": 2,
        "label": "entrada do módulo est",
    }
    warnings = _run_entry_preamble(sess, _FakeSelector(), steps, fallback)
    assert sess.sent == ["0\r", "k\r", "\r", "cd /dakota11/est; dbrt /dakota11/prg/est/est\r"]
    assert any("sistema aberto via entrada do módulo est" in w for w in warnings)


def test_preamble_fallback_nao_dispara_quando_ancora_ok():
    """Âncora final presente → o fallback nunca é tentado."""
    sess = _FakeSession([
        (0.05, "Digite a sua opcao: "),
        (0.10, "(ferblo)MIG24:/dakota1/u/ferblo > "),
        (0.15, "DAKOTA S/A  MENU PRINCIPAL"),
    ])
    steps = [
        {"wait_text": "Digite a sua opcao", "send": "0\r", "timeout_s": 2, "optional": True},
        {"wait_text": "MIG24:", "send": "k\r", "timeout_s": 2},
        {"wait_text": "DAKOTA S/A", "timeout_s": 2},
    ]
    fallback = {"send": "cd /x; dbrt y\r", "wait_text": "DAKOTA S/A", "timeout_s": 1}
    warnings = _run_entry_preamble(sess, _FakeSelector(), steps, fallback)
    assert sess.sent == ["0\r", "k\r"]
    assert warnings == []


def test_preamble_sem_fallback_mantem_comportamento():
    """Sem fallback, âncora estourada vira só warning (comportamento 0.8.68)."""
    sess = _FakeSession([(0.05, "(ferblo)MIG24:/dakota1/u/ferblo > ")])
    steps = [
        {"wait_text": "MIG24:", "send": "k\r", "timeout_s": 2},
        {"wait_text": "DAKOTA S/A", "timeout_s": 0.3},
    ]
    warnings = _run_entry_preamble(sess, _FakeSelector(), steps)
    assert sess.sent == ["k\r"]
    assert len(warnings) == 1
    assert "DAKOTA S/A" in warnings[0]
