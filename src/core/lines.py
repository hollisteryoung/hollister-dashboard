"""
core.lines — Per-line configuration: table names, column aliases, sub-systems.

The same pipeline scripts run against NGP2, HU3, and any future lines.
All physical differences (DB table names, historian column names, which
sub-systems are present, shift boundaries, OEE thresholds) are declared here
rather than scattered through the pipeline code.  Scripts call get_line_config()
once at module level and use get_table() / get_col() / get_thresholds() to stay
line-agnostic.  The active line is chosen via --line CLI arg or the LINE env var.
"""

import os
import sys
from types import SimpleNamespace

# Physical historian column names shared across all lines.  Logical→physical
# mappings that differ per line should be declared in the per-line "columns"
# dict; missing keys fall back to get_col()'s identity fallback.
_DEFAULT_COLUMNS = {
    "start_ts":       "Start_TS",
    "end_ts":         "End_TS",
    "running_status": "RunningStatus",
    "faults":         "Faults",
    "stop_type":      "StopType",
    "start_id":       "StartID",
    "alarm_text":     "ActualAlarmText",
    "output_ppm":     "Output_PPM",
    "counter_total":  "Counter_Total",
    "counter_good":   "Counter_Good",
    "t_stamp":        "t_stamp",
}

# Stop-duration thresholds shared across all lines.  Override per line by
# declaring a "thresholds" dict with only the keys that differ.
_DEFAULT_THRESHOLDS = {
    "micro_stop_min": 2,
    "short_stop_min": 2,
    "long_stop_min": 10,
    "burst_gap_sec": 60,
}

