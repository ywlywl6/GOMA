\ Model mapper_review1_bugfix
\ LP format - for model browsing. Use MPS format to capture full model detail.
Minimize
  0.0657375 B3_x + 0.0657375 B3_y + 0.057146 B3_z
   + 38.2744375 Nnorm_receiver_src1_x + 38.2744375 Nnorm_receiver_src1_y
   + 31.2 Nnorm_receiver_src1_z + 38.2744375 Nnorm_oldread_src1_z
   + 0.057146 Nnorm_receiver_src3_x + 31.2 Nnorm_source_src3_x
   - 23.742625 Nnorm_source_src3_sram_x + 0.057146 Nnorm_receiver_src3_y
   + 31.2 Nnorm_source_src3_y - 23.742625 Nnorm_source_src3_sram_y
   + 31.2 Nnorm_source_src3_z - 24.1255625 Nnorm_source_src3_sram_z
   + 0.057146 Nnorm_oldread_receiver_src3_z
   + 31.2 Nnorm_oldread_source_src3_z
   - 23.742625 Nnorm_oldread_source_src3_sram_z
   + 7.457375 Nnorm_source_src4_sram_x + 31.2 Nnorm_source_src4_ddr_x
   + 7.457375 Nnorm_source_src4_sram_y + 31.2 Nnorm_source_src4_ddr_y
   + 7.0744375 Nnorm_source_src4_sram_z + 31.2 Nnorm_source_src4_ddr_z
   + 0.0657375 Nnorm_oldread_source_src4_rf_z
   + 7.457375 Nnorm_oldread_source_src4_sram_z
   + 31.2 Nnorm_oldread_source_src4_ddr_z + 0.107093 Constant
