# `gateway/internal/audit`

Pacote Go experimental para writer/auditoria append-only.

Estado atual:

- nao faz parte do runtime principal Python usado pelo gateway/control plane;
- nao e chamado por `gateway/dakota_gateway/gateway.py` nem pelo runner atual;
- serve como base de experimentacao para futuras evolucoes de auditoria de baixo nivel.

Fronteira arquitetural:

- runtime oficial atual: `Expect/Tcl` + gateway/control plane em Python + SQLite;
- componente experimental: este pacote Go.

Se houver evolucao futura, a integracao deve acontecer por fronteira explicita e testada.
Enquanto isso, trate este diretorio como laboratorio isolado, nao como dependencia operacional obrigatoria.
