"""Testes — ownership/setgid do diretorio de captura (_fix_capture_dir_owner).

O diretorio de captura precisa de setgid para que arquivos criados por sessoes
privilegiadas (ex.: root) herdem o grupo do diretorio (results:cpd no AIX) em
vez do grupo primario do processo — caso contrario o audit.lock fica inacessivel
para as demais sessoes da captura.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

GATEWAY_DIR = str(Path(__file__).resolve().parents[1])
if GATEWAY_DIR not in sys.path:
    sys.path.insert(0, GATEWAY_DIR)


def test_fix_capture_dir_owner_aix_aplica_setgid(tmp_path):
    """No AIX, alem do chown para results, o dir deve receber chmod 02770."""
    from control.services.gateway_state_service import _fix_capture_dir_owner

    fake_pw = MagicMock(pw_uid=1234, pw_gid=5678)
    log_dir = str(tmp_path)

    with patch("control.services.gateway_state_service.platform.system", return_value="AIX"), \
         patch("pwd.getpwnam", return_value=fake_pw), \
         patch("os.chown") as mock_chown, \
         patch("os.chmod") as mock_chmod:
        _fix_capture_dir_owner(log_dir)

    mock_chown.assert_called_once_with(log_dir, 1234, 5678)
    mock_chmod.assert_called_once_with(log_dir, 0o2770)


def test_fix_capture_dir_owner_fora_do_aix_e_noop(tmp_path):
    """Fora do AIX a funcao nao deve tocar no diretorio."""
    from control.services.gateway_state_service import _fix_capture_dir_owner

    with patch("control.services.gateway_state_service.platform.system", return_value="Linux"), \
         patch("os.chown") as mock_chown, \
         patch("os.chmod") as mock_chmod:
        _fix_capture_dir_owner(str(tmp_path))

    mock_chown.assert_not_called()
    mock_chmod.assert_not_called()


def test_fix_capture_dir_owner_sem_usuario_results_e_noop(tmp_path):
    """Sem o usuario 'results' no sistema, nao deve falhar nem alterar nada."""
    from control.services.gateway_state_service import _fix_capture_dir_owner

    with patch("control.services.gateway_state_service.platform.system", return_value="AIX"), \
         patch("pwd.getpwnam", side_effect=KeyError("results")), \
         patch("os.chown") as mock_chown, \
         patch("os.chmod") as mock_chmod:
        _fix_capture_dir_owner(str(tmp_path))

    mock_chown.assert_not_called()
    mock_chmod.assert_not_called()
