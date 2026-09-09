// verilog for PRINCEV
module princev2(
	         input [63:0]  x,
	         input [127:0] k,
		 output [63:0] y);

   wire [63:0] 		       kn[13:0];

   wire [63:0] 		       rc[11:0];

   wire [63:0] 		       t[47:0];

   assign rc[0]  = 64'h0000000000000000;

   assign rc[1]  = 64'h13198a2e03707344;

   assign rc[2]  = 64'ha4093822299f31d0;

   assign rc[3]  = 64'h082efa98ec4e6c89;

   assign rc[4]  = 64'h452821e638d01377;

   assign rc[5]  = 64'hbe5466cf34e90c6c;

   assign rc[6]  = 64'h7ef84f78fd955cb1;

   assign rc[7]  = 64'h7aacf4538d971a60;

   assign rc[8]  = 64'hc882d32f25323c54;

   assign rc[9]  = 64'h9b8ded979cd838c7;

   assign rc[10] = 64'hd3b5a399ca0c2399;

   assign rc[11] = 64'h3f84d5b5b5470917;

   assign t[0] = x;

   keyconst keyconst0 (.sk(k[127:64]), .sc(rc[0]), .so(kn[0]));

   assign t[1] = t[0] ^ kn[0];

   sboxlayerprince sboxlayer0 (.si(t[1]), .so(t[2]));

   mixcolumnsprince mixcolumns0 (.si(t[2]),.so(t[3]));

   shiftrowsprince shiftrows0 (.si(t[3]),.so(t[4]));

   keyconst keyconst1 (.sk(k[63:0]), .sc(rc[1]), .so(kn[1]));

   assign t[5] = t[4] ^ kn[1];

   sboxlayerprince sboxlayer1 (.si(t[5]), .so(t[6]));

   mixcolumnsprince mixcolumns1 (.si(t[6]),.so(t[7]));

   shiftrowsprince shiftrows1 (.si(t[7]),.so(t[8]));

   keyconst keyconst2 (.sk(k[127:64]), .sc(rc[2]), .so(kn[2]));

   assign t[9] = t[8] ^ kn[2];

   sboxlayerprince sboxlayer2 (.si(t[9]), .so(t[10]));

   mixcolumnsprince mixcolumns2 (.si(t[10]),.so(t[11]));

   shiftrowsprince shiftrows2 (.si(t[11]),.so(t[12]));

   keyconst keyconst3 (.sk(k[63:0]), .sc(rc[3]), .so(kn[3]));

   assign t[13] = t[12] ^ kn[3];

   sboxlayerprince sboxlayer3 (.si(t[13]), .so(t[14]));

   mixcolumnsprince mixcolumns3 (.si(t[14]),.so(t[15]));

   shiftrowsprince shiftrows3 (.si(t[15]),.so(t[16]));

   keyconst keyconst4 (.sk(k[127:64]), .sc(rc[4]), .so(kn[4]));

   assign t[17] = t[16] ^ kn[4];

   sboxlayerprince sboxlayer4 (.si(t[17]), .so(t[18]));

   mixcolumnsprince mixcolumns4 (.si(t[18]),.so(t[19]));

   shiftrowsprince shiftrows4 (.si(t[19]),.so(t[20]));

   keyconst keyconst5 (.sk(k[63:0]), .sc(rc[5]), .so(kn[5]));

   assign t[21] = t[20] ^ kn[5];

   sboxlayerprince sboxlayer5 (.si(t[21]), .so(t[22]));

   assign kn[6] = k[127:64];

   assign t[23] = t[22] ^ kn[6];

   mixcolumnsprince mixcolumns7 (.si(t[23]),.so(t[24]));

   keyconst keyconst7 (.sk(k[63:0]), .sc(rc[11]), .so(kn[7]));

   assign t[25] = t[24] ^ kn[7];

   sboxlayerprinceinv sboxlayer7 (.si(t[25]), .so(t[26]));

   keyconst keyconst8 (.sk(k[127:64]), .sc(rc[6]), .so(kn[8]));

   assign t[27] = t[26] ^ kn[8];

   shiftrowsprinceinv shiftrows8 (.si(t[27]),.so(t[28]));

   mixcolumnsprince mixcolumns8 (.si(t[28]),.so(t[29]));

   sboxlayerprinceinv sboxlayer8 (.si(t[29]), .so(t[30]));

   keyconst keyconst9 (.sk(k[63:0]), .sc(rc[7]), .so(kn[9]));

   assign t[31] = t[30] ^ kn[9];

   shiftrowsprinceinv shiftrows9 (.si(t[31]),.so(t[32]));

   mixcolumnsprince mixcolumns9 (.si(t[32]),.so(t[33]));

   sboxlayerprinceinv sboxlayer9 (.si(t[33]), .so(t[34]));

   keyconst keyconst10 (.sk(k[127:64]), .sc(rc[8]), .so(kn[10]));

   assign t[35] = t[34] ^ kn[10];

   shiftrowsprinceinv shiftrows10 (.si(t[35]),.so(t[36]));

   mixcolumnsprince mixcolumns10 (.si(t[36]),.so(t[37]));

   sboxlayerprinceinv sboxlayer10 (.si(t[37]), .so(t[38]));

   keyconst keyconst11 (.sk(k[63:0]), .sc(rc[9]), .so(kn[11]));

   assign t[39] = t[38] ^ kn[11];

   shiftrowsprinceinv shiftrows11 (.si(t[39]),.so(t[40]));

   mixcolumnsprince mixcolumns11 (.si(t[40]),.so(t[41]));

   sboxlayerprinceinv sboxlayer11 (.si(t[41]), .so(t[42]));

   keyconst keyconst12 (.sk(k[127:64]), .sc(rc[10]), .so(kn[12]));

   assign t[43] = t[42] ^ kn[12];

   shiftrowsprinceinv shiftrows12 (.si(t[43]),.so(t[44]));

   mixcolumnsprince mixcolumns12 (.si(t[44]),.so(t[45]));

   sboxlayerprinceinv sboxlayer12 (.si(t[45]), .so(t[46]));

   keyconst keyconst13 (.sk(k[63:0]), .sc(rc[11]), .so(kn[13]));

   assign t[47] = t[46] ^ kn[13];

   assign y = t[47];

