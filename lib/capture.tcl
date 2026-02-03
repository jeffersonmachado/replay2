########################################################################
## capture.tcl
## Namespace responsável por capturar a "tela" atual do sistema legado.
##
## Ideia central:
## - Manter um buffer por sessão (spawn_id) representando a "tela atual"
## - Ler saída incremental com Expect de forma não-bloqueante
## - Detectar limites de tela (ex.: clear-screen) para descartar conteúdo antigo
## - Fornecer uma API determinística: ::capture::snapshot
########################################################################

namespace eval ::capture {
    namespace export snapshot

    # Tamanho máximo de bytes a ler em uma captura simples.
    variable maxBytes 65535

    # Buffer por spawn_id (tela atual bruta)
    variable buffers
    array set buffers {}
}

proc ::capture::apply_screen_boundaries {text} {
    # Mantém apenas o trecho após a última sequência típica de "nova tela".
    # Foca em clear-screen + home:
    #   ESC[2J ESC[H  e também a ordem inversa.
    set lastEnd -1

    if {[regexp -indices -all {\x1B\[[0-9;?]*2J\x1B\[[0-9;?]*H} $text matches]} {
        foreach {startIdx endIdx} $matches {
            set lastEnd $endIdx
        }
    }
    if {[regexp -indices -all {\x1B\[[0-9;?]*H\x1B\[[0-9;?]*2J} $text matches2]} {
        foreach {startIdx endIdx} $matches2 {
            if {$endIdx > $lastEnd} { set lastEnd $endIdx }
        }
    }

    if {$lastEnd >= 0} {
        return [string range $text [expr {$lastEnd + 1}] end]
    }
    return $text
}

proc ::capture::snapshot {spawn_id args} {
    # Lê saída incremental até silêncio (quiet_ms) ou timeout total (timeout).
    # Atualiza um buffer por spawn_id e retorna a tela bruta atual completa.
    #
    # Parâmetros opcionais:
    #   -timeout <segundos>   (default: 1.0)  pode ser fracionário
    #   -quiet_ms <ms>        (default: 200)
    #
    # Retorna:
    #   String com o buffer bruto da tela atual.

    array set opts {
        -timeout 1.0
        -quiet_ms 200
    }
    array set opts $args

    variable maxBytes
    variable buffers

    if {![info exists buffers($spawn_id)]} {
        set buffers($spawn_id) ""
    }

    set timeout_ms [expr {int(double($opts(-timeout)) * 1000.0)}]
    if {$timeout_ms < 0} { set timeout_ms 0 }
    set quiet_ms [expr {int($opts(-quiet_ms))}]
    if {$quiet_ms < 0} { set quiet_ms 0 }

    set start_ms [clock milliseconds]
    set last_read_ms $start_ms
    set incoming ""
    set seen_output 0

    while {1} {
        set chunk ""
        set matched 0

        set rc [catch {
            expect -i $spawn_id -timeout 0 {
                -re {.+} {
                    set chunk $expect_out(0,string)
                    set matched 1
                }
                timeout {
                    # nada disponível agora
                }
                eof {
                    # sessão terminou
                }
            }
        } err]

        if {$rc != 0} {
            puts stderr "capture::snapshot erro em expect: $err"
            break
        }

        if {$matched} {
            append incoming $chunk
            set last_read_ms [clock milliseconds]
            set seen_output 1
            if {[string length $incoming] >= $maxBytes} {
                break
            }
            continue
        }

        set now_ms [clock milliseconds]
        if {$seen_output && $quiet_ms > 0 && ($now_ms - $last_read_ms) >= $quiet_ms} {
            break
        }
        if {$timeout_ms > 0 && ($now_ms - $start_ms) >= $timeout_ms} {
            break
        }

        after 10
    }

    set buf $buffers($spawn_id)
    append buf $incoming
    set buf [::capture::apply_screen_boundaries $buf]

    if {[string length $buf] > $maxBytes} {
        set buf [string range $buf end-[expr {$maxBytes - 1}] end]
    }

    set buffers($spawn_id) $buf
    return $buf
}