Subject To
 onehot_a01: a01_x + a01_y + a01_z = 1
 onehot_a12: a12_x + a12_y + a12_z = 1
 hierarchy_34_x: L_3_x - k_3_x = 0
 hierarchy_34_y: L_3_y - k_3_y = 0
 hierarchy_34_z: L_3_z - k_3_z = 0
 all_unit_01_ub_0: - unit_k0_x + all_unit_01 <= 0
 all_unit_01_ub_1: - unit_k0_y + all_unit_01 <= 0
 all_unit_01_ub_2: - unit_k0_z + all_unit_01 <= 0
 all_unit_01_lb: - unit_k0_x - unit_k0_y - unit_k0_z + all_unit_01 >= -2
 all_unit_12_ub_0: - unit_k1_x + all_unit_12 <= 0
 all_unit_12_ub_1: - unit_k1_y + all_unit_12 <= 0
 all_unit_12_ub_2: - unit_k1_z + all_unit_12 <= 0
 all_unit_12_lb: - unit_k1_x - unit_k1_y - unit_k1_z + all_unit_12 >= -2
 physical_a01_x: a01_x + unit_k0_x - all_unit_01 <= 1
 physical_a12_x: a12_x + unit_k1_x - all_unit_12 <= 1
 canonical_a12_upper_x: - a01_x + a12_x + all_unit_12 <= 1
 canonical_a12_lower_x: a01_x - a12_x + all_unit_12 <= 1
 physical_a01_y: a01_y + unit_k0_y - all_unit_01 <= 1
 physical_a12_y: a12_y + unit_k1_y - all_unit_12 <= 1
 canonical_a12_upper_y: - a01_y + a12_y + all_unit_12 <= 1
 canonical_a12_lower_y: a01_y - a12_y + all_unit_12 <= 1
 physical_a01_z: a01_z + unit_k0_z - all_unit_01 <= 1
 physical_a12_z: a12_z + unit_k1_z - all_unit_12 <= 1
 canonical_a12_upper_z: - a01_z + a12_z + all_unit_12 <= 1
 canonical_a12_lower_z: a01_z - a12_z + all_unit_12 <= 1
 canonical_all_unit_a01_x: a01_x - all_unit_01 >= 0
 chi3_x_ub_0: - a01_x + chi3_x <= 0
 chi3_x_ub_1: - a12_x + chi3_x <= 0
 chi3_x_ub_2: - unit_k1_y + chi3_x <= 0
 chi3_x_ub_3: - unit_k1_z + chi3_x <= 0
 chi3_x_lb: - a01_x - a12_x - unit_k1_y - unit_k1_z + chi3_x >= -3
 chi3_y_ub_0: - a01_y + chi3_y <= 0
 chi3_y_ub_1: - a12_y + chi3_y <= 0
 chi3_y_ub_2: - unit_k1_x + chi3_y <= 0
 chi3_y_ub_3: - unit_k1_z + chi3_y <= 0
 chi3_y_lb: - a01_y - a12_y - unit_k1_x - unit_k1_z + chi3_y >= -3
 chi3_z_ub_0: - a01_z + chi3_z <= 0
 chi3_z_ub_1: - a12_z + chi3_z <= 0
 chi3_z_ub_2: - unit_k1_x + chi3_z <= 0
 chi3_z_ub_3: - unit_k1_y + chi3_z <= 0
 chi3_z_lb: - a01_z - a12_z - unit_k1_x - unit_k1_y + chi3_z >= -3
 capacity_level_1: occupied_scaled_1_x + occupied_scaled_1_y
   + occupied_scaled_1_z <= 6144
 capacity_level_3: occupied_scaled_3_x + occupied_scaled_3_y
   + occupied_scaled_3_z <= 11.31370849898476
 src1_resident_alpha_x_ub_0: - B1_x + src1_resident_alpha_x <= 0
 src1_resident_alpha_x_ub_1: - a01_x + src1_resident_alpha_x <= 0
 src1_resident_alpha_x_lb: - B1_x - a01_x + src1_resident_alpha_x >= -1
 src1_resident_background_x_ub_pos: - B1_x + src1_resident_background_x
   <= 0
 src1_resident_background_x_ub_neg: a01_x + src1_resident_background_x
   <= 1
 src1_resident_background_x_lb: - B1_x + a01_x + src1_resident_background_x
   >= 0
 src1_background_count_x_ub_value: - 1000 y_1_x
   + 1000 src1_background_count_x <= 0
 src1_background_count_x_ub_gate: - 1000 src1_resident_background_x
   + 1000 src1_background_count_x <= 0
 src1_background_count_x_lb: - 1000 y_1_x - 1000 src1_resident_background_x
   + 1000 src1_background_count_x >= -1000
 define_Nnorm_receiver_src1_x: - 0.00762939453125 src1_resident_alpha_x
   - 1000 src1_background_count_x + 1000 Nnorm_receiver_src1_x = 0
 src1_resident_alpha_y_ub_0: - B1_y + src1_resident_alpha_y <= 0
 src1_resident_alpha_y_ub_1: - a01_y + src1_resident_alpha_y <= 0
 src1_resident_alpha_y_lb: - B1_y - a01_y + src1_resident_alpha_y >= -1
 src1_resident_background_y_ub_pos: - B1_y + src1_resident_background_y
   <= 0
 src1_resident_background_y_ub_neg: a01_y + src1_resident_background_y
   <= 1
 src1_resident_background_y_lb: - B1_y + a01_y + src1_resident_background_y
   >= 0
 src1_background_count_y_ub_value: - 1000 y_1_y
   + 1000 src1_background_count_y <= 0
 src1_background_count_y_ub_gate: - 1000 src1_resident_background_y
   + 1000 src1_background_count_y <= 0
 src1_background_count_y_lb: - 1000 y_1_y - 1000 src1_resident_background_y
   + 1000 src1_background_count_y >= -1000
 define_Nnorm_receiver_src1_y: - 0.1220703125 src1_resident_alpha_y
   - 1000 src1_background_count_y + 1000 Nnorm_receiver_src1_y = 0
 src1_resident_alpha_z_ub_0: - B1_z + src1_resident_alpha_z <= 0
 src1_resident_alpha_z_ub_1: - a01_z + src1_resident_alpha_z <= 0
 src1_resident_alpha_z_lb: - B1_z - a01_z + src1_resident_alpha_z >= -1
 src1_resident_background_z_ub_pos: - B1_z + src1_resident_background_z
   <= 0
 src1_resident_background_z_ub_neg: a01_z + src1_resident_background_z
   <= 1
 src1_resident_background_z_lb: - B1_z + a01_z + src1_resident_background_z
   >= 0
 src1_background_count_z_ub_value: - 1000 y_1_z
   + 1000 src1_background_count_z <= 0
 src1_background_count_z_ub_gate: - 1000 src1_resident_background_z
   + 1000 src1_background_count_z <= 0
 src1_background_count_z_lb: - 1000 y_1_z - 1000 src1_resident_background_z
   + 1000 src1_background_count_z >= -1000
 define_Nnorm_receiver_src1_z: - 0.1953125 src1_resident_alpha_z
   - 1000 src1_background_count_z + 1000 Nnorm_receiver_src1_z = 0
 define_Nnorm_oldread_src1_z: 0.1953125 B1_z - 1000 Nnorm_receiver_src1_z
   + 1000 Nnorm_oldread_src1_z = 0
 define_a12_without_chi3_x: - a12_x + chi3_x + a12_without_chi3_x = 0
 src3_case_chi_x_ub_0: - B3_x + src3_case_chi_x <= 0
 src3_case_chi_x_ub_1: - chi3_x + src3_case_chi_x <= 0
 src3_case_chi_x_lb: - B3_x - chi3_x + src3_case_chi_x >= -1
 src3_case_alpha_x_ub_0: - B3_x + src3_case_alpha_x <= 0
 src3_case_alpha_x_ub_1: - a12_without_chi3_x + src3_case_alpha_x <= 0
 src3_case_alpha_x_lb: - B3_x - a12_without_chi3_x + src3_case_alpha_x
   >= -1
 src3_case_background_x_ub_pos: - B3_x + src3_case_background_x <= 0
 src3_case_background_x_ub_neg: a12_x + src3_case_background_x <= 1
 src3_case_background_x_lb: - B3_x + a12_x + src3_case_background_x >= 0
 src3_case_partition_x: - B3_x + src3_case_chi_x + src3_case_alpha_x
   + src3_case_background_x = 0
 src3_receiver_chi_count_x_ub_value: - 1000 inv_L3k1k0_x
   + 1000 src3_receiver_chi_count_x <= 0
 src3_receiver_chi_count_x_ub_gate: - 1000 src3_case_chi_x
   + 1000 src3_receiver_chi_count_x <= 0
 src3_receiver_chi_count_x_lb: - 1000 inv_L3k1k0_x - 1000 src3_case_chi_x
   + 1000 src3_receiver_chi_count_x >= -1000
 src3_receiver_alpha_count_x_ub_value: - 1000 inv_L3k1_x
   + 1000 src3_receiver_alpha_count_x <= 0
 src3_receiver_alpha_count_x_ub_gate: - 1000 src3_case_alpha_x
   + 1000 src3_receiver_alpha_count_x <= 0
 src3_receiver_alpha_count_x_lb: - 1000 inv_L3k1_x - 1000 src3_case_alpha_x
   + 1000 src3_receiver_alpha_count_x >= -1000
 src3_receiver_background_count_x_ub_value: - 1000 y_3_x
   + 1000 src3_receiver_background_count_x <= 0
 src3_receiver_background_count_x_ub_gate: - 1000 src3_case_background_x
   + 1000 src3_receiver_background_count_x <= 0
 src3_receiver_background_count_x_lb: - 1000 y_3_x
   - 1000 src3_case_background_x + 1000 src3_receiver_background_count_x
   >= -1000
 define_Nnorm_receiver_src3_x: - 1000 src3_receiver_chi_count_x
   - 1000 src3_receiver_alpha_count_x
   - 1000 src3_receiver_background_count_x + 1000 Nnorm_receiver_src3_x
   = 0
 Nnorm_source_src3_sram_x_ub_value: - 1000 Nnorm_source_src3_x
   + 1000 Nnorm_source_src3_sram_x <= 0
 Nnorm_source_src3_sram_x_ub_gate: - 1000 B1_x
   + 1000 Nnorm_source_src3_sram_x <= 0
 Nnorm_source_src3_sram_x_lb: - 1000 B1_x - 1000 Nnorm_source_src3_x
   + 1000 Nnorm_source_src3_sram_x >= -1000
 define_a12_without_chi3_y: - a12_y + chi3_y + a12_without_chi3_y = 0
 src3_case_chi_y_ub_0: - B3_y + src3_case_chi_y <= 0
 src3_case_chi_y_ub_1: - chi3_y + src3_case_chi_y <= 0
 src3_case_chi_y_lb: - B3_y - chi3_y + src3_case_chi_y >= -1
 src3_case_alpha_y_ub_0: - B3_y + src3_case_alpha_y <= 0
 src3_case_alpha_y_ub_1: - a12_without_chi3_y + src3_case_alpha_y <= 0
 src3_case_alpha_y_lb: - B3_y - a12_without_chi3_y + src3_case_alpha_y
   >= -1
 src3_case_background_y_ub_pos: - B3_y + src3_case_background_y <= 0
 src3_case_background_y_ub_neg: a12_y + src3_case_background_y <= 1
 src3_case_background_y_lb: - B3_y + a12_y + src3_case_background_y >= 0
 src3_case_partition_y: - B3_y + src3_case_chi_y + src3_case_alpha_y
   + src3_case_background_y = 0
 src3_receiver_chi_count_y_ub_value: - 1000 inv_L3k1k0_y
   + 1000 src3_receiver_chi_count_y <= 0
 src3_receiver_chi_count_y_ub_gate: - 1000 src3_case_chi_y
   + 1000 src3_receiver_chi_count_y <= 0
 src3_receiver_chi_count_y_lb: - 1000 inv_L3k1k0_y - 1000 src3_case_chi_y
   + 1000 src3_receiver_chi_count_y >= -1000
 src3_receiver_alpha_count_y_ub_value: - 1000 inv_L3k1_y
   + 1000 src3_receiver_alpha_count_y <= 0
 src3_receiver_alpha_count_y_ub_gate: - 1000 src3_case_alpha_y
   + 1000 src3_receiver_alpha_count_y <= 0
 src3_receiver_alpha_count_y_lb: - 1000 inv_L3k1_y - 1000 src3_case_alpha_y
   + 1000 src3_receiver_alpha_count_y >= -1000
 src3_receiver_background_count_y_ub_value: - 1000 y_3_y
   + 1000 src3_receiver_background_count_y <= 0
 src3_receiver_background_count_y_ub_gate: - 1000 src3_case_background_y
   + 1000 src3_receiver_background_count_y <= 0
 src3_receiver_background_count_y_lb: - 1000 y_3_y
   - 1000 src3_case_background_y + 1000 src3_receiver_background_count_y
   >= -1000
 define_Nnorm_receiver_src3_y: - 1000 src3_receiver_chi_count_y
   - 1000 src3_receiver_alpha_count_y
   - 1000 src3_receiver_background_count_y + 1000 Nnorm_receiver_src3_y
   = 0
 Nnorm_source_src3_sram_y_ub_value: - 1000 Nnorm_source_src3_y
   + 1000 Nnorm_source_src3_sram_y <= 0
 Nnorm_source_src3_sram_y_ub_gate: - 1000 B1_y
   + 1000 Nnorm_source_src3_sram_y <= 0
 Nnorm_source_src3_sram_y_lb: - 1000 B1_y - 1000 Nnorm_source_src3_y
   + 1000 Nnorm_source_src3_sram_y >= -1000
 define_a12_without_chi3_z: - a12_z + chi3_z + a12_without_chi3_z = 0
 src3_case_chi_z_ub_0: - B3_z + src3_case_chi_z <= 0
 src3_case_chi_z_ub_1: - chi3_z + src3_case_chi_z <= 0
 src3_case_chi_z_lb: - B3_z - chi3_z + src3_case_chi_z >= -1
 src3_case_alpha_z_ub_0: - B3_z + src3_case_alpha_z <= 0
 src3_case_alpha_z_ub_1: - a12_without_chi3_z + src3_case_alpha_z <= 0
 src3_case_alpha_z_lb: - B3_z - a12_without_chi3_z + src3_case_alpha_z
   >= -1
 src3_case_background_z_ub_pos: - B3_z + src3_case_background_z <= 0
 src3_case_background_z_ub_neg: a12_z + src3_case_background_z <= 1
 src3_case_background_z_lb: - B3_z + a12_z + src3_case_background_z >= 0
 src3_case_partition_z: - B3_z + src3_case_chi_z + src3_case_alpha_z
   + src3_case_background_z = 0
 src3_receiver_chi_count_z_ub_value: - 1000 inv_L3k1k0_z
   + 1000 src3_receiver_chi_count_z <= 0
 src3_receiver_chi_count_z_ub_gate: - 1000 src3_case_chi_z
   + 1000 src3_receiver_chi_count_z <= 0
 src3_receiver_chi_count_z_lb: - 1000 inv_L3k1k0_z - 1000 src3_case_chi_z
   + 1000 src3_receiver_chi_count_z >= -1000
 src3_receiver_alpha_count_z_ub_value: - 1000 inv_L3k1_z
   + 1000 src3_receiver_alpha_count_z <= 0
 src3_receiver_alpha_count_z_ub_gate: - 1000 src3_case_alpha_z
   + 1000 src3_receiver_alpha_count_z <= 0
 src3_receiver_alpha_count_z_lb: - 1000 inv_L3k1_z - 1000 src3_case_alpha_z
   + 1000 src3_receiver_alpha_count_z >= -1000
 src3_receiver_background_count_z_ub_value: - 1000 y_3_z
   + 1000 src3_receiver_background_count_z <= 0
 src3_receiver_background_count_z_ub_gate: - 1000 src3_case_background_z
   + 1000 src3_receiver_background_count_z <= 0
 src3_receiver_background_count_z_lb: - 1000 y_3_z
   - 1000 src3_case_background_z + 1000 src3_receiver_background_count_z
   >= -1000
 define_Nnorm_receiver_src3_z: - 1000 src3_receiver_chi_count_z
   - 1000 src3_receiver_alpha_count_z
   - 1000 src3_receiver_background_count_z + 1000 Nnorm_receiver_src3_z
   = 0
 Nnorm_source_src3_sram_z_ub_value: - 1000 Nnorm_source_src3_z
   + 1000 Nnorm_source_src3_sram_z <= 0
 Nnorm_source_src3_sram_z_ub_gate: - 1000 B1_z
   + 1000 Nnorm_source_src3_sram_z <= 0
 Nnorm_source_src3_sram_z_lb: - 1000 B1_z - 1000 Nnorm_source_src3_z
   + 1000 Nnorm_source_src3_sram_z >= -1000
 define_Nnorm_oldread_receiver_src3_z: - 1000 Nnorm_receiver_src3_z
   + 0.1953125 B3_times_k2_z + 1000 Nnorm_oldread_receiver_src3_z = 0
 define_Nnorm_oldread_source_src3_z: 0.1953125 B3_z
   - 1000 Nnorm_source_src3_z + 1000 Nnorm_oldread_source_src3_z = 0
 Nnorm_oldread_source_src3_sram_z_ub_value:
   - 1000 Nnorm_oldread_source_src3_z
   + 1000 Nnorm_oldread_source_src3_sram_z <= 0
 Nnorm_oldread_source_src3_sram_z_ub_gate: - 1000 B1_z
   + 1000 Nnorm_oldread_source_src3_sram_z <= 0
 Nnorm_oldread_source_src3_sram_z_lb: - 1000 B1_z
   - 1000 Nnorm_oldread_source_src3_z
   + 1000 Nnorm_oldread_source_src3_sram_z >= -1000
 src4_select_sram_x_ub_pos: - B1_x + src4_select_sram_x <= 0
 src4_select_sram_x_ub_neg: B3_x + src4_select_sram_x <= 1
 src4_select_sram_x_lb: - B1_x + B3_x + src4_select_sram_x >= 0
 src4_select_ddr_x_ub_first: B1_x + src4_select_ddr_x <= 1
 src4_select_ddr_x_ub_second: B3_x + src4_select_ddr_x <= 1
 src4_select_ddr_x_lb: B1_x + B3_x + src4_select_ddr_x >= 1
 src4_source_partition_x: B3_x + src4_select_sram_x + src4_select_ddr_x
   = 1
 Nnorm_source_src4_sram_x_ub_value: - 1000 inv_k2_x
   + 1000 Nnorm_source_src4_sram_x <= 0
 Nnorm_source_src4_sram_x_ub_gate: - 1000 src4_select_sram_x
   + 1000 Nnorm_source_src4_sram_x <= 0
 Nnorm_source_src4_sram_x_lb: - 1000 inv_k2_x - 1000 src4_select_sram_x
   + 1000 Nnorm_source_src4_sram_x >= -1000
 Nnorm_source_src4_ddr_x_ub_value: - 1000 inv_k2_x
   + 1000 Nnorm_source_src4_ddr_x <= 0
 Nnorm_source_src4_ddr_x_ub_gate: - 1000 src4_select_ddr_x
   + 1000 Nnorm_source_src4_ddr_x <= 0
 Nnorm_source_src4_ddr_x_lb: - 1000 inv_k2_x - 1000 src4_select_ddr_x
   + 1000 Nnorm_source_src4_ddr_x >= -1000
 src4_select_sram_y_ub_pos: - B1_y + src4_select_sram_y <= 0
 src4_select_sram_y_ub_neg: B3_y + src4_select_sram_y <= 1
 src4_select_sram_y_lb: - B1_y + B3_y + src4_select_sram_y >= 0
 src4_select_ddr_y_ub_first: B1_y + src4_select_ddr_y <= 1
 src4_select_ddr_y_ub_second: B3_y + src4_select_ddr_y <= 1
 src4_select_ddr_y_lb: B1_y + B3_y + src4_select_ddr_y >= 1
 src4_source_partition_y: B3_y + src4_select_sram_y + src4_select_ddr_y
   = 1
 Nnorm_source_src4_sram_y_ub_value: - 1000 inv_k2_y
   + 1000 Nnorm_source_src4_sram_y <= 0
 Nnorm_source_src4_sram_y_ub_gate: - 1000 src4_select_sram_y
   + 1000 Nnorm_source_src4_sram_y <= 0
 Nnorm_source_src4_sram_y_lb: - 1000 inv_k2_y - 1000 src4_select_sram_y
   + 1000 Nnorm_source_src4_sram_y >= -1000
 Nnorm_source_src4_ddr_y_ub_value: - 1000 inv_k2_y
   + 1000 Nnorm_source_src4_ddr_y <= 0
 Nnorm_source_src4_ddr_y_ub_gate: - 1000 src4_select_ddr_y
   + 1000 Nnorm_source_src4_ddr_y <= 0
 Nnorm_source_src4_ddr_y_lb: - 1000 inv_k2_y - 1000 src4_select_ddr_y
   + 1000 Nnorm_source_src4_ddr_y >= -1000
 src4_select_sram_z_ub_pos: - B1_z + src4_select_sram_z <= 0
 src4_select_sram_z_ub_neg: B3_z + src4_select_sram_z <= 1
 src4_select_sram_z_lb: - B1_z + B3_z + src4_select_sram_z >= 0
 src4_select_ddr_z_ub_first: B1_z + src4_select_ddr_z <= 1
 src4_select_ddr_z_ub_second: B3_z + src4_select_ddr_z <= 1
 src4_select_ddr_z_lb: B1_z + B3_z + src4_select_ddr_z >= 1
 src4_source_partition_z: B3_z + src4_select_sram_z + src4_select_ddr_z
   = 1
 Nnorm_source_src4_sram_z_ub_value: - 1000 inv_k2_z
   + 1000 Nnorm_source_src4_sram_z <= 0
 Nnorm_source_src4_sram_z_ub_gate: - 1000 src4_select_sram_z
   + 1000 Nnorm_source_src4_sram_z <= 0
 Nnorm_source_src4_sram_z_lb: - 1000 inv_k2_z - 1000 src4_select_sram_z
   + 1000 Nnorm_source_src4_sram_z >= -1000
 Nnorm_source_src4_ddr_z_ub_value: - 1000 inv_k2_z
   + 1000 Nnorm_source_src4_ddr_z <= 0
 Nnorm_source_src4_ddr_z_ub_gate: - 1000 src4_select_ddr_z
   + 1000 Nnorm_source_src4_ddr_z <= 0
 Nnorm_source_src4_ddr_z_lb: - 1000 inv_k2_z - 1000 src4_select_ddr_z
   + 1000 Nnorm_source_src4_ddr_z >= -1000
 define_Nnorm_oldread_source_src4_rf_z: - 1000 B3_z
   + 0.1953125 B3_times_k2_z + 1000 Nnorm_oldread_source_src4_rf_z = 0
 define_Nnorm_oldread_source_src4_sram_z: 0.1953125 src4_select_sram_z
   - 1000 Nnorm_source_src4_sram_z + 1000 Nnorm_oldread_source_src4_sram_z
   = 0
 define_Nnorm_oldread_source_src4_ddr_z: 0.1953125 src4_select_ddr_z
   - 1000 Nnorm_source_src4_ddr_z + 1000 Nnorm_oldread_source_src4_ddr_z
   = 0
 hierarchy_01_x: [ L_1_x * k_0_x ] = 131072
 hierarchy_12_x: - L_1_x + [ L_2_x * k_1_x ] = 0
 hierarchy_23_x: - L_2_x + [ L_3_x * k_2_x ] = 0
 hierarchy_01_y: [ L_1_y * k_0_y ] = 8192
 hierarchy_12_y: - L_1_y + [ L_2_y * k_1_y ] = 0
 hierarchy_23_y: - L_2_y + [ L_3_y * k_2_y ] = 0
 hierarchy_01_z: [ L_1_z * k_0_z ] = 5120
 hierarchy_12_z: - L_1_z + [ L_2_z * k_1_z ] = 0
 hierarchy_23_z: - L_2_z + [ L_3_z * k_2_z ] = 0
 reciprocal_L_1_x: [ L_1_x * y_1_x ] = 1
 reciprocal_L_1_y: [ L_1_y * y_1_y ] = 1
 reciprocal_L_1_z: [ L_1_z * y_1_z ] = 1
 reciprocal_L_2_x: [ L_2_x * y_2_x ] = 1
 reciprocal_L_2_y: [ L_2_y * y_2_y ] = 1
 reciprocal_L_2_z: [ L_2_z * y_2_z ] = 1
 reciprocal_L_3_x: [ L_3_x * y_3_x ] = 1
 reciprocal_L_3_y: [ L_3_y * y_3_y ] = 1
 reciprocal_L_3_z: [ L_3_z * y_3_z ] = 1
 reciprocal_k2_x: [ k_2_x * inv_k2_x ] = 1
 define_product_L3k1_x: product_L3k1_x + [ - L_3_x * k_1_x ] = 0
 define_product_L3k1k0_x: product_L3k1k0_x + [ - k_0_x * product_L3k1_x ]
   = 0
 reciprocal_product_L3k1_x: [ product_L3k1_x * inv_L3k1_x ] = 1
 reciprocal_product_L3k1k0_x: [ product_L3k1k0_x * inv_L3k1k0_x ] = 1
 reciprocal_k2_y: [ k_2_y * inv_k2_y ] = 1
 define_product_L3k1_y: product_L3k1_y + [ - L_3_y * k_1_y ] = 0
 define_product_L3k1k0_y: product_L3k1k0_y + [ - k_0_y * product_L3k1_y ]
   = 0
 reciprocal_product_L3k1_y: [ product_L3k1_y * inv_L3k1_y ] = 1
 reciprocal_product_L3k1k0_y: [ product_L3k1k0_y * inv_L3k1k0_y ] = 1
 reciprocal_k2_z: [ k_2_z * inv_k2_z ] = 1
 define_product_L3k1_z: product_L3k1_z + [ - L_3_z * k_1_z ] = 0
 define_product_L3k1k0_z: product_L3k1k0_z + [ - k_0_z * product_L3k1_z ]
   = 0
 reciprocal_product_L3k1_z: [ product_L3k1_z * inv_L3k1_z ] = 1
 reciprocal_product_L3k1k0_z: [ product_L3k1k0_z * inv_L3k1k0_z ] = 1
 define_occupied_scaled_1_x: occupied_scaled_1_x + [
   - 1.6276041666666666e-04 L_1_z * selected_length_1_x ] = 0
 define_occupied_scaled_1_y: occupied_scaled_1_y + [
   - 1.6276041666666666e-04 L_1_z * selected_length_1_y ] = 0
 define_occupied_scaled_1_z: occupied_scaled_1_z + [
   - 1.6276041666666666e-04 L_1_y * selected_length_1_z ] = 0
 define_occupied_scaled_3_x: occupied_scaled_3_x + [
   - 0.0883883476483184 L_3_z * selected_length_3_x ] = 0
 define_occupied_scaled_3_y: occupied_scaled_3_y + [
   - 0.0883883476483184 L_3_z * selected_length_3_y ] = 0
 define_occupied_scaled_3_z: occupied_scaled_3_z + [
   - 0.0883883476483184 L_3_y * selected_length_3_z ] = 0
 pe_product_xy: product_k2_xy + [ - k_2_x * k_2_y ] = 0
 pe_product_xyz: [ k_2_z * product_k2_xy ] = 65536
 define_Nnorm_source_src3_x: - 1000 Nnorm_receiver_src3_x + [
   1000 k_2_x * Nnorm_source_src3_x ] = 0
 define_Nnorm_source_src3_y: - 1000 Nnorm_receiver_src3_y + [
   1000 k_2_y * Nnorm_source_src3_y ] = 0
 define_Nnorm_source_src3_z: - 1000 Nnorm_receiver_src3_z + [
   1000 k_2_z * Nnorm_source_src3_z ] = 0
 unit_k0_x_force_one: unit_k0_x = 1 -> k_0_x = 1
 unit_k0_x_force_nonunit: unit_k0_x = 0 -> k_0_x >= 2
 unit_k0_y_force_one: unit_k0_y = 1 -> k_0_y = 1
 unit_k0_y_force_nonunit: unit_k0_y = 0 -> k_0_y >= 2
 unit_k0_z_force_one: unit_k0_z = 1 -> k_0_z = 1
 unit_k0_z_force_nonunit: unit_k0_z = 0 -> k_0_z >= 2
 unit_k1_x_force_one: unit_k1_x = 1 -> k_1_x = 1
 unit_k1_x_force_nonunit: unit_k1_x = 0 -> k_1_x >= 2
 unit_k1_y_force_one: unit_k1_y = 1 -> k_1_y = 1
 unit_k1_y_force_nonunit: unit_k1_y = 0 -> k_1_y >= 2
 unit_k1_z_force_one: unit_k1_z = 1 -> k_1_z = 1
 unit_k1_z_force_nonunit: unit_k1_z = 0 -> k_1_z >= 2
 selected_length_1_x_off: B1_x = 0 -> selected_length_1_x = 0
 selected_length_1_x_on: B1_x = 1 -> - L_1_y + selected_length_1_x = 0
 selected_length_1_y_off: B1_y = 0 -> selected_length_1_y = 0
 selected_length_1_y_on: B1_y = 1 -> - L_1_x + selected_length_1_y = 0
 selected_length_1_z_off: B1_z = 0 -> selected_length_1_z = 0
 selected_length_1_z_on: B1_z = 1 -> - L_1_x + selected_length_1_z = 0
 selected_length_3_x_off: B3_x = 0 -> selected_length_3_x = 0
 selected_length_3_x_on: B3_x = 1 -> - L_3_y + selected_length_3_x = 0
 selected_length_3_y_off: B3_y = 0 -> selected_length_3_y = 0
 selected_length_3_y_on: B3_y = 1 -> - L_3_x + selected_length_3_y = 0
 selected_length_3_z_off: B3_z = 0 -> selected_length_3_z = 0
 selected_length_3_z_on: B3_z = 1 -> - L_3_x + selected_length_3_z = 0
 B3_times_k2_z_off: B3_z = 0 -> B3_times_k2_z = 0
 B3_times_k2_z_on: B3_z = 1 -> - k_2_z + B3_times_k2_z = 0