endmodule // prince
module sboxprince (
		         // Outputs
		         so,
		         // Inputs
		         si
		   ) ;

   output reg [3:0] so;

   input [3:0] 	    si;

         always @ *
	            case (si)
		      00: so <= 'hb;

		      01: so <= 'hf;

		      02: so <= 'h3;

		      03: so <= 'h2;

		      04: so <= 'ha;

		      05: so <= 'hc;

		      06: so <= 'h9;

		      07: so <= 'h1;

		      08: so <= 'h6;

		      09: so <= 'h7;

		      10: so <= 'h8;

		      11: so <= 'h0;

		      12: so <= 'he;

		      13: so <= 'h5;

		      14: so <= 'hd;

		      15: so <= 'h4;

		      default: so <= 0;

		    endcase // case (si)
      endmodule //


module sboxprinceinv (
		            // Outputs
		            so,
		            // Inputs
		            si
		      ) ;

   output reg [3:0] so;

   input [3:0] 	    si;

         always @ *
	            case (si)
		      00: so <= 'hb;

		      01: so <= 'h7;

		      02: so <= 'h3;

		      03: so <= 'h2;

		      04: so <= 'hf;

		      05: so <= 'hd;

		      06: so <= 'h8;

		      07: so <= 'h9;

		      08: so <= 'ha;

		      09: so <= 'h6;

		      10: so <= 'h4;

		      11: so <= 'h0;

		      12: so <= 'h5;

		      13: so <= 'he;

		      14: so <= 'hc;

		      15: so <= 'h1;

		      default: so <= 0;

		    endcase // case (si)
      endmodule //


