# Copyright 2020-2024 Efabless Corporation
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
import re


class ABCScriptCreator_v2_3_1:
    # Taken from openlane version 2.3.1
    def __init__(self, config):
        self.config = config

        self.rs_K = "resub -K "
        self.rs = "resub"
        self.rsz = "resub -z"
        self.rf = "drf -l"
        self.rfz = "drf -l -z"
        self.rw = "drw -l"
        self.rwz = "drw -l -z"
        self.rw_K = "drw -l -K"

        self.b = "balance"
        self.resyn2 = f"{self.b}; {self.rw}; {self.rf}; {self.b}; {self.rw}; {self.rwz}; {self.b}; {self.rfz}; {self.rwz}; {self.b}"
        self.share = f"strash; multi -m; {self.resyn2}"
        self.resyn2a = f"{self.b};{self.rw};{self.b};{self.rw};{self.rwz};{self.b};{self.rwz};{self.b}"
        self.resyn3 = f"{self.b}; resub; resub -K 6; {self.b};resub -z;resub -z -K 6; {self.b};resub -z -K 5; {self.b}"
        self.resyn2rs = f"{self.b};{self.rs_K} 6;{self.rw};{self.rs_K} 6 -N 2;{self.rf};{self.rs_K} 8;{self.rw};{self.rs_K} 10;{self.rwz};{self.rs_K} 10 -N 2;{self.b} {self.rs_K} 12;{self.rfz};{self.rs_K} 12 -N 2;{self.rwz};{self.b}"

        self.choice = f"fraig_store; {self.resyn2}; fraig_store; {self.resyn2}; fraig_store; fraig_restore"
        self.choice2 = f"fraig_store; {self.b}; fraig_store; {self.resyn2}; fraig_store; {self.resyn2}; fraig_store; {self.resyn2}; fraig_store; fraig_restore"

        self.area_mfs3 = ""
        self.delay_mfs3 = ""
        if config["SYNTH_ABC_USE_MFS3"]:
            self.area_mfs3 = "mfs3 -aemvz -I 4 -O 2"
            self.delay_mfs3 = "mfs3 -emvz -I 4 -O 2"

        self.map_old_area = "map -p -a -B 0.2 -A 0.9 -M 0"
        self.map_old_dly = "map -p -B 0.2 -A 0.9 -M 0"
        self.retime_area = "retime {D} -M 5"
        self.retime_dly = "retime {D} -M 6"
        self.map_new_area = "amap -m -Q 0.1 -F 20 -A 20 -C 5000"

        if config["SYNTH_ABC_AREA_USE_NF"]:
            self.map_new_area = "&get -n; &nf -R 1000; &put"

        self.max_fanout = config["MAX_FANOUT_CONSTRAINT"]
        self.max_transition = (
            config.get("MAX_TRANSITION_CONSTRAINT") or 0
        ) * 1000  # ns -> ps
        self.fine_tune = ""
        if config["SYNTH_ABC_BUFFERING"]:
            max_tr_arg = ""
            if self.max_transition != 0:
                max_tr_arg = f" -S {self.max_transition}"
            self.fine_tune = (
                f"buffer -N {self.max_fanout}{max_tr_arg};upsize {{D}};dnsize {{D}}"
            )
        elif config["SYNTH_SIZING"]:
            self.fine_tune = "upsize {D};dnsize {D}"

    def generate_abc_script(self, strategy):
        s = ''
        if strategy == "AREA 3":
            # ORFS Area Script
            s += "strash" + ';'
            s += "dch" + ';'
            s += "map -B 0.9" + ';'
            s += "topo" + ';'
            s += "stime -c" + ';'
            s += f"buffer -c -N {self.max_fanout}" + ';'
            s += "upsize -c" + ';'
            s += "dnsize -c" + ';'
        elif strategy == "DELAY 4":
            # ORFS Delay Script
            def repeated_sequence(s):
                s += "&st" + ';'
                s += "&syn2" + ';'
                s += "&if -g -K 6" + ';'
                s += "&synch2" + ';'
                s += "&nf" + ';'

            s += "&get -n" + ';'
            s += "&st" + ';'
            s += "&dch" + ';'
            s += "&nf" + ';'

            for _ in range(5):
                s += repeated_sequence(s)
            s += "&put" + ';'
            s += f"buffer -c -N {self.max_fanout}" + ';'
            s += "topo" + ';'
            s += "stime -c" + ';'
            s += "upsize -c" + ';'
            s += "dnsize -c" + ';'
        else:
            s += "fx" + ';'
            s += "mfs" + ';'
            s += "strash" + ';'
            s += self.rf + ';'

            # Resynth/Retime
            if strategy == "AREA 2":
                s += self.choice2 + ';'
            else:
                s += self.resyn2 + ';'
            if strategy.startswith("AREA ") or strategy == "DELAY 3":
                s += self.retime_area + ';'
            else:
                s += self.retime_dly + ';'
            s += "scleanup" + ';'

            if strategy in ["AREA 4", "DELAY 2"]:
                s += self.choice + ';'
            elif strategy != "DELAY 0":
                s += self.choice2 + ';'
            if strategy.startswith("AREA ") or strategy == "DELAY 3":
                s += self.map_new_area + ';'
            else:
                s += self.map_old_dly + ';'

            # Area Recovery
            if strategy in ["AREA 1", "AREA 2"]:
                s += self.choice2 + ';'
                s += self.map_new_area + ';'
            elif strategy in ["DELAY 1"]:
                s += self.choice2 + ';'
                s += "map" + ';'
            elif strategy in ["DELAY 2"]:
                s += self.choice + ';'
                s += "map" + ';'
            elif strategy in ["DELAY 3"]:
                s += self.choice2 + ';'
                s += self.map_old_dly + ';'

            if strategy.startswith("AREA "):
                s += self.area_mfs3 + ';'
            else:
                s += self.delay_mfs3 + ';'

            s += "retime {D}" + ';'

            # & space
            s += "&get -n" + ';'
            s += "&st" + ';'
            s += "&dch" + ';'
            s += "&nf" + ';'
            s += "&put" + ';'
            s += self.fine_tune + ';'

        # Common Conclusion
        s += "stime -p" + ';'
        s += "print_stats -m" + ';'
        return s.replace(';;',';')


