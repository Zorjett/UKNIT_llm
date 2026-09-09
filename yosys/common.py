# Copyright 2020-2023 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#  Parts of this file adapted from https://github.com/YosysHQ/yosys/blob/master/techlibs/common/synth.cc
#
#  Copyright (C) 2012  Claire Xenia Wolf <claire@yosyshq.com>
#
#  Permission to use, copy, modify, and/or distribute this software for any
#  purpose with or without fee is hereby granted, provided that the above
#  copyright notice and this permission notice appear in all copies.
#
#  THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
#  WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
#  MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
#  ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
#  WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
#  ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
#  OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.


from pyosys import libyosys as ys

def ol_proc(report_dir, design):
    # processing stage
    # converting behavioral RTL into structural elements

    # remove empty parts of processes
    ys.run_pass("proc_clean", design)
    # eliminate dead trees in decision trees
    ys.run_pass("proc_rmdead", design)
    # remove redundant assignments
    ys.run_pass("proc_prune", design)


    # convert initial block to init attributes
    ys.run_pass("proc_init", design)
    # detect asynchronous resets and converts them to a different internal representation
    ys.run_pass("proc_arst", design)
    # convert switches to ROMs
    ys.run_pass("proc_rom", design)
    # convert decision trees to multiplexers
    ys.run_pass("proc_mux", design)
    # extract latches from processes and convert them to d-type latches
    ys.run_pass(f"tee -o {report_dir}/latch.rpt proc_dlatch", design)
    # extract flip-flops from processes and convert them to d-type flip flops
    ys.run_pass("proc_dff", design)
    # extract memory writes from processes and convert them to $memwr cells
    ys.run_pass("proc_memwr", design)

    
    # remove empty parts of processes
    ys.run_pass("proc_clean", design)
    # check 
    ys.run_pass(f"tee -o {report_dir}/pre_synth_chk.rpt check", design)
    # simplify expressions
    ys.run_pass("opt_expr", design)

def ol_synth(design_name, report_dir, design):
    # main flow
    # set top module
    ys.run_pass(f"hierarchy -check -top {design_name} -nokeep_prints -nokeep_asserts", design)

    # converting to structural elements
    ol_proc(report_dir, design)
    
    # flatten the design
    ys.run_pass("flatten",design)

    # simplify expressions
    ys.run_pass("opt_expr", design)

    # remove empty parts of processes
    ys.run_pass("opt_clean", design)

    # optimization
    ys.run_pass("opt -nodffe -nosdff", design)

    # optimize fsm
    ys.run_pass("fsm", design)

    # optimization
    ys.run_pass("opt", design)

    #  reduce the word size
    ys.run_pass("wreduce", design)

    # peephole optimization
    ys.run_pass("peepopt", design)

    # remove empty parts of processes
    ys.run_pass("opt_clean", design)

    # optimizes arithmetic units
    ys.run_pass("alumacc", design)

    # resource sharing optimization
    ys.run_pass("share", design)

    # optimization
    ys.run_pass("opt", design)

    # maps memories to basic cells
    ys.run_pass("memory -nomap", design)

    # remove empty parts of processes
    ys.run_pass("opt_clean", design)

    # optimization
    ys.run_pass("opt -fast -full", design)

    # maps memories to basic cells
    ys.run_pass("memory_map", design)

    # optimization
    ys.run_pass("opt -full", design)

    # technology mapping
    ys.run_pass("techmap", design)

    # optimization
    ys.run_pass("opt -fast", design)
    ys.run_pass("abc -fast", design)
    ys.run_pass("opt -fast", design)

    ys.run_pass("hierarchy -check -nokeep_prints -nokeep_asserts", design)
    ys.run_pass("stat", design)
    ys.run_pass("check", design)

def read_verilog_files(config,design):
    verilog_include_args = ''
    if "VERILOG_INCLUDE_DIRS" in config.keys() and config["VERILOG_INCLUDE_DIRS"] != None:
        for dir in config["VERILOG_INCLUDE_DIRS"]:
            verilog_include_args += "-I%s " % (dir) 
    
    for file in config['VERILOG_FILES']:
        ys.run_pass(f'read_verilog -noautowire -sv {verilog_include_args} {file}', design)

