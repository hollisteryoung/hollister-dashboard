"""
domain.faults — Fault and reason-code parsing from raw historian strings.

The historian stores alarm context as free-text in the Faults column of
StatusBlocks, e.g. "FC=@Fault_PLC1_0042@(Gripper no part) RC=1007".  This
module parses those strings into structured FC / RC labels so the pipeline
can group and display alarms without joining to a separate lookup table.
rc_display_label() and rc_description_from_fc_entries() are the main public
API consumed by domain.clusters and the analytics layer.
"""

import re

import pandas as pd


_FC_PATTERN = re.compile(r'FC=@?((?:Fault|Alarm|Warning)_(?:PLC1_)?\d+)@?\s*(?:\(([^)]*)\))?')
_RC_PATTERN = re.compile(r'RC=(\d+)')


def _rc_fault_id_variants(rc: int) -> list:
    """Return all fault_id strings that could encode a given RC code.

    Covers the three prefix styles and two zero-padding widths used by the PLC:
      Fault_0042, Fault_42, Fault_PLC1_42  (and Alarm_*, Warning_* variants).
    """
    prefixes = ["Fault", "Alarm", "Warning"]
    variants = []
    for pfx in prefixes:
        variants.extend([f"{pfx}_{rc:04d}", f"{pfx}_{rc}", f"{pfx}_PLC1_{rc}"])
    return variants


def parse_faults_field(faults_str):
    """Parse StatusBlocks.Faults into structured RC/FC data.

    Returns dict with:
      rc_code         – first (primary) RC as int, or None
      rc_codes_all    – list of all RC codes as ints (may be >1)
      rc_description  – description for the primary RC, or None
      fc_entries      – list of {fault_id, description} for all FC codes
      raw             – original string
    """
    if not faults_str or faults_str == 'x':
        return {'rc_code': None, 'rc_codes_all': [], 'rc_description': None, 'fc_entries': [], 'raw': faults_str or ''}

    rc_codes_all = [int(m) for m in _RC_PATTERN.findall(faults_str)]
    rc_code = rc_codes_all[0] if rc_codes_all else None

    fc_entries = [
        {'fault_id': m.group(1), 'description': m.group(2)}
        for m in _FC_PATTERN.finditer(faults_str)
    ]

    rc_description = None
    if rc_code is not None:
        for variant in _rc_fault_id_variants(rc_code):
            for fc in fc_entries:
                if fc['fault_id'] == variant and fc['description']:
                    rc_description = fc['description']
                    break
            if rc_description:
                break

    return {
        'rc_code': rc_code,
        'rc_codes_all': rc_codes_all,
        'rc_description': rc_description,
        'fc_entries': fc_entries,
        'raw': faults_str,
    }


def rc_display_label(parsed, fallback_alarm_text=None):
    """Build a human-readable label for the root cause.

    Priority: RC with desc → RC without → first FC with desc → FOF fallback → Unattributed.
    """
    if parsed['rc_code'] is not None:
        if parsed['rc_description']:
            return parsed['rc_description']
        return f"RC={parsed['rc_code']}"
    if parsed['fc_entries']:
        first = parsed['fc_entries'][0]
        if first['description']:
            return first['description']
        return first['fault_id']
    if fallback_alarm_text and fallback_alarm_text != 'No Alarm Text':
        return fallback_alarm_text
    return 'Unattributed'


def rc_description_from_fc_entries(rc, fc_entries):
    """Find the embedded description for a given RC code in the FC entry list."""
    for variant in _rc_fault_id_variants(rc):
        for fc in fc_entries:
            if fc['fault_id'] == variant and fc['description']:
                return fc['description']
    return None