LINE_CONFIGS = {
    "ngp2": {
        "line_id": "ngp2",
        "label": "NGP2",
        "work_center": "NGP2",
        "database": "ngp2_may7n-27n",
        "shared_tables": {
            "stop_reasons": "master",
            "stop_reasons_expanded": "master",
            "order_management": "master",
        },
        "tables": {
            "status_blocks": "tbl_SCP_NGP2_StatusBlocks",
            "downtime_overlays": "tbl_SCP_NGP2_DowntimeOverlays",
            "fof_changes": "tbl_TG_NGP2_HH_FOFchanges_WithReasonCode",
            "fof_changes_alt": "tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT",
            "live_oee": "tbl_TG_NGP2_HH_LiveOEE",
            "machine_state": "tbl_TG_NGP2_HH_OmacState",
            "output_stats": "tbl_TG_NGP2_HH_OutputStats",
            "ppm": "tbl_TG_NGP2_HH_PartsPerMinute",
            "alarm_stack": "tbl_TG_NGP2_HH_AlarmStack",
            "buttons": "tbl_TG_NGP2_HH_Buttons_TEMP",
            "operator_stop_events": "tbl_TG_NGP2_HH_OperatorStopEvents",
            "resets": "tbl_TG_NGP2_HH_Resets_TEMP",
            "station_reject_summary": "tbl_TG_NGP2_HH_StationRejectCountSummary",
            "format": "tbl_TG_NGP2_Format",
            "hydration_counters": "tbl_TG_NGP2_HH_StationsCounters_Hydration",
            "foil_counters": "tbl_TG_NGP2_HH_StationsCounters_Foil",
            "foam_current_product": "tbl_TG_NGP2_Foam_CurrentProduct",
            "foam_function_state": "tbl_TG_NGP2_Foam_FoamFunctionState",
            "foam_machine_state": "tbl_TG_NGP2_Foam_MachineState",
            "foam_message_text": "tbl_TG_NGP2_Foam_MessageTextRaw",
            "foam_air_flow": "tbl_TG_NGP2_Foam_AirInjectFlowRate",
            "foam_air_pressure": "tbl_TG_NGP2_Foam_AirInjectPressure",
            "foam_nozzle_time": "tbl_TG_NGP2_Foam_NozzleTimeActual",
            "foam_solution_flow": "tbl_TG_NGP2_Foam_SolutionFlowRate",
            "schubert_product": "tbl_TG_NGP2_Schubert_ActiveProduct",
            "schubert_alarms": "tbl_TG_NGP2_Schubert_AlarmStack",
            "schubert_mode": "tbl_TG_NGP2_Schubert_MachineMode",
            "schubert_state": "tbl_TG_NGP2_Schubert_MachineState",
            "order_management": "tbl_SCP_OrderManagement",
            "stop_reasons": "tbl_LKUP_StopReasons",
            "stop_reasons_expanded": "tbl_LKUP_StopReasonsExpanded",
        },
        "fof_ndx_col": "tbl_tg_ngp2_hh_fofchanges_withreasoncode_ndx",
        "machine_state_type": "omac",
        "machine_state_text_col": "OmacMachineState_Text",
        "machine_state_number_col": "OmacMachineState_Number",
        "output_stats_ndx_col": "tbl_tg_ngp2_hh_outputstats_ndx",
        "output_stats_extra_bad_cols": ["Counter_Bad_Stn073", "Counter_Bad_Stn081"],
        "ppm_ndx_col": "tbl_tg_ngp2_hh_partsperminute_ndx",
        # Logical → physical historian column names.  The engine references columns
        # only through get_col(cfg, <logical>); override here per line without
        # touching analytics code.  Undeclared keys fall back to the logical name.
        "columns": _DEFAULT_COLUMNS,
        "sub_systems": ["foam", "foil", "schubert", "hydration"],
        "halted_states": ["Halted"],
        "stopped_states": ["Stopped"],
        "running_states": ["Execute"],
        "excluded_days": ["2026-05-19", "2026-05-20"],
        "default_time_range": ("2026-05-07", "2026-05-27"),
        "shift_boundaries": [7, 19],
        # When a sub-system's data starts mid-window (sensor reconfig, late
        # commissioning), queries that target it must clip to this start date
        # to avoid pulling in null/garbage rows that skew stats.
        "subsystem_availability": {
            "hydration": {"from": "2026-03-12"},
            "foam": {"from": "2026-04-17"},
        },
        "station_sequence": [
            ("Stn010", "Load catheter rear"),
            ("Stn012", "Camera align cap rear"),
            ("Stn012", "Pressing aligned cap"),
            ("Stn030", "Load catheter front"),
            ("Stn031", "Camera align cap front"),
            ("Stn031", "Pressing aligned cap"),
            ("Stn052", "Positioning tip"),
            ("Stn064", "Remove cap"),
            ("Stn080", "Foam dosing"),
            ("Stn080", "Time after dosing"),
            ("Stn090", "Re-assemble cap"),
            ("Stn095", "Check presence cap"),
            ("Stn100", "For counter"),
            ("Stn100", "IPC"),
            ("Stn100", "Pick and Place catheter"),
            ("Stn100", "Rework"),
        ],
        # Declarative SPC panels. The engine loops over this list; nothing about
        # nozzle/camera/vision is hardcoded in the pipelines or presentation.
        #  - collector "single": one counter table + LIKE pattern + fixed lane count
        #    (handled generically by _collect_counter_subgroups).
        #  - collector "<name>": a custom collector registered in
        #    spc.CUSTOM_SPC_COLLECTORS (see "camera_dual" — the escape hatch).
        # Presentation strings (title/lane_prefix/div ids) live here so the same
        # config drives both the shift and weekly renderers.
        "spc_panels": [
            {
                "key": "nozzle_spc",
                "title": "Nozzle Reject SPC",
                "lane_prefix": "N",
                "div_id": "nozzle-spc",
                "weekly_div_base": "w-nozzle",
                "gate_table": "hydration_counters",
                "collector": "single",
                "table_key": "hydration_counters",
                "counter_col": "Hyd_Station_Fail_Counters",
                "name_col": "Hyd_Station_Fail_Counter_Names",
                "pattern": "Stn080_Foam dosing_1_12_%",
                "n_expected": 10,
                "divisor": 10,
                "lane_key_mode": "name_template",
                "name_template": "Stn080_Foam dosing_1_12_{i}",
            },
            {
                "key": "camera_spc",
                "title": "Camera Reject SPC (Cap Orientation)",
                "lane_prefix": "Lane ",
                "div_id": "camera-spc",
                "weekly_div_base": "w-camera",
                "gate_table": "hydration_counters",
                "collector": "camera_dual",
                "lane_key_mode": "synthetic",
                "lane_key_prefix": "lane",
                "n_expected": 10,
                "active_filter": True,
                "params": {
                    "table_key": "hydration_counters",
                    "counter_col": "Hyd_Station_Fail_Counters",
                    "name_col": "Hyd_Station_Fail_Counter_Names",
                    "rear_pattern": "Stn012_Camera align cap rear_1_7_%",
                    "front_pattern": "Stn031_Camera align cap front_1_9_%",
                    "reset_name": "Stn012_Camera align cap rear_1_7_2",
                    "divisor": 10,
                    "n_expected": 10,
                    "min_subgroups": 5,
                },
            },
            {
                "key": "vision_spc",
                "title": "Vision System SPC (Stn 041 Print Inspection)",
                "weekly_title": "Vision System SPC (Stn 041)",
                "lane_prefix": "Lane ",
                "div_id": "vision-spc",
                "weekly_div_base": "w-vision",
                "gate_table": "foil_counters",
                "collector": "single",
                "table_key": "foil_counters",
                "counter_col": "Foil_Station_Fail_Counters",
                "name_col": "Foil_Station_Fail_Counter_Names",
                "pattern": "%St_041 Vision system printing%",
                "n_expected": 5,
                "divisor": 5,
                "lane_key_mode": "discovered_sorted",
            },
        ],
        "thresholds": _DEFAULT_THRESHOLDS,
        "segments": {
            "order": ["hydration", "foil", "schubert", "manual", "unattributed"],
            "code_ranges": {
                "hydration": (0, 2000),
                "foil":      (2000, 3000),
                "schubert":  (3000, 9999),
            },
            "keywords": {
                "hydration": r"catheter|gripper|PMK|hydration|cover holder|\bfoam\b|\bdosing\b|nozzle|stn080|inject\s*pressure",
                "foil":      r"printer|labeler|pouch|edge\s*trim|sealing|foil",
                "schubert":  r"schubert|frame [a-z]|closing area",
            },
            "colors": {
                "hydration":    "#e76f51",
                "foil":         "#e9c46a",
                "schubert":     "#2a9d8f",
                "manual":       "#636e72",
                "unattributed": "#b2bec3",
            },
            "labels": {
                "hydration":    "Hydration",
                "foil":         "Foil",
                "schubert":     "Schubert",
                "manual":       "Manual",
                "unattributed": "Other",
            },
            "cause_clusters": {
                "hydration": [
                    ("Gripper",         r"gripper|funnel"),
                    ("Catheter Reject", r"catheter|rejected"),
                    ("Foam",            r"\bfoam\b|\bdosing\b|nozzle|stn080|inject\s*pressure"),
                    ("PMK / Media",     r"\bPMK\b|empty.*mode"),
                    ("Timing",          r"takes longer|maximum allowed|\bMSM\b"),
                    ("Cover",           r"cover holder"),
                    ("Cap",             r"\bcap\b"),
                    ("Limit Switch",    r"limit switch"),
                ],
                "foil": [
                    ("Printer",          r"printer"),
                    ("Labeler",          r"labeler"),
                    ("Edge Trim",        r"edge\s*trim|trim broken"),
                    ("Scanner",          r"laser scanner|scanner"),
                    ("Positioning",      r"position|product not in"),
                    ("Sealing Lane",     r"sealing\s*lane"),
                    ("Detection Fault",  r"detection\s*fault|reject\s*fault"),
                ],
                "schubert": [
                    ("Line Stop / Wait", r"waiting|line stop|downstream"),
                    ("Frame",            r"frame"),
                    ("Closing",          r"closing"),
                ],
            },
        },
    },
    "hu3": {
        "line_id": "hu3",
        "label": "HU3",
        "work_center": "HU3",
        "database": "hu3_may",
        "tables": {
            "status_blocks": "tbl_SCP_HU3_StatusBlocks",
            "downtime_overlays": "tbl_SCP_HU3_DowntimeOverlays",
            "fof_changes": "tbl_TG_HU3_HH_FOFchanges_WithReasonCode",
            "live_oee": "tbl_TG_HU3_HH_LiveOEE",
            "machine_state": "tbl_TG_HU3_HH_MachineState",
            "output_stats": "tbl_TG_HU3_HH_OutputStats",
            "ppm": "tbl_TG_HU3_HH_PartsPerMinute",
            "op_marked_defects": "tbl_SCP_HU3_OpMarkedDefects",
            "operator_actions": "tbl_TG_HU3_HH_OperatorActions",
            "station_cycle": "tbl_TG_HU3_HH_StationCycleMinMaxResults",
            "batch_report": "tbl_TG_HU3_HH_BatchReport",
            "pouch_counters": "tbl_TG_HU3_HH_StationsCounters_Pouch",
            "order_management": "tbl_SCP_OrderManagement",
            "stop_reasons": "tbl_LKUP_StopReasons",
            "stop_reasons_expanded": "tbl_LKUP_StopReasonsExpanded",
        },
        "shared_tables": {
            "order_management": "master",
            "stop_reasons": "master",
            "stop_reasons_expanded": "master",
        },
        "fof_ndx_col": "tbl_tg_HU3_hh_fofchanges_withreasoncode_ndx",
        "machine_state_type": "custom",
        "machine_state_text_col": "StateMachineState_Text",
        "machine_state_number_col": "StateMachineState_Number",
        "output_stats_ndx_col": "tbl_tg_hu3_hh_outputstats_ndx",
        "output_stats_extra_bad_cols": [],
        "ppm_ndx_col": "tbl_tg_hu3_hh_partsperminute_ndx",
        "columns": _DEFAULT_COLUMNS,
        "sub_systems": [],
        "halted_states": ["Halted", "HaltConditionReached"],
        "stopped_states": ["Stopped"],
        "running_states": ["Producing"],
        "excluded_days": [],
        "default_time_range": ("2026-05-04", "2026-05-21"),
        "shift_boundaries": [7, 19],
        "station_sequence": [
            ("Stn005", "Station 005"),
            ("Stn020", "Station 020"),
            ("Stn022", "Station 022"),
            ("Stn026", "Station 026"),
            ("Stn070", "Station 070"),
            ("Stn074", "Station 074"),
            ("Stn076", "Station 076"),
            ("Stn148", "Station 148"),
            ("Stn149", "Station 149"),
            ("Stn410", "Station 410"),
        ],
        # op_marked_defects ends 2026-05-11; all other tables cover the full window.
        "subsystem_availability": {
            "op_marked_defects": {"to": "2026-05-12"},
        },
        "spc_panels": [],   # HU3 has no counter-reset SPC tables
        "thresholds": _DEFAULT_THRESHOLDS,
        "segments": {
            "order": ["web", "sealing", "handling", "pneumatic", "manual", "unattributed"],
            "code_ranges": {},
            "keywords": {
                "web":       r"reel|web torn|splice|all rolls empty|vacuum splice",
                "sealing":   r"sealing head|temperature|knife|hot printer|current limit",
                "handling":  r"gripper|fork|pickup position|push.?out piston|parallel gripper|cylinder switch",
                "pneumatic": r"valve|air valve|flow monitor|cooling",
            },
            "colors": {
                "web":          "#3B82F6",
                "sealing":      "#F59E0B",
                "handling":     "#8B5CF6",
                "pneumatic":    "#10B981",
                "manual":       "#636e72",
                "unattributed": "#b2bec3",
            },
            "labels": {
                "web":          "Web / Reel",
                "sealing":      "Sealing",
                "handling":     "Handling",
                "pneumatic":    "Pneumatic",
                "manual":       "Manual",
                "unattributed": "Other",
            },
            "cause_clusters": {
                "web": [
                    ("End of Reel", r"end of reel|prewarning.*reel|all rolls empty"),
                    ("Web Torn",    r"web torn"),
                    ("Splice",      r"splice|splice module|last splice"),
                ],
                "sealing": [
                    ("Sealing Head", r"sealing head|current limit"),
                    ("Temperature",  r"temperature"),
                    ("Knife",        r"knife"),
                    ("Printer",      r"hot printer"),
                ],
                "handling": [
                    ("Gripper",  r"gripper|pickup position|parallel gripper"),
                    ("Fork",     r"fork"),
                    ("Piston",   r"push.?out piston"),
                    ("Cylinder", r"cylinder switch"),
                ],
                "pneumatic": [
                    ("Air Pressure", r"air valve"),
                    ("Valve on Web", r"valve.*web|valve.*inserted|valve.*area"),
                    ("Cooling",      r"cooling|flow monitor"),
                ],
            },
        },
    },
}