Bounds
 1 <= L_1_x <= 131072
 7.62939e-06 <= y_1_x <= 1
 1 <= L_1_y <= 8192
 1.220703125e-04 <= y_1_y <= 1
 1 <= L_1_z <= 5120
 1.9531250000000001e-04 <= y_1_z <= 1
 1 <= L_2_x <= 131072
 7.62939e-06 <= y_2_x <= 1
 1 <= L_2_y <= 8192
 1.220703125e-04 <= y_2_y <= 1
 1 <= L_2_z <= 5120
 1.9531250000000001e-04 <= y_2_z <= 1
 1 <= L_3_x <= 131072
 7.62939e-06 <= y_3_x <= 1
 1 <= L_3_y <= 8192
 1.220703125e-04 <= y_3_y <= 1
 1 <= L_3_z <= 5120
 1.9531250000000001e-04 <= y_3_z <= 1
 1 <= k_0_x <= 131072
 1 <= k_0_y <= 8192
 1 <= k_0_z <= 5120
 1 <= k_1_x <= 131072
 1 <= k_1_y <= 8192
 1 <= k_1_z <= 5120
 1 <= k_2_x <= 131072
 1 <= k_2_y <= 8192
 1 <= k_2_z <= 5120
 1 <= k_3_x <= 131072
 1 <= k_3_y <= 8192
 1 <= k_3_z <= 5120
 7.62939e-06 <= inv_k2_x <= 1
 1 <= product_L3k1_x <= 131072
 1 <= product_L3k1k0_x <= 131072
 7.62939e-06 <= inv_L3k1_x <= 1
 7.62939e-06 <= inv_L3k1k0_x <= 1
 1.220703125e-04 <= inv_k2_y <= 1
 1 <= product_L3k1_y <= 8192
 1 <= product_L3k1k0_y <= 8192
 1.220703125e-04 <= inv_L3k1_y <= 1
 1.220703125e-04 <= inv_L3k1k0_y <= 1
 1.9531250000000001e-04 <= inv_k2_z <= 1
 1 <= product_L3k1_z <= 5120
 1 <= product_L3k1k0_z <= 5120
 1.9531250000000001e-04 <= inv_L3k1_z <= 1
 1.9531250000000001e-04 <= inv_L3k1k0_z <= 1
 selected_length_1_x <= 8192
 occupied_scaled_1_x <= 6144
 selected_length_1_y <= 131072
 occupied_scaled_1_y <= 6144
 selected_length_1_z <= 131072
 occupied_scaled_1_z <= 6144
 selected_length_3_x <= 8192
 occupied_scaled_3_x <= 11.31370849898476
 selected_length_3_y <= 131072
 occupied_scaled_3_y <= 11.31370849898476
 selected_length_3_z <= 131072
 occupied_scaled_3_z <= 11.31370849898476
 1 <= product_k2_xy <= 65536
 src1_background_count_x <= 1
 Nnorm_receiver_src1_x <= 1
 src1_background_count_y <= 1
 Nnorm_receiver_src1_y <= 1
 src1_background_count_z <= 1
 Nnorm_receiver_src1_z <= 1
 Nnorm_oldread_src1_z <= 1
 src3_receiver_chi_count_x <= 1
 src3_receiver_alpha_count_x <= 1
 src3_receiver_background_count_x <= 1
 Nnorm_receiver_src3_x <= 1
 Nnorm_source_src3_x <= 1
 Nnorm_source_src3_sram_x <= 1
 src3_receiver_chi_count_y <= 1
 src3_receiver_alpha_count_y <= 1
 src3_receiver_background_count_y <= 1
 Nnorm_receiver_src3_y <= 1
 Nnorm_source_src3_y <= 1
 Nnorm_source_src3_sram_y <= 1
 src3_receiver_chi_count_z <= 1
 src3_receiver_alpha_count_z <= 1
 src3_receiver_background_count_z <= 1
 Nnorm_receiver_src3_z <= 1
 Nnorm_source_src3_z <= 1
 Nnorm_source_src3_sram_z <= 1
 B3_times_k2_z <= 5120
 Nnorm_oldread_receiver_src3_z <= 1
 Nnorm_oldread_source_src3_z <= 1
 Nnorm_oldread_source_src3_sram_z <= 1
 Nnorm_source_src4_sram_x <= 1
 Nnorm_source_src4_ddr_x <= 1
 Nnorm_source_src4_sram_y <= 1
 Nnorm_source_src4_ddr_y <= 1
 Nnorm_source_src4_sram_z <= 1
 Nnorm_source_src4_ddr_z <= 1
 Nnorm_oldread_source_src4_rf_z <= 1
 Nnorm_oldread_source_src4_sram_z <= 1
 Nnorm_oldread_source_src4_ddr_z <= 1
 Constant = 1
