# Threat model (resumo)

## Ameaças cobertas

- **Alteração** de eventos gravados: detectada pela hash-chain (`prev_hash`/`hash`).
- **Remoção**/truncamento: detectado por gaps em `seq_global` e quebra de `prev_hash`.
- **Reordenação**: detectada pela hash-chain e por `seq_global`.
- **Forja** de eventos por atacante sem a chave: mitigada por **HMAC**.

## Ameaças não cobertas (por design)

- Comprometimento do host gateway **com acesso à chave HMAC**: atacante poderia forjar eventos válidos.\n
  Mitigação operacional: cofre de segredos, rotação de chaves, hardening do host, WORM externo.
- Replay “perfeito” de concorrência global pode divergir por timing do sistema remoto.\n
  Mitigação: checkpoints mais frequentes, replay por sessão quando aceitável, e validações de negócio.

## Recomendação

- Rodar o gateway como bastion dedicado.
- Armazenar logs em destino imutável (WORM/S3 Object Lock) ou pipeline de logs com retenção.

