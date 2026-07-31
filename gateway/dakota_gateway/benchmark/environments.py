"""Modelos de ambiente do benchmark real (contrato §7).

Descreve a capacidade computacional de cada ambiente comparado — AIX (LPAR
com entitled capacity, SMT, shared/dedicated, capped) e Linux
(sockets/cores/threads, NUMA, cgroup) — além do acesso (host/porta e
referência de credencial). Segredos NUNCA aparecem em texto claro:
``user_secret_ref`` é apenas uma referência (``env:VAR``, ``file:<path>`` ou
``ssh-key:user@host``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CpuModel:
    """Modelo de CPU/capacidade do ambiente (AIX e Linux)."""

    model: str = ""
    virtual_processors: int = 0
    physical_processors: int = 0
    # AIX (LPAR)
    entitled_capacity: float = 0.0
    smt_mode: int = 0
    shared_or_dedicated: str = ""
    capped_or_uncapped: str = ""
    pool: str = ""
    # Linux
    sockets: int = 0
    cores_per_socket: int = 0
    threads_per_core: int = 0
    numa_nodes: int = 0
    frequency_mhz: float = 0.0
    vm_or_container: str = ""
    vcpu_limit: int = 0
    cgroup_limit: str = ""

    def to_dict(self) -> dict:
        """Serializa todos os campos do modelo de CPU."""
        return {
            "model": self.model,
            "virtual_processors": self.virtual_processors,
            "physical_processors": self.physical_processors,
            "entitled_capacity": self.entitled_capacity,
            "smt_mode": self.smt_mode,
            "shared_or_dedicated": self.shared_or_dedicated,
            "capped_or_uncapped": self.capped_or_uncapped,
            "pool": self.pool,
            "sockets": self.sockets,
            "cores_per_socket": self.cores_per_socket,
            "threads_per_core": self.threads_per_core,
            "numa_nodes": self.numa_nodes,
            "frequency_mhz": self.frequency_mhz,
            "vm_or_container": self.vm_or_container,
            "vcpu_limit": self.vcpu_limit,
            "cgroup_limit": self.cgroup_limit,
        }

    @staticmethod
    def from_dict(d: dict) -> "CpuModel":
        """Constrói de dict, tolerando campos ausentes e ignorando extras."""
        conhecidos = set(CpuModel().to_dict())
        filtrado = {k: v for k, v in dict(d or {}).items() if k in conhecidos}
        return CpuModel(**filtrado)


@dataclass(frozen=True)
class EnvironmentModel:
    """Ambiente de execução do benchmark (AIX ou Linux) — §7."""

    environment_id: str
    platform: str  # "AIX" | "Linux"
    architecture: str  # "POWER" | "x86_64"
    host: str
    port: int = 22
    user_secret_ref: str = ""  # "env:VAR" | "file:<path>" | "ssh-key:user@host"
    application_endpoint: str = ""
    gateway_endpoint: str = ""
    cpu: CpuModel = field(default_factory=CpuModel)
    memory_mb: int = 0
    storage: dict = field(default_factory=dict)
    virtualization: dict = field(default_factory=dict)
    replay2_db_path: str = ""  # replay.db remoto (coleta de host_metrics)
    # Canal dedicado da coleta de host_metrics (§13): quando o login do
    # endpoint está sob ForceCommand de captura (sem acesso à replay.db), a
    # coleta usa outro usuário SSH e/ou outro comando remoto — sem afetar o
    # replay da jornada. Vazio = mesmo usuário do endpoint e "python3 -".
    metrics_ssh_user: str = ""
    metrics_remote_cmd: str = ""

    def to_dict(self) -> dict:
        """Serializa o ambiente (sem nunca conter segredo em texto claro)."""
        return {
            "environment_id": self.environment_id,
            "platform": self.platform,
            "architecture": self.architecture,
            "host": self.host,
            "port": self.port,
            "user_secret_ref": self.user_secret_ref,
            "application_endpoint": self.application_endpoint,
            "gateway_endpoint": self.gateway_endpoint,
            "cpu": self.cpu.to_dict(),
            "memory_mb": self.memory_mb,
            "storage": dict(self.storage),
            "virtualization": dict(self.virtualization),
            "replay2_db_path": self.replay2_db_path,
            "metrics_ssh_user": self.metrics_ssh_user,
            "metrics_remote_cmd": self.metrics_remote_cmd,
        }

    @staticmethod
    def from_dict(d: dict) -> "EnvironmentModel":
        """Constrói de dict (ex.: JSON de ambiente), tolerando extras."""
        dados = dict(d or {})
        cpu = CpuModel.from_dict(dados.pop("cpu", {}) or {})
        conhecidos = set(EnvironmentModel(
            environment_id="", platform="", architecture="", host="",
        ).to_dict()) - {"cpu"}
        filtrado = {k: v for k, v in dados.items() if k in conhecidos}
        return EnvironmentModel(cpu=cpu, **filtrado)
