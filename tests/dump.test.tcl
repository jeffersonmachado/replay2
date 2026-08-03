package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib events.tcl]
source [file join $rootDir lib dump.tcl]

# Isola o estado do ::dump entre testes
proc dump_test_reset {} {
    dict set ::dump::cfg dir ""
    dict set ::dump::cfg on_unknown 1
    dict set ::dump::cfg enabled 0
    set ::dump::seq 0
}

test dump_configure_01 "número ímpar de argumentos é erro" -body {
    catch {::dump::configure -dir} msg
    set msg
} -result {dump::configure espera pares chave/valor}

test dump_configure_02 "opção desconhecida é erro" -body {
    catch {::dump::configure -bogus 1} msg
    set msg
} -result {dump::configure: opção desconhecida: -bogus}

test dump_configure_03 "-dir ativa enabled automaticamente; vazio desativa" -body {
    dump_test_reset
    ::dump::configure -dir /tmp/dump_test_x
    set on1 [::dump::enabled]
    ::dump::configure -dir ""
    set on2 [::dump::enabled]
    dump_test_reset
    list $on1 $on2
} -result [list 1 0]

test dump_enabled_01 "enabled exige flag e dir não vazio" -body {
    dump_test_reset
    set a [::dump::enabled]
    ::dump::configure -enabled 1
    set b [::dump::enabled]
    ::dump::configure -dir /tmp/dump_test_x
    set c [::dump::enabled]
    dump_test_reset
    list $a $b $c
} -result [list 0 0 1]

test dump_safe_filename_01 "caracteres problemáticos viram underscore" -body {
    ::dump::_safe_filename {sha256:ab/cd e*f}
} -result {sha256_ab_cd_e_f}

test dump_safe_filename_02 "trunca em 80 caracteres" -body {
    string length [::dump::_safe_filename [string repeat a 120]]
} -result 80

test dump_safe_filename_03 "string que zera após sanitização vira 'dump'" -body {
    # espaço sanitiza para _, então testa string já vazia de fato
    ::dump::_safe_filename {}
} -result {dump}

test dump_dump_unknown_01 "desabilitado retorna vazio e não grava nada" -body {
    dump_test_reset
    set r [::dump::dump_unknown [dict create ts_ms 1]]
    dump_test_reset
    set r
} -result {}

test dump_dump_unknown_02 "grava pasta com meta e telas; retorna o caminho" -body {
    dump_test_reset
    set base [file join [::tcltest::configure -tmpdir] dump_out]
    file delete -force $base
    ::dump::configure -dir $base
    set folder [::dump::dump_unknown [dict create \
        type unknown_screen \
        ts_ms 123456 \
        pid 999 \
        signature "sig:abc/def" \
        raw_screen "TELA RAW" \
        norm_screen "tela norm" \
    ]]
    set exists_meta [file exists [file join $folder meta.tcldict.txt]]
    set exists_raw [file exists [file join $folder raw_screen.txt]]
    set exists_norm [file exists [file join $folder norm_screen.txt]]
    set meta ""
    if {$exists_meta} {
        set f [open [file join $folder meta.tcldict.txt] r]
        set meta [read $f]
        close $f
    }
    set raw ""
    if {$exists_raw} {
        set f [open [file join $folder raw_screen.txt] r]
        set raw [read $f]
        close $f
    }
    dump_test_reset
    list \
        [expr {$folder ne ""}] \
        [string match "*unknown_123456_*" $folder] \
        $exists_meta $exists_raw $exists_norm \
        [dict get $meta signature] \
        [dict get $meta raw_len] \
        [dict get $meta norm_len] \
        $raw
} -result [list 1 1 1 1 1 "sig:abc/def" 8 9 "TELA RAW"]

test dump_dump_unknown_03 "on_unknown=0 não grava" -body {
    dump_test_reset
    set base [file join [::tcltest::configure -tmpdir] dump_off]
    file delete -force $base
    ::dump::configure -dir $base -on_unknown 0
    set r [::dump::dump_unknown [dict create ts_ms 1 raw_screen x]]
    set made [file isdirectory $base]
    dump_test_reset
    list $r $made
} -result [list {} 0]

test dump_dump_unknown_04 "sem signature usa 'unknown' no nome da pasta" -body {
    dump_test_reset
    set base [file join [::tcltest::configure -tmpdir] dump_nosig]
    file delete -force $base
    ::dump::configure -dir $base
    set folder [::dump::dump_unknown [dict create ts_ms 7]]
    set tail [file tail $folder]
    dump_test_reset
    string match "unknown_7_*_unknown" $tail
} -result 1

test dump_event_sink_01 "só reage a unknown_screen" -body {
    dump_test_reset
    set base [file join [::tcltest::configure -tmpdir] dump_sink]
    file delete -force $base
    ::dump::configure -dir $base
    ::dump::event_sink [dict create type outro_tipo ts_ms 1]
    set n1 [llength [glob -nocomplain -directory $base *]]
    ::dump::event_sink [dict create type unknown_screen ts_ms 2 raw_screen z]
    set n2 [llength [glob -nocomplain -directory $base *]]
    dump_test_reset
    list $n1 $n2
} -result [list 0 1]
