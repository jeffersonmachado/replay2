#!/usr/bin/env tclsh
# Benchmarking suite for screen capture performance
# Usage: tclsh benchmark.tcl

package require Tcl 8.5

source lib/capture.tcl
source lib/normalize.tcl
source lib/signature.tcl

proc generate_test_screen {width height lines_of_text} {
    # Generate a test screen with specified dimensions
    set screen ""
    set header "[string repeat "-" $width]"
    append screen "$header\n"
    
    for {set i 0} {$i < $lines_of_text} {incr i} {
        set line_num [format "%05d" $i]
        set padding [string repeat " " [expr {$width - 20}]]
        append screen "Line$line_num: Test content$padding\n"
    }
    
    append screen "[string repeat "-" $width]\n"
    return $screen
}

proc benchmark_capture {} {
    # Benchmark screen capture parsing
    puts "\n=== Screen Capture Benchmarks ==="
    
    set results {}
    
    set configs {
        {80 24 20}
        {120 40 30}
        {160 60 50}
    }
    
    foreach config $configs {
        lassign $config width height lines
        set screen [generate_test_screen $width $height $lines]
        set lines_count [llength [split $screen "\n"]]
        
        # Benchmark apply_screen_boundaries (1000x)
        set start [clock milliseconds]
        for {set i 0} {$i < 1000} {incr i} {
            ::capture::apply_screen_boundaries $screen
        }
        set duration [expr {[clock milliseconds] - $start}]
        set avg_us [expr {($duration * 1000.0) / 1000}]
        
        lappend results [list $width $height $lines_count $duration $avg_us]
        
        puts "Screen ${width}x${height} ($lines_count lines):"
        puts "  apply_screen_boundaries: ${duration}ms (${avg_us}us/call)"
    }
    
    return $results
}

proc benchmark_normalize {} {
    # Benchmark ANSI normalization
    puts "\n=== ANSI Normalization Benchmarks ==="
    
    set screen1 "Simple text"
    set screen2 "Simple \033\[31mtext\033\[0m with color"
    set screen3 "Complex \033\[1;32;40mFormatted Text\033\[0m with format"
    
    foreach {name screen} [list \
        "Simple" $screen1 \
        "Colored" $screen2 \
        "Complex" $screen3 \
    ] {
        set len [string length $screen]
        
        set start [clock milliseconds]
        for {set i 0} {$i < 500} {incr i} {
            catch {::normalize::screen $screen}
        }
        set duration [expr {[clock milliseconds] - $start}]
        set avg_us [expr {($duration * 1000.0) / 500}]
        
        puts "$name screen ($len bytes): ${duration}ms (${avg_us}us/call)"
    }
}

proc benchmark_signature {} {
    # Benchmark screen signature generation
    puts "\n=== Screen Signature Benchmarks ==="
    
    set screen1 "Screen 1\nLine 1\nLine 2\n"
    set screen2 [string repeat "A" 1000]
    set screen3 "Special chars: \033\[1m\033\[31mColored\033\[0m Text"
    
    foreach {name screen} {
        "Small" $screen1
        "Large" $screen2
        "ANSI" $screen3
    } {
        set start [clock milliseconds]
        for {set i 0} {$i < 100} {incr i} {
            catch {::signature::from_screen $screen}
        }
        set duration [expr {[clock milliseconds] - $start}]
        set avg_ms [expr {$duration / 100.0}]
        
        puts "$name screen ([string length $screen] bytes):"
        puts "  Signature generation: ${avg_ms}ms/call"
    }
}

proc generate_report {results} {
    # Generate performance report
    puts "\n=== Performance Report ===" 
    puts "Date: [clock format [clock seconds]]"
    puts "Tcl Version: [info patchlevel]"
    
    # Summary
    set slowest 0
    set fastest 999999
    foreach result $results {
        lassign $result w h l d us
        if {$d > $slowest} {set slowest $d}
        if {$d < $fastest} {set fastest $d}
    }
    
    puts "\nCapture performance:"
    puts "  Slowest: ${slowest}ms"
    puts "  Fastest: ${fastest}ms"
    puts "  Average: [format "%.1f" [expr {($slowest + $fastest) / 2.0}]]ms"
    
    # Recommendations
    puts "\n=== Recommendations ==="
    if {$slowest > 500} {
        puts "Warning: Slow capture detected. Consider optimizing."
    } else {
        puts "✅ Performance is acceptable."
    }
}

# Main
if {[catch {
    set results [benchmark_capture]
    benchmark_normalize
    benchmark_signature
    generate_report $results
    puts "\n✅ Benchmarking complete!"
} err]} {
    puts "❌ Error: $err"
    puts $::errorInfo
    exit 1
}