class ABCScriptCreator_v2_1_1:
    # scripts taken from OpenLane version 2.1.1
    def __init__(self,config):
        abc_rs_K = f"resub -K "
        abc_rs   = f"resub"
        abc_rsz  = f"resub -z"
        abc_rf   = f"drf -l"
        abc_rfz  = f"drf -l -z"
        abc_rw   = f"drw -l"
        abc_rwz  = f"drw -l -z"
        abc_rw_K = f"drw -l -K"

        abc_b        = f"balance"
        abc_resyn2   = f"{abc_b}; {abc_rw}; {abc_rf}; {abc_b}; {abc_rw}; {abc_rwz}; {abc_b}; {abc_rfz}; {abc_rwz}; {abc_b}"
        abc_share    = f"strash; multi -m; {abc_resyn2}"
        abc_resyn2a  = f"{abc_b};{abc_rw};{abc_b};{abc_rw};{abc_rwz};{abc_b};{abc_rwz};{abc_b}"
        abc_resyn3   = f"balance;resub;resub -K 6;balance;resub -z;resub -z -K 6;balance;resub -z -K 5;balance"
        abc_resyn2rs = f"{abc_b};{abc_rs_K} 6;{abc_rw};{abc_rs_K} 6 -N 2;{abc_rf};{abc_rs_K} 8;{abc_rw};{abc_rs_K} 10;{abc_rwz};{abc_rs_K} 10 -N 2;{abc_b} {abc_rs_K} 12;{abc_rfz};{abc_rs_K} 12 -N 2;{abc_rwz};{abc_b}"

        abc_choice   = f"fraig_store; {abc_resyn2}; fraig_store; {abc_resyn2}; fraig_store; fraig_restore"
        abc_choice2  = f"fraig_store; balance; fraig_store; {abc_resyn2}; fraig_store; {abc_resyn2}; fraig_store; {abc_resyn2}; fraig_store; fraig_restore"

        abc_map_old_dly  = f"map -p -B 0.2 -A 0.9 -M 0"

        abc_area_recovery_1 = f"{abc_choice}; map;"
        abc_area_recovery_2 = f"{abc_choice2}; map;"

        abc_retime_area  = f"retime -D {{D}} -M 5"
        abc_retime_dly   = f"retime -D {{D}} -M 6"
        abc_map_new_area = f"amap -m -Q 0.1 -F 20 -A 20 -C 5000"


        if config["SYNTH_ABC_BUFFERING"] and "MAX_FANOUT_CONSTRAINT" in config.keys():
            max_tr_arg = ""
            if config["MAX_TRANSITION_CONSTRAINT"] != 0:
                max_TR = config["MAX_TRANSITION_CONSTRAINT"] * 1000
                max_tr_arg = f" -S {max_TR}"
            abc_fine_tune = f"buffer -N {config['MAX_FANOUT_CONSTRAINT']}{max_tr_arg};upsize -D {{D}};dnsize -D {{D}}"
        elif config["SYNTH_SIZING"]:
            abc_fine_tune = f"upsize -D {{D}};dnsize -D {{D}}"
        else:
            abc_fine_tune = f""
            self.area_scripts = [
                f"fx;mfs;strash;{abc_rf};{abc_resyn2};{abc_retime_area};scleanup;{abc_choice2};{abc_map_new_area};retime -D {{D}};&get -n;&st;&dch;&nf;&put;{abc_fine_tune};stime -p;print_stats -m;",
                f"fx;mfs;strash;{abc_rf};{abc_resyn2};{abc_retime_area};scleanup;{abc_choice2};{abc_map_new_area};{abc_choice2};{abc_map_new_area};retime -D {{D}};&get -n;&st;&dch;&nf;&put;{abc_fine_tune};stime -p;print_stats -m;",
                f"fx;mfs;strash;{abc_rf};{abc_choice2};{abc_retime_area};scleanup;{abc_choice2};{abc_map_new_area};{abc_choice2};{abc_map_new_area};retime -D {{D}};&get -n;&st;&dch;&nf;&put;{abc_fine_tune};stime -p;print_stats -m;",
                f"strash;dch;map -B 0.9;topo;stime -c;buffer -c -N {config['MAX_FANOUT_CONSTRAINT']};upsize -c;dnsize -c;stime -p;print_stats -m;"]

            self.delay_scripts = [
                f"fx;mfs;strash;{abc_rf};{abc_resyn2};{abc_retime_dly}; scleanup;{abc_map_old_dly};retime -D {{D}};&get -n;&st;&dch;&nf;&put;{abc_fine_tune};stime -p;print_stats -m;",
                f"fx;mfs;strash;{abc_rf};{abc_resyn2};{abc_retime_dly}; scleanup;{abc_choice2};{abc_map_old_dly};{abc_area_recovery_2}; retime -D {{D}};&get -n;&st;&dch;&nf;&put;{abc_fine_tune};stime -p;print_stats -m;",
                f"fx;mfs;strash;{abc_rf};{abc_resyn2};{abc_retime_dly}; scleanup;{abc_choice};{abc_map_old_dly};{abc_area_recovery_1}; retime -D {{D}};&get -n;&st;&dch;&nf;&put;{abc_fine_tune};stime -p;print_stats -m;",
                f"fx;mfs;strash;{abc_rf};{abc_resyn2};{abc_retime_area};scleanup;{abc_choice2};{abc_map_new_area};{abc_choice2};{abc_map_old_dly};retime -D {{D}};&get -n;&st;&dch;&nf;&put;{abc_fine_tune};stime -p;print_stats -m;",
                f"&get -n;&st;&dch;&nf;&put;&get -n;&st;&syn2;&if -g -K 6;&synch2;&nf;&put;&get -n;&st;&syn2;&if -g -K 6;&synch2;&nf;&put;&get -n;&st;&syn2;&if -g -K 6;&synch2;&nf;&put;&get -n;&st;&syn2;&if -g -K 6;&synch2;&nf;&put;&get -n;&st;&syn2;&if -g -K 6;&synch2;&nf;&put;buffer -c -N {config['MAX_FANOUT_CONSTRAINT']};topo;stime -c;upsize -c;dnsize -c;;stime -p;print_stats -m;"]
        
    def generate_abc_script(self,strategy):
        if strategy in ["AREA 0","AREA 1","AREA 2","AREA 3"]:
            num = int(strategy.split(" ")[1])
            return self.area_scripts[num].replace(';;',';')
        elif strategy in ["DELAY 0","DELAY 1","DELAY 2","DELAY 3","DELAY 4"]:
            num = int(strategy.split(" ")[1])
            return self.delay_scripts[num].replace(';;',';')
        else:
            raise Exception(f"Strategy {strategy} not found!")
            