def build_rc_lookup(df, faults_col='Faults'):
    """Build a cross-row lookup dict {rc_code: description} from a DataFrame.

    Registers descriptions for all RC codes (primary and secondary) so that
    a secondary RC that never appears as primary in a given window still gets
    a description via cross-row backfill.
    """
    lookup = {}
    for faults in df[faults_col].dropna():
        parsed = parse_faults_field(faults)
        for rc in parsed['rc_codes_all']:
            if rc not in lookup:
                desc = rc_description_from_fc_entries(rc, parsed['fc_entries'])
                if desc:
                    lookup[rc] = desc
    return lookup


def add_rc_columns(df, faults_col='Faults', fallback_col=None):
    """Parse Faults field and add RC attribution columns to DataFrame.

    Columns added:
      rc_code           – primary (first) RC as int, or None. Preserved for
                          backward compatibility with all downstream groupby operations.
      rc_label          – human-readable label for the primary RC.
      rc_codes_all      – list[int] of all RC codes in this stop (may be >1).
                          Use explode_rc_codes() to redistribute weight across all RCs.
      rc_codes_count    – len(rc_codes_all); 0 for stops with no RC.
      attribution_method – 'RC' | 'FC-fallback' | 'FOF-fallback' | 'Unattributed'
    """
    lookup = build_rc_lookup(df, faults_col)
    rc_codes = []
    rc_labels = []
    all_rc_lists = []
    rc_counts = []
    methods = []

    for _, row in df.iterrows():
        parsed = parse_faults_field(row[faults_col] if pd.notna(row[faults_col]) else None)
        if parsed['rc_code'] is not None and not parsed['rc_description']:
            parsed['rc_description'] = lookup.get(parsed['rc_code'])
        fallback = row[fallback_col] if fallback_col and fallback_col in row.index else None
        label = rc_display_label(parsed, fallback)

        rc_codes.append(parsed['rc_code'])
        rc_labels.append(label)
        all_rc_lists.append(parsed['rc_codes_all'])
        rc_counts.append(len(parsed['rc_codes_all']))

        if parsed['rc_code'] is not None:
            methods.append('RC')
        elif parsed['fc_entries']:
            methods.append('FC-fallback')
        elif fallback and fallback != 'No Alarm Text':
            methods.append('FOF-fallback')
        else:
            methods.append('Unattributed')

    df['rc_code'] = rc_codes
    df['rc_label'] = rc_labels
    df['rc_codes_all'] = all_rc_lists
    df['rc_codes_count'] = rc_counts
    df['attribution_method'] = methods
    return df


def explode_rc_codes(df, weight_col=None, strategy='equal', rc_lookup=None):
    """Explode multi-RC stops into one row per RC code for honest attribution.

    Consumers that want accurate Pareto / segment charts should call this instead
    of reading rc_code directly. Stops with no RC codes pass through unchanged.

    Parameters
    ----------
    df         : DataFrame produced by add_rc_columns (must have rc_codes_all column)
    weight_col : numeric column to redistribute across RC codes (e.g. 'duration_min')
    strategy   : 'equal'        – divide weight_col evenly across all RC codes
                 'primary_full' – primary RC gets full weight, secondaries get 0
    rc_lookup  : optional {rc_code: description} dict from build_rc_lookup(); when
                 provided, rc_label is updated per-row to match the exploded RC code

    Returns a long-form DataFrame with one row per (stop, rc_code).
    rc_code on each row is set to the specific RC for that row.
    """
    records = []
    for _, row in df.iterrows():
        codes = row['rc_codes_all'] if isinstance(row.get('rc_codes_all'), list) else []
        if not codes:
            records.append(row.to_dict())
            continue
        n = len(codes)
        for i, code in enumerate(codes):
            r = row.to_dict()
            r['rc_code'] = code
            if rc_lookup is not None and 'rc_label' in r:
                r['rc_label'] = rc_lookup.get(code, f"RC={code}")
            if weight_col and weight_col in r and r[weight_col] is not None:
                if strategy == 'equal':
                    r[weight_col] = row[weight_col] / n
                elif strategy == 'primary_full':
                    r[weight_col] = row[weight_col] if i == 0 else 0.0
            records.append(r)
    return pd.DataFrame(records)