Binaries
 B1_x B1_y B1_z B3_x B3_y B3_z a01_x a01_y a01_z a12_x a12_y a12_z
 unit_k0_x unit_k0_y unit_k0_z unit_k1_x unit_k1_y unit_k1_z all_unit_01
 all_unit_12 chi3_x chi3_y chi3_z src1_resident_alpha_x
 src1_resident_background_x src1_resident_alpha_y
 src1_resident_background_y src1_resident_alpha_z
 src1_resident_background_z a12_without_chi3_x src3_case_chi_x
 src3_case_alpha_x src3_case_background_x a12_without_chi3_y
 src3_case_chi_y src3_case_alpha_y src3_case_background_y
 a12_without_chi3_z src3_case_chi_z src3_case_alpha_z
 src3_case_background_z src4_select_sram_x src4_select_ddr_x
 src4_select_sram_y src4_select_ddr_y src4_select_sram_z src4_select_ddr_z
Generals
 L_1_x L_1_y L_1_z L_2_x L_2_y L_2_z L_3_x L_3_y L_3_z k_0_x k_0_y k_0_z
 k_1_x k_1_y k_1_z k_2_x k_2_y k_2_z k_3_x k_3_y k_3_z product_L3k1_x
 product_L3k1k0_x product_L3k1_y product_L3k1k0_y product_L3k1_z
 product_L3k1k0_z product_k2_xy B3_times_k2_z
End