def get_line_config(line_id=None):
    """Get config for a line. Checks --line arg, LINE env var, then defaults to ngp2."""
    if line_id is None:
        for i, arg in enumerate(sys.argv):
            if arg == "--line" and i + 1 < len(sys.argv):
                line_id = sys.argv[i + 1].lower()
                break
        if line_id is None:
            line_id = os.environ.get("LINE", "ngp2").lower()
    if line_id not in LINE_CONFIGS:
        raise ValueError(f"Unknown line: {line_id!r}. Available: {list(LINE_CONFIGS)}")
    cfg = LINE_CONFIGS[line_id]
    # Allow the deployment to point a line at a differently-named database without
    # editing code: DB_NAME_<LINE> (e.g. DB_NAME_NGP2) overrides, else generic DB_NAME.
    override = os.environ.get(f"DB_NAME_{line_id.upper()}") or os.environ.get("DB_NAME")
    if override:
        cfg = {**cfg, "database": override}
        # Shared lookup tables (stop reasons, order mgmt) live in a separate DB in
        # the dev snapshots ("master") but are co-located on the live server.
        # DB_NAME_SHARED overrides; default is the same DB as the line's data.
        shared_db = os.environ.get("DB_NAME_SHARED", override)
        if cfg.get("shared_tables"):
            cfg["shared_tables"] = {k: shared_db for k in cfg["shared_tables"]}
    return cfg


