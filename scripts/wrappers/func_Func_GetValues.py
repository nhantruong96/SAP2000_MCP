# ============================================================
# Wrapper: SapModel.Func.GetValues
# Category: Functions
# Description: Retrieve the time and value data point pairs of any defined function
# Verified: 2026-09-04
# Prerequisites: Model open, at least one function defined
# ============================================================
"""
Usage: Reads back the discrete (abscissa, ordinate) pairs of ANY function
       family — response spectrum, time history, power spectral density,
       steady state. Use it to plot a spectrum, to check what a code-based
       spectrum actually generated, or to export a curve for comparison.

API Signature:
  SapModel.Func.GetValues(Name, NumberItems, MyTime, Value)

ByRef Output:
  raw[0]  = NumberItems (Long)   — number of pairs returned
  raw[1]  = MyTime (tuple)       — abscissa array
  raw[2]  = Value (tuple)        — ordinate array
  raw[-1] = ret_code (0=success)

Parameters:
  Name        : str   — Name of an existing function, any family
  NumberItems : int   — Placeholder, pass 0
  MyTime      : list  — Placeholder, pass []
  Value       : list  — Placeholder, pass []

Notes:
  - This method lives on the Func parent object, NOT on Func.FuncRS or
    Func.FuncTH. SapModel.Func.FuncRS.GetValues does not exist.
  - MyTime is in seconds for response spectrum and time history functions,
    and in cycles per second for power spectral density and steady state.
  - An undefined name returns ret_code 1, not an empty array, so a zero
    length result means the function really has no points.
"""

# --- Minimal setup (fresh model) ---
SapModel.InitializeNewModel()
SapModel.File.NewBlank()
ret = SapModel.SetPresentUnits(6)  # kN_m_C
assert ret == 0, f"SetPresentUnits failed: {ret}"

# --- Reference 1: user response spectrum with known period/value pairs ---
rs_name = "RS_CHECK"
periods = [0.0, 0.15, 0.50, 1.50, 4.00]
values = [0.40, 1.00, 1.00, 0.33, 0.125]
raw = SapModel.Func.FuncRS.SetUser(rs_name, len(periods), periods, values, 0.05)
assert raw[-1] == 0, f"FuncRS.SetUser failed: {raw}"

# --- Reference 2: sine time history, a different function family ---
# SetSine returns a bare Long (no ByRef outputs), unlike SetUser.
th_name = "TH_CHECK"
ret = SapModel.Func.FuncTH.SetSine(th_name, 1.0, 16, 4, 1.25)
assert ret == 0, f"FuncTH.SetSine failed: {ret}"

# --- Target function: read back the response spectrum ---
raw = SapModel.Func.GetValues(rs_name, 0, [], [])
assert raw[-1] == 0, f"Func.GetValues failed on {rs_name}: {raw[-1]}"

n_read = raw[0]
time_read = list(raw[1])
value_read = list(raw[2])

# --- Verification: the defined curve must round-trip exactly ---
assert n_read == len(periods), f"count mismatch: {n_read} vs {len(periods)}"
assert len(time_read) == n_read, f"MyTime length {len(time_read)} != {n_read}"
assert len(value_read) == n_read, f"Value length {len(value_read)} != {n_read}"
for i in range(n_read):
    assert abs(time_read[i] - periods[i]) < 1e-9, f"period[{i}] {time_read[i]} != {periods[i]}"
    assert abs(value_read[i] - values[i]) < 1e-9, f"value[{i}] {value_read[i]} != {values[i]}"

# --- Verification on a second function family ---
raw_th = SapModel.Func.GetValues(th_name, 0, [], [])
assert raw_th[-1] == 0, f"Func.GetValues failed on {th_name}: {raw_th[-1]}"
n_th = raw_th[0]
th_time = list(raw_th[1])
th_value = list(raw_th[2])
assert n_th > 0, "sine TH returned no data points"
assert len(th_time) == n_th and len(th_value) == n_th, "TH array length mismatch"
# 4 cycles of a 1.0 s sine at 16 steps per cycle: 65 points ending at 4.0 s
assert abs(th_time[-1] - 4.0) < 1e-6, f"expected last time 4.0 s, got {th_time[-1]}"

# --- Negative control: an undefined name must NOT return 0 ---
raw_bad = SapModel.Func.GetValues("NO_SUCH_FUNCTION", 0, [], [])
assert raw_bad[-1] != 0, "undefined function name wrongly returned success"

# --- Result ---
result["function"] = "SapModel.Func.GetValues"
result["byref_layout"] = "[NumberItems, MyTime[], Value[], ret_code]"
result["rs_num_items"] = n_read
result["rs_time"] = time_read
result["rs_value"] = value_read
result["th_num_items"] = n_th
result["th_time_last"] = th_time[-1]
result["missing_name_ret"] = raw_bad[-1]
result["status"] = "verified"
