# Handlers de telas (`screens/`)

Coloque aqui seus módulos `.tcl` que registram handlers na máquina de estados via:

- `::state_machine::register <assinatura> <estado> <proc_handler>`

O entrypoint [`bin/main.exp`](../bin/main.exp) carrega automaticamente `screens/*.tcl`.

O demo executável (simulador + telas de exemplo) fica em `examples/`.