module sboxlayerprince (
			   // Outputs
			   so,
			   // Inputs
			   si
			) ;

   output [63:0] so;

   input [63:0]  si;


   genvar 	 i;

      generate
	 for (i = 0; i < 16; i = i + 1) begin:sboxlayer
	           sboxprince sbox00 (.si(si[4*i+3:4*i]),
				      .so(so[4*i+3:4*i]));

	 end
	    endgenerate
endmodule // sboxlayerprince


module sboxlayerprinceinv (
			      // Outputs
			      so,
			      // Inputs
			      si
			   ) ;

   output [63:0] so;

   input [63:0]  si;


   genvar 	 i;

      generate
	 for (i = 0; i < 16; i = i + 1) begin:sboxlayer
	           sboxprinceinv sbox00 (.si(si[4*i+3:4*i]),
					 .so(so[4*i+3:4*i]));

	 end
	    endgenerate
endmodule // sboxlayerprinceinv


module shiftrowsprince (/*AUTOARG*/
			      // Outputs
			      so,
			      // Inputs
			      si
			) ;

   output [63:0] so;

   input [63:0]  si;

   wire [3:0] 	 state [3:0][3:0];

   wire [3:0] 	 pstate [3:0][3:0];


   assign state[0][0] = si[63:60];

   assign state[1][0] = si[59:56];

   assign state[2][0] = si[55:52];

   assign state[3][0] = si[51:48];

   assign state[0][1] = si[47:44];

   assign state[1][1] = si[43:40];

   assign state[2][1] = si[39:36];

   assign state[3][1] = si[35:32];

   assign state[0][2] = si[31:28];

   assign state[1][2] = si[27:24];

   assign state[2][2] = si[23:20];

   assign state[3][2] = si[19:16];

   assign state[0][3] = si[15:12];

   assign state[1][3] = si[11:8];

   assign state[2][3] = si[7:4];

   assign state[3][3] = si[3:0];

   assign pstate[0][0] = state[0][0];

   assign pstate[0][1] = state[0][1];

   assign pstate[0][2] = state[0][2];

   assign pstate[0][3] = state[0][3];

   assign pstate[1][0] = state[1][1];

   assign pstate[1][1] = state[1][2];

   assign pstate[1][2] = state[1][3];

   assign pstate[1][3] = state[1][0];

   assign pstate[2][0] = state[2][2];

   assign pstate[2][1] = state[2][3];

   assign pstate[2][2] = state[2][0];

   assign pstate[2][3] = state[2][1];

   assign pstate[3][0] = state[3][3];

   assign pstate[3][1] = state[3][0];

   assign pstate[3][2] = state[3][1];

   assign pstate[3][3] = state[3][2];

   assign so[63:60] = pstate[0][0];

   assign so[59:56] = pstate[1][0];

   assign so[55:52] = pstate[2][0];

   assign so[51:48] = pstate[3][0];

   assign so[47:44] = pstate[0][1];

   assign so[43:40] = pstate[1][1];

   assign so[39:36] = pstate[2][1];

   assign so[35:32] = pstate[3][1];

   assign so[31:28] = pstate[0][2];

   assign so[27:24] = pstate[1][2];

   assign so[23:20] = pstate[2][2];

   assign so[19:16] = pstate[3][2];

   assign so[15:12] = pstate[0][3];

   assign so[11:8] = pstate[1][3];

   assign so[7:4] = pstate[2][3];

   assign so[3:0] = pstate[3][3];

   endmodule // princeshiftrows