def get_table(cfg, logical_name):
    """Return the physical table name for a logical name, prefixed with dbo.

    If the table lives in a different database (declared in shared_tables),
    the returned reference uses three-part naming: database.dbo.table.
    """
    tbl = cfg["tables"].get(logical_name)
    if tbl is None:
        raise KeyError(f"Table {logical_name!r} not configured for line {cfg['line_id']}")
    shared_db = cfg.get("shared_tables", {}).get(logical_name)
    if shared_db:
        return f"{shared_db}.dbo.{tbl}"
    return f"dbo.{tbl}"


def get_col(cfg, logical):
    """Return the physical historian column name for a logical column key.

    Falls back to the logical key itself when no mapping exists, so universal
    columns (e.g. t_stamp) need not be declared per line.
    """
    return cfg.get("columns", {}).get(logical, logical)


def get_subsystem_window(cfg, key, time_from, time_to):
    """Return (from, to) clipped by subsystem_availability[key].

    Use this whenever querying a table that may have a narrower data window
    than the main analysis period. If no entry exists for key, returns
    (time_from, time_to) unchanged.
    """
    avail = cfg.get("subsystem_availability", {}).get(key, {})
    start = max(time_from, avail["from"]) if "from" in avail else time_from
    end = min(time_to, avail["to"]) if "to" in avail else time_to
    return start, end


