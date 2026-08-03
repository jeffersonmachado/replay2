package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib events.tcl]
source [file join $rootDir lib plugins.tcl]

proc plugins_test_dir {} {
    set d [file join [::tcltest::configure -tmpdir] plugins_screens]
    file mkdir $d
    return $d
}

test plugins_discover_01 "diretório inexistente retorna lista vazia" -body {
    ::plugins::discover /caminho/que/nao/existe
} -result {}

test plugins_discover_02 "retorna apenas .tcl, ordenado" -body {
    set d [plugins_test_dir]
    foreach f {b.tcl a.tcl notas.txt} {
        set fh [open [file join $d $f] w]
        puts $fh "# vazio"
        close $fh
    }
    set got [::plugins::discover $d]
    file delete -force $d
    list [llength $got] [expr {[lindex $got 0] eq [file join $d a.tcl]}]
} -result [list 2 1]

test plugins_read_state_01 "arquivo ausente ou vazio retorna dict vazio" -body {
    set d [plugins_test_dir]
    set f1 [file join $d inexistente.txt]
    set f2 [file join $d vazio.txt]
    set fh [open $f2 w]
    close $fh
    set r [list [dict size [::plugins::read_state $f1]] [dict size [::plugins::read_state $f2]] [dict size [::plugins::read_state ""]]]
    file delete -force $d
    set r
} -result [list 0 0 0]

test plugins_read_state_02 "conteúdo não-dict é erro" -body {
    set d [plugins_test_dir]
    set f [file join $d ruim.txt]
    set fh [open $f w]
    puts $fh "conteúdo que não é dict \{abc"
    close $fh
    set r [catch {::plugins::read_state $f} msg]
    file delete -force $d
    list $r [string match "plugins::read_state: arquivo inválido*" $msg]
} -result [list 1 1]

test plugins_state_roundtrip_01 "write_state + read_state preservam o dict" -body {
    set d [plugins_test_dir]
    set f [file join $d sub dir estado.txt]
    set st [dict create screen_a.tcl 1 screen_b.tcl 0]
    ::plugins::write_state $f $st
    set got [::plugins::read_state $f]
    file delete -force $d
    list [dict get $got screen_a.tcl] [dict get $got screen_b.tcl]
} -result [list 1 0]

test plugins_write_state_01 "plugins_file vazio é erro" -body {
    catch {::plugins::write_state "" [dict create]} msg
    set msg
} -result {plugins::write_state: plugins_file vazio}

test plugins_is_enabled_01 "ausente no estado = enabled; decide pelo basename" -body {
    set st [dict create screen_a.tcl 0]
    list \
        [::plugins::is_enabled $st /qualquer/caminho/screen_a.tcl] \
        [::plugins::is_enabled $st /qualquer/caminho/screen_b.tcl]
} -result [list 0 1]

test plugins_set_enabled_01 "alterna flag e persiste entre leituras" -body {
    set d [plugins_test_dir]
    set f [file join $d estado.txt]
    ::plugins::set_enabled $f screen_a.tcl 0
    set off [::plugins::is_enabled [::plugins::read_state $f] screen_a.tcl]
    ::plugins::set_enabled $f screen_a.tcl 1
    set on [::plugins::is_enabled [::plugins::read_state $f] screen_a.tcl]
    file delete -force $d
    list $off $on
} -result [list 0 1]

test plugins_load_screens_01 "carrega habilitados, pula desabilitados e sobrevive a plugin quebrado" -body {
    set d [plugins_test_dir]
    set good [file join $d good_plugin.tcl]
    set bad [file join $d zzz_broken.tcl]
    set fh [open $good w]
    puts $fh {set ::plugins_test_good_loaded 1}
    close $fh
    set fh [open $bad w]
    puts $fh {error "plugin quebrado proposital"}
    close $fh
    set estado [file join $d estado.txt]
    ::plugins::write_state $estado [dict create ausente.tcl 0]
    # marca good como disabled via estado e confirma que não carrega
    ::plugins::set_enabled $estado good_plugin.tcl 0
    set loaded1 [::plugins::load_screens $d $estado]
    set good_loaded_when_disabled [info exists ::plugins_test_good_loaded]
    # reabilita e carrega de verdade
    ::plugins::set_enabled $estado good_plugin.tcl 1
    set loaded2 [::plugins::load_screens $d $estado]
    set good_loaded_when_enabled [info exists ::plugins_test_good_loaded]
    unset -nocomplain ::plugins_test_good_loaded
    file delete -force $d
    list \
        [llength $loaded1] \
        $good_loaded_when_disabled \
        [expr {[lsearch $loaded2 $good] >= 0}] \
        $good_loaded_when_enabled \
        [expr {[lsearch $loaded2 $bad] < 0}]
} -result [list 0 0 1 1 1]
