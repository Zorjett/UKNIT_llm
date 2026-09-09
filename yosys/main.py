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

import os
import yosys.common as common
import json5
from yosys.construct_abc_script import ABCScriptCreator_v2_3_1
from yosys.construct_abc_script import ABCScriptCreator_v2_1_1
from functools import wraps
from contextlib import contextmanager
import sys
from pyosys import libyosys as ys

class Yosys:
    def __init__(self,user_config,index,directory='./',openlane=False):
        self.index = index
        self.directory = directory
        self.libdir = directory + 'libfiles/'
        self.resdir = directory + 'yosys_results_%s/' % (self.index)
        if not os.path.exists(self.resdir): os.makedirs(self.resdir)
        default_config_file = directory + 'config.json5'

        self.user_config = user_config
        self.design = ys.Design()
        self.openlane = openlane
        with open(default_config_file, 'r') as file:
            self.config = json5.load(file)
        self.overwrite_config()
        

    def get_deps(self):
        for lib in self.config["SYNTH_LIBS"]:
            ys.run_pass(f'read_liberty -lib -ignore_miss_dir -setattr blackbox {self.libdir + lib}', self.design)

    def overwrite_config(self,user_config=None):
        if user_config == None: user_config = self.user_config
        openlane_configs = [
            "SYNTH_DRIVING_CELL", "SYNTH_DIRECT_WIRE_BUFFERING", 
            "SYNTH_BUFFER_CELL", "SYNTH_TIEHI_CELL", 
            "SYNTH_TIELO_CELL", "MAX_TRANSITION_CONSTRAINT", 
            "OUTPUT_CAP_LOAD",
        ]
        # some paramters are required
        # verilog file
        if "VERILOG_FILES" not in user_config.keys():
            raise Exception("VERILOG_FILES should be defined in config file!")
        if not isinstance(user_config["VERILOG_FILES"],list):
            raise Exception("VERILOG_FILES should be a list!")
        if "DESIGN_NAME" not in user_config.keys() or not isinstance(user_config["DESIGN_NAME"],str):
            raise Exception("DESIGN_NAME should be a string")
        if "SYNTH_LIBS" in user_config.keys():
            if not isinstance(user_config["SYNTH_LIBS"],list):
                raise Exception("SYNTH_LIBS should be defined in a list")
            else:
                # remove openlane as one of the standards
                for key in openlane_configs:
                    if key in self.config.keys():
                        del self.config[key]

        for key,value in user_config.items():
            self.config[key] = value

    def divert_log(self):
        original_stdout = os.dup(1)
        original_stderr = os.dup(2)
        # Redirect to files
        yosys_log = open(self.resdir + self.config["YOSYS_OUTPUT_FILE"], "w")
        
        # Redirect stdout/stderr to files
        os.dup2(yosys_log.fileno(), 1)  # Redirect stdout
        os.dup2(yosys_log.fileno(), 1)  # Redirect stderr
        return original_stdout, original_stderr, yosys_log

    @staticmethod
    def revert_log(original_stdout, original_stderr, yosys_log):
        # Restore original stdout/stderr
        os.dup2(original_stdout, 1)
        os.dup2(original_stderr, 2)
        yosys_log.close()


    @contextmanager
    def suppress_output(self):
        original_stdout = os.dup(1)
        original_stderr = os.dup(2)
        yosys_log = open(self.resdir + self.config["YOSYS_OUTPUT_FILE"], "w")
        os.dup2(yosys_log.fileno(), 1)  # Redirect stdout
        os.dup2(yosys_log.fileno(), 2)  # Redirect stderr
        try:
            yield
        finally:
            os.dup2(original_stdout, 1)
            os.dup2(original_stderr, 2)
            yosys_log.close()  # Close the log file
    
    def run_yosys(self):
        # create directories
        self.report_dir = os.path.join(self.resdir,"reports")
        os.makedirs(self.report_dir,exist_ok=True)
        # diverting the stdout
        # original_stdout, original_stderr, yosys_log = self.divert_log()
        with self.suppress_output():
            
            # get dependencies
            self.get_deps()
            # Prepare Liberty Flags
            lib_args = ''
            for lib in self.config["SYNTH_LIBS"]:
                lib_args += " -liberty " + self.libdir + lib

            dfflib_args = ''
            for lib in self.config["SYNTH_LIBS"]:
                dfflib_args += " -liberty " + self.libdir + lib

            
            # Create SDC File
            sdc_file = os.path.join(self.directory, "synthesis.sdc")
            with open(sdc_file, "w") as f:
                if "SYNTH_DRIVING_CELL" in self.config.keys():
                    f.write(f"set_driving_cell {self.libdir + self.config['SYNTH_DRIVING_CELL'].replace('/',' ')}\n")
                if "OUTPUT_CAP_LOAD" in self.config.keys():
                    f.write(f"set_load {self.config['OUTPUT_CAP_LOAD']}\n")
            self.config["SDC_FILE"] = sdc_file

            # Get ABC strategy
            strategy_name = self.config["SYNTH_STRATEGY"]
            abc = ABCScriptCreator_v2_1_1(config=self.config) # extra {D}
            # abc = ABCScriptCreator_v2_3_1(config=self.config) 
            strategy_script = f'+read_constr,{sdc_file};'+abc.generate_abc_script(strategy_name)

            #############################################
            ############## Start Netlist ################
            #############################################
            # read the verilog scripts
            if "VERILOG_FILES" in self.config.keys():
                common.read_verilog_files(self.config,self.design)
            else:
                raise Exception("SCRIPT NOT CALLED CORRECTLY: VERILOG_FILES MUST BE SET")
            
            # elaborate design hierarchy
            ys.run_pass(f"hierarchy -check -top {self.config['DESIGN_NAME']} -nokeep_prints -nokeep_asserts", self.design)
            
            # rename the top module to design_name
            ys.run_pass(f"rename -top {self.config['DESIGN_NAME']}", self.design)

            # generates a visualization of the design hierarchy.
            # ys.run_pass(f"select -module {self.config['DESIGN_NAME']}", self.design)
            # ys.run_pass(f"show -format dot -prefix {self.directory}/hierarchy_%s" % (self.index), self.design)
            # ys.run_pass(f"select -clear", self.design)

            # netlist
            common.ol_synth(self.config["DESIGN_NAME"], self.report_dir, self.design)

            # remove instructions for formal verification/simulations (if any)
            ys.run_pass(f"delete t:\$print",self.design)
            ys.run_pass(f"delete t:\$assert",self.design)

            # generates a visualization of the design hierarchy.
            # ys.run_pass(f"show -format dot -prefix {self.directory}/primitive_techmap_%s" % (self.index), self.design)
            ys.run_pass("opt", self.design)
            ys.run_pass("opt_clean -purge", self.design)
            
            # mapping flip-flops
            ys.run_pass(f"dfflibmap {dfflib_args}", self.design)

            # saving a checkpoint
            ys.run_pass("design -save checkpoint", self.design)

            #############################################
            ############## Start Synthesis ##############
            #############################################
            # running abc strategy
            self.run_strategy(strategy_script, lib_args)

            # writing stats
            self.write_stats(lib_args, self.resdir + self.config["SAVE_NETLIST"],self.design)

        # # reverting the stdout
        # self.revert_log(original_stdout, original_stderr, yosys_log)

    def run_strategy(self, script, lib_args):
        with self.suppress_output():
            # print(f"[INFO] Using strategy \"{self.config['SYNTH_STRATEGY']}\"...")

            ys.run_pass("design -load checkpoint",self.design)
            ys.run_pass(f'tee -o {self.resdir + self.config["ABC_OUTPUT_FILE"]} abc -D "{self.config["CLOCK_PERIOD"] * 1000}" -constr "{self.config["SDC_FILE"]}" -script "{script}" -showtmp {lib_args}',self.design)

            # setting all undefined signals to be 0
            ys.run_pass("setundef -zero", self.design)

            # technology mapping of constant hi- and/or lo-drivers
            if 'SYNTH_TIEHI_CELL' in self.config.keys() and 'SYNTH_TIELO_CELL' in self.config.keys():
                ys.run_pass(f"hilomap -hicell {self.libdir + self.config['SYNTH_TIEHI_CELL'].replace('/',' ')} -locell {self.libdir + self.config['SYNTH_TIELO_CELL'].replace('/',' ')}",self.design)
            
            # split into individual bits
            if self.config["SYNTH_SPLITNETS"]:
                ys.run_pass("splitnets", self.design)
                ys.run_pass("opt_clean -purge", self.design)

            # insert buffer cells for connected wires
            if "SYNTH_DIRECT_WIRE_BUFFERING" in self.config.keys() and self.config["SYNTH_DIRECT_WIRE_BUFFERING"]:
                s = self.libdir + self.config['SYNTH_BUFFER_CELL'].replace("/"," ")
                ys.run_pass(f"insbuf -buf {s}", self.design)
            
            return

    def write_stats(self, lib_args, output, design):
        with self.suppress_output():
            chk = self.report_dir + "/chk_%s.rpt" % (self.index)
            stat_json = self.report_dir + "/stat_%s.json" % (self.index)
            stat_log = self.report_dir + "/stat_%s.log" % (self.index)

            ys.run_pass(f"tee -o {chk} check", design)
            ys.run_pass(f"tee -o {stat_json} stat -json {lib_args}", design)
            ys.run_pass(f"tee -o {stat_log} stat {lib_args}", design)
            
            ys.run_pass(f"write_verilog -noattr -noexpr -nohex -nodec -defparam {output}", design)
            ys.run_pass(f"write_json {output}.json", design)
            ys.run_pass(f"design -reset")

    @property
    def latency(self):
        pattern = "ABC: WireLoad = \"none\""
        try:
            with open(self.resdir + self.config["ABC_OUTPUT_FILE"],'r') as f:
                for line in f:
                    if pattern in line:
                        tokens = line.split(' = ')
                        for num,token in enumerate(tokens):
                            if 'Delay' == token[-5:]:
                                latency = float(tokens[num+1].strip().split(' ')[0])
                                return latency
            raise Exception(f'Unable to find latency in {self.resdir + self.config["ABC_OUTPUT_FILE"]}')
        except:
            raise Exception(f'Error reading {self.resdir + self.config["ABC_OUTPUT_FILE"]}')
    
    @property
    def area(self):
        pattern = "ABC: WireLoad = \"none\""
        try:
            with open(self.resdir + self.config["ABC_OUTPUT_FILE"],'r') as f:
                for line in f:
                    if pattern in line:
                        tokens = line.split(' = ')
                        for num,token in enumerate(tokens):
                            if 'Area' == token[-4:]:
                                area = float(tokens[num+1].strip().split(' ')[0])
                                return area
            raise Exception(f'Unable to find area in {self.resdir + self.config["ABC_OUTPUT_FILE"]}')
        except:
            raise Exception(f'Error reading {self.resdir + self.config["ABC_OUTPUT_FILE"]}')
    



if __name__ == '__main__':
    user_config = {
        "VERILOG_FILES": ["./verilog_files/princev2.sv"], # verilog file
        "DESIGN_NAME": "princev2", # the top module in the verilog files
        # "VERILOG_FILES": ["./uKNIT-BC.sv"], # verilog file
        # "DESIGN_NAME": "uknit_top", # the top module in the verilog files
        # "SYNTH_LIBS" : ["./libfiles/NanGate_15nm_OCL_typical_conditional_nldm.lib"]
        # "CLOCK_PERIOD": 5 # default clock period
    }
    yosys = Yosys(user_config)
    yosys.run_yosys()
    print(yosys.latency, yosys.area)

    