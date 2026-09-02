'''
This file contains functions that help to construct the verilog file
'''

from pathlib import Path

def prepare_preamble(nr,verilog_file,xsize=64,ksize=128,ysize=64):
    filename = Path(verilog_file).stem
    statements = []
    statements.append('module '+ filename + '(')
    statements.append('\tinput [%s:0] x,' % (xsize-1))
    statements.append('\tinput [%s:0] k,' % (ksize-1))
    statements.append('\tinput clk,')
    statements.append('\toutput [%s:0] y);' % (ysize-1))
    statements.append('\twire [%s:0] kn[%s:0];' % (xsize-1,nr)) # round key
    statements.append('\twire [%s:0] t[%s:0];' % (xsize-1,3*nr-1)) # temp wires

    # round constant (similar to PRINCE's)
    statements.append('\twire [%s:0] rc[%s:0];' % (xsize-1,nr))
    statements.append("\tassign rc[0]  = 64'h0000000000000000;")
    statements.append("\tassign rc[1]  = 64'h13198a2e03707344;")
    statements.append("\tassign rc[2]  = 64'ha4093822299f31d0;")
    statements.append("\tassign rc[3]  = 64'h082efa98ec4e6c89;")
    statements.append("\tassign rc[4]  = 64'h452821e638d01377;")
    statements.append("\tassign rc[5]  = 64'hbe5466cf34e90c6c;")
    statements.append("\tassign rc[6]  = 64'h7ef84f78fd955cb1;")
    statements.append("\tassign rc[7]  = 64'h7aacf4538d971a60;")
    statements.append("\tassign rc[8]  = 64'hc882d32f25323c54;")
    statements.append("\tassign rc[9]  = 64'h9b8ded979cd838c7;")
    statements.append("\tassign rc[10] = 64'hd3b5a399ca0c2399;")
    statements.append("\tassign rc[11] = 64'h3f84d5b5b5470917;")
    statements.append("\tassign rc[12] = 64'h3f84d5b5b5470917;")
    return statements

def get_subst_layer(t_index,sbox_index):
    return '\tsboxlayer%s sboxlayer%s (.si(t[%s]), .so(t[%s]));' % (sbox_index,sbox_index,t_index,t_index+1)

def get_linear_layer(t_index,linear_index):
    return '\tlinear%s linear%s (.si(t[%s]),.so(t[%s]));' % (linear_index,linear_index,t_index,t_index+1)

def get_key_schedule_and_const(mkey_index,rkey_index,constant_index):
    return '\tkeyconst keyconst%s (.sk(k[%s:%s]), .sc(rc[%s]), .so(kn[%s]));' % (rkey_index,(1-mkey_index)*64+63,(1-mkey_index)*64,constant_index,rkey_index)

def get_add_key(t_index,rkey_index):
    return '\tassign t[%s] = t[%s] ^ kn[%s];' % (t_index+1, t_index, rkey_index)

def get_sboxes_in_subst_layer(layer_index):
    t = []
    for i in range(16):
        t.append(layer_index)
        t.append(i)
    s = "module sboxlayer%s (\n\
    // Outputs\n\
    so,\n\
    // Inputs\n\
    si\n\
    ) ;\n\
    output [63:0] so;\n\
    input [63:0]  si;\n\
    \n\
    sbox%s_%s sbox00 (.si(si[3:0]),.so(so[3:0]));\n\
    sbox%s_%s sbox01 (.si(si[7:4]),.so(so[7:4]));\n\
    sbox%s_%s sbox02 (.si(si[11:8]),.so(so[11:8]));\n\
    sbox%s_%s sbox03 (.si(si[15:12]),.so(so[15:12]));\n\
    sbox%s_%s sbox04 (.si(si[19:16]),.so(so[19:16]));\n\
    sbox%s_%s sbox05 (.si(si[23:20]),.so(so[23:20]));\n\
    sbox%s_%s sbox06 (.si(si[27:24]),.so(so[27:24]));\n\
    sbox%s_%s sbox07 (.si(si[31:28]),.so(so[31:28]));\n\
    sbox%s_%s sbox08 (.si(si[35:32]),.so(so[35:32]));\n\
    sbox%s_%s sbox09 (.si(si[39:36]),.so(so[39:36]));\n\
    sbox%s_%s sbox10 (.si(si[43:40]),.so(so[43:40]));\n\
    sbox%s_%s sbox11 (.si(si[47:44]),.so(so[47:44]));\n\
    sbox%s_%s sbox12 (.si(si[51:48]),.so(so[51:48]));\n\
    sbox%s_%s sbox13 (.si(si[55:52]),.so(so[55:52]));\n\
    sbox%s_%s sbox14 (.si(si[59:56]),.so(so[59:56]));\n\
    sbox%s_%s sbox15 (.si(si[63:60]),.so(so[63:60]));\n\
    endmodule\n" % tuple([layer_index]+t)
    return s

def get_sboxes_implementation(sbox,layer_index,sbox_index):
    sbox = [hex(sbox[i])[2:] for i in range(16)]
    s = "module sbox%s_%s (\n\
        // Outputs\n\
        so,\n\
        // Inputs\n\
        si\n\
        ) ;\n\
        output reg [3:0] so;\n\
        input [3:0]  si;\n\
        always @ *\n\
        \tcase (si)\n\
        \t00: so <= 'h%s;\n\
        \t01: so <= 'h%s;\n\
        \t02: so <= 'h%s;\n\
        \t03: so <= 'h%s;\n\
        \t04: so <= 'h%s;\n\
        \t05: so <= 'h%s;\n\
        \t06: so <= 'h%s;\n\
        \t07: so <= 'h%s;\n\
        \t08: so <= 'h%s;\n\
        \t09: so <= 'h%s;\n\
        \t10: so <= 'h%s;\n\
        \t11: so <= 'h%s;\n\
        \t12: so <= 'h%s;\n\
        \t13: so <= 'h%s;\n\
        \t14: so <= 'h%s;\n\
        \t15: so <= 'h%s;\n\
        \tdefault: so <= 0;\n\
        endcase\n\
    endmodule // \n" % tuple([layer_index,sbox_index]+sbox)
    return s

def get_matrix_implementation(matrix,layer_index):
    s = 'module linear%s (/*AUTOARG*/\n\
        // Outputs\n\
        so,\n\
        // Inputs\n\
        si\n\
        ) ;\n\
        output [63:0] so;\n\
        input  [63:0] si;\n' % (layer_index)
    for i in range(64):
        s += '\n\t assign so[%s] = ' % i    
        for j in range(64):
            if matrix[63-i][63-j] == 1:
                s += 'si[%s] ^ ' % j
        s = s[:-3] + ';'
    s += '\nendmodule'
    return s

def get_key_add_and_const_implementation():
    s = 'module keyconst (/*AUTOARG*/\n\
        // Outputs\n\
        so,\n\
        // Inputs\n\
        sk,sc\n\
        ) ;\n\
        output [63:0] so;\n\
        \n\
        input [63:0]  sk;\n\
        input [63:0]  sc;\n\
        \n\
        assign so = sc ^ sk;\n\
        endmodule'
    return s