def has_subsystem(cfg, name):
    """Check whether a sub-system exists on this line."""
    return name in cfg["sub_systems"]


def has_table(cfg, logical_name):
    """Check whether a table is configured for this line."""
    return logical_name in cfg["tables"]


def get_spc_panels(cfg):
    """Return the declarative SPC panel list for a line (empty if none)."""
    return cfg.get("spc_panels", [])


def get_thresholds(cfg):
    """Return stop-duration thresholds for the line, with safe defaults."""
    return {**_DEFAULT_THRESHOLDS, **cfg.get("thresholds", {})}


def get_status_block_cols(cfg):
    """Return a SimpleNamespace with the physical StatusBlocks column names.

    Attributes mirror the module-level C_* constants used historically in
    metrics/oee.py and pipelines/weekly_analysis.py, but derived from
    cfg so they are line-agnostic.

        cols.C_START   — start timestamp column name
        cols.C_END     — end timestamp column name
        cols.C_RUN     — running-status flag column name
        cols.C_FAULTS  — faults / RC payload column name
        cols.C_STOPTYPE — stop-type classifier column name
        cols.C_STARTID — FK column referencing fof_changes
        cols.C_ALARM   — alarm text column name (fof_changes)
    """
    return SimpleNamespace(
        C_START=get_col(cfg, "start_ts"),
        C_END=get_col(cfg, "end_ts"),
        C_RUN=get_col(cfg, "running_status"),
        C_FAULTS=get_col(cfg, "faults"),
        C_STOPTYPE=get_col(cfg, "stop_type"),
        C_STARTID=get_col(cfg, "start_id"),
        C_ALARM=get_col(cfg, "alarm_text"),
    )


def output_dir(cfg, phase="analyses"):
    """Return the output directory for a line, e.g. output/analyses/hu3/."""
    return os.path.join("output", phase, cfg["line_id"])
