# 2번 마이그레이션 골든 테스트 결과 (2026-08-12)

대상: `scratch/backtest_sar_bb_20260809.py`  /  기간: 전체(31,793봉, 2025-01-10 ~ 2026-08-12)
대조 항목: 거래별 entry_time, exit_time, direction, entry_price, exit_price, pnl_pt, is_force, reason + 요약지표 8종

| 조합 | 거래수 | 결과 |
|---|---:|---|
| `b1_sar_af005` | 543 | 일치 |
| `b1_sar_af04` | 1087 | 일치 |
| `b1_sar_cap3` | 1091 | 일치 |
| `b1_sar_intraday_only` | 832 | 일치 |
| `b1_sar_mult03` | 845 | 일치 |
| `b1_sar_mult10` | 820 | 일치 |
| `b1_sar_slmult05` | 1037 | 일치 |
| `b1_sar_slmult20` | 715 | 일치 |
| `b2_bb_nogapfill` | 4191 | 일치 |
| `b2_bb_squeeze_on` | 2072 | 일치 |
| `b2_bb_trail` | 2071 | 일치 |
| `b2_bb_w14s25` | 1622 | 일치 |
| `b2_bb_w40s15` | 2647 | 일치 |
| `b2_sar_bbw60` | 790 | 일치 |
| `b2_sar_sq_w200q50` | 707 | 일치 |
| `b2_sar_sq_w50q10` | 861 | 일치 |
| `b3_bo_bb` | 1319 | 일치 |
| `b3_bo_k005` | 755 | 일치 |
| `b3_bo_mid_prevrange` | 772 | 일치 |
| `b3_bo_open_std` | 729 | 일치 |
| `b3_bo_pc_prevrange` | 625 | 일치 |
| `b3_kf_qr` | 828 | 일치 |
| `b3_kf_window80` | 844 | 일치 |
| `b3_trim2` | 833 | 일치 |
| `b4_atrcut2` | 829 | 일치 |
| `b4_gapguard` | 535 | 일치 |
| `b4_ma100` | 816 | 일치 |
| `b4_ma100_invert` | 125 | 일치 |
| `b4_ma_slope_lb100` | 467 | 일치 |
| `b4_minstd` | 567 | 일치 |
| `b4_regime` | 807 | 일치 |
| `b4_trend15` | 822 | 일치 |
| `b5_bb_overnight_ts` | 1871 | 일치 |
| `b5_entry_window` | 645 | 일치 |
| `b5_loss_limit` | 742 | 일치 |
| `b5_overnight` | 751 | 일치 |
| `b5_reentry` | 636 | 일치 |
| `b5_reverse` | 967 | 일치 |
| `b5_slip05` | 823 | 일치 |
| `b5_timestop_tight` | 1761 | 일치 |
| `p1_bb_default` | 1995 | 일치 |
| `p1_bb_ma_slope` | 1413 | 일치 |
| `p1_sar_breakout_open` | 683 | 일치 |
| `p1_sar_breakout_pc_atr` | 714 | 일치 |
| `p1_sar_default` | 832 | 일치 |
| `p1_sar_intraday_atr_cap` | 952 | 일치 |
| `p1_sar_nosqueeze_regime_ts` | 2729 | 일치 |
| `p1_sar_overnight_reverse` | 856 | 일치 |

**합계: 48조합 / 51,974거래 / 불일치 0건**