module shiftrowsprinceinv (/*AUTOARG*/
			         // Outputs
			         so,
			         // Inputs
			         si
			   ) ;

   output [63:0] so;

   input [63:0]  si;

   wire [3:0] 	 state [3:0][3:0];

   wire [3:0] 	 pstate [3:0][3:0];


   assign state[0][0] = si[63:60];

   assign state[1][0] = si[59:56];

   assign state[2][0] = si[55:52];

   assign state[3][0] = si[51:48];

   assign state[0][1] = si[47:44];

   assign state[1][1] = si[43:40];

   assign state[2][1] = si[39:36];

   assign state[3][1] = si[35:32];

   assign state[0][2] = si[31:28];

   assign state[1][2] = si[27:24];

   assign state[2][2] = si[23:20];

   assign state[3][2] = si[19:16];

   assign state[0][3] = si[15:12];

   assign state[1][3] = si[11:8];

   assign state[2][3] = si[7:4];

   assign state[3][3] = si[3:0];

   assign pstate[0][0] = state[0][0];

   assign pstate[0][1] = state[0][1];

   assign pstate[0][2] = state[0][2];

   assign pstate[0][3] = state[0][3];

   assign pstate[1][0] = state[1][3];

   assign pstate[1][1] = state[1][0];

   assign pstate[1][2] = state[1][1];

   assign pstate[1][3] = state[1][2];

   assign pstate[2][0] = state[2][2];

   assign pstate[2][1] = state[2][3];

   assign pstate[2][2] = state[2][0];

   assign pstate[2][3] = state[2][1];

   assign pstate[3][0] = state[3][1];

   assign pstate[3][1] = state[3][2];

   assign pstate[3][2] = state[3][3];

   assign pstate[3][3] = state[3][0];

   assign so[63:60] = pstate[0][0];

   assign so[59:56] = pstate[1][0];

   assign so[55:52] = pstate[2][0];

   assign so[51:48] = pstate[3][0];

   assign so[47:44] = pstate[0][1];

   assign so[43:40] = pstate[1][1];

   assign so[39:36] = pstate[2][1];

   assign so[35:32] = pstate[3][1];

   assign so[31:28] = pstate[0][2];

   assign so[27:24] = pstate[1][2];

   assign so[23:20] = pstate[2][2];

   assign so[19:16] = pstate[3][2];

   assign so[15:12] = pstate[0][3];

   assign so[11:8] = pstate[1][3];

   assign so[7:4] = pstate[2][3];

   assign so[3:0] = pstate[3][3];

   endmodule // princeinvshiftrows

module keyconst (/*AUTOARG*/
		       // Outputs
		       so,
		       // Inputs
		       sk,sc
		 ) ;

   output [63:0] so;


   input [63:0]  sk;

   input [63:0]  sc;


   assign so = sc ^ sk;

endmodule // keyconst

module mixcolumnsprince (/*AUTOARG*/
			       // Outputs
			       so,
			       // Inputs
			       si
			 ) ;

   output [63:0] so;


   input [63:0]  si;


   wire [3:0] 	 state [3:0][3:0];

   wire [3:0] 	 pstate [3:0][3:0];

   assign state[0][0] = si[63:60];

   assign state[1][0] = si[59:56];

   assign state[2][0] = si[55:52];

   assign state[3][0] = si[51:48];

   assign state[0][1] = si[47:44];

   assign state[1][1] = si[43:40];

   assign state[2][1] = si[39:36];

   assign state[3][1] = si[35:32];

   assign state[0][2] = si[31:28];

   assign state[1][2] = si[27:24];

   assign state[2][2] = si[23:20];

   assign state[3][2] = si[19:16];

   assign state[0][3] = si[15:12];

   assign state[1][3] = si[11:8];

   assign state[2][3] = si[7:4];

   assign state[3][3] = si[3:0];

         assign pstate[0][0] =
			                  (state[0][0] & 'b0111) ^
			                  (state[1][0] & 'b1011) ^
			                  (state[2][0] & 'b1101) ^
			      (state[3][0] & 'b1110) ;

         assign pstate[1][0] =
			                  (state[0][0] & 'b1011) ^
			                  (state[1][0] & 'b1101) ^
			                  (state[2][0] & 'b1110) ^
			      (state[3][0] & 'b0111) ;

         assign pstate[2][0] =
			                  (state[0][0] & 'b1101) ^
			                  (state[1][0] & 'b1110) ^
			                  (state[2][0] & 'b0111) ^
			      (state[3][0] & 'b1011) ;

         assign pstate[3][0] =
			                  (state[0][0] & 'b1110) ^
			                  (state[1][0] & 'b0111) ^
			                  (state[2][0] & 'b1011) ^
			      (state[3][0] & 'b1101) ;

         assign pstate[0][1] =
			                  (state[0][1] & 'b1011) ^
			                  (state[1][1] & 'b1101) ^
			                  (state[2][1] & 'b1110) ^
			      (state[3][1] & 'b0111) ;

         assign pstate[1][1] =
			                  (state[0][1] & 'b1101) ^
			                  (state[1][1] & 'b1110) ^
			                  (state[2][1] & 'b0111) ^
			      (state[3][1] & 'b1011) ;

         assign pstate[2][1] =
			                  (state[0][1] & 'b1110) ^
			                  (state[1][1] & 'b0111) ^
			                  (state[2][1] & 'b1011) ^
			      (state[3][1] & 'b1101) ;

         assign pstate[3][1] =
			                  (state[0][1] & 'b0111) ^
			                  (state[1][1] & 'b1011) ^
			                  (state[2][1] & 'b1101) ^
			      (state[3][1] & 'b1110) ;

         assign pstate[0][2] =
			                  (state[0][2] & 'b1011) ^
			                  (state[1][2] & 'b1101) ^
			                  (state[2][2] & 'b1110) ^
			      (state[3][2] & 'b0111) ;

         assign pstate[1][2] =
			                  (state[0][2] & 'b1101) ^
			                  (state[1][2] & 'b1110) ^
			                  (state[2][2] & 'b0111) ^
			      (state[3][2] & 'b1011) ;

         assign pstate[2][2] =
			                  (state[0][2] & 'b1110) ^
			                  (state[1][2] & 'b0111) ^
			                  (state[2][2] & 'b1011) ^
			      (state[3][2] & 'b1101) ;

         assign pstate[3][2] =
			                  (state[0][2] & 'b0111) ^
			                  (state[1][2] & 'b1011) ^
			                  (state[2][2] & 'b1101) ^
			      (state[3][2] & 'b1110) ;

         assign pstate[0][3] =
			                  (state[0][3] & 'b0111) ^
			                  (state[1][3] & 'b1011) ^
			                  (state[2][3] & 'b1101) ^
			      (state[3][3] & 'b1110) ;

         assign pstate[1][3] =
			                  (state[0][3] & 'b1011) ^
			                  (state[1][3] & 'b1101) ^
			                  (state[2][3] & 'b1110) ^
			      (state[3][3] & 'b0111) ;

         assign pstate[2][3] =
			                  (state[0][3] & 'b1101) ^
			                  (state[1][3] & 'b1110) ^
			                  (state[2][3] & 'b0111) ^
			      (state[3][3] & 'b1011) ;

         assign pstate[3][3] =
			                  (state[0][3] & 'b1110) ^
			                  (state[1][3] & 'b0111) ^
			                  (state[2][3] & 'b1011) ^
			      (state[3][3] & 'b1101) ;

   assign so[63:60] = pstate[0][0];

   assign so[59:56] = pstate[1][0];

   assign so[55:52] = pstate[2][0];

   assign so[51:48] = pstate[3][0];

   assign so[47:44] = pstate[0][1];

   assign so[43:40] = pstate[1][1];

   assign so[39:36] = pstate[2][1];

   assign so[35:32] = pstate[3][1];

   assign so[31:28] = pstate[0][2];

   assign so[27:24] = pstate[1][2];

   assign so[23:20] = pstate[2][2];

   assign so[19:16] = pstate[3][2];

   assign so[15:12] = pstate[0][3];

   assign so[11:8] = pstate[1][3];

   assign so[7:4] = pstate[2][3];

   assign so[3:0] = pstate[3][3];

   endmodule // princemixcolumn